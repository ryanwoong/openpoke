# Implementation writeup

## Fixing overload

The overload problem came from context growing with use: conversation and execution history accumulated, every saved agent name entered the interaction prompt, and broad email searches brought full bodies into the model before it knew which messages mattered. Large HTML emails made this worse. Increasing the context window alone would postpone the failure and keep paying for irrelevant input.

I addressed it at three points:

- **Retrieve less upfront.** Agent discovery searches a persisted directory and returns at most five candidates instead of injecting the whole roster. Email discovery returns bounded headers and snippets; a separate read tool fetches the selected message or conversation. MIME/HTML normalization removes presentation noise before that content reaches the model.
- **Budget every model request.** A shared client counts estimated input, including tool schemas, against the selected model's discovered or overridden context limit. It reserves output space and headroom, then summarizes eligible history and large successful email results when necessary. Summaries run with bounded concurrency and are cached; original sources remain in private local snapshots. Current instructions, tool arguments, and call/result pairing are protected. A recognized context rejection gets one smaller-budget retry; an irreducible request fails explicitly.
- **Bound work and preserve task ownership.** Separate model, compaction, and overall deadlines prevent indefinite work. Request IDs isolate concurrent answers and progress. Email discovery/read limits and explicit body-coverage tracking distinguish a selected conversation from an exhaustive mailbox query, so reaching a limit does not justify an invented total or an unsupported completeness claim.

This combines smaller inputs with a final budget guard. The tradeoff is that compaction adds latency and model-generated summaries can lose detail. Token counts are estimates, and lexical agent discovery can miss paraphrases. The detailed [compaction](features/automatic-context-compaction.md), [agent discovery](features/agent-discovery.md), and [email retrieval](features/email-evidence-retrieval.md) notes explain those boundaries.

## Frontend and design decisions

I built a workspace around Conversation, Execution, and Reminders, with settings for integrations, models, timezone, and alerts. Chat supports Markdown, request-scoped progress, failure/retry controls, and Yes/No buttons for explicit reply prompts. Keeping progress tied to a request avoids making a background inbox check look like an answer to the user's current question.

The execution canvas makes delegation visible through stable agent, model, Gmail, and tool nodes. Pan, zoom, Fit, and a shared inspection sidebar let users move from an overview to individual calls. Historical tasks use frozen invocation graphs so inspecting an earlier run does not shift under incoming activity. SQLite persistence keeps that history available after a backend restart.

I kept diagnostics in Execution so the main conversation remains readable. Polling reuses backend trace data without extra model calls. Responsive panels, keyboard navigation, focus restoration, and reduced-motion behavior support the same workflows across screen sizes and input preferences. Animation communicates running and completed work; labels preserve that information when motion is disabled.

## Chosen feature: reminders users can inspect and control

The Reminders view turns a promise made in chat into something users can verify. Upcoming and History separate pending work from its outcome. Users can inspect dates and status, edit reminder text or scheduled email content, cancel items, and review email drafts before approving them. Editing a scheduled email pauses it and requires renewed approval.

This deserves attention because a reminder is useful only if the user can trust what will happen and when. A dedicated view makes mistakes discoverable before execution and avoids requiring another carefully phrased chat message to change a time or recipient. Email-body previews and explicit approval give consequential actions a concrete review step. The scheduler still requires the backend to remain running, and cancelling an action already in progress cannot retract an email already being sent.

## With another week

### Visible evidence in answers

- Link claims and summaries to their source emails.
- Show sender, date, and the relevant excerpt.
- Let users open the original conversation to verify details.
- Goal: make answers easier to trust and act on.

### Proactive daily briefing

- Bring important emails, unanswered conversations, and upcoming reminders into one view.
- Let users choose the delivery time and topics to include.
- Link each item to its source conversation or reminder.
- Goal: give users a useful starting point without needing a prompt.
