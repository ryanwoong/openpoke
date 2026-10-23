# Scheduling

New reminder and future-email requests with common English scheduling phrases route directly to an execution agent. This bypasses the chat model's decision about whether the app has a reminder tool. Other phrasings can still delegate through the normal interaction flow. Both prompts describe scheduling capabilities explicitly; model selection is unchanged.

The execution agent translates the request into `scheduleTask` arguments. The tool requires a future timestamp and validates the action before persisting it. Confirmation text comes from the saved record, including the user's local time and timezone. The runtime discards unsupported success claims, retries an initial refusal once, and reports failure if the model never supplies a valid tool call. Successful scheduling results bypass model paraphrasing, including through the usual delegation path. Tool error dictionaries are treated as failures.

Reminders store the message to deliver. The scheduler writes that message directly to chat when due, without another model call. Existing generic triggers keep their execution-agent behavior.

Future emails store the exact recipient, subject, and plain-text body locally in a paused trigger. The user sees this draft and its schedule before approving. A direct affirmative reply to the displayed draft activates it; a negative reply cancels it. An expired schedule cannot be approved. Changes through generic trigger tools cannot bypass draft approval. When due, the scheduler creates and sends the approved content through Gmail. Gmail errors are recorded on the trigger and surfaced in chat. Tests mock Gmail; they send no real email.

Limitations: natural-language interpretation still depends on the selected model. Common phrases have a deterministic routing safeguard, not an exhaustive language parser. Ambiguous requests may need to be restated with the task and future time. The backend must be running for delivery; polling is every ten seconds. The existing scheduler does not guarantee exactly-once delivery across crashes or multiple backend workers. Clearing chat also clears triggers, as before.

Validation: `server/tests/test_scheduling.py` exercises routing, saved confirmations, due delivery, refusal recovery, fabricated success, storage errors, approval/cancellation, expired approvals, and Gmail failures using an isolated SQLite database.
