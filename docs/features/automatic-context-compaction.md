# Automatic context compaction

All agent completions pass through `server/openrouter_client/client.py`: chat, execution, email search, and background summarization. The guard budgets serialized messages **and tool schemas**, reserves answer space using `max_tokens`, and caps estimated input at the smaller of **75% of the context window** or the window minus answer space and 512 tokens of overhead.

## Context limits

Newly saved models retain advertised `context_length` / `max_model_len` metadata. Models without metadata use `OPENPOKE_CONTEXT_WINDOW_TOKENS` (**32768**); set this fallback to the actual server limit when needed. `OPENPOKE_CONTEXT_OUTPUT_TOKENS` defaults to **4096**, capped at one quarter of the window.

Token counts use a UTF-8-size estimate (serialized bytes / 3 plus overhead), not an exact model-specific tokenizer. Recognized context rejections reduce the budget and retry **once**. Learned limits/budgets are cached per endpoint/model for the process lifetime. Unrelated HTTP errors are not context retries.

## Compaction pipeline

`server/openrouter_client/context.py` operates on a deep copy:

- Summarize older framed history while retaining up to six recent complete entries within a bounded allowance. Execution history starts with the latest ten tasks and explicitly labels that scope; full logs remain saved. Background working memory also summarizes under token pressure before its message-count threshold.
- Batch large successful email-search results into bounded chunks. Oversized email bodies retain their identifiers/metadata in each fragment. Summary calls use the selected model, expose no tools, and run with at most three concurrent requests. Notes must preserve evidence, distinguish receipts from promotions, and deduplicate order IDs across chunks/queries; email matches are never an order total.
- Reduce accumulated notes when necessary. Older tool exchanges are replaced only as whole user turns. System rules, current requests, tool schemas, and current tool-call arguments remain intact. Compaction cannot approve drafts or execute tools.

A 128-entry in-process cache reuses summaries by provider/model, source, task, and output budget. Sources exceeding 64 chunks, failed/truncated summaries, or irreducibly oversized requests fail clearly. Compaction adds model calls and remains subject to normal task deadlines. Summary calls appear as context-compaction spans in Execution.

## Storage and checks

Original sources are archived in ignored `server/data/context_artifacts/` as content-addressed JSON (directory `0700`, files `0600`); these are local snapshots, not a model-accessible tool. Clear Data removes snapshots and cached summaries and blocks in-flight cache repopulation and stale working-memory writes. Full conversation/execution logs are otherwise unchanged.

Tests use temporary artifacts and mocked providers to cover limits, recent history, exact arguments, tool pairing, Unicode batching, cache/clear races, metadata, and overflow recovery. They do not send emails or change saved credentials.

## `context.py` walkthrough

[`context.py`](../../server/openrouter_client/context.py) decides what can be reduced and produces a smaller request. [`client.py`](../../server/openrouter_client/client.py) selects the model/window, performs HTTP requests, supplies the summary callback, and handles the single overflow retry. Keeping the transport outside the compactor means the compactor never invokes application tools or chooses a different provider.

### Budget helpers

| Function                         | Behavior                                                                                                                                                                                                                                             |
| -------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `estimate_tokens(value)`         | JSON-serializes the value with Unicode intact, measures UTF-8 bytes, and returns `ceil(bytes / 3) + 32`. Passing the entire payload includes messages, tool definitions, and request fields. This estimate can differ from the provider's tokenizer. |
| `input_budget(window, reserve)`  | Returns `max(0, min(floor(window × 0.75), window − reserve − 512))`. The result is the maximum estimated input size, not the model's full context size.                                                                                              |
| `context_overflow(status, body)` | Recognizes context-related errors for HTTP 400, 413, and 422. It returns whether the error looks like overflow and, when available, a reported window of at least 1024 tokens. Authentication and unrelated validation errors do not qualify.        |

For a 131072-token window and a 4096-token answer reserve, the input budget is **98304 estimated tokens**. The separate safety margin matters because the estimate is not an exact tokenizer count. If a provider still rejects the request, `client.py` reduces the budget and tries again; this helper does not retry HTTP requests itself.

### Recognizing history and splitting source data

`framed_history()` only accepts a leading `<conversation_history>` block followed by a recognized application-generated section, such as `<active_agents>` or `<new_user_message>`. A user mentioning similar tags in ordinary text is not enough to make that text eligible for rewriting.

