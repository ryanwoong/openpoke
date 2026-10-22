# Bug records

Track bugs discovered while developing and using OpenPoke here. Keep reproduction steps, evidence, causes, fixes, and remaining uncertainty together. Do not copy private email content, credentials, or full conversation logs into these records.

“Implemented” means the working tree contains the change. It does not mean the change has been deployed or a live external integration has been verified.

| ID                                        | Issue                                                                               | Status                                       |
| ----------------------------------------- | ----------------------------------------------------------------------------------- | -------------------------------------------- |
| [BUG-001](001-composio-oauth-endpoint.md) | Gmail authorization uses a retired Composio endpoint                                | Implemented; live OAuth verification pending |
| [BUG-002](002-model-selection-logging.md) | Agent logs show the default model instead of the requested model                    | Implemented; covered by regression tests     |
| [BUG-003](003-gmail-connection-state.md)  | Gmail connection disappears after restart and agents repeat stale connection claims | Implemented; covered by regression tests     |
| [BUG-004](004-premature-chat-replies.md)  | Chat invents an email summary while the actual search is still running              | Implemented; covered by regression tests     |

Additional development notes: [email search resilience](../notes/email-search-resilience.md) and [chat reset behavior](../notes/chat-reset-behavior.md).

Calendar access is an unimplemented feature, not an available integration. Gmail can provide email evidence of events, but cannot query a calendar.

## Adding a record

Use the next available ID and a short descriptive filename. Include:

- Discovery date and status.
- User-visible symptoms and minimal reproduction steps.
- Evidence, distinguishing observations from hypotheses.
- Root cause, if established.
- Changed behavior and relevant source files.
- Regression tests and remaining limitations.

Update existing records as fixes are verified or new evidence changes the diagnosis. Add newly discovered bugs to this index.

## Checks

From the repository root:

```sh
.venv/bin/python -m unittest discover -s server/tests -v
```

From `web/`:

```sh
npm run lint
npx tsc --noEmit
```

The configured Prettier command formats frontend and Markdown files. It currently cannot format Python files because no Python parser is configured.
