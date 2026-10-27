# BUG-008: Search limits produce false blanket incomplete-answer warnings

Discovered: 2026-10-27. Status: implemented; regression tests, lint, direct live retrieval, and isolated model-driven verification pass.

## Symptoms and evidence

A targeted dealership-parts lookup returned useful order-confirmation evidence in about ninety seconds but prefaced the answer with a blanket incomplete-coverage warning. The live trace showed that the first search selected 22 emails, while its payload limiter returned 21 bodies and marked the whole result incomplete. Three more searches returned zero, nine, and one messages. The four-search guard then forced an incomplete answer even though useful correspondence had already been read.

This was a consequence of the initial [deadline fix](007-email-search-deadline-exhaustion.md), not another task timeout. Two different facts were conflated: whether every optional discovery result had been read and whether the relevant selected conversation was complete.

## Fix

- Discover bounded headers first, then read only the relevant thread/message through the connector's supported ID-based tools. Remove the nested planning model and full-body discovery payload clipping.
- Split long selected reads into resumable, offset-marked fragments backed by immutable mailbox-scoped snapshots. Preserve all cleaned body text and track missing fragments explicitly.
- Track selected-read coverage and exhaustive query coverage separately. A targeted lookup may finish with a fully read relevant conversation even when unrelated header pages remain.
- Permit body reads after the discovery-call limit. Finalize without an automatic incomplete warning when no required evidence is missing. Keep specific warnings for failed/unfinished selected reads, unread exhaustive query pages, or unread matching bodies.
- Retain deadline bounds, read-only retries, no-evidence failures, and distinctions between quotes and confirmed orders.

Technical flow and tool-result compatibility changes: [email evidence retrieval](../features/email-evidence-retrieval.md).

## Verification and limits

Tests cover complete-thread retrieval, lossless body-fragment continuation, last-fragment-only misuse, provider pagination, stale/cross-mailbox cursors, missing bodies, reset races, exhaustive page/body coverage, and recovery after a failed scope. Worker tests cover a normal answer after four discovery calls and continued body reads without blanket warnings.

A direct live read-only Gmail check returned four header matches and eight complete bodies from the selected conversation, with zero coverage gaps. It used temporary snapshots and did not send messages or change mailbox labels/read status. An isolated check with the configured model made two discovery calls and one selected conversation read, returned the requested part numbers, and produced neither a blanket incomplete warning nor an outstanding-read warning. Only the two read-only tools were enabled; no reply was posted to the user's chat.

The full backend suite completed 180 tests with one opt-in live agent-discovery evaluation skipped; all remaining tests passed. Project lint passed. Markdown uses Prettier; Python uses Ruff because the configured Prettier installation has no working Python parser.

These changes do not establish every purchase in the mailbox, read PDF attachments, or turn quotations into confirmed orders. Large exhaustive tasks can still have genuine outstanding evidence; the application reports those specific gaps rather than treating stopped optional exploration as a failure.
