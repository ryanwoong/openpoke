import clsx from "clsx";
import React, { RefObject } from "react";

import { LoadingIndicator } from "../ui/LoadingIndicator";

import type { ChatBubble } from "./types";
import { MarkdownMessage } from "./MarkdownMessage";

interface ChatMessagesProps {
  messages: ReadonlyArray<ChatBubble>;
  isWaitingForResponse: boolean;
  isLoadingHistory?: boolean;
  scrollContainerRef: RefObject<HTMLDivElement | null>;
  onScroll: () => void;
}

export function ChatMessages({ messages, isWaitingForResponse, isLoadingHistory = false, scrollContainerRef, onScroll }: ChatMessagesProps) {
  return (
    <div
      ref={scrollContainerRef}
      onScroll={onScroll}
      aria-busy={isLoadingHistory}
      className="flex h-[70vh] flex-col gap-2 overflow-y-auto p-4"
    >
      {messages.length === 0 &&
        (isLoadingHistory ? (
          <LoadingIndicator
            label="Loading your conversation…"
            className="chat-empty flex-1 flex-col justify-center gap-3 [&>svg]:h-8 [&>svg]:w-8"
          />
        ) : (
          <EmptyState />
        ))}

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
        <LoadingIndicator label="Waiting for a response…" />
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
