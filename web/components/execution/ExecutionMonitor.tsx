"use client";

import { useEffect, useState } from "react";
import { activityLabel, routeEdge, executionHistory, liveExecutionGraph, recordedExecutionGraph, type ExecutionSpan, type ExecutionTask } from "./executionGraph";
import { ExecutionSidebar, type ExecutionPanel } from "./ExecutionSidebar";
import { useExecutionViewport } from "./useExecutionViewport";
import styles from "./ExecutionMonitor.module.css";

type Snapshot = { task: ExecutionTask; capturedAt: number };

function elapsed(start: string, end: string | null, now: number) {
  return `${Math.max(0, (Date.parse(end || new Date(now).toISOString()) - Date.parse(start)) / 1000).toFixed(1)}s`;
}

function statusLabel(status: string, recorded = false) {
  if (status === "running") return recorded ? "In progress at capture" : "Working";
  return status === "failed" ? "Needs attention" : status === "cancelled" ? "Cancelled" : status === "idle" ? "Idle" : "Completed";
}

function NodeIcon({ kind }: { kind: string }) {
  const paths: Record<string, string> = {
    interaction: "M5 4h14a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2h-8l-6 4v-4a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2Z M7 9h10 M7 13h6",
    execution: "M4 3h6v6H4z M14 15h6v6h-6z M10 6h7v9 M7 9v9h7",
    model: "m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5Z",
    access: "M4 5h16v14H4z m0 1 8 6 8-6",
    tool: "M4 4h6v6H4z M14 4h6v6h-6z M4 14h6v6H4z M14 14h6v6h-6z",
  };
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d={paths[kind] || paths.tool} />
    </svg>
  );
}

