import { useEffect, useRef } from "react";
import type { GmailConnection } from "../SettingsModal";

interface ChatInputProps {
  value: string;
  canSubmit: boolean;
  placeholder: string;
  onChange: (value: string) => void;
  onSubmit: () => Promise<void> | void;
  gmail: GmailConnection;
  onOpenGmail: () => void;
}

export function ChatInput({ value, canSubmit, placeholder, onChange, onSubmit, gmail, onOpenGmail }: ChatInputProps) {
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const input = inputRef.current;
    if (!input) return;
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 180)}px`;
  }, [value]);

  return (
    <>
      <form
        className="chat-composer"
        onSubmit={(event) => {
          event.preventDefault();
          if (canSubmit) void onSubmit();
        }}
      >
        <textarea
          ref={inputRef}
          aria-label="Message your assistant"
          rows={1}
          value={value}
          onChange={(event) => onChange(event.target.value)}
          placeholder={placeholder}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
              event.preventDefault();
              if (canSubmit) void onSubmit();
            }
          }}
        />
        <div className="composer-toolbar">
          <button
            type="button"
            className="gmail-status"
            onClick={onOpenGmail}
            title={gmail.connected ? gmail.email || "Manage Gmail connection" : "Connect Gmail in settings"}
          >
            <svg
              viewBox="0 0 24 24"
              fill="none"
              aria-hidden="true"
            >
              <path
                d="M3 19V6l9 7 9-7v13"
                stroke="#4285F4"
                strokeWidth="3"
                strokeLinejoin="round"
              />
              <path
                d="m3 6 9 7 9-7"
                stroke="#EA4335"
                strokeWidth="3"
                strokeLinejoin="round"
              />
              <path
                d="M21 10v9"
                stroke="#34A853"
                strokeWidth="3"
              />
              <path
                d="M3 10v9"
                stroke="#FBBC04"
                strokeWidth="3"
              />
            </svg>
            {gmail.checking ? "Checking Gmail…" : gmail.connected ? "Gmail connected" : "Connect Gmail"}
            {gmail.connected && !gmail.checking && <span className="gmail-status-dot" />}
          </button>
          <button
            type="submit"
            className="composer-send"
            disabled={!canSubmit}
            aria-label="Send message"
          >
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.8"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden="true"
            >
              <path d="M12 19V5m-7 7 7-7 7 7" />
            </svg>
          </button>
        </div>
      </form>
      <p className="composer-hint">
        Enter to send <span>·</span> Shift + Enter for a new line
      </p>
    </>
  );
}
