"""Schemas for the email search task tools."""

from __future__ import annotations

from typing import Any, Dict, List


TASK_TOOL_NAME = "task_email_search"
READ_TOOL_NAME = "task_email_read"

_SCHEMAS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": TASK_TOOL_NAME,
            "description": "Discover Gmail messages using headers and short snippets only. Compose a Gmail query yourself; this does not run another planning model or read bodies. Use returned thread_id/message id with task_email_read for actual contents. Follow next_page_token with the same query for more headers. mode=all requests exhaustive query/body coverage; default auto/discover is a targeted lookup. Preview is an inbox overview, not an exact count.",
            "parameters": {
                "type": "object",
                "properties": {
                    "search_query": {"type": "string", "description": "Gmail search syntax, e.g. from:dealership.example subject:parts. Plain keywords are allowed; do not pass natural-language instructions."},
                    "mode": {"type": "string", "enum": ["auto", "preview", "discover", "all"], "description": "Default auto: headers-first lookup. all requires following every page and reading matching messages before claiming complete coverage."},
                    "page_token": {"type": "string", "description": "Exact next_page_token from the previous discovery page for the same query."},
                },
                "required": ["search_query"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": READ_TOOL_NAME,
            "description": "Read the complete relevant Gmail conversation by thread_id, or one message by message_id. Provide exactly one ID returned by discovery. Returns cleaned body fragments with offsets. Continue the exact next_cursor with the same ID until exhausted; snippets cannot substitute for unread bodies. Attachments are listed but not opened.",
            "parameters": {
                "type": "object",
                "properties": {
                    "thread_id": {"type": "string", "maxLength": 128},
                    "message_id": {"type": "string", "maxLength": 128},
                    "cursor": {"type": "string", "maxLength": 1024},
                },
                "additionalProperties": False,
            },
        },
    },
]


def get_schemas() -> List[Dict[str, Any]]:
    return _SCHEMAS
