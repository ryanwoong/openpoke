# BUG-004: Premature results before an asynchronous search finishes

Discovered: 2026-10-21. Status: implemented; regression tests pass.

## Symptoms and reproduction

Ask the chat to check today's emails. The interaction agent acknowledges the request and delegates the search, but then sends an unrelated email summary before the execution agent finishes. When the execution result arrives, a second, different summary is sent.

Observed sequence on 2026-10-21, using the timestamps recorded in the conversation log:

- 22:31:20: acknowledgement.
- 22:31:31: six-email summary, without the requested execution result.
- 22:32:30: execution agent returns seven emails.
- 22:32:39: chat publishes the corresponding seven-email summary.

This is premature generation followed by a separate result turn, not continuation after an output-token cutoff. The first summary was not grounded in the completed search.

## Cause

`server/agents/interaction_agent/runtime.py` appended tool results and always requested another model response. Neither successful delegation nor `wait` ended the loop. Unverified assistant content could also survive in the loop summary and be published as fallback text.

## Fix

- Successful delegation ends model generation for the current turn.
- Multiple delegations in the same tool-call batch are preserved, but subsequent user messages and drafts after the handoff are skipped.
- Successful `wait` ends the turn immediately and skips remaining tools in that batch.
- Acknowledgements sent before delegation are preserved. Unpublished assistant text is suppressed after yielding.
- Failed delegation does not end the turn or count as submitted work, allowing an error response or recovery.
- The later execution-result turn still delivers its verified summary normally.

The interaction system prompt documents these turn boundaries.

## Verification and limits

`server/tests/test_interaction_handoff.py` exercises acknowledgement followed by delegation, silent wait, same-batch parallel delegation, suppression of premature messages and fallback text, failed submission recovery, and later result delivery.

These tests reproduce the control-flow failure with scripted model responses. They do not guarantee that a model will never fabricate information before any delegation, or that every execution-agent summary is correct. Existing incorrect chat messages are not deleted by this fix. Reload the backend to apply the new control flow.
