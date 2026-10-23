"""Validated scheduled actions and draft approval shared by agent runtimes."""

import json
import re

from ..utils.timezones import now_in_user_timezone
from .triggers import get_trigger_service
from .triggers.utils import parse_datetime, resolve_timezone, utc_now
from .timezone_store import get_timezone_store

AGENT_NAME = "Scheduled tasks"
PAYLOAD_PREFIX = "openpoke:scheduled-action:v1:"


def is_scheduling_request(message):
    text = message.lower().strip()
    if re.match(r"(?:how|why|what|when|where|does|is|can i)\b", text) or re.search(r"\bremind me (?:what|who|how|why|which|whether)\b", text):
        return False
    if re.search(r"\b(?:don't|do not|never)\s+(?:remind|schedule|set)\b", text):
        return False
    return bool(re.search(r"\bremind\s+me\b|\b(?:set|create|add)\b.*\b(?:reminder|timer|alarm)\b", text) or re.search(r"\b(?:schedule|send|email)\b.*\b(?:at\s+\d|in\s+\w+\s+(?:minutes?|mins?|hours?|days?)|tomorrow|tonight|later|every|next\s+\w+)", text))


def decode_action(payload):
    if not payload.startswith(PAYLOAD_PREFIX):
        return None
    action = json.loads(payload[len(PAYLOAD_PREFIX) :])
    if action.get("kind") not in ("reminder", "email"):
        raise ValueError("Unknown scheduled action kind")
    return action


def schedule_task(*, kind, start_time, message=None, recipient_email=None, subject=None, body=None, recurrence_rule=None):
    timezone_name = get_timezone_store().get_timezone()
    start = parse_datetime(start_time, resolve_timezone(timezone_name))
    if start <= utc_now():
        raise ValueError("Choose a future start_time; nothing was scheduled")
    if kind == "reminder":
        if not isinstance(message, str) or not message.strip():
            raise ValueError("A reminder message is required")
        action = {"kind": kind, "message": message.strip()}
    elif kind == "email":
        if not all(isinstance(value, str) and value.strip() for value in (recipient_email, subject, body)):
            raise ValueError("The recipient, subject, and body are required")
        # Save the exact draft locally; Gmail is only contacted after approval at firing time.
        action = {"kind": kind, "recipient_email": recipient_email, "subject": subject, "body": body, "approved_at": None}
    else:
        raise ValueError("kind must be reminder or email")
    record = get_trigger_service().create_trigger(
        agent_name=AGENT_NAME,
        payload=PAYLOAD_PREFIX + json.dumps(action),
        start_time=start.isoformat(),
        timezone_name=timezone_name,
        recurrence_rule=recurrence_rule,
        status="paused" if kind == "email" else "active",
    )
    if not record.next_trigger:
        get_trigger_service().update_trigger(record.id, agent_name=AGENT_NAME, status="completed")
        raise ValueError("The recurrence has no future occurrence; nothing was scheduled")
    return {"trigger_id": record.id, "user_message": confirmation(record)}


def confirmation(record):
    action = decode_action(record.payload)
    when = parse_datetime(record.next_trigger, resolve_timezone(record.timezone)).strftime("%Y-%m-%d %H:%M:%S %Z")
    recurrence = f" Repeats: {record.recurrence_rule}." if record.recurrence_rule else ""
    if action["kind"] == "reminder":
        return f"Reminder scheduled for {when}: {action['message']}{recurrence}"
    if not action.get("approved_at"):
        return f"Email draft for {when} (awaiting approval):\nTo: {action['recipient_email']}\nSubject: {action['subject']}\n\n{action['body']}\n\nReply yes to approve this exact email and its schedule, or no to cancel.{recurrence}"
    return f"Email scheduled for {when} to {action['recipient_email']}.{recurrence}"


def resolve_email_approval(message, conversation_log):
    answer = message.strip().lower().rstrip(".!")
    if answer not in ("yes", "yes please", "approve", "approved", "confirm", "no", "cancel"):
        return None
    entries = list(conversation_log.iter_entries())
    previous = next(((tag, payload) for tag, _, payload in reversed(entries[:-1]) if tag in ("poke_reply", "user_message")), None)
    if previous is None or previous[0] != "poke_reply":
        return None
    service = get_trigger_service()
    pending = []
    for record in service.list_triggers(agent_name=AGENT_NAME):
        action = decode_action(record.payload)
        if not action or action["kind"] != "email" or action.get("approved_at") or record.status != "paused":
            continue
        if confirmation(record) in previous[1]:
            pending.append((record, action))
    if not pending:
        return None
    if answer in ("no", "cancel"):
        for record, _ in pending:
            service.update_trigger(record.id, agent_name=AGENT_NAME, status="completed")
        return "Email schedule cancelled. Nothing was sent."
    if any(parse_datetime(record.next_trigger, resolve_timezone(record.timezone)) <= utc_now() for record, _ in pending):
        return "An email's scheduled time has passed. Please choose a new time; nothing was sent."
    replies = []
    for record, action in pending:
        action["approved_at"] = now_in_user_timezone().isoformat()
        updated = service.update_trigger(record.id, agent_name=AGENT_NAME, payload=PAYLOAD_PREFIX + json.dumps(action), status="active")
        replies.append(confirmation(updated))
    return "\n\n".join(replies)


def scheduling_context():
    return (
        f"Current user-local time: {now_in_user_timezone().isoformat()}. "
        "Use scheduleTask to save this request. Convert the requested time into an explicit future ISO 8601 start_time. "
        "For reminders, kind=reminder and message is the text to deliver. For future email, kind=email and supply the exact recipient_email, subject, body. "
        "Email drafts are saved paused for user review; never send immediately. Supply recurrence_rule only for recurring requests. "
        "Do not claim success without a successful tool result. If the time or content is ambiguous, ask for clarification."
    )
