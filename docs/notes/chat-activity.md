# Chat activity

The conversation renders tool progress from the existing `/api/execution` poll (one second). No extra model calls or streaming transport are required.

- `chatActivity.ts` correlates root interaction spans by `request_id`, selects user-facing tools, and suppresses nested Gmail calls already represented by a parent tool. Labels follow running, completed, failed, or cancelled states. Execution tools return `[success, payload]`; unwrap that envelope before checking errors or counting emails. Email counts and expandable subject/sender summaries come from the same search payload; the trace's 50-item limit is displayed as `50+`.
- `useChatActivity` stores normalized labels, statuses, and brief email summaries in `localStorage` (`openpoke.chat-activity.v1`). Raw tool arguments, email bodies, and full outputs are not cached. Activities attach to the initiating user message by exact text and occurrence, including repeated prompts.
- Activity always renders at the end of its user turn, below acknowledgements and the final reply. Later user messages start a new turn; historical activity stays with its original turn. While a reply is pending, the same activity block shows “Preparing your reply…” without adding another assistant identity.
- `ChatActivitySteps` displays live rows with spinners, completion checks, and failure indicators. Email result labels expand to show only subject and sender. Finished groups collapse into an expandable summary. Chat reset clears cached activity.

Persistence is browser-local, including across server restarts; other browsers do not share it. Restored running steps show an interrupted/reconnecting state until matching live spans arrive. Background tasks and requests initiated outside this browser are excluded. Server-side message IDs and durable activity storage would be required for cross-device history.
