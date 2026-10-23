import assert from "node:assert/strict";
import { test } from "node:test";
import { executionHistory, liveExecutionGraph, recordedExecutionGraph, routeEdge, type ExecutionSpan } from "./executionGraph";

const started_at = "2026-10-22T12:00:00.000Z";
function span(id: string, kind: string, label: string, parent: string | null = null, runId = "task", status = "completed", input: unknown = {}): ExecutionSpan {
  return { id, kind, label, parent_id: parent, run_id: runId, status, input, output: null, started_at, ended_at: status === "running" ? null : started_at };
}

test("background checks update one service and do not flood task history or the live canvas", () => {
  const spans = Array.from({ length: 40 }, (_, index) => span(`poll${index}`, "access", "Gmail · GMAIL_FETCH_EMAILS", null, `poll${index}`));
  spans.push(span("task", "interaction", "Interaction agent", null, "task", "completed", { prompt: "What can you do?" }));
  const graph = liveExecutionGraph(spans);
  assert.equal(graph.nodes.length, 5);
  assert.equal(graph.nodes.filter((node) => node.id === "gmail").length, 1);
  assert.deepEqual(
    executionHistory(spans).map((task) => task.title),
    ["What can you do?"],
  );
  const background = executionHistory(spans, true);
  assert.equal(background.length, 1);
  assert.equal(background[0].occurrences, 40);
  assert.equal(background[0].id, "poll39");
});

test("live activity combines concurrent tasks, preserves idle roles, and animates the actual ancestry", () => {
  const spans = [
    span("task", "interaction", "Interaction agent", null, "task", "completed", { prompt: "Read my email" }),
    span("dispatch", "tool", "send_message_to_agent", "task"),
    span("worker", "execution", "Email checker", "dispatch", "task", "running"),
    span("tool", "tool", "search_email", "worker", "task", "running"),
    span("gmail", "access", "Gmail · GMAIL_FETCH_EMAILS", "tool", "task", "running"),
    span("other", "interaction", "Interaction agent", null, "other", "running", { prompt: "Another prompt" }),
    span("model", "model", "OpenRouter · test", "other", "other", "running"),
  ];
  const graph = liveExecutionGraph(spans);
  assert.equal(graph.nodes.find((node) => node.id === "gmail")?.status, "running");
  assert.equal(graph.nodes.find((node) => node.id === "models")?.status, "running");
  assert.equal(graph.nodes.find((node) => node.id === "interaction")?.status, "running");
  assert.equal(graph.nodes.find((node) => node.id === "worker:Email checker")?.status, "running");
  assert.ok(graph.edges.some((edge) => edge.source === "interaction" && edge.target === "execution" && edge.active));
  assert.ok(graph.edges.some((edge) => edge.source === "tools" && edge.target === "gmail" && edge.active));
  const finished = liveExecutionGraph(spans.map((entry) => ({ ...entry, status: "completed", ended_at: started_at })));
  assert.ok(finished.nodes.every((node) => node.status === "idle"));
  assert.ok(finished.edges.every((edge) => !edge.active));
  assert.ok(finished.nodes.some((node) => node.id === "worker:Email checker"));
});

test("a completed root remains a running task while its worker runs; historical edges never animate", () => {
  const spans = [span("task", "interaction", "Interaction agent", null, "task", "completed", { prompt: "Check mail" }), span("worker", "execution", "Checker", "task", "task", "running")];
  assert.equal(executionHistory(spans)[0].status, "running");
  assert.equal(executionHistory(spans)[0].ended_at, null);
  const snapshot = recordedExecutionGraph(spans);
  assert.equal(snapshot.nodes.length, 2);
  assert.equal(snapshot.nodes[1].status, "running");
  assert.ok(snapshot.edges.every((edge) => !edge.active));
});

test("background access does not invent an active agent or an agent-to-service transfer", () => {
  const graph = liveExecutionGraph([span("poll", "access", "Gmail · GMAIL_FETCH_EMAILS", null, "poll", "running")]);
  assert.equal(graph.nodes.find((node) => node.id === "gmail")?.status, "running");
  assert.equal(graph.nodes.find((node) => node.id === "interaction")?.status, "idle");
  assert.equal(graph.nodes.find((node) => node.id === "execution")?.status, "idle");
  assert.ok(graph.edges.every((edge) => !edge.active));
});

test("idle worker retention is bounded while all active workers remain visible", () => {
  const spans = Array.from({ length: 15 }, (_, index) => span(`worker${index}`, "execution", `Worker ${index}`, null, `worker${index}`, index < 7 ? "running" : "completed"));
  const workers = liveExecutionGraph(spans).nodes.filter((node) => node.id.startsWith("worker:"));
  assert.equal(workers.filter((node) => node.status === "running").length, 7);
  assert.equal(workers.filter((node) => node.status !== "running").length, 4);
});

test("reused idle workers are retained by their most recent invocation", () => {
  const spans = Array.from({ length: 8 }, (_, index) => span(`worker${index}`, "execution", `Worker ${index}`, null, `worker${index}`));
  spans.push(span("reused", "execution", "Worker 0", null, "reused"));
  const workers = liveExecutionGraph(spans).nodes.filter((node) => node.id.startsWith("worker:"));
  assert.equal(workers.length, 4);
  assert.ok(workers.some((node) => node.id === "worker:Worker 0"));
  assert.ok(!workers.some((node) => node.id === "worker:Worker 4"));
});

test("a failed descendant keeps task history marked with an issue after activity ends", () => {
  const spans = [span("task", "interaction", "Interaction agent", null, "task", "completed", { prompt: "Find email" }), span("failed", "tool", "email_search", "task", "task", "failed")];
  assert.equal(executionHistory(spans)[0].status, "failed");
});

test("rounded connector lanes stay outside cards, including bypasses and worker branches", () => {
  const spans = [
    span("task", "interaction", "Interaction agent", null, "task", "running", { prompt: "Read mail" }),
    span("model", "model", "OpenRouter", "task", "task", "running"),
    span("dispatch", "tool", "send_message_to_agent", "task"),
    span("worker1", "execution", "Worker 1", "dispatch", "task", "running"),
    span("worker2", "execution", "Worker 2", "dispatch", "task", "running"),
    span("tool", "tool", "email_search", "worker1", "task", "running"),
    span("gmail", "access", "Gmail · GMAIL_FETCH_EMAILS", "tool", "task", "running"),
  ];
  for (const graph of [liveExecutionGraph(spans), recordedExecutionGraph(spans)]) {
    const nodes = new Map(graph.nodes.map((node) => [node.id, node]));
    for (const edge of graph.edges) {
      const route = routeEdge(nodes.get(edge.source)!, nodes.get(edge.target)!);
      assert.ok(!route.path.includes("NaN"));
      for (let index = 1; index < route.points.length; index++) {
        const start = route.points[index - 1];
        const end = route.points[index];
        for (let step = 0; step <= 100; step++) {
          const x = start.x + ((end.x - start.x) * step) / 100;
          const y = start.y + ((end.y - start.y) * step) / 100;
          for (const node of graph.nodes) {
            assert.ok(!(x > node.x && x < node.x + 240 && y > node.y && y < node.y + node.height), `${edge.source} → ${edge.target} crosses ${node.id}`);
          }
        }
      }
    }
  }
});
