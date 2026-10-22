"use client";

import { useEffect, useRef, useState } from "react";
import type { Settings } from "../SettingsModal";
import type { ChatBubble } from "./types";

export function useBackgroundAlerts(messages: ChatBubble[], isLoadingHistory: boolean, settings: Settings) {
  const seen = useRef<Set<string> | null>(null);
  const audio = useRef<AudioContext | null>(null);
  const chimeEnabled = useRef(settings.chime);
  chimeEnabled.current = settings.chime;
  const originalTitle = useRef("");
  const unread = useRef(false);
  const highlightTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const [highlightReply, setHighlightReply] = useState(false);

  useEffect(() => {
    originalTitle.current = document.title;
    const prepareAudio = () => {
      if (!chimeEnabled.current || !("AudioContext" in window)) return;
      try {
        audio.current ??= new AudioContext();
        if (audio.current.state === "suspended") void audio.current.resume().catch(() => {});
      } catch {}
    };
    const acknowledge = () => {
      if (document.visibilityState !== "visible" || !document.hasFocus()) return;
      unread.current = false;
      document.title = originalTitle.current;
      clearTimeout(highlightTimer.current);
      highlightTimer.current = setTimeout(() => setHighlightReply(false), 4000);
    };
    window.addEventListener("pointerdown", prepareAudio);
    window.addEventListener("keydown", prepareAudio);
    window.addEventListener("focus", acknowledge);
    document.addEventListener("visibilitychange", acknowledge);
    return () => {
      window.removeEventListener("pointerdown", prepareAudio);
      window.removeEventListener("keydown", prepareAudio);
      window.removeEventListener("focus", acknowledge);
      document.removeEventListener("visibilitychange", acknowledge);
      document.title = originalTitle.current;
      clearTimeout(highlightTimer.current);
      if (audio.current) void audio.current.close().catch(() => {});
      audio.current = null;
    };
  }, []);

  useEffect(() => {
    document.title = unread.current && settings.titleAlert ? "New message!" : originalTitle.current;
  }, [settings.titleAlert]);

  useEffect(() => {
    if (isLoadingHistory) return;
    const newReply = seen.current !== null && messages.some((message) => message.role === "assistant" && !seen.current!.has(message.id));
    seen.current = new Set(messages.map((message) => message.id));
    if (messages.length === 0) {
      unread.current = false;
      document.title = originalTitle.current;
      setHighlightReply(false);
    }
    if (!newReply || (document.visibilityState === "visible" && document.hasFocus())) return;

    unread.current = true;
    clearTimeout(highlightTimer.current);
    setHighlightReply(true);
    if (settings.titleAlert) document.title = "New message!";
    if (settings.chime && audio.current?.state === "running") {
      try {
        const context = audio.current;
        const start = context.currentTime;
        for (const [offset, frequency] of [
          [0, 660],
          [0.16, 880],
        ]) {
          const oscillator = context.createOscillator();
          const gain = context.createGain();
          oscillator.frequency.value = frequency;
          gain.gain.setValueAtTime(0, start + offset);
          gain.gain.linearRampToValueAtTime(0.15, start + offset + 0.015);
          gain.gain.exponentialRampToValueAtTime(0.001, start + offset + 0.4);
          oscillator.connect(gain);
          gain.connect(context.destination);
          oscillator.onended = () => {
            oscillator.disconnect();
            gain.disconnect();
          };
          oscillator.start(start + offset);
          oscillator.stop(start + offset + 0.4);
        }
      } catch {}
    }
  }, [messages, isLoadingHistory, settings.chime, settings.titleAlert]);

  return highlightReply;
}
