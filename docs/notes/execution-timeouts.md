# Execution timeouts and Gmail searches

The execution timeout is configured through `OPENPOKE_EXECUTION_TIMEOUT_SECONDS` (default: 180 seconds, minimum: 1). A task still has a finite deadline; upstream outages or slow model responses can exhaust it.

The Chipotle search trace on October 23 showed a 90-second worker deadline being consumed by repeated model planning and selection calls, while four Gmail fetches took about eight seconds combined. The execution agent invoked three nested search tasks with Gmail query strings. After cancellation, the interaction model received the failure but replied about an earlier reminder.

Search requests containing supported Gmail operators such as `after:`, `before:`, `from:`, or `subject:` now execute directly without the nested search-planning model. Natural-language search requests retain the search model. Gmail fetches run off the event loop. Both paths follow result pages and deduplicate message IDs; a failed page, repeated pagination token, or page limit returns an explicit failure rather than a partial total. The pagination limit is ten pages. Matching emails remain distinct from verified orders; the execution prompt requires inspection for promotions and duplicate confirmations before counting orders.

Execution failures produce a task-specific reply directly, bypassing model paraphrasing. Successful result summaries include the originating task. Result batches are isolated by traced user request, while parallel delegations from one request can still batch together. Unresolved tool errors cannot become successful final responses. Verified scheduling replies retain their existing behavior.

The chat request status treats failed or cancelled execution as failure. A synchronous Gmail request that finishes after its parent worker has been cancelled cannot leave the chat permanently busy.

Regression tests use mocked Gmail and model responses. They cover timeout delivery, separate and parallel request batches, tool failures, the configured timeout, direct searches without model calls, pagination and deduplication, incomplete results, and event-loop responsiveness. Frontend tests cover cancelled workers and lingering Gmail spans. Validation does not send emails or rerun the user's live search.
