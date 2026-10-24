"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import SettingsModal, { useSettings, type GmailConnection } from "@/components/SettingsModal";
import { ChatSidebar } from "@/components/chat/ChatSidebar";
import { ChatHeader } from "@/components/chat/ChatHeader";
import { ChatInput } from "@/components/chat/ChatInput";
import { useChatActivity } from "@/components/chat/useChatActivity";
import { ChatMessages } from "@/components/chat/ChatMessages";
import { ErrorBanner } from "@/components/chat/ErrorBanner";
import { useBackgroundAlerts } from "@/components/chat/useBackgroundAlerts";
import { useAutoScroll } from "@/components/chat/useAutoScroll";
import type { ChatBubble } from "@/components/chat/types";

import { useExecutionActivity } from "@/components/execution/useExecutionActivity";
import { chatRequestStatus, hasActiveChatRequest } from "@/lib/chatRequestStatus";
import { ExecutionMonitor } from "@/components/execution/ExecutionMonitor";

import { toBubbles } from "@/lib/chatHistory";

import { Reminders } from "@/components/reminders/Reminders";
import { useReminders } from "@/components/reminders/useReminders";

const POLL_INTERVAL_MS = 1500;

export default function Page() {
  const { settings, setSettings } = useSettings();
  const [activeView, setActiveView] = useState<"conversation" | "execution" | "reminders">("conversation");
  const [executionResetKey, setExecutionResetKey] = useState(0);
  const reminders = useReminders(executionResetKey);
  const [open, setOpen] = useState(false);
  const [input, setInput] = useState("");
  const [isLoadingHistory, setIsLoadingHistory] = useState(true);
  const [gmail, setGmail] = useState<GmailConnection>({ connected: false, email: "", checking: true });
  const [settingsTab, setSettingsTab] = useState<"Main" | "Integrations">("Main");
  const [messages, setMessages] = useState<ChatBubble[]>([]);
  const highlightReply = useBackgroundAlerts(messages, isLoadingHistory, settings);
  const [error, setError] = useState<string | null>(null);
  const [pendingRequests, setPendingRequests] = useState<string[]>([]);
  const { spans: executionSpans, error: executionError } = useExecutionActivity(executionResetKey);
  const { activities, beginActivity, removeActivity, clearActivity } = useChatActivity(executionSpans);
  const isWaitingForResponse = pendingRequests.length > 0 || hasActiveChatRequest(executionSpans);
  const scrollItems = useMemo(() => [...messages, ...activities], [messages, activities]);
  const { scrollContainerRef, handleScroll } = useAutoScroll({
    items: scrollItems,
    isWaiting: isWaitingForResponse,
    isVisible: activeView === "conversation",
  });
  const openSettings = useCallback(() => {
    setSettingsTab("Main");
    setOpen(true);
  }, []);
  const openGmail = useCallback(() => {
    setSettingsTab("Integrations");
    setOpen(true);
  }, []);
  const closeSettings = useCallback(() => setOpen(false), [setOpen]);

  const loadHistory = useCallback(async () => {
    try {
      const res = await fetch("/api/chat/history", { cache: "no-store" });
      if (!res.ok) return false;
      const data = await res.json();
      setMessages((previous) => toBubbles(data, previous));
      return true;
    } catch (err: any) {
      if (err?.name === "AbortError") return;
      console.error("Failed to load chat history", err);
    } finally {
      setIsLoadingHistory(false);
    }
  }, []);

  useEffect(() => {
    void loadHistory();
  }, [loadHistory]);

  const finishedRequests = JSON.stringify(pendingRequests.map((id) => ({ id, progress: chatRequestStatus(executionSpans, id) })).filter(({ progress }) => progress && progress.status !== "running"));
  useEffect(() => {
    const finished: Array<{ id: string; progress: NonNullable<ReturnType<typeof chatRequestStatus>> }> = JSON.parse(finishedRequests);
    if (!finished.length) return;
    let disposed = false;
    let timer: ReturnType<typeof setTimeout>;
    async function syncFinishedRequests() {
      const loaded = await loadHistory();
      if (disposed) return;
      if (!loaded) {
        timer = setTimeout(syncFinishedRequests, POLL_INTERVAL_MS);
        return;
      }
      const ids = new Set(finished.map((request) => request.id));
      setPendingRequests((previous) => previous.filter((id) => !ids.has(id)));
      const failure = finished.find((request) => request.progress.status === "failed");
      if (failure) setError(failure.progress.error || "The request could not be completed.");
    }
    void syncFinishedRequests();
    return () => {
      disposed = true;
      clearTimeout(timer);
    };
  }, [finishedRequests, loadHistory]);

  // Detect and store browser timezone on first load
  useEffect(() => {
    let active = true;
    const detectAndStoreTimezone = async () => {
      // Only run if timezone not already stored
      if (settings.timezone) return;

      try {
        const browserTimezone = Intl.DateTimeFormat().resolvedOptions().timeZone;

        // Send to server
        const response = await fetch("/api/timezone", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ timezone: browserTimezone }),
        });

        if (response.ok && active) {
          // Update local settings
          setSettings({ ...settings, timezone: browserTimezone });
        }
      } catch (error) {
        // Fail silently - timezone detection is not critical
        console.debug("Timezone detection failed:", error);
      }
    };

    void detectAndStoreTimezone();
    return () => {
      active = false;
    };
  }, [settings, setSettings]);

  useEffect(() => {
    const intervalId = window.setInterval(() => {
      void loadHistory();
    }, POLL_INTERVAL_MS);

    return () => window.clearInterval(intervalId);
  }, [loadHistory]);

  useEffect(() => {
    const syncView = () => setActiveView(window.location.hash === "#reminders" ? "reminders" : window.location.hash === "#execution" ? "execution" : "conversation");
    syncView();
    window.addEventListener("hashchange", syncView);
    return () => window.removeEventListener("hashchange", syncView);
  }, []);

  const canSubmit = input.trim().length > 0;
  const inputPlaceholder = "Ask anything, or give me something to do…";

  const sendMessage = useCallback(
    async (text: string) => {
      const trimmed = text.trim();
      if (!trimmed) return;

      setError(null);
      const requestId = crypto.randomUUID();
      setPendingRequests((previous) => [...previous, requestId]);
      beginActivity(requestId, trimmed, messages.filter((message) => message.role === "user" && message.text === trimmed).length + 1);

      // Optimistically add the user message immediately
      const userMessage: ChatBubble = {
        id: `user-${requestId}`,
        role: "user",
        text: trimmed,
        timestamp: new Date().toISOString(),
      };
      setMessages((prev) => {
        const newMessages = [...prev, userMessage];
        return newMessages;
      });

      try {
        const res = await fetch("/api/chat", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            messages: [{ role: "user", content: trimmed }],
            request_id: requestId,
          }),
        });

        if (!(res.ok || res.status === 202)) {
          const detail = await res.text();
          throw new Error(detail || `Request failed (${res.status})`);
        }
      } catch (err: any) {
        console.error("Failed to send message", err);
        setError(err?.message || "Failed to send message");
        // Remove the optimistic message on error
        setMessages((prev) => prev.filter((msg) => msg.id !== userMessage.id));
        setPendingRequests((previous) => previous.filter((id) => id !== requestId));
        removeActivity(requestId);
        throw err instanceof Error ? err : new Error("Failed to send message");
      }
    },
    [beginActivity, messages, removeActivity],
  );

  const handleSubmit = useCallback(async () => {
    if (!canSubmit) return;
    const value = input;
    setInput("");
    try {
      await sendMessage(value);
    } catch {
      setInput(value);
    }
  }, [canSubmit, input, sendMessage, setInput]);

  const handleInputChange = useCallback(
    (value: string) => {
      setInput(value);
    },
    [setInput],
  );

  const clearError = useCallback(() => setError(null), [setError]);
  const handleDataCleared = useCallback(() => {
    clearActivity();
    setExecutionResetKey((value) => value + 1);
    setMessages([]);
    setInput("");
    setError(null);
    setPendingRequests([]);
  }, [clearActivity]);

  return (
    <main className="chat-workspace">
      <ChatSidebar
        email={gmail.connected ? gmail.email : ""}
        onOpenSettings={openSettings}
        activeView={activeView}
        onNavigate={setActiveView}
        reminderCount={reminders.count}
      />
      <section
        id="conversation"
        className="chat-main"
        aria-label="Your assistant"
        hidden={activeView !== "conversation"}
      >
        <ChatHeader
          isWaitingForResponse={isWaitingForResponse}
          highlightReply={highlightReply}
        />
        <ChatMessages
          messages={messages}
          timeZone={settings.timezone || undefined}
          activities={activities}
          isLoadingHistory={isLoadingHistory}
          isWaitingForResponse={isWaitingForResponse}
          scrollContainerRef={scrollContainerRef}
          onScroll={handleScroll}
          onSuggestion={setInput}
        />
        <div className="chat-composer-area">
          {error && (
            <ErrorBanner
              message={error}
              onDismiss={clearError}
            />
          )}
          <ChatInput
            value={input}
            canSubmit={canSubmit}
            placeholder={inputPlaceholder}
            onChange={handleInputChange}
            onSubmit={handleSubmit}
            gmail={gmail}
            onOpenGmail={openGmail}
          />
        </div>
      </section>
      <section
        id="execution"
        className="chat-main"
        aria-label="Execution workspace"
        hidden={activeView !== "execution"}
      >
        <ExecutionMonitor
          resetKey={executionResetKey}
          spans={executionSpans}
          error={executionError}
        />
      </section>
      <section
        id="reminders"
        className="chat-main"
        aria-label="Reminders workspace"
        hidden={activeView !== "reminders"}
      >
        {activeView === "reminders" && (
          <Reminders
            items={reminders.items}
            loading={reminders.loading}
            error={reminders.error}
            onChange={reminders.change}
          />
        )}
      </section>
      <SettingsModal
        open={open}
        onClose={closeSettings}
        settings={settings}
        onSave={setSettings}
        initialTab={settingsTab}
        onGmailStatusChange={setGmail}
        onDataCleared={handleDataCleared}
        isChatBusy={isLoadingHistory || isWaitingForResponse}
      />
    </main>
  );
}
