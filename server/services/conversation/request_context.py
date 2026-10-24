"""Keep the initiating request attached to asynchronous workers and their replies."""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from uuid import uuid4


@dataclass
class ChatRequestContext:
    request_id: str
    prompt: str


current_request: ContextVar[ChatRequestContext | None] = ContextVar("chat_request", default=None)


@contextmanager
def request_scope(request_id: str | None, prompt: str):
    context = ChatRequestContext(request_id or uuid4().hex, prompt)
    token = current_request.set(context)
    try:
        yield context
    finally:
        current_request.reset(token)


def task_failure_message(detail: str) -> str:
    if "cancelled by user" in detail.lower():
        return "Task cancelled. Work already completed is preserved; an external action already in progress may still finish."
    if "context summary" in detail.lower() or "compaction timed out" in detail.lower():
        return "I couldn't finish processing the search results, so I don't have a verified answer. Try again or use a smaller date range."
    if "timed out" in detail.lower():
        return "The task timed out before it finished. I don't have a verified result for this request. You can retry or use a smaller task."
    return "I couldn't finish this task, so I don't have a verified result for this request. Please try again."
