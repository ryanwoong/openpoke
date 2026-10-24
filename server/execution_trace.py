"""Bounded, process-local execution spans shared by asynchronous agent tasks."""

import asyncio
import inspect
import threading
from contextvars import ContextVar
from datetime import datetime, timezone
from functools import wraps
from uuid import uuid4

_current_span = ContextVar("execution_span", default=None)
_SECRET_KEYS = {
    "authorization",
    "api_key",
    "apikey",
    "access_token",
    "refresh_token",
    "password",
    "secret",
}


def sanitize(value, depth=0):
    if depth > 5:
        return "[depth limit]"
    if isinstance(value, dict):
        return {
            str(key): (
                "[redacted]"
                if str(key).lower() == "token" or str(key).lower().endswith("_token") or any(secret in str(key).lower() for secret in _SECRET_KEYS)
                else sanitize(item, depth + 1)
            )
            for key, item in list(value.items())[:50]
        }
    if isinstance(value, (list, tuple)):
        return [sanitize(item, depth + 1) for item in value[:50]]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:4000]


class TraceStore:
    def __init__(self, limit=1000):
        self.limit = limit
        self._spans = {}
        self._lock = threading.Lock()

    def start(self, kind, label, detail, parent=None):
        identifier = uuid4().hex
        with self._lock:
            if parent and parent["id"] not in self._spans:
                parent = None
            span = {
                "id": identifier,
                "run_id": parent["run_id"] if parent else identifier,
                "parent_id": parent["id"] if parent else None,
                "kind": kind,
                "label": str(label)[:200],
                "status": "running",
                "started_at": datetime.now(timezone.utc).isoformat(),
                "ended_at": None,
                "input": sanitize(detail),
                "output": None,
            }
            self._spans[identifier] = span
            # Evict whole inactive runs so retained graphs never lose their parents.
            while len(self._spans) > self.limit:
                active = {item["run_id"] for item in self._spans.values() if item["status"] == "running"}
                candidate = next((item["run_id"] for item in self._spans.values() if item["run_id"] not in active), None)
                if candidate is None:
                    break
                self._spans = {key: item for key, item in self._spans.items() if item["run_id"] != candidate}
        return span

    def finish(self, span, status, output):
        with self._lock:
            span.update(status=status, ended_at=datetime.now(timezone.utc).isoformat(), output=sanitize(output))

    def snapshot(self):
        with self._lock:
            return [dict(span) for span in self._spans.values()]

    def clear(self):
        with self._lock:
            self._spans.clear()


trace_store = TraceStore()


def current_run_id():
    span = _current_span.get()
    return span["run_id"] if span else None


def traced(kind, describe):
    """Preserve parent context across create_task and restore it after each span."""

    def decorate(function):
        def begin(args, kwargs):
            label, detail = describe(*args, **kwargs)
            span = trace_store.start(kind, label, detail, _current_span.get())
            return span, _current_span.set(span)

        def finish(span, result):
            success = getattr(result, "success", True)
            if isinstance(result, tuple) and result and isinstance(result[0], bool):
                success = result[0]
            payload = result[1] if isinstance(result, tuple) and len(result) == 2 else result
            if isinstance(payload, dict):
                success = success and payload.get("success", True) is not False and payload.get("successful", True) is not False and not payload.get("error")
            if span["kind"] == "model" and isinstance(result, dict):
                result = {"model": result.get("model"), "usage": result.get("usage")}
            if hasattr(result, "__dataclass_fields__"):
                result = vars(result)
            trace_store.finish(span, "completed" if success else "failed", result)

        if inspect.iscoroutinefunction(function):

            @wraps(function)
            async def async_wrapper(*args, **kwargs):
                span, token = begin(args, kwargs)
                try:
                    result = await function(*args, **kwargs)
                    finish(span, result)
                    return result
                except BaseException as exc:
                    from .services.conversation.request_context import current_request

                    context = current_request.get()
                    reason = context.stopped_reason if context and isinstance(exc, asyncio.CancelledError) else None
                    error = "Task timed out at its overall deadline" if reason == "timeout" else "Task cancelled by user" if reason == "cancelled" else str(exc)
                    trace_store.finish(span, "cancelled" if isinstance(exc, asyncio.CancelledError) else "failed", {"error": error})
                    raise
                finally:
                    _current_span.reset(token)

            return async_wrapper

        @wraps(function)
        def sync_wrapper(*args, **kwargs):
            span, token = begin(args, kwargs)
            try:
                result = function(*args, **kwargs)
                finish(span, result)
                return result
            except BaseException as exc:
                trace_store.finish(span, "failed", {"error": str(exc)})
                raise
            finally:
                _current_span.reset(token)

        return sync_wrapper

    return decorate