export function ExecutionMonitor({ resetKey, spans, error }: { resetKey: number; spans: ExecutionSpan[]; error: string }) {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [panel, setPanel] = useState<ExecutionPanel | null>(null);
  const [backgroundHistory, setBackgroundHistory] = useState(false);
  const [selectedNodeId, setSelectedNodeId] = useState("");
  const [selectedSpan, setSelectedSpan] = useState<ExecutionSpan | null>(null);
  const [detailParent, setDetailParent] = useState<"recent" | "node" | null>(null);
  const [cameraKey, setCameraKey] = useState(0);

  useEffect(() => {
    setSnapshot(null);
    setPanel(null);
    setSelectedNodeId("");
    setSelectedSpan(null);
    setDetailParent(null);
  }, [resetKey]);

  const now = Date.now();
  const recorded = Boolean(snapshot);
  const visibleSpans = snapshot?.task.spans || spans;
  const graph = snapshot ? recordedExecutionGraph(visibleSpans) : liveExecutionGraph(spans, now);
  const byId = new Map(graph.nodes.map((node) => [node.id, node]));
  const selectedNode = byId.get(selectedNodeId);
  const detailSpan = selectedSpan ? visibleSpans.find((span) => span.id === selectedSpan.id) || selectedSpan : null;
  const nodeSpans = selectedNode ? visibleSpans.filter((span) => selectedNode.spanIds.includes(span.id)).reverse() : [];
  const tasks = executionHistory(spans, backgroundHistory);
  const activeTasks = executionHistory(spans).filter((task) => task.status === "running").length;
  const activeCount = spans.filter((span) => span.status === "running").length;
  const backgroundByRun = new Set(spans.filter((span) => !span.parent_id && !(span.kind === "interaction" && span.input && typeof span.input === "object" && "prompt" in span.input)).map((span) => span.run_id));
  const seenBackground = new Set<string>();
  const recent = visibleSpans
    .slice()
    .reverse()
    .filter((span) => {
      if (recorded || !backgroundByRun.has(span.run_id)) return true;
      const key = `${span.kind}:${span.label}`;
      if (seenBackground.has(key)) return false;
      seenBackground.add(key);
      return true;
    })
    .slice(0, 25);
  const routes = graph.edges.map((edge) => ({ ...edge, route: routeEdge(byId.get(edge.source)!, byId.get(edge.target)!) }));
  const ports = new Map<string, { x: number; y: number; active: boolean }>();
  for (const edge of routes) {
    for (const point of [edge.route.start, edge.route.end]) {
      const key = `${point.x}:${point.y}`;
      ports.set(key, { ...point, active: !recorded && (edge.active || Boolean(ports.get(key)?.active)) });
    }
  }
  const routePoints = routes.flatMap((edge) => [...edge.route.points, edge.route.start, edge.route.end]);
  const left = Math.min(...graph.nodes.map((node) => node.x), ...routePoints.map((point) => point.x));
  const top = Math.min(...graph.nodes.map((node) => node.y), ...routePoints.map((point) => point.y));
  const right = Math.max(...graph.nodes.map((node) => node.x + 240), ...routePoints.map((point) => point.x));
  const bottom = Math.max(...graph.nodes.map((node) => node.y + node.height), ...routePoints.map((point) => point.y));
  const { viewportRef, view, isDragging, fit, zoomTo, handlers } = useExecutionViewport({ x: left, y: top, width: right - left, height: bottom - top }, resetKey + cameraKey);

  function inspectTask(task: ExecutionTask) {
    setDetailParent(null);
    setSnapshot({ task: { ...task, spans: task.spans.map((span) => ({ ...span })) }, capturedAt: Date.now() });
    setSelectedNodeId("");
    setSelectedSpan(null);
    setPanel(null);
    setCameraKey((key) => key + 1);
  }

  function backToLive() {
    setDetailParent(null);
    setSnapshot(null);
    setSelectedNodeId("");
    setSelectedSpan(null);
    setPanel(null);
    setCameraKey((key) => key + 1);
  }

  function inspectSpan(span: ExecutionSpan, parent: "recent" | "node" = "node") {
    setDetailParent(parent);
    setSelectedSpan(span);
    setPanel("details");
  }

  function closePanel() {
    if (panel === "details" && detailParent) {
      setSelectedSpan(null);
      setPanel(detailParent === "recent" ? "recent" : "details");
      setDetailParent(null);
      return;
    }
    setPanel(null);
  }

  return (
    <section
      className={styles.monitor}
      aria-label="Agent execution monitor"
      data-mode={recorded ? "history" : "live"}
    >
      <header className={styles.toolbar}>
        <div className={styles.heading}>
          <strong>{snapshot ? "Task inspection" : "Live execution"}</strong>
          <p role="status">{error || (snapshot ? `Recorded at ${new Date(snapshot.capturedAt).toLocaleTimeString()} · live view paused` : activeTasks ? `${activeTasks} task${activeTasks === 1 ? "" : "s"} in progress` : activeCount ? "Background activity in progress" : "Watching · agents are ready")}</p>
        </div>
        <div className={styles.actions}>
          {snapshot && (
            <button
              type="button"
              className={styles.backToLive}
              onClick={backToLive}
            >
              ● Back to live
            </button>
          )}
          <button
            type="button"
            className={styles.iconButton}
            aria-label="Recent activity"
            title="Recent activity"
            aria-expanded={panel === "recent" || (panel === "details" && detailParent === "recent")}
            onClick={() => setPanel((value) => (value === "recent" ? null : "recent"))}
          >
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.7"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden="true"
            >
              <path d="M3 12h4l3-8 4 16 3-8h4" />
            </svg>
          </button>
          <button
            type="button"
            className={styles.iconButton}
            aria-label="History"
            title="History"
            aria-expanded={panel === "history"}
            onClick={() => setPanel((value) => (value === "history" ? null : "history"))}
          >
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.7"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden="true"
            >
              <path d="M3 10a9 9 0 1 1 2.5 8M3 4v6h6" />
              <path d="M12 7v5l3 2" />
            </svg>
          </button>
        </div>
        <div className={styles.zoom}>
          <button
            type="button"
            aria-label="Zoom out"
            disabled={view.zoom <= 0.001}
            onClick={() => zoomTo(view.zoom / 1.2)}
          >
            −
          </button>
          <button
            type="button"
            aria-label="Reset zoom"
            onClick={() => zoomTo(1)}
          >
            {Math.round(view.zoom * 100)}%
          </button>
          <button
            type="button"
            aria-label="Zoom in"
            disabled={view.zoom >= 1.5}
            onClick={() => zoomTo(view.zoom * 1.2)}
          >
            +
          </button>
          <button
            type="button"
            onClick={fit}
            aria-label="Fit graph to view"
          >
            Fit
          </button>
        </div>
      </header>
      {snapshot && (
        <div className={styles.inspectionBanner}>
          <span>
            Inspecting: <strong>{snapshot.task.title}</strong>
          </span>
          <small>
            {statusLabel(snapshot.task.status, true)} · {elapsed(snapshot.task.started_at, snapshot.task.ended_at, snapshot.capturedAt)}
          </small>
        </div>
      )}
      <div className={styles.legend}>
        <span>Interaction</span>
        <span>Execution</span>
        <span>Models</span>
        <span>Tools & services</span>
      </div>
      <div className={styles.body}>
        <div
          ref={viewportRef}
          className={styles.viewport}
          data-dragging={isDragging}
          tabIndex={0}
          aria-label="Pannable execution graph"
          style={{ backgroundPosition: `${view.x}px ${view.y}px` }}
          {...handlers}
        >
          <div
            className={styles.canvas}
            style={{ width: right + 28, height: bottom + 30, transform: `translate(${view.x}px, ${view.y}px) scale(${view.zoom})` }}
          >
            <svg
              className={styles.edges}
              width={right + 28}
              height={bottom + 30}
              aria-hidden="true"
            >
              <defs>
                <marker
                  id="execution-arrow"
                  viewBox="0 0 10 10"
                  refX="7"
                  refY="5"
                  markerUnits="userSpaceOnUse"
                  markerWidth="9"
                  markerHeight="9"
                  orient="auto-start-reverse"
                >
                  <path
                    d="m3 2 4 3-4 3"
                    className={styles.arrowhead}
                  />
                </marker>
                <marker
                  id="execution-arrow-active"
                  viewBox="0 0 10 10"
                  refX="7"
                  refY="5"
                  markerUnits="userSpaceOnUse"
                  markerWidth="9"
                  markerHeight="9"
                  orient="auto-start-reverse"
                >
                  <path
                    d="m3 2 4 3-4 3"
                    className={`${styles.arrowhead} ${styles.activeArrowhead}`}
                  />
                </marker>
              </defs>
              {routes.map((edge) => (
                <g
                  key={`${edge.source}->${edge.target}`}
                  data-active={!recorded && edge.active}
                >
                  <path
                    className={styles.edgeTrack}
                    d={edge.route.path}
                    markerEnd={`url(#${!recorded && edge.active ? "execution-arrow-active" : "execution-arrow"})`}
                  />
                  {!recorded && edge.active && (
                    <path
                      className={styles.edgeFlow}
                      d={edge.route.path}
                    />
                  )}
                </g>
              ))}
              {Array.from(ports.entries()).map(([key, port]) => (
                <circle
                  key={key}
                  className={styles.port}
                  cx={port.x}
                  cy={port.y}
                  r="3"
                  data-active={port.active}
                />
              ))}
            </svg>
            {graph.nodes.map((node) => (
              <button
                type="button"
                key={node.id}
                className={`${styles.node} ${selectedNodeId === node.id && panel === "details" ? styles.selected : ""}`}
                data-node-id={node.id}
                data-kind={node.kind}
                data-status={node.status}
                data-animate={!recorded && node.status === "running"}
                data-recent={!recorded && node.status !== "running" && Boolean(node.updatedAt && now - Date.parse(node.updatedAt) < 2000)}
                style={{ left: node.x, top: node.y, height: node.height }}
                aria-pressed={selectedNodeId === node.id && panel === "details"}
                onClick={() => {
                  setDetailParent(null);
                  setSelectedNodeId(node.id);
                  setSelectedSpan(recorded ? visibleSpans.find((span) => span.id === node.spanIds[0]) || null : null);
                  setPanel("details");
                }}
              >
                <div className={styles.nodeHeader}>
                  <span className={styles.nodeIcon}>
                    <NodeIcon kind={node.kind} />
                  </span>
                  <small className={styles.nodeCategory}>{recorded ? node.kind : node.id.startsWith("worker:") ? "Worker" : ["interaction", "execution"].includes(node.id) ? "Agent" : "Service"}</small>
                  <span
                    className={styles.nodeStatus}
                    data-status={node.status}
                  >
                    {statusLabel(node.status, recorded)}
                  </span>
                </div>
                <strong>{node.label}</strong>
                <p>{node.description}</p>
                {!recorded && (
                  <small className={styles.nodeMeta}>
                    <svg
                      viewBox="0 0 16 16"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="1.2"
                      strokeLinecap="round"
                      aria-hidden="true"
                    >
                      <circle
                        cx="8"
                        cy="8"
                        r="5.5"
                      />
                      <path d="M8 4.8V8l2 1.3" />
                    </svg>
                    {node.meta}
                  </small>
                )}
              </button>
            ))}
          </div>
        </div>
        <ExecutionSidebar
          view={panel}
          parentView={panel === "details" && detailParent === "recent" ? "recent" : null}
          onClose={closePanel}
        >
          {(displayedPanel) => (
            <>
              {displayedPanel === "history" && (
                <div>
                  <div className={styles.drawerHeading}>
                    <div>
                      <h2>History</h2>
                      <p>Inspect a recorded task without running it again.</p>
                    </div>
                    <button
                      type="button"
                      aria-label="Close history"
                      onClick={() => setPanel(null)}
                    >
                      ×
                    </button>
                  </div>
                  <div
                    className={styles.tabs}
                    role="group"
                    aria-label="History category"
                  >
                    <button
                      type="button"
                      aria-pressed={!backgroundHistory}
                      onClick={() => setBackgroundHistory(false)}
                    >
                      Tasks
                    </button>
                    <button
                      type="button"
                      aria-pressed={backgroundHistory}
                      onClick={() => setBackgroundHistory(true)}
                    >
                      Background
                    </button>
                  </div>
                  {backgroundHistory && <p className={styles.panelNote}>Repeated background checks are grouped here for reference.</p>}
                  {!tasks.length && <p className={styles.panelNote}>{backgroundHistory ? "No background activity recorded yet." : "No tasks yet. Send a prompt in Conversation."}</p>}
                  <div
                    key={backgroundHistory ? "background" : "tasks"}
                    className={styles.historyList}
                  >
                    {tasks.map((task) => {
                      const content = (
                        <>
                          <span className={styles.itemTitle}>{task.title}</span>
                          <span className={styles.itemMeta}>
                            <time>{new Date(task.started_at).toLocaleTimeString()}</time>
                            <span data-status={task.status}>{statusLabel(task.status)}</span>
                          </span>
                          <small>
                            {backgroundHistory ? `${task.occurrences} occurrence${task.occurrences === 1 ? "" : "s"} · ` : ""}
                            {elapsed(task.started_at, task.ended_at, now)} · {task.spans.length} activities
                          </small>
                        </>
                      );
                      return backgroundHistory ? (
                        <div
                          key={task.id}
                          className={styles.historyItem}
                        >
                          {content}
                        </div>
                      ) : (
                        <button
                          type="button"
                          key={task.id}
                          onClick={() => inspectTask(task)}
                          className={styles.historyItem}
                        >
                          {content}
                        </button>
                      );
                    })}
                  </div>
                </div>
              )}
              {displayedPanel === "details" && (
                <div>
                  <div className={styles.drawerHeading}>
                    <h2>{detailSpan ? activityLabel(detailSpan) : selectedNode?.label || "Activity"}</h2>
                    <button
                      type="button"
                      aria-label={detailParent === "recent" ? "Back to recent activity" : "Close activity details"}
                      onClick={closePanel}
                    >
                      ×
                    </button>
                  </div>
                  {detailSpan ? (
                    <>
                      <p className={styles.panelNote}>
                        {statusLabel(detailSpan.status, recorded)} · {elapsed(detailSpan.started_at, detailSpan.ended_at, snapshot?.capturedAt || now)}
                      </p>
                      <h3>Input / access</h3>
                      <pre>{JSON.stringify(detailSpan.input, null, 2)}</pre>
                      <h3>Result</h3>
                      <pre>{detailSpan.status === "running" ? (recorded ? "No result at capture time." : "Waiting for result…") : JSON.stringify(detailSpan.output, null, 2)}</pre>
                      {selectedNode && (
                        <button
                          type="button"
                          className={styles.textButton}
                          onClick={() => {
                            setSelectedSpan(null);
                            setDetailParent(null);
                          }}
                        >
                          Back to {selectedNode.label}
                        </button>
                      )}
                    </>
                  ) : (
                    <>
                      <p className={styles.panelNote}>{selectedNode?.description || "This worker is no longer in the live view. Its activity remains available in History."}</p>
                      <h3>{recorded ? "Recorded activity" : "Latest activity"}</h3>
                      {!nodeSpans.length && <p className={styles.panelNote}>No activity recorded yet.</p>}
                      {nodeSpans.slice(0, 15).map((span) => (
                        <button
                          type="button"
                          className={styles.activityItem}
                          key={span.id}
                          onClick={() => inspectSpan(span)}
                        >
                          <span>{activityLabel(span)}</span>
                          <small>
                            {statusLabel(span.status, recorded)} · {new Date(span.started_at).toLocaleTimeString()}
                          </small>
                        </button>
                      ))}
                    </>
                  )}
                </div>
              )}
              {displayedPanel === "recent" && (
                <div>
                  <div className={styles.drawerHeading}>
                    <div>
                      <h2>{recorded ? "Recorded activity" : "Recent activity"}</h2>
                      <p>{recorded ? "Activity captured in this snapshot." : "Background checks grouped · latest 25 activities"}</p>
                    </div>
                    <button
                      type="button"
                      aria-label="Close recent activity"
                      onClick={() => setPanel(null)}
                    >
                      ×
                    </button>
                  </div>
                  <div className={styles.activityList}>
                    {!recent.length && <p className={styles.panelNote}>Activity will appear here when agents start working.</p>}
                    {recent.map((span) => (
                      <button
                        type="button"
                        key={span.id}
                        className={styles.feedItem}
                        onClick={() => {
                          setSelectedNodeId("");
                          inspectSpan(span, "recent");
                        }}
                      >
                        <time>{new Date(span.started_at).toLocaleTimeString()}</time>
                        <span>{activityLabel(span)}</span>
                        <svg
                          className={styles.feedChevron}
                          viewBox="0 0 20 20"
                          fill="none"
                          stroke="currentColor"
                          strokeWidth="1.5"
                          strokeLinecap="round"
                          strokeLinejoin="round"
                          aria-hidden="true"
                        >
                          <path d="m8 5 5 5-5 5" />
                        </svg>
                        <small data-status={span.status}>{statusLabel(span.status, recorded)}</small>
                      </button>
                    ))}
                  </div>
                </div>
              )}
            </>
          )}
        </ExecutionSidebar>
      </div>
    </section>
  );
}
