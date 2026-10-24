from __future__ import annotations

import hashlib
import json
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

from ..services.scheduling import PAYLOAD_PREFIX, decode_action
from ..services.triggers import get_trigger_service
from ..services.triggers.utils import build_recurrence, load_rrule, parse_datetime, resolve_timezone, to_storage_timestamp, utc_now

router = APIRouter(prefix="/reminders", tags=["reminders"])


class ReminderChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str
    action: Literal["edit", "cancel", "approve", "resume"]
    start_time: str | None = None
    message: str | None = None
    recipient_email: str | None = None
    subject: str | None = None
    body: str | None = None


def version(record):
    return hashlib.sha256(record.model_dump_json().encode()).hexdigest()


def present(record):
    action = decode_action(record.payload)
    kind = action["kind"] if action else "task"
    upcoming = record.status in ("active", "paused", "running") and bool(record.next_trigger)
    if record.status == "cancelled":
        status = "cancelled"
    elif record.last_error and not record.next_trigger:
        status = "failed"
    elif record.status == "running":
        status = "running"
    elif not upcoming:
        status = "completed"
    elif kind == "email" and not action.get("approved_at"):
        status = "awaiting_approval"
    elif record.status == "paused":
        status = "paused"
    else:
        status = "scheduled"
    return {
        **record.model_dump(exclude={"payload", "status"}),
        "kind": kind,
        "status": status,
        "upcoming": upcoming,
        "editable": upcoming and record.status != "running",
        "message": action.get("message", "") if action else record.payload,
        "recipient_email": action.get("recipient_email", "") if action else "",
        "subject": action.get("subject", "") if action else "",
        "body": action.get("body", "") if action else "",
        "version": version(record),
    }


@router.get("")
def list_reminders():
    items = [present(record) for record in get_trigger_service().list_triggers()]
    return {"items": items, "count": sum(item["upcoming"] for item in items)}


def required(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} is required")
    return value


@router.patch("/{trigger_id}")
def change_reminder(trigger_id: int, change: ReminderChange):
    service = get_trigger_service()
    record = service.get_trigger(trigger_id)
    if record is None:
        raise HTTPException(404, "Reminder not found")
    if version(record) != change.version:
        raise HTTPException(409, "This item changed. Refresh and review it before trying again.")
    if not present(record)["editable"] and not (change.action == "cancel" and present(record)["upcoming"]):
        raise HTTPException(409, "Only upcoming items that have not started can be changed.")
    action = decode_action(record.payload)
    fields = {}
    try:
        if change.action == "cancel":
            fields = {"status": "cancelled", "next_trigger": None}
        elif change.action in ("approve", "resume"):
            if parse_datetime(record.next_trigger, resolve_timezone(record.timezone)) <= utc_now():
                raise ValueError("The scheduled time has passed. Edit the date and time first.")
            if change.action == "approve":
                if not action or action["kind"] != "email" or action.get("approved_at"):
                    raise ValueError("This item is not awaiting email approval")
                action["approved_at"] = utc_now().isoformat()
                fields["payload"] = PAYLOAD_PREFIX + json.dumps(action)
            elif action and action["kind"] == "email":
                raise ValueError("Email drafts must be approved")
            fields["status"] = "active"
        else:
            tz = resolve_timezone(record.timezone)
            start = parse_datetime(required(change.start_time, "Date and time"), tz)
            if start <= utc_now():
                raise ValueError("Choose a future date and time")
            recurrence = build_recurrence(record.recurrence_rule, start, tz)
            next_fire = load_rrule(recurrence).after(utc_now().astimezone(tz), inc=True) if recurrence else start
            if next_fire is None:
                raise ValueError("This recurrence has no future occurrence")
            fields = {
                "start_time": to_storage_timestamp(start),
                "next_trigger": to_storage_timestamp(next_fire),
                "recurrence_rule": recurrence,
                "last_error": None,
            }
            if action and action["kind"] == "email":
                action = {
                    "kind": "email",
                    "recipient_email": required(change.recipient_email, "Recipient"),
                    "subject": required(change.subject, "Subject"),
                    "body": required(change.body, "Body"),
                    "approved_at": None,
                }
                fields.update(payload=PAYLOAD_PREFIX + json.dumps(action), status="paused")
            else:
                message = required(change.message, "Message or instructions")
                if action:
                    fields["payload"] = PAYLOAD_PREFIX + json.dumps({"kind": "reminder", "message": message})
                else:
                    if message.startswith(PAYLOAD_PREFIX):
                        raise ValueError("Task instructions cannot contain a structured action")
                    fields["payload"] = message
        updated = service.update_if_unchanged(record, fields)
    except (ValueError, TypeError, OverflowError) as exc:
        raise HTTPException(400, str(exc)) from exc
    if updated is None:
        raise HTTPException(409, "This item changed. Refresh and review it before trying again.")
    return present(updated)
