import type { ExecutionSpan } from "../components/execution/executionGraph";

type RequestStatus = { status: "running" | "completed" | "failed"; error?: string };

function rootRequestId(span: ExecutionSpan) {
  if (span.parent_id || span.kind !== "interaction" || !span.input || typeof span.input !== "object") return undefined;
  return (span.input as Record<string, unknown>).request_id;
}

export function chatRequestStatus(spans: ExecutionSpan[], requestId: string): RequestStatus | null {
  const root = spans.find((span) => rootRequestId(span) === requestId);
  if (!root) return null;
  const run = spans.filter((span) => span.run_id === root.run_id);
  if (run.some((span) => span.status === "running")) return { status: "running" };
  const lastInteraction = run.filter((span) => span.kind === "interaction").at(-1) || root;
  if (["failed", "cancelled"].includes(lastInteraction.status)) {
    const error = lastInteraction.output && typeof lastInteraction.output === "object" ? (lastInteraction.output as Record<string, unknown>).error : null;
    return { status: "failed", error: typeof error === "string" && error ? error : lastInteraction.status === "cancelled" ? "The request was cancelled." : "The request could not be completed." };
  }
  return { status: "completed" };
}

export function hasActiveChatRequest(spans: ExecutionSpan[]) {
  const userRuns = new Set(spans.filter((span) => !span.parent_id && span.kind === "interaction" && span.input && typeof span.input === "object" && typeof (span.input as Record<string, unknown>).prompt === "string").map((span) => span.run_id));
  return spans.some((span) => userRuns.has(span.run_id) && span.status === "running");
}
