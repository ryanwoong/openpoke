import type { ChatBubble } from "../components/chat/types";
export function toBubbles(payload: unknown, previous: ReadonlyArray<ChatBubble> = []): ChatBubble[] {
  const entries = (payload as { messages?: unknown } | null)?.messages;
  if (!Array.isArray(entries)) return [];
  const existing = new Map<string, string[]>();
  for (const message of previous) {
    const key = JSON.stringify([message.role, message.text]);
    const ids = existing.get(key) ?? [];
    ids.push(message.id);
    existing.set(key, ids);
  }
  return entries
    .filter((entry) => typeof entry?.role === "string" && typeof entry?.content === "string" && entry.content.trim().length > 0)
    .map((entry) => ({
      id: existing.get(JSON.stringify([entry.role, entry.content]))?.shift() ?? crypto.randomUUID(),
      role: entry.role,
      text: entry.content,
    }));
}
