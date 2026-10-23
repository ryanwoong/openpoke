"""Structured scheduling tool available independently of model selection."""

from ....services.scheduling import schedule_task

SCHEMA = {
    "type": "function",
    "function": {
        "name": "scheduleTask",
        "description": "Save a reminder or an email for a future time. Emails remain paused until the user approves the exact draft and schedule.",
        "parameters": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["reminder", "email"]},
                "start_time": {"type": "string", "description": "Future ISO 8601 timestamp, including UTC offset. Interpret the user's schedule in their local timezone."},
                "message": {"type": "string", "description": "Reminder text to deliver when due."},
                "recipient_email": {"type": "string"},
                "subject": {"type": "string"},
                "body": {"type": "string"},
                "recurrence_rule": {"type": "string", "description": "Optional iCalendar RRULE for a recurring task."},
            },
            "required": ["kind", "start_time"],
            "additionalProperties": False,
        },
    },
}
