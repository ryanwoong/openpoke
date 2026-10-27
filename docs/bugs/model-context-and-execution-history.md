# Model context and execution history fixes

Date: 2026-10-26. Implemented in the working tree.

## 1. Excessive context compaction; hidden discovery metadata

**Finding:** The local model had no saved context size, so requests used the 32,768-token fallback and a 24,576-token input budget. A Subaru search fetched 13 full emails (~97 KB); one promotional email contributed ~73 KB. Compaction split the results into nine batches, with up to three running concurrently. Each batch appeared separately in Recent activity.

**Fix:** Discovery retains the selected model’s server metadata and exposes it in the add/edit form. Recognized `context_length` or `max_model_len` values supply the context size. An optional integer override (minimum 1,024 tokens) persists across saves and discovery refreshes; clearing it restores automatic selection. Learned context limits are scoped by configured context size so changing it does not reuse an older limit.

**Limit:** Full-body promotional content can still trigger compaction. These changes do not narrow searches or trim email bodies. Set overrides to the inference server’s configured capacity.

**Files:** `web/components/ModelSettings.tsx`, `server/model_settings.py`, `server/routes/model_settings.py`, `server/openrouter_client/client.py`.

## 2. Execution history disappeared after backend reloads

**Finding:** Execution spans existed only in process memory. Backend restarts cleared the monitor’s history even though conversation and agent logs remained on disk.

**Fix:** SQLite stores span starts, terminal updates, inputs, outputs, and parent/run relationships in `server/data/execution_history.db`. Snapshots read the database. Startup marks unfinished spans cancelled with a restart explanation. Clearing history deletes persisted spans; late completions cannot recreate them.

**Retention:** The existing 1,000-span target remains. Eviction removes whole inactive runs and preserves active runs. `OPENPOKE_TRACE_DB` overrides the database path. History already lost before capture is not reconstructed from agent logs.

**Files:** `server/execution_trace.py`, `server/app.py`.

## Validation

- Model/context changes: 42 backend tests; TypeScript, lint, and mocked desktop/mobile browser checks passed.
- Persistence changes: 152 backend tests run, one skipped; lint passed. Verified history remained visible after a live backend reload. Tests cover reopening, interrupted runs, retention, and clearing with late completion.
