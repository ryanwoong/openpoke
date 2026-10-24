"""Short-lived, explicit retry checkpoints for completed email reads only."""

import copy
import json
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field

from .request_context import current_request


@dataclass
class ReadCheckpoint:
    prompt: str
    mailbox: str
    saved_at: float
    results: dict = field(default_factory=dict)


class ReadCheckpoints:
    def __init__(self, ttl_seconds=900, limit=16, max_bytes=4 * 1024 * 1024):
        self.ttl_seconds = ttl_seconds
        self.limit = limit
        self.max_bytes = max_bytes
        self._entries = OrderedDict()
        self._lock = threading.Lock()

    def _prune(self):
        now = time.monotonic()
        for key in list(self._entries):
            if now - self._entries[key].saved_at > self.ttl_seconds:
                del self._entries[key]

    def clear(self):
        with self._lock:
            self._entries.clear()

    def invalidate(self, request_id):
        with self._lock:
            self._entries.pop(request_id, None)

    def available(self, request_id, *, prompt=None, mailbox=None):
        with self._lock:
            self._prune()
            entry = self._entries.get(request_id)
            return bool(entry and entry.results and (prompt is None or entry.prompt == prompt) and (mailbox is None or entry.mailbox == mailbox))

    def get(self, tool, arguments, mailbox):
        context = current_request.get()
        if not context or context.stopped_reason or not context.read_only or not mailbox:
            return False, None
        key = json.dumps([tool, arguments], sort_keys=True)
        with self._lock:
            self._prune()
            for identifier in (context.request_id, context.retry_of):
                entry = self._entries.get(identifier)
                if entry and entry.prompt == context.prompt and entry.mailbox == mailbox and key in entry.results:
                    return True, copy.deepcopy(entry.results[key])
        return False, None

    def put(self, tool, arguments, mailbox, value):
        context = current_request.get()
        if not context or not context.read_only or context.stopped_reason or not mailbox:
            return
        key = json.dumps([tool, arguments], sort_keys=True)
        with self._lock:
            self._prune()
            entry = self._entries.get(context.request_id)
            if entry is None or entry.mailbox != mailbox or entry.prompt != context.prompt:
                entry = ReadCheckpoint(context.prompt, mailbox, time.monotonic())
            results = {**entry.results, key: value}
            if len(json.dumps(results, ensure_ascii=False).encode()) > self.max_bytes:
                return
            entry.results = copy.deepcopy(results)
            self._entries[context.request_id] = entry
            self._entries.move_to_end(context.request_id)
            while len(self._entries) > self.limit:
                self._entries.popitem(last=False)


read_checkpoints = ReadCheckpoints()
