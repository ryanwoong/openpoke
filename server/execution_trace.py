"""Durable execution spans shared by asynchronous agent tasks."""

import asyncio
import atexit
import inspect
import json
import os
import sqlite3
import threading
from contextvars import ContextVar
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
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
        return {str(key): ("[redacted]" if str(key).lower() == "token" or str(key).lower().endswith("_token") or any(secret in str(key).lower() for secret in _SECRET_KEYS) else sanitize(item, depth + 1)) for key, item in list(value.items())[:50]}
    if isinstance(value, (list, tuple)):
        return [sanitize(item, depth + 1) for item in value[:50]]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:4000]


class TraceStore:
    def __init__(self, limit=1000, path=None):
        self.limit = limit
        self._lock = threading.Lock()
        if path is not None:
            path = Path(path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch(mode=0o600, exist_ok=True)
            path.chmod(0o600)
        self._connection = sqlite3.connect(str(path) if path is not None else ":memory:", check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        with self._connection:
            self._connection.execute("""
                CREATE TABLE IF NOT EXISTS execution_spans (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    id TEXT NOT NULL UNIQUE,
                    run_id TEXT NOT NULL,
                    parent_id TEXT,
                    kind TEXT NOT NULL,
                    label TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    input TEXT NOT NULL,
                    output TEXT NOT NULL
                )
            """)
            self._connection.execute("CREATE INDEX IF NOT EXISTS execution_spans_run ON execution_spans (run_id, status)")

    def start(self, kind, label, detail, parent=None):
        identifier = uuid4().hex
        with self._lock, self._connection:
            if parent and not self._connection.execute("SELECT 1 FROM execution_spans WHERE id = ?", (parent["id"],)).fetchone():
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
            self._connection.execute(
                "INSERT INTO execution_spans (id, run_id, parent_id, kind, label, status, started_at, ended_at, input, output) VALUES (:id, :run_id, :parent_id, :kind, :label, :status, :started_at, :ended_at, :input, :output)",
                {**span, "input": json.dumps(span["input"]), "output": "null"},
            )
            # Evict whole inactive runs so retained graphs never lose their parents.
            while self._connection.execute("SELECT COUNT(*) FROM execution_spans").fetchone()[0] > self.limit:
                candidate = self._connection.execute("""
                    SELECT run_id FROM execution_spans
                    GROUP BY run_id HAVING SUM(status = 'running') = 0
                    ORDER BY MIN(started_at), MIN(sequence) LIMIT 1
                """).fetchone()
                if candidate is None:
                    break
                self._connection.execute("DELETE FROM execution_spans WHERE run_id = ?", (candidate["run_id"],))
        return span

    def finish(self, span, status, output):
        with self._lock, self._connection:
            span.update(status=status, ended_at=datetime.now(timezone.utc).isoformat(), output=sanitize(output))
            self._connection.execute(
                "UPDATE execution_spans SET status = ?, ended_at = ?, output = ? WHERE id = ?",
                (span["status"], span["ended_at"], json.dumps(span["output"]), span["id"]),
            )

    def recover_interrupted(self):
        with self._lock, self._connection:
            self._connection.execute(
                "UPDATE execution_spans SET status = 'cancelled', ended_at = ?, output = ? WHERE status = 'running'",
                (datetime.now(timezone.utc).isoformat(), json.dumps({"error": "The backend restarted before this activity finished. Submit the task again to retry."})),
            )

    def snapshot(self):
        with self._lock:
            rows = self._connection.execute("SELECT id, run_id, parent_id, kind, label, status, started_at, ended_at, input, output FROM execution_spans ORDER BY started_at, sequence").fetchall()
            return [{**dict(row), "input": json.loads(row["input"]), "output": json.loads(row["output"])} for row in rows]

    def clear(self):
        with self._lock, self._connection:
            self._connection.execute("DELETE FROM execution_spans")

    def close(self):
        with self._lock:
            self._connection.close()


trace_store = TraceStore(path=os.environ.get("OPENPOKE_TRACE_DB", Path(__file__).resolve().parent / "data" / "execution_history.db"))
atexit.register(trace_store.close)


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