`recent_history()` finds complete tagged log entries and walks backward through at most six of them. It retains entries verbatim while they fit within `max(256, budget // 8)` estimated tokens. It returns **older material to summarize** and **recent material to keep**. Six is a maximum: a large recent entry may need to be summarized rather than retained verbatim.

`source_chunks()` packs JSON-array records, or lines of plain text, into bounded UTF-8 chunks. It prefers record boundaries. For a large email record, `email_fragments()` binary-searches for body fragments that fit while repeating the original identifiers and metadata in each fragment. A `source_fragment` number marks those fragments, so repeated IDs must be reconciled rather than counted as separate emails. Oversized metadata fails explicitly. Other oversized text can be split across chunks without dropping a multibyte character.

### `ContextCompactor.prepare()`: the decision order

The public entry point returns `(prepared_payload, compacted)`:

1. **Copy and measure.** Deep-copy the request. If it already fits, return immediately with `False`; no summaries or snapshots are created.
2. **Protect the current task.** Locate the latest user turn and separate any recognized history prefix from the active request. Reject a current request that leaves too little room, rather than shortening its instructions.
3. **Compact reference history.** Summarize oversized framed conversation history and the reference text after `# Execution History` in an execution-agent system message. Keep the system-message prefix and the bounded recent entries unchanged, then measure again.
4. **Compact successful email results.** Match tool results to their call IDs or tool wrapper. Only `task_email_search`, `gmail_fetch_emails`, and `GMAIL_FETCH_EMAILS` are eligible, and only on success. Replace large `result` / `messages` data with an evidence summary, the original returned-email count when the source is a list, and an explicit warning against treating that count as an order total. Call IDs, arguments, and the surrounding protocol remain unchanged.
5. **Compact older complete turns.** If necessary, replace messages before the latest user turn with reference memory. Remove their tool calls and results together; preserve system/developer messages and the entire active turn.
6. **Check the complete result.** Return the smaller copy only if it fits. Otherwise raise `ContextBudgetError`, which inherits the shared model-request error type. The oversized original is never sent as a fallback.

The input object is not mutated. Failed tool results, unrecognized tools, and current mutation arguments are not compressed through the email-result path. In particular, this path cannot shorten a draft body, change its recipient, or manufacture approval.

### `ContextCompactor.summarize()`: producing memory

Before summarizing, the compactor archives the complete source block. It then:

- Chooses a target source-chunk byte size of `max(512, min(18000, 2 × (budget // 4)))`; more than **64 chunks** fails explicitly.
- Requests between **128 and 768 output tokens per chunk**, depending on the input budget. A shared semaphore permits at most three summary calls concurrently on the current event loop, including calls from simultaneous compactions.
- Supplies the current task when summarizing search results. `SUMMARY_SYSTEM` tells the model to treat sources as data, preserve evidence and uncertainties, and avoid inventing facts or adding overlapping batch totals. The callback in `client.py` sends these calls without tool schemas and rejects empty or output-truncated summaries.
- Joins the notes and, when needed, performs up to **four reduction rounds**, targeting `max(512, budget // 3)` estimated tokens. A round that does not shrink the notes fails. An empty result or one still above `max(1024, budget // 2)` estimated tokens also fails; `prepare()` separately checks the full request afterward.

Returned notes are HTML-escaped inside a `<compacted_reference artifact="…">` wrapper, identifying them as reference memory. This preserves the wrapper boundary; it does not make a model-generated summary lossless or independently verified.

### Cache, archives, and clearing

`ContextCompactor.summaries` is a 128-entry least-recently-used cache. A SHA-256 key covers the provider/model scope, current task, output-token budget, and exact chunk. Search summaries include the current task in the key. General history summaries use an empty task value so they can be reused across questions; changing the provider/model scope still separates the cache entries. The cache is process-local and disappears on restart.

`archive()` hashes each full source block and writes `<hash>.json` containing `{"source": "…"}`. Exclusive creation avoids concurrent rewrites of the same snapshot. These files preserve the original reference data for local inspection; the model cannot read them through a tool merely because it sees an artifact ID.

`clear()` holds a reentrant lock, increments a generation counter, clears cached notes, and deletes snapshots. Each summary checks that generation before sending queued work, after receiving a response, and before returning combined notes. Work already sent to a provider may finish, but its stale result cannot repopulate the cleared cache. Unfinished sibling tasks are cancelled and awaited when a batch exits. The separate working-memory generation check in the summarization service prevents a stale briefing from being written after Clear Data.
