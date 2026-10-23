"use client";

import { useCallback, useEffect, useState } from "react";
import type { ExecutionSpan } from "../execution/executionGraph";
import { restoreChatActivity, updateChatActivity, type ChatActivity } from "../../lib/chatActivity";

const STORAGE_KEY = "openpoke.chat-activity.v1";

export function useChatActivity(spans: ExecutionSpan[]) {
  const [activities, setActivities] = useState<ChatActivity[]>([]);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    try {
      setActivities(restoreChatActivity(localStorage.getItem(STORAGE_KEY)));
    } catch {
      setActivities([]);
    }
    setReady(true);
  }, []);

  useEffect(() => {
    setActivities((previous) => {
      const next = updateChatActivity(previous, spans);
      return JSON.stringify(previous) === JSON.stringify(next) ? previous : next;
    });
  }, [spans]);

  useEffect(() => {
    if (!ready) return;
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(activities));
    } catch {
      // Storage may be disabled or full; live activity still works in memory.
    }
  }, [activities, ready]);

  const beginActivity = useCallback((requestId: string, userText: string, occurrence: number) => {
    setActivities((previous) => [...previous, { requestId, userText, occurrence, steps: [] }]);
  }, []);
  const removeActivity = useCallback((requestId: string) => setActivities((previous) => previous.filter((activity) => activity.requestId !== requestId)), []);
  const clearActivity = useCallback(() => setActivities([]), []);

  return { activities, beginActivity, removeActivity, clearActivity };
}
