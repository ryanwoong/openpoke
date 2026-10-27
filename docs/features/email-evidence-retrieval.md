# Headers-first email evidence retrieval

Email lookups use one execution model to discover candidate headers, select a relevant conversation, and read its bodies. The former nested search planner is removed. Discovery never fetches every matching body merely to decide which emails matter.

## Tool flow

1. `task_email_search(search_query, mode="discover", page_token=...)` sends a Gmail query with `format="metadata"`, `include_payload=false`, and `verbose=false`. It returns bounded headers/snippets, message and thread IDs, and `next_page_token`. `auto` uses this flow except for simple inbox previews; `preview` remains a ten-message overview. Compose Gmail query syntax in the execution model rather than passing natural-language instructions to another model.
2. `task_email_read(thread_id=...)` reads the selected complete conversation using Composio's [thread retrieval tool](https://docs.composio.dev/toolkits/gmail). It follows provider thread pages and deduplicates messages. Use `message_id` instead to read a single message. Exactly one ID is required. These operations do not modify mailbox state.
3. Short conversations return full cleaned bodies in one response. Long conversations return explicit body offsets, total character lengths, and `next_cursor`. Continue that cursor with the same ID. No body is silently dropped to fit the response budget.
4. The worker answers the actual question once the relevant conversation is read. It distinguishes quotations, requests to order, dealer confirmations, and payment/arrival evidence. A missing invoice is a specific factual gap, not proof that a complete relevant conversation was only partially read.

Discovery results now use a metadata envelope rather than the previous full-body list. `body_scope="headers"` explicitly forbids treating snippets as complete body evidence. Full content requires the separate read tool, including when discovery uses `mode="all"`.

## Coverage and limits

Lookup discovery pages are candidates; unread unrelated pages do not invalidate a selected conversation. For an exhaustive request or exact total, use `mode="all"`, follow all discovery pages for the same query, and read the matching bodies. The runtime tracks query-page chains and required message IDs separately from selected thread/message reads.

Read coverage is keyed by scope and snapshot. A read is complete only when every expected message index has contiguous, consistent body fragments from offset zero through its total length, and the end of the snapshot was reached. Receiving the last fragment alone is insufficient. Successful retries can close a specific failed read. Headers and failed API calls never count as body reads.

Four discovery calls and eight body-read calls are allowed per worker; calls remain time-bounded and answer time is reserved. Reaching a discovery limit disables further discovery while leaving body reads available. Limits trigger finalization without inventing missing coverage. Actual unfinished selected reads or exhaustive queries retain specific application-added caveats. Model failure falls back to retrieved evidence; no retrieved evidence remains an explicit failure.

Responses use a model-budget-derived byte allowance capped at 60 KB. Thread retrieval follows at most ten provider pages and snapshots at most 4 MiB. Larger tasks may require narrower queries or individual message reads. No automatic background mailbox crawl is introduced.

## Saved reads

Cleaned conversation snapshots use the existing private, content-addressed context-artifact store. Cursors identify a snapshot and body position, are validated against the connected mailbox and requested scope, and never accept arbitrary paths. Following a cursor reads the saved snapshot rather than refetching the conversation. Restart without a cursor for current mailbox data. Clear Data removes snapshots and blocks stale in-flight reads from restoring them.

Successful discovery pages and body-fragment pages can use the existing mailbox/request-scoped retry checkpoints. Cached pages remain evidence for their exact arguments; they do not establish completion of a whole query or conversation. Sending and other mailbox mutations remain excluded from saved-read retries.

## Validation

Regression tests cover metadata-only discovery, full selected conversations, provider and body-fragment pagination, scope/mailbox isolation, reset races, malformed cursors, actual missing bodies, exhaustive coverage, recovery, and normal answers after the search limit. A live read-only check retrieved four header matches and eight complete conversation bodies with zero coverage gaps. An isolated configured-model check also used two discovery calls and one body read and answered with the requested part numbers without an incomplete warning. The backend suite passed (180 tests, one optional live test skipped), and project lint passed. See [BUG-008](../bugs/008-false-incomplete-email-answers.md) for the triggering case and further verification.
