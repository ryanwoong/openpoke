import clsx from "clsx";
import React, { RefObject } from "react";

import type { ChatBubble } from "./types";
import { MarkdownMessage } from "./MarkdownMessage";

interface ChatMessagesProps {
  messages: ReadonlyArray<ChatBubble>;
  isWaitingForResponse: boolean;
  scrollContainerRef: RefObject<HTMLDivElement | null>;
  onScroll: () => void;
}

export function ChatMessages({ messages, isWaitingForResponse, scrollContainerRef, onScroll }: ChatMessagesProps) {
  return (
    <div
      ref={scrollContainerRef}
      onScroll={onScroll}
      className="flex h-[70vh] flex-col gap-2 overflow-y-auto p-4"
    >
      {messages.length === 0 && <EmptyState />}

      {messages.map((message, index) => {
        const isUser = message.role === "user";
        const isDraft = message.role === "draft";
        const next = messages[index + 1];
        const tail = !next || next.role !== message.role;

        return (
          <div
            key={message.id}
            className={clsx("chat-message flex", isUser ? "justify-end" : "justify-start")}
          >
            <div className={clsx("min-w-0", isUser ? "bubble-out" : "bubble-in", tail ? (isUser ? "bubble-tail-out" : "bubble-tail-in") : "", isDraft && "whitespace-pre-wrap")}>{message.role === "assistant" ? <MarkdownMessage text={message.text} /> : <span className={isDraft ? "block whitespace-pre-wrap" : "whitespace-pre-wrap"}>{message.text}</span>}</div>
          </div>
        );
      })}

      {isWaitingForResponse && <TypingIndicator />}
    </div>
  );
}

function TypingIndicator() {
  return (
    <div className="chat-message flex justify-start">
      <div className="bubble-in bubble-tail-in">
        <div
          className="typing-indicator"
          role="status"
          aria-label="Waiting for a response"
        >
          <div
            className="flex space-x-1"
            aria-hidden="true"
          >
            <div className="typing-dot"></div>
            <div className="typing-dot"></div>
            <div className="typing-dot"></div>
          </div>
        </div>
      </div>
    </div>
  );
}

function EmptyState() {
  return (
    <div className="chat-empty mx-auto my-12 max-w-sm text-center text-gray-500">
      <h2 className="mb-2 text-xl font-semibold text-gray-700">Start a conversation</h2>
      <p className="text-sm">Your messages will appear here. Send something to get started.</p>
    </div>
  );
}
