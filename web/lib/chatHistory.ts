import type { ChatBubble } from "../components/chat/types";
export function toBubbles(payload: unknown, previous: ReadonlyArray<ChatBubble> = []): ChatBubble[] {
  const entries = (payload as { messages?: unknown } | null)?.messages;
  if (!Array.isArray(entries)) return [];
  const existing = new Map<string, ChatBubble[]>();
  for (const message of previous) {
    const key = JSON.stringify([message.role, message.text, message.requestId || null, message.source || null]);
    const matches = existing.get(key) ?? [];
    matches.push(message);
    existing.set(key, matches);
  }
  return entries
    .filter((entry) => typeof entry?.role === "string" && typeof entry?.content === "string" && entry.content.trim().length > 0)
    .map((entry) => {
      const previousMessage = existing.get(JSON.stringify([entry.role, entry.content, entry.request_id || null, entry.source || null]))?.shift();
      return {
        id: previousMessage?.id ?? crypto.randomUUID(),
        role: entry.role,
        text: entry.content,
        ...(entry.retryable === true ? { retryable: true } : {}),
        ...(typeof entry.request_id === "string" && entry.request_id ? { requestId: entry.request_id } : {}),
        ...(["scheduled", "failure"].includes(entry.source) ? { source: entry.source as "scheduled" | "failure" } : {}),
        timestamp: typeof entry.timestamp === "string" && entry.timestamp ? entry.timestamp : (previousMessage?.timestamp ?? null),
      };
    });
}
