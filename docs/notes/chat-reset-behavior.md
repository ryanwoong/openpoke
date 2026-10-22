# Chat reset behavior

Recorded: 2026-10-21. Classification: behavior note, not a bug. Behavior unchanged.

## Behavior

Create a scheduled automation, then use the chat header's Clear button. The automation is deleted along with conversation and agent history.

## Implementation

`server/routes/chat.py` implements `DELETE /chat/history` by calling `get_trigger_service().clear_all()` as well as clearing conversation logs, execution logs, and the agent roster.

## Verification

This behavior was identified by reading the handler, not by deleting live user data. No behavior change is requested or proposed by this note.
