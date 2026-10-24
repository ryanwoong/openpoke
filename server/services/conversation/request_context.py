"""Keep task ownership, deadlines, and cancellation across asynchronous handoffs."""

import asyncio
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from uuid import uuid4


@dataclass
class ChatRequestContext:
    request_id: str
    prompt: str
    retry_of: str | None = None
    deadline: float | None = None
    tasks: set[asyncio.Task] = field(default_factory=set)
    timer: asyncio.TimerHandle | None = None
    stopped_reason: str | None = None
    read_only: bool = True


current_request: ContextVar[ChatRequestContext | None] = ContextVar("chat_request", default=None)
_requests: dict[str, ChatRequestContext] = {}


def remaining_seconds() -> float | None:
    context = current_request.get()
    return max(0, context.deadline - time.monotonic()) if context and context.deadline is not None else None


def _release(context, task):
    context.tasks.discard(task)
    if not context.tasks:
        if context.timer:
            context.timer.cancel()
        if _requests.get(context.request_id) is context:
            _requests.pop(context.request_id, None)


def create_request_task(coroutine):
    task = asyncio.create_task(coroutine)
    context = current_request.get()
    if context:
        context.tasks.add(task)
        task.add_done_callback(lambda finished: _release(context, finished))
    return task


def stop_request(request_id: str, reason: str = "cancelled") -> bool:
    context = _requests.get(request_id)
    if not context or context.stopped_reason:
        return False
    context.stopped_reason = reason
    try:
        if reason != "cleared":
            from . import get_conversation_log

            token = current_request.set(context)
            try:
                message = "Task timed out at its overall deadline" if reason == "timeout" else "Task cancelled by user"
                get_conversation_log().record_task_failure(task_failure_message(message))
            finally:
                current_request.reset(token)
    finally:
        for task in tuple(context.tasks):
            task.get_loop().call_soon_threadsafe(task.cancel)

    return True


def clear_requests():
    for request_id in tuple(_requests):
        stop_request(request_id, "cleared")


@contextmanager
def request_scope(request_id: str | None, prompt: str, *, timeout_seconds: float | None = None, retry_of: str | None = None):
    context = ChatRequestContext(request_id or uuid4().hex, prompt, retry_of=retry_of, deadline=time.monotonic() + timeout_seconds if timeout_seconds else None)
    token = current_request.set(context)
    try:
        owner = asyncio.current_task()
    except RuntimeError:
        owner = None
    if owner:
        _requests[context.request_id] = context
        context.tasks.add(owner)
        if timeout_seconds:
            context.timer = asyncio.get_running_loop().call_later(timeout_seconds, stop_request, context.request_id, "timeout")
    try:
        yield context
    finally:
        if owner:
            _release(context, owner)
        current_request.reset(token)


def task_failure_message(detail: str) -> str:
    if "cancelled by user" in detail.lower():
        return "Task cancelled. Work already completed is preserved; an external action already in progress may still finish."
    if "context summary" in detail.lower() or "compaction timed out" in detail.lower():
        return "I couldn't finish processing the search results, so I don't have a verified answer. Try again or use a smaller date range."
    if "timed out" in detail.lower():
        return "The task timed out before it finished. I don't have a verified result for this request. You can retry or use a smaller task."
    return "I couldn't finish this task, so I don't have a verified result for this request. Please try again."
