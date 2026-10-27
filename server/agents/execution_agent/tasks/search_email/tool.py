"""Headers-first Gmail discovery and selective, resumable body retrieval."""

from __future__ import annotations

import asyncio
import base64
import json
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Callable, Dict

from server.config import get_settings
from server.logging_config import logger
from server.model_settings import completion_target
from server.openrouter_client.context import context_compactor, input_budget
from server.services.conversation.request_context import current_request, remaining_seconds
from server.services.gmail import EmailTextCleaner, execute_gmail_tool, get_active_gmail_user_id
from .schemas import TASK_TOOL_NAME, READ_TOOL_NAME

SEARCH_TIMEOUT_SECONDS = 90
ANSWER_RESERVE_SECONDS = 120
MAX_THREAD_PAGES = 10
MAX_SNAPSHOT_BYTES = 4 * 1024 * 1024
_EMAIL_CLEANER = EmailTextCleaner(max_url_length=40)


def _email_input_budget() -> int:
    settings = get_settings()
    target = completion_target(settings.execution_agent_model)
    window = target.context_length or settings.context_window_tokens
    return input_budget(window, min(settings.context_output_tokens, window // 4))


def _byte_limit() -> int:
    return min(60000, max(4000, _email_input_budget() * 3 // 8))


def _data(raw: Any) -> dict:
    if not isinstance(raw, dict) or raw.get("successful") is False or raw.get("success") is False or raw.get("error"):
        raise ValueError("Gmail returned an unsuccessful response")
    data = raw.get("data", raw)
    if not isinstance(data, dict):
        raise ValueError("Gmail returned invalid response data")
    return data


def _identifier(value: Any) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 128:
        raise ValueError("Expected a Gmail message/thread ID from a tool result")
    return value


def _metadata(message: dict) -> dict:
    if not isinstance(message, dict):
        raise ValueError("Gmail returned an invalid message")
    payload = message.get("payload")
    headers = payload.get("headers", []) if isinstance(payload, dict) else []
    headers = {h["name"].casefold(): h.get("value", "") for h in headers if isinstance(h, dict) and isinstance(h.get("name"), str)} if isinstance(headers, list) else {}
    stamp = message.get("messageTimestamp") or headers.get("date") or ""
    try:
        date = datetime.fromisoformat(stamp.replace("Z", "+00:00")) if isinstance(stamp, str) else None
    except ValueError:
        try:
            date = parsedate_to_datetime(stamp)
        except (ValueError, TypeError):
            date = None
    if not date and str(message.get("internalDate", "")).isdigit():
        date = datetime.fromtimestamp(int(message["internalDate"]) / 1000, timezone.utc)
    stamp = date.astimezone(timezone.utc).isoformat() if date and date.tzinfo else str(stamp)[:100]
    preview = message.get("snippet") or message.get("preview") or ""
    if isinstance(preview, dict):
        preview = preview.get("body", "")
    return {
        "id": _identifier(message.get("messageId") or message.get("id")),
        "thread_id": str(message.get("threadId") or message.get("thread_id") or "")[:128],
        "subject": str(message.get("subject") or headers.get("subject") or "(No subject)")[:300],
        "sender": str(message.get("sender") or headers.get("from") or "Unknown sender")[:200],
        "recipient": str(message.get("to") or headers.get("to") or "")[:200],
        "timestamp": stamp,
        "snippet": str(preview)[:280],
    }


async def _invoke(tool: str, mailbox: str, arguments: dict) -> dict:
    raw = await asyncio.to_thread(execute_gmail_tool, tool, mailbox, arguments=arguments)
    if get_active_gmail_user_id() != mailbox:
        raise ValueError("Gmail account changed during retrieval; restart the read")
    return _data(raw)


async def _bounded(operation):
    remaining = remaining_seconds()
    allowance = min(SEARCH_TIMEOUT_SECONDS, max(0, remaining - ANSWER_RESERVE_SECONDS)) if remaining is not None else SEARCH_TIMEOUT_SECONDS
    if allowance <= 0:
        return {"error": "No read was started; time was reserved for the answer."}
    try:
        async with asyncio.timeout(allowance):
            return await operation()
    except TimeoutError:
        return {"error": "Gmail retrieval timed out; the requested read was not completed."}
    except (OSError, ValueError, RuntimeError) as exc:
        logger.warning("Gmail retrieval failed: %s", exc)
        return {"error": str(exc)}


async def task_email_search(search_query: str, mode: str = "auto", page_token: str | None = None) -> Any:
    if not isinstance(search_query, str) or not search_query.strip():
        return {"error": "search_query must not be empty"}
    if mode not in {"auto", "preview", "discover", "all"}:
        return {"error": "mode must be auto, preview, discover, or all"}
    if page_token is not None and (not isinstance(page_token, str) or len(page_token) > 4096):
        return {"error": "Invalid Gmail page token"}
    mailbox = get_active_gmail_user_id()
    if not mailbox:
        return {"error": "Gmail not connected. Please connect Gmail in settings first."}
    query = search_query.strip()
    preview = mode == "preview" or mode == "auto" and re.fullmatch(r"(?:(?:is:unread|in:inbox|newer_than:\d+[dmy])\s*)+", query, re.IGNORECASE)

    async def discover():
        page_size = 10 if preview else min(25, max(1, _byte_limit() // 1800))
        arguments = {"query": query, "max_results": page_size, "include_payload": False, "verbose": False, "include_spam_trash": False, "format": "metadata", "metadata_headers": ["From", "To", "Subject", "Date"]}
        if page_token:
            arguments["page_token"] = page_token
        data = await _invoke("GMAIL_FETCH_EMAILS", mailbox, arguments)
        raw_messages = data.get("messages")
        if not isinstance(raw_messages, list):
            raise ValueError("Gmail returned no valid message list")
        if len(raw_messages) > page_size and not preview:
            raise ValueError("Gmail exceeded the requested metadata page size; narrow the query")
        by_id = {}
        for message in raw_messages[:page_size]:
            record = _metadata(message)
            by_id.setdefault(record["id"], record)
        messages = list(by_id.values())
        next_token = data.get("nextPageToken") or None
        if next_token is not None and not isinstance(next_token, str):
            raise ValueError("Gmail returned an invalid page token")
        if next_token and next_token == page_token:
            raise ValueError("Gmail repeated a result page; query coverage is not complete")
        return {
            "mode": "preview" if preview else "discovery",
            "query": query,
            "messages": messages,
            "body_scope": "headers",
            "page_token": page_token,
            "next_page_token": next_token,
            "search_complete": not bool(next_token) and len(raw_messages) <= page_size,
            "coverage_required": mode == "all",
            "has_more": bool(next_token) or len(raw_messages) > page_size,
            "returned_count": len(messages),
            "total_count": None,
            "content_scope": "Headers and snippets only; bodies were not read. Read a relevant thread/message with task_email_read. Follow next_page_token for exhaustive query coverage; matches are not orders.",
        }

    return await _bounded(discover)


def _cursor(artifact: str, index: int, offset: int) -> str:
    return base64.urlsafe_b64encode(json.dumps([artifact, index, offset]).encode()).decode()


def _decode_cursor(cursor: str) -> tuple[str, int, int]:
    if not isinstance(cursor, str) or len(cursor) > 1024:
        raise ValueError("Invalid read cursor")
    try:
        data = json.loads(base64.b64decode(cursor, altchars=b"-_", validate=True))
    except (ValueError, TypeError):
        raise ValueError("Invalid read cursor") from None
    if not isinstance(data, list) or len(data) != 3 or not isinstance(data[0], str) or not re.fullmatch(r"[a-f0-9]{64}", data[0]) or any(type(value) is not int or value < 0 for value in data[1:]):
        raise ValueError("Invalid read cursor")
    return tuple(data)


def _body_record(message: dict, thread_id: str | None) -> dict:
    record = _metadata(message)
    if thread_id and record["thread_id"] and record["thread_id"] != thread_id:
        raise ValueError("Gmail returned a message from a different thread")
    record["thread_id"] = record["thread_id"] or thread_id or ""
    payload = message.get("payload")
    record["body_available"] = any(isinstance(message.get(key), str) for key in ("messageText", "textBody", "htmlBody")) or bool(_EMAIL_CLEANER._extract_mime_body(payload, "text/html") or _EMAIL_CLEANER._extract_mime_body(payload, "text/plain")) or isinstance(payload, dict) and isinstance(payload.get("body"), dict) and payload["body"].get("data") == ""
    record["clean_text"] = _EMAIL_CLEANER.clean_email_content(message)
    attachments = message.get("attachmentList") or []
    _, _, filenames = _EMAIL_CLEANER.extract_attachment_info(attachments if isinstance(attachments, list) else [])
    record["attachment_filenames"] = filenames
    return record


async def _snapshot(mailbox: str, scope: str, thread_id: str | None, message_id: str | None, generation: int) -> str:
    records = {}
    page = None
    seen = set()
    for _ in range(MAX_THREAD_PAGES):
        arguments = {"thread_id": thread_id} if thread_id else {"message_id": message_id, "format": "full"}
        if page:
            arguments["page_token"] = page
        data = await _invoke("GMAIL_FETCH_MESSAGE_BY_THREAD_ID" if thread_id else "GMAIL_FETCH_MESSAGE_BY_MESSAGE_ID", mailbox, arguments)
        raw_messages = data.get("messages") if thread_id else [data]
        if not isinstance(raw_messages, list) or not raw_messages:
            raise ValueError("Gmail returned no messages for the requested read")
        for message in raw_messages:
            record = _body_record(message, thread_id)
            if message_id and record["id"] != message_id:
                raise ValueError("Gmail returned a different message ID")
            records[record["id"]] = record
        source = json.dumps({"mailbox": mailbox, "scope_id": scope, "messages": sorted(records.values(), key=lambda record: (record["timestamp"], record["id"]))}, ensure_ascii=False)
        if len(source.encode()) > MAX_SNAPSHOT_BYTES:
            raise ValueError("Conversation exceeds the snapshot limit; read individual message IDs instead")
        page = data.get("nextPageToken") or None
        if not page:
            context = current_request.get()
            if context and context.stopped_reason:
                raise asyncio.CancelledError()
            return context_compactor.archive(source, generation=generation)
        if not isinstance(page, str) or page in seen:
            raise ValueError("Gmail repeated or invalidated a thread page; the conversation was not fully read")
        seen.add(page)
    raise ValueError("Conversation page limit reached; read individual message IDs instead")


def _read_page(artifact: str, snapshot: dict, index: int, offset: int) -> dict:
    records = snapshot["messages"]
    if index >= len(records) or offset > len(records[index]["clean_text"]):
        raise ValueError("Read cursor is outside the saved conversation")
    result = {"scope_id": snapshot["scope_id"], "snapshot_id": artifact, "message_count": len(records), "messages": [], "body_scope": "full", "read_complete": False, "next_cursor": None}
    limit = _byte_limit()
    while index < len(records):
        record = records[index]
        text = record["clean_text"]
        low, high = 0, len(text) - offset
        while low < high:
            middle = (low + high + 1) // 2
            fragment = {**record, "clean_text": text[offset : offset + middle], "message_index": index, "body_offset": offset, "body_total_chars": len(text)}
            candidate = {**result, "messages": result["messages"] + [fragment]}
            if len(json.dumps(candidate).encode()) <= limit - 1024:
                low = middle
            else:
                high = middle - 1
        fragment = {**record, "clean_text": text[offset : offset + low], "message_index": index, "body_offset": offset, "body_total_chars": len(text)}
        if len(json.dumps({**result, "messages": result["messages"] + [fragment]}).encode()) > limit - 1024 or low == 0 and offset < len(text):
            break
        result["messages"].append(fragment)
        offset += low
        if offset < len(text):
            break
        index += 1
        offset = 0
    if not result["messages"]:
        raise ValueError("Message metadata exceeds the response allowance")
    if index < len(records):
        result["next_cursor"] = _cursor(artifact, index, offset)
        result["body_scope"] = "fragments"
    else:
        result["read_complete"] = all(record["body_available"] for record in records)
    unavailable = [record["id"] for record in records[:index] if not record["body_available"]]
    result["remaining_message_count"] = len(records) - index + len(unavailable)
    result["unread_message_ids"] = (unavailable + [record["id"] for record in records[index : index + 10]])[:10]
    if not result["read_complete"]:
        result["content_scope"] = "Continue next_cursor to finish the selected conversation. Missing bodies must not be inferred from snippets." if result["next_cursor"] else "Some messages have no readable body; try their individual IDs or report that specific gap."
    return result


async def task_email_read(thread_id: str | None = None, message_id: str | None = None, cursor: str | None = None) -> dict:
    if bool(thread_id) == bool(message_id):
        return {"error": "Provide exactly one thread_id or message_id from discovery"}
    mailbox = get_active_gmail_user_id()
    if not mailbox:
        return {"error": "Gmail not connected. Please connect Gmail in settings first."}

    async def read():
        identifier = _identifier(thread_id or message_id)
        scope = ("thread:" if thread_id else "message:") + identifier
        generation = context_compactor.generation
        if cursor:
            artifact, index, offset = _decode_cursor(cursor)
        else:
            artifact = await _snapshot(mailbox, scope, thread_id, message_id, generation)
            index, offset = 0, 0
        path = context_compactor.directory / f"{artifact}.json"
        try:
            snapshot = json.loads(json.loads(path.read_text(encoding="utf-8"))["source"])
        except (FileNotFoundError, KeyError, ValueError):
            raise ValueError("Saved read is unavailable; restart the read without a cursor") from None
        if not isinstance(snapshot, dict) or not isinstance(snapshot.get("messages"), list):
            raise ValueError("Read cursor does not reference a saved conversation")
        if snapshot.get("mailbox") != mailbox or snapshot.get("scope_id") != scope:
            raise ValueError("Read cursor does not belong to this mailbox and conversation")
        if generation != context_compactor.generation:
            raise ValueError("Conversation data was cleared; restart the read")
        return _read_page(artifact, snapshot, index, offset)

    return await _bounded(read)


def build_registry(agent_name: str) -> Dict[str, Callable[..., Any]]:
    return {TASK_TOOL_NAME: task_email_search, READ_TOOL_NAME: task_email_read}
