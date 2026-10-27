# BUG-007: Expanding email searches exhaust the task deadline

Discovered: 2026-10-26. Status: implemented; regression tests and lint pass. Fresh live Gmail/model verification pending.

The original bounds and fallback described here were subsequently refined by [BUG-008](008-false-incomplete-email-answers.md). Current retrieval uses headers-first discovery, direct body reads, and evidence-specific coverage; the nested planner and blanket incomplete-warning policy below describe the initial fix.

## Symptoms and evidence

A targeted dealership-parts lookup timed out after 600 seconds despite successful Gmail reads. The initial search returned 46 messages. A broader vehicle search spent 240 seconds in compaction; narrower reads then succeeded, but a nested planner continued trying thread IDs, inbox queries, and date ranges until the overall deadline cancelled the worker.

The archived compaction input contained 117 cleaned emails, 226,099 bytes, with no structural HTML detected. This is distinct from [BUG-006](006-email-html-compaction.md): the remaining problem was search scope, accumulated full-body context, and stopping behavior. The archive alone does not reconstruct every model call or establish that every retrieved message was relevant.

## Cause

Delegation expanded a simple lookup into an exhaustive audit. A worker could make eight planning turns; each nested search could make another eight turns and many overlapping Gmail queries. Full bodies were repeatedly passed to the search planner just to select message IDs. There was no reserved answer phase before the global deadline, and completed reads could be discarded when later exploration failed.

## Fix

- Interaction and execution prompts preserve the requested scope, favor the identified sender/subject/dates, and stop when the returned evidence answers the question. They discourage broad vehicle searches and thread-ID guessing. Gmail documents message search and its thread-wide limitations in its [filtering guide](https://developers.google.com/workspace/gmail/api/guides/filtering).
- Each public email-search call has a 90-second time budget, three distinct queries, three nested planning turns, and at most sixty candidate messages (fewer for small model windows). Repeated queries stop exploration. The existing ten-page-per-query limit and explicit API/pagination failures remain.
- The nested planner receives metadata and 160-character snippets, with previously shown messages represented by IDs. Selected full cleaned bodies go to the worker rather than the selector.
- Worker result payloads keep complete message records within a byte allowance derived from the model input budget, capped at 60 KB. Omitted bodies, candidate/query limits, unfinished selection, and search timeouts return `messages`, `search_complete: false`, and a warning. These are candidates or partial findings, not verified complete coverage. Authentication and pagination errors are still explicit failures.
- A worker starts at most four email-search calls and stops starting reads when 120 seconds remain. Planning after a read is limited to 75 seconds; final synthesis has at most 60 seconds and leaves thirty seconds of deadline margin. If planning or synthesis is unavailable, deterministic excerpts from successfully retrieved messages are returned instead.
- Partial answers carry an application-added incomplete-coverage warning and are delivered directly, avoiding another model pass that could omit the caveat. Without retrieved evidence, a bounded search remains a failure. Partial results are not cached as completed retry checkpoints. User cancellation still propagates.

Implementation: [search tool](../../server/agents/execution_agent/tasks/search_email/tool.py), [execution runtime](../../server/agents/execution_agent/runtime.py), and the interaction/execution/search prompts and tool description.

## Verification and tradeoffs

[Bounded-search tests](../../server/tests/test_email_search_bounds.py) cover planner snippets versus full worker bodies, oversized model tool batches, repeated queries, candidate/payload limits, timeouts after successful pages, no-evidence failures, cancellation, concurrent isolation, reserved finalization, synthesis fallback, persistent partial warnings, and retry-cache safety.

The backend suite completed 180 tests with one opt-in live-model evaluation skipped; all remaining tests passed. Project lint passed. Markdown uses Prettier; Python uses Ruff because the configured Prettier installation has no working Python parser.

These limits favor delivering supported findings promptly over an exhaustive audit. A large legitimate search may return partial coverage and require narrower sender/date queries; neither snippets nor email counts establish purchase totals. The overall task deadline is unchanged. A synchronous Gmail HTTP request already running in a thread may finish after cancellation, but its late result is not processed by the cancelled search. Full live behavior and latency remain dependent on the Gmail service and configured model.
