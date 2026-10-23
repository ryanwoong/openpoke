"use client";

import { useEffect, useState } from "react";
import type { ExecutionSpan } from "./executionGraph";

export function useExecutionActivity(resetKey: number) {
  const [spans, setSpans] = useState<ExecutionSpan[]>([]);
  const [error, setError] = useState("");

  useEffect(() => {
    setSpans([]);
    setError("");
    let disposed = false;
    let timer: ReturnType<typeof setTimeout>;
    const controller = new AbortController();
    async function poll() {
      try {
        const response = await fetch("/api/execution", { cache: "no-store", signal: controller.signal });
        if (!response.ok) throw new Error("Execution monitor is unavailable");
        const data = await response.json();
        if (!disposed) {
          setSpans(data.spans);
          setError("");
        }
      } catch {
        if (!disposed) setError("Disconnected · retrying…");
      } finally {
        if (!disposed) timer = setTimeout(poll, 1000);
      }
    }
    void poll();
    return () => {
      disposed = true;
      controller.abort();
      clearTimeout(timer);
    };
  }, [resetKey]);

  return { spans, error };
}
