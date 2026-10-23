# BUG-005: Chat denies supported scheduling capabilities

Discovered: 2026-10-23. Status: implemented; regression tests and an isolated live-model check pass. Live Gmail delivery verification pending.

## Symptoms and cause

Requests such as “remind me to take a desk break 3 mins from now” received unsupported-capability refusals. All three reported requests used zero execution agents, and no triggers were saved.

The chat model saw only its own tools and overlooked the execution agents' scheduling tools. Delegation depended on prompt compliance, so the runtime accepted the refusal. Separately, tool error dictionaries could incorrectly count as successful calls. Different models handled the requests through `openrouter/free`, but this did not establish model routing as the cause.

## Fix

- Both prompts explicitly describe scheduling support. Common scheduling requests now route directly to execution, with model selection unchanged.
- `scheduleTask` validates a future timestamp and saves a structured action through the existing trigger service. Confirmations come from saved records, including local time and timezone, and bypass model paraphrasing.
- Unsupported success claims are discarded, an initial refusal gets one retry, and tool error dictionaries count as failures.
- The scheduler delivers saved reminder messages directly to chat without another model call.
- Future emails remain paused until the user approves the exact draft and time. Expired or unrelated approvals cannot activate them. The scheduler sends approved content when due and surfaces Gmail failures.

Implementation: [scheduling service](../../server/services/scheduling.py), [interaction runtime](../../server/agents/interaction_agent/runtime.py), [execution runtime](../../server/agents/execution_agent/runtime.py), and [scheduler](../../server/services/trigger_scheduler.py). Additional workflow details: [scheduling notes](../notes/scheduling.md).

## Verification and limits

The full backend suite passed with 50 tests, including [scheduling regressions](../../server/tests/test_scheduling.py) for persistence, delivery, recurrence, false confirmations, failures, and email approval. Lint passed. A live `openrouter/free` check saved a three-minute reminder in a temporary database. Gmail delivery was mocked; no real email was sent.

Natural-language interpretation still depends on the selected model; routing covers common English phrases. Delivery requires the backend to run and polls every ten seconds. Exactly-once delivery across crashes or multiple workers is not guaranteed. Clearing chat still deletes triggers.
