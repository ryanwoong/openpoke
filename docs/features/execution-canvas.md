# Execution canvas: implementation

## Data flow

```text
POST /api/chat { messages, request_id }
  → Python chat handler → asyncio.create_task(interaction.execute)
  → @traced wrappers → process-local TraceStore
  → GET /api/v1/chat/execution → Next.js /api/execution
  → useExecutionActivity → live graph + chat working state
```

The handler returns HTTP 202 with `X-Request-ID`. [`execution_trace.py`](../../server/execution_trace.py) records decorated interaction, execution, model, tool, and Gmail calls. Each span contains:

```ts
{
  (id, run_id, parent_id, kind, label, status, started_at, ended_at, input, output);
}
```

A root uses its own ID as `run_id`. Children inherit the current span through a `ContextVar`; `asyncio.create_task()` and `asyncio.to_thread()` propagate that context. Background execution therefore stays attached to its delegation after the delegating function returns. A finished parent can still have running descendants. Edges describe invocation ancestry, not every message or data dependency. Batched agent results inherit the context of the task that drains the batch rather than linking every contributor.

Spans enter the store as `running`, then become `completed`, `failed`, or `cancelled`. Wrappers inspect returned success/error fields and record exceptions before re-raising them. Blocking tool calls run in threads; cancellation cannot forcibly stop a thread already executing.

[`useExecutionActivity.ts`](../../web/components/execution/useExecutionActivity.ts) fetches a full snapshot immediately, then waits 1,000 ms after each response before fetching again. Failures preserve the last snapshot and retry. Unmounting aborts the request and clears the timer. There is no event stream, graph database, or extra model call.

## Graph projection and animation

[`executionGraph.ts`](../../web/components/execution/executionGraph.ts) maps retained spans into stable interaction, execution-pool, Gmail, model-provider, and tools nodes. Named workers add nodes; all active workers and four recently used idle workers remain visible. Repeated inbox checks update Gmail instead of creating more nodes.

For every running span, the projection walks its ancestry and activates the corresponding connections. Idle edges show capabilities; active edges show work on that invocation path. Standalone Gmail polling activates Gmail alone. Short completed calls briefly highlight their node so they remain perceptible between polls.

Cards use role icons and status badges. `routeEdge()` generates rounded orthogonal SVG paths with endpoint ports and open arrowheads. Bypass lanes route connections around intermediate cards. Active connections animate a dashed overlay above a solid track; active blocks pulse and completed blocks briefly settle. Historical graphs never animate. `prefers-reduced-motion` disables motion while preserving status labels and colors.

## Camera and navigation

Conversation and Execution stay mounted; `#conversation` and `#execution` select the visible view. [`useExecutionViewport.ts`](../../web/components/execution/useExecutionViewport.ts) measures the viewport with `ResizeObserver`, fits cards and routed edges with 32px padding, and centers horizontally and vertically until the user moves the camera.

Left-drag/touch and arrow keys pan; scrolling zooms around the cursor. Toolbar zoom uses the viewport center. Manual movement preserves `{x, y, zoom}` across polls; **Fit** resumes automatic fitting. Pointer capture and a movement threshold distinguish drags from card clicks.

## Sidebar and history

[`ExecutionMonitor.tsx`](../../web/components/execution/ExecutionMonitor.tsx) owns selection and inspection. [`ExecutionSidebar.tsx`](../../web/components/execution/ExecutionSidebar.tsx) provides one shared right sidebar:

- **Recent activity:** latest 25 entries, grouping repeated background operations. Completed entries have a green status dot. Selecting an entry pushes details over the still-mounted list, preserving scroll; X/Escape returns to the list.
- **History:** groups spans by `run_id`. Tasks require an interaction root with `input.prompt`. Background groups show counts and status and are not clickable.
- **Task inspection:** copies the selected task into a frozen snapshot. One card represents each invocation, arranged by tree depth. Running descendants keep the task active even if its root finished. **Back to live** discards the snapshot; neither action executes anything.

Desktop sidebar width/opacity and mobile position animate. Content transitions within the same panel; closing content becomes inert and unmounts after 220ms. Escape dismisses the current layer; closing the root restores trigger focus. Reduced motion skips transitions and the closing delay.

## Chat working state

The page owns polling and shares spans with the canvas. Each submission adds a UUID to local pending requests and forwards it as `request_id`. [`chatRequestStatus.ts`](../../web/lib/chatRequestStatus.ts) matches `input.request_id` on the interaction root and checks its descendants. Old replies to identical prompts, acknowledgements, and background checks cannot complete that request.

Once execution finishes, chat refreshes conversation history before removing the pending ID; failed final interactions surface their error. There is no fixed working-indicator timeout. Polling failures retain pending state, and active task spans restore the indicator after reload. `useAutoScroll()` ignores hidden layout, distinguishes programmatic scrolling from user movement, and restores the bottom when returning to a conversation that was following new messages.

## Storage and visibility limits

`TraceStore` uses an insertion-ordered dictionary protected by `threading.Lock`. It targets 1,000 spans by evicting whole oldest inactive runs; active runs may exceed that limit. Traces disappear on restart and are separate per Python worker. Clearing chat clears traces without cancelling work; later calls whose parent was removed start new roots.

Sanitization redacts sensitive structured keys, truncates strings to 4,000 characters and containers to 50 entries, and stops beyond nesting depth 5. Model results retain only model/usage metadata. Full model messages and reasoning are omitted. Free-text personal data can remain, and the store has no per-user partitioning. Context-source labels are declared descriptions, not audited filesystem reads; uninstrumented operations are invisible.

## Checks

```sh
.venv/bin/python -m unittest discover -s server/tests
npm test --prefix web
npm run lint --prefix web
npm run build --prefix web
```

Backend tests cover trace ancestry, concurrency, thread propagation, failures, sanitization, retention, and request-ID forwarding. Frontend tests cover graph projection, history grouping, animation suppression, and request completion. Browser checks cover long requests, tab switching, nested navigation, and temporary polling/history failures.
