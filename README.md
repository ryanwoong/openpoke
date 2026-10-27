# OpenPoke 🌴

OpenPoke is a local-first email and scheduling assistant built on the open-source [OpenPoke](https://github.com/shlokkhemani/OpenPoke) project. A FastAPI backend coordinates interaction and execution agents; a Next.js frontend provides chat, a live execution monitor, reminders, and model settings.

This version adds a redesigned frontend, automatic context compaction, bounded agent discovery, headers-first email retrieval, and controls for reviewing scheduled work. See the short [implementation writeup](docs/WRITEUP.md) for the overload fix, frontend decisions, chosen feature, and next steps.

## What you can do

- **Chat about your inbox:** search candidate email headers, read selected conversations, and ask follow-up questions. Draft, reply, and forward through the Gmail integration.
- **Inspect execution:** watch agent, model, and tool activity on a live graph; inspect individual calls and saved task history.
- **Manage reminders:** review upcoming reminders, scheduled tasks, and email drafts; edit or cancel items and approve scheduled emails.
- **Choose models:** manage OpenRouter or OpenAI-compatible providers, discover models, and inspect or override their context-window limits.

## Requirements

- Python **3.11+** (the backend uses `asyncio.timeout`).
- Node.js **18.17+** and npm **9+**.
- An OpenRouter API key for the default model configuration, or a configured OpenAI-compatible inference endpoint.
- A Composio API key and Gmail auth config ID for Gmail workflows.

## Run locally

Run these commands from the repository root.

1. Copy the environment template:

   ```sh
   cp .env.example .env
   ```

   Set `OPENROUTER_API_KEY` in `.env` for the default OpenRouter setup. For Gmail, also set `COMPOSIO_API_KEY` and `COMPOSIO_GMAIL_AUTH_CONFIG_ID` from your Composio Gmail integration. Keep credentials out of Git.

2. Create a virtual environment and install backend dependencies:

   ```sh
   python3 -m venv .venv
   source .venv/bin/activate
   python --version
   python -m pip install -r server/requirements.txt
   ```

   Verify that the version is 3.11 or newer. On Windows PowerShell, use `py -3 -m venv .venv` and `.\.venv\Scripts\Activate.ps1` instead.

3. Install frontend dependencies:

   ```sh
   npm ci --prefix web
   ```

4. Start the backend:

   ```sh
   python -m server.server --reload
   ```

   It listens on [http://localhost:8001](http://localhost:8001). Check [health](http://localhost:8001/api/v1/health) or inspect the [API docs](http://localhost:8001/docs).

5. In a second terminal, start the frontend:

   ```sh
   npm run dev --prefix web
   ```

   Open [http://localhost:3000](http://localhost:3000). Keep both processes running. Next.js loads the root `.env` and proxies requests to the backend.

6. Open **Settings → Integrations** and connect Gmail through the Composio OAuth flow. Email search, drafting, and important-email monitoring need a connected account. **Settings → Model** manages providers and models; a self-hosted endpoint must be reachable from the backend and support the tools needed by the agents.

### Try it

- Ask “Find the most recent email from [a sender in your inbox] and summarize the conversation.” Open **Execution** to inspect the discovery and read calls.
- Ask “Remind me tomorrow at 9 AM to review my inbox.” Open **Reminders → Upcoming**, inspect the scheduled time, then edit or delete the reminder. Set your timezone in Settings first.
- In **Execution**, open History and select a task to inspect its saved invocation graph.

Scheduled work runs inside the backend process, so leave it running for reminders and emails to execute.

## Configuration and troubleshooting

| Setting                                       | Purpose                                                                                                                                  |
| --------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------- |
| `OPENPOKE_PORT`                               | Backend port; defaults to `8001`.                                                                                                        |
| `PY_SERVER_URL`                               | Frontend proxy target; defaults to `http://localhost:8001`. Set this too if you change the backend port.                                 |
| `OPENPOKE_CONTEXT_WINDOW_TOKENS`              | Fallback model window when metadata is unavailable; defaults to `32768`. Prefer the discovered size or a per-model override in Settings. |
| `OPENPOKE_CONTEXT_OUTPUT_TOKENS`              | Desired output reserve; defaults to `4096`, capped at a quarter of the model window.                                                     |
| `OPENPOKE_EXECUTION_TIMEOUT_SECONDS`          | Overall chat deadline; defaults to `600`.                                                                                                |
| `OPENPOKE_MODEL_TIMEOUT_SECONDS`              | Individual model-call deadline; defaults to `180`.                                                                                       |
| `OPENPOKE_CONTEXT_COMPACTION_TIMEOUT_SECONDS` | Compaction phase deadline; defaults to `240`, subject to the overall deadline.                                                           |
| `OPENPOKE_TRACE_DB`                           | Execution-history SQLite path; defaults to `server/data/execution_history.db`.                                                           |

Restart the relevant processes after changing `.env`. A frontend `502` usually means the backend is unavailable or the proxy target is wrong. Missing-model-key errors require a valid OpenRouter key or a saved custom provider. For context rejections on a local model, set its context override to the inference server's actual configured capacity.

## Validation

From the repository root:

```sh
.venv/bin/python -m unittest discover -s server/tests
npm test --prefix web
npm run lint --prefix web
npm run build --prefix web
```

The backend suite uses mocked model/tool responses and temporary stores; the optional live agent-discovery evaluation is skipped unless explicitly enabled. Tests cover context budgets, email evidence coverage, scheduling, request isolation, and execution persistence. Frontend tests cover chat behavior and execution graph projection. These checks do not establish live provider availability or Gmail OAuth configuration.

## Project layout and local data

- `server/` — FastAPI routes, agents, Gmail tools, scheduler, context budgeting, and execution tracing.
- `web/` — Next.js frontend and backend proxy routes.
- `server/tests/` — backend regression tests.
- `docs/` — implementation writeup and detailed feature/bug notes.
- `server/data/` — ignored runtime state, including conversation logs, agent records, reminders, context snapshots, and execution history.

Execution history survives backend restarts; interrupted spans are marked cancelled. Retention targets the latest 1,000 spans by removing whole inactive runs and preserving active runs. Clear Data removes saved conversation/execution state and context snapshots. Stored email content and logs may contain personal data. This is a local development app with shared state and no per-user isolation; deployment would require authentication and storage isolation.

## License

MIT — see [LICENSE](LICENSE).
