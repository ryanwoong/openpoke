# Chat execution and compaction fixes

## Context overflow and incomplete summaries

Long history and email bodies exceeded the model window. The shared client now budgets input plus tool schemas, reserves answer space, and compacts only reference material. Original sources remain saved; current instructions and mutation arguments stay intact.

Some reasoning models then exhausted the small summary output allowance before producing usable notes. Summary calls now get a separate generation allowance, with at most two bounded retries. Empty or truncated summaries are rejected and never cached as evidence. A partial or failed search cannot become a verified total.

## Tasks timing out during compaction

A real order-count request reached the old 180-second task limit while summaries were still running. Refreshing the website did not cancel it. Limits now have separate scopes:

| Setting                                       | Default     | Scope                                                            |
| --------------------------------------------- | ----------- | ---------------------------------------------------------------- |
| `OPENPOKE_EXECUTION_TIMEOUT_SECONDS`          | 600 seconds | Entire chat request, including delegated work and final delivery |
| `OPENPOKE_MODEL_TIMEOUT_SECONDS`              | 180 seconds | Each provider-call attempt                                       |
| `OPENPOKE_CONTEXT_COMPACTION_TIMEOUT_SECONDS` | 240 seconds | Each request-preparation/compaction phase                        |

Compaction still counts toward the overall deadline. Deadline expiry cancels that request’s async workers; an external action already running in a thread may still finish. Browser refresh/navigation does not cancel server work. Restart the backend to load changed defaults; explicit environment overrides take precedence.

Completed email reads can be reused through **Retry with saved results**. Checkpoints are process-local, expire after 15 minutes, and require the same prompt, mailbox, and exact tool arguments. They retain full results without trimming receipt evidence. Any attempted non-search tool disables checkpoint retry. Sending, scheduling, and other writes are never checkpoint-replayed. New searches fetch fresh data; this is a limited read retry, not durable workflow resumption across backend restarts. Clear Data removes checkpoints and summaries and cancels active requests.

## Background messages replacing task context

Scheduled confirmations looked like ordinary answers, while result delivery read the newest global chat history. Each request now retains its own ID and original prompt across async handoffs. Scheduled updates have a separate source. Final delivery uses only the task’s results, cannot launch another worker, and stops after one answer. A batch with failures reports an incomplete result rather than inferring success from its successful members.

## Misleading progress and errors

Completed tools were labelled “Completed N steps” even when the task timed out later. Activity now shows the actual stage—searching, compacting, analyzing, or writing—and reports the overall failure/timeout alongside completed-step counts. Standalone activity retains the OpenPoke identity. Failures use concise inline messages; detailed errors and internal instructions stay in execution diagnostics.
