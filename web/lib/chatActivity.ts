import type { ExecutionSpan } from "../components/execution/executionGraph";

export type EmailSummary = { subject: string; sender: string };
export type ActivityStep = { id: string; label: string; status: "running" | "completed" | "failed" | "cancelled" | "interrupted"; emails?: EmailSummary[] };
export type ChatActivity = { requestId: string; userText: string; occurrence: number; steps: ActivityStep[] };

const labels: Record<string, [string, string]> = {
  task_email_search: ["Searching your inbox…", "Searched your inbox"],
  gmail_create_draft: ["Creating an email draft…", "Created an email draft"],
  gmail_execute_draft: ["Sending your email…", "Sent your email"],
  gmail_delete_draft: ["Deleting an email draft…", "Deleted an email draft"],
  gmail_forward_email: ["Forwarding your email…", "Forwarded your email"],
  gmail_reply_to_thread: ["Sending your reply…", "Sent your reply"],
  gmail_get_contacts: ["Checking your contacts…", "Checked your contacts"],
  gmail_get_people: ["Looking up contact details…", "Looked up contact details"],
  gmail_search_people: ["Searching your contacts…", "Searched your contacts"],
  gmail_list_drafts: ["Checking your email drafts…", "Checked your email drafts"],
  createTrigger: ["Creating your reminder…", "Created your reminder"],
  updateTrigger: ["Updating your reminder…", "Updated your reminder"],
  listTriggers: ["Checking your reminders…", "Checked your reminders"],
  GMAIL_FETCH_EMAILS: ["Checking your inbox…", "Checked your inbox"],
};

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" ? (value as Record<string, unknown>) : {};
}

function activityStep(span: ExecutionSpan): ActivityStep | null {
  const name = span.label.replace(/^Gmail · /, "");
  if (!Object.hasOwn(labels, name) || !["tool", "access"].includes(span.kind)) return null;
  const envelope = Array.isArray(span.output) && span.output.length === 2 && typeof span.output[0] === "boolean" ? span.output : null;
  const payload = envelope ? envelope[1] : span.output;
  const output = record(payload);
  const failed = span.status === "failed" || (envelope && envelope[0] === false) || output.status === "error" || Boolean(output.error) || output.success === false || output.successful === false;
  const status = span.status === "cancelled" ? "cancelled" : failed ? "failed" : span.status === "running" ? "running" : "completed";
  let label = labels[name][status === "running" ? 0 : 1];
  if (status === "failed") label = `Couldn’t complete: ${labels[name][0].replace(/…$/, "").toLowerCase()}`;
  if (status === "cancelled") label = `Cancelled: ${labels[name][0].replace(/…$/, "").toLowerCase()}`;
  let emails: EmailSummary[] | undefined;
  if (status === "completed" && name === "task_email_search" && Array.isArray(payload)) {
    // Trace sanitization retains at most 50 items, so a full list is a lower bound.
    const count = payload.length;
    const summaries = payload.filter((email) => typeof email?.subject === "string" && typeof email?.sender === "string").map((email) => ({ subject: email.subject.slice(0, 300) || "(No subject)", sender: email.sender.slice(0, 300) }));
    if (summaries.length) emails = summaries;
    label = `Found ${count}${count === 50 ? "+" : ""} matching email${count === 1 ? "" : "s"}`;
  }
  return { id: span.id, label, status, ...(emails ? { emails } : {}) };
}

export function updateChatActivity(activities: ChatActivity[], spans: ExecutionSpan[]): ChatActivity[] {
  return activities.map((activity) => {
    const root = spans.find((span) => !span.parent_id && span.kind === "interaction" && record(span.input).request_id === activity.requestId);
    if (!root) return activity;
    const run = spans.filter((span) => span.run_id === root.run_id);
    const byId = new Map(run.map((span) => [span.id, span]));
    const steps = run
      .filter((span) => {
        // Public tools already represent their nested Gmail calls.
        let parent = span.parent_id ? byId.get(span.parent_id) : undefined;
        while (parent) {
          if (parent.kind === "tool" && Object.hasOwn(labels, parent.label)) return false;
          parent = parent.parent_id ? byId.get(parent.parent_id) : undefined;
        }
        return true;
      })
      .sort((a, b) => a.started_at.localeCompare(b.started_at))
      .map(activityStep)
      .filter((step): step is ActivityStep => step !== null);
    const merged = new Map(activity.steps.map((step) => [step.id, step]));
    for (const step of steps) merged.set(step.id, step);
    return { ...activity, steps: [...merged.values()] };
  });
}

export function restoreChatActivity(raw: string | null): ChatActivity[] {
  try {
    const value: unknown = JSON.parse(raw || "[]");
    if (!Array.isArray(value)) return [];
    return value
      .filter(
        (item): item is ChatActivity =>
          typeof item?.requestId === "string" &&
          typeof item?.userText === "string" &&
          Number.isInteger(item?.occurrence) &&
          item.occurrence > 0 &&
          Array.isArray(item?.steps) &&
          item.steps.every((step: ActivityStep) => typeof step?.id === "string" && typeof step?.label === "string" && ["running", "completed", "failed", "cancelled", "interrupted"].includes(step?.status) && (step.emails === undefined || (Array.isArray(step.emails) && step.emails.length <= 50 && step.emails.every((email) => typeof email?.subject === "string" && typeof email?.sender === "string")))),
      )
      .map((activity) => ({ ...activity, steps: activity.steps.map((step) => (step.status === "running" ? { ...step, status: "interrupted", label: "Activity interrupted; waiting to reconnect" } : step)) }));
  } catch {
    return [];
  }
}

export function chatActivityPositions(messages: ReadonlyArray<{ role: string; text: string }>, activities: ChatActivity[]): Map<number, ChatActivity[]> {
  const positions = new Map<number, ChatActivity[]>();
  const occurrences = new Map<string, number>();
  messages.forEach((message, index) => {
    if (message.role !== "user") return;
    const occurrence = (occurrences.get(message.text) || 0) + 1;
    occurrences.set(message.text, occurrence);
    for (const activity of activities.filter((item) => item.userText === message.text && item.occurrence === occurrence && item.steps.length)) {
      const nextUserIndex = messages.findIndex((next, nextIndex) => nextIndex > index && next.role === "user");
      const position = nextUserIndex >= 0 ? nextUserIndex : messages.length;
      positions.set(position, [...(positions.get(position) || []), activity]);
    }
  });
  return positions;
}
