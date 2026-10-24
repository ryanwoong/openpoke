"use client";

import { useCallback, useEffect, useRef, useState } from "react";

export type Reminder = {
  id: number;
  agent_name: string;
  kind: "reminder" | "email" | "task";
  status: "scheduled" | "awaiting_approval" | "paused" | "running" | "completed" | "cancelled" | "failed";
  upcoming: boolean;
  editable: boolean;
  start_time: string | null;
  next_trigger: string | null;
  recurrence_rule: string | null;
  timezone: string | null;
  last_error: string | null;
  updated_at: string;
  message: string;
  recipient_email: string;
  subject: string;
  body: string;
  version: string;
};

export type ReminderChange = { action: "edit" | "cancel" | "approve" | "resume"; start_time?: string; message?: string; recipient_email?: string; subject?: string; body?: string };

export function useReminders(resetKey: number) {
  const [items, setItems] = useState<Reminder[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const generation = useRef(0);
  const requestSequence = useRef(0);

  const refresh = useCallback(async () => {
    const sequence = ++requestSequence.current;
    const currentGeneration = generation.current;
    try {
      const response = await fetch("/api/reminders", { cache: "no-store" });
      if (!response.ok) throw new Error("Reminders are unavailable. Retrying…");
      const data = await response.json();
      if (currentGeneration === generation.current && sequence === requestSequence.current) {
        setItems(data.items);
        setError("");
      }
    } catch (err) {
      if (currentGeneration === generation.current && sequence === requestSequence.current) setError(err instanceof Error ? err.message : "Could not load reminders");
    } finally {
      if (currentGeneration === generation.current && sequence === requestSequence.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    generation.current += 1;
    setItems([]);
    setLoading(true);
    let disposed = false;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      await refresh();
      if (!disposed) timer = setTimeout(poll, 2000);
    }
    void poll();
    window.addEventListener("focus", refresh);
    return () => {
      disposed = true;
      generation.current += 1;
      clearTimeout(timer);
      window.removeEventListener("focus", refresh);
    };
  }, [refresh, resetKey]);

  const change = useCallback(
    async (item: Reminder, update: ReminderChange) => {
      try {
        const response = await fetch(`/api/reminders/${item.id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...update, version: item.version }) });
        const data = await response.json();
        if (!response.ok) throw new Error(typeof data.error === "string" ? data.error : typeof data.detail === "string" ? data.detail : "Could not update this item");
        return data as Reminder;
      } finally {
        await refresh();
      }
    },
    [refresh],
  );

  return { items, count: items.filter((item) => item.upcoming).length, loading, error, change };
}
