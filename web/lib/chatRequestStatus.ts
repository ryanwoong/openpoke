import type { ExecutionSpan } from "../components/execution/executionGraph";

type RequestStatus = { status: "running" | "completed" | "failed"; error?: string };

function rootRequestId(span: ExecutionSpan) {
  if (span.parent_id || span.kind !== "interaction" || !span.input || typeof span.input !== "object") return undefined;
  return (span.input as Record<string, unknown>).request_id;
}

function hasRunningSpan(spans: ExecutionSpan[]) {
  const byId = new Map(spans.map((span) => [span.id, span]));
  return spans.some((span) => {
    if (span.status !== "running") return false;
    let parent = span.parent_id ? byId.get(span.parent_id) : undefined;
    while (parent) {
      if (parent.kind === "execution" && ["failed", "cancelled"].includes(parent.status)) return false;
      parent = parent.parent_id ? byId.get(parent.parent_id) : undefined;
    }
    return true;
  });
}

export function chatRequestStatus(spans: ExecutionSpan[], requestId: string): RequestStatus | null {
  const root = spans.find((span) => rootRequestId(span) === requestId);
  if (!root) return null;
  const run = spans.filter((span) => span.run_id === root.run_id);
  if (hasRunningSpan(run)) return { status: "running" };
  const lastInteraction = run.filter((span) => span.kind === "interaction").at(-1) || root;
  if (["failed", "cancelled"].includes(lastInteraction.status)) {
    const error = lastInteraction.output && typeof lastInteraction.output === "object" ? (lastInteraction.output as Record<string, unknown>).error : null;
    return { status: "failed", error: typeof error === "string" && error ? error : lastInteraction.status === "cancelled" ? "The request was cancelled." : "The request could not be completed." };
  }
  const unfinishedExecution = run.find((span) => span.kind === "execution" && ["failed", "cancelled"].includes(span.status));
  if (unfinishedExecution) {
    const output = unfinishedExecution.output && typeof unfinishedExecution.output === "object" ? (unfinishedExecution.output as Record<string, unknown>) : null;
    return { status: "failed", error: typeof output?.error === "string" && output.error ? output.error : "The task stopped before it could finish. See the latest message for details." };
  }
  return { status: "completed" };
}

export function hasActiveChatRequest(spans: ExecutionSpan[]) {
  const userRuns = new Set(spans.filter((span) => !span.parent_id && span.kind === "interaction" && span.input && typeof span.input === "object" && typeof (span.input as Record<string, unknown>).prompt === "string").map((span) => span.run_id));
  return hasRunningSpan(spans.filter((span) => userRuns.has(span.run_id)));
}
