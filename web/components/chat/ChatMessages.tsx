import { chatActivityPositions, type ChatActivity } from "../../lib/chatActivity";
import { ChatActivitySteps } from "./ChatActivitySteps";
import clsx from "clsx";
import React, { RefObject } from "react";
import { LoadingIndicator } from "../ui/LoadingIndicator";
import type { ChatBubble } from "./types";
import { MarkdownMessage } from "./MarkdownMessage";
import { BrandMark } from "./BrandMark";
import { messageTimestamp } from "../../lib/messageTimestamp";

interface ChatMessagesProps {
  messages: ReadonlyArray<ChatBubble>;
  timeZone?: string;
  activities?: ChatActivity[];
  isWaitingForResponse: boolean;
  isLoadingHistory?: boolean;
  scrollContainerRef: RefObject<HTMLDivElement | null>;
  onScroll: () => void;
  onSuggestion?: (text: string) => void;
}

export function ChatMessages({ messages, timeZone, activities = [], isWaitingForResponse, isLoadingHistory = false, scrollContainerRef, onScroll, onSuggestion }: ChatMessagesProps) {
  const activityPositions = chatActivityPositions(messages, activities);
  const hasActivity = activityPositions.has(messages.length);
  const renderActivity = (position: number) =>
    activityPositions.get(position)?.map((activity) => (
      <ChatActivitySteps
        key={activity.requestId}
        steps={activity.steps}
        isPreparingReply={isWaitingForResponse && position === messages.length}
      />
    ));
  return (
    <div
      ref={scrollContainerRef}
      onScroll={onScroll}
      aria-busy={isLoadingHistory}
      className="chat-messages"
      role="region"
      aria-label="Conversation messages"
      tabIndex={0}
    >
      <div className={clsx("conversation-content", messages.length === 0 && "conversation-content-empty")}>
        {messages.length === 0 &&
          (isLoadingHistory ? (
            <LoadingIndicator
              label="Loading your conversation…"
              className="chat-empty"
            />
          ) : (
            <EmptyState onSuggestion={onSuggestion} />
          ))}
        {messages.map((message, index) => {
          const sentTime = messageTimestamp(message.timestamp, timeZone);
          const timestampId = `message-time-${message.id}`;
          const isUser = message.role === "user";
          const isDraft = message.role === "draft";
          const showIdentity = !isUser && (index === 0 || messages[index - 1].role !== message.role);
          return (
            <React.Fragment key={message.id}>
              {renderActivity(index)}
              <div className={clsx("chat-message", isUser ? "user-message" : "assistant-message")}>
                {showIdentity && (
                  <div className="message-identity">
                    <span className="assistant-avatar">
                      <BrandMark />
                    </span>
                    <span>{isDraft ? "Email draft" : "OpenPoke"}</span>
                  </div>
                )}
                <div className="message-body">
                  <div
                    className={clsx(isUser ? "bubble-out" : "assistant-content assistant-bubble", isDraft && "draft-content")}
                    tabIndex={sentTime ? 0 : undefined}
                    aria-describedby={sentTime ? timestampId : undefined}
                  >
                    {message.role === "assistant" ? <MarkdownMessage text={message.text} /> : <span className="whitespace-pre-wrap">{message.text}</span>}
                  </div>
                  {sentTime && (
                    <time
                      id={timestampId}
                      className="message-timestamp"
                      dateTime={sentTime.dateTime}
                      aria-label={`Sent ${sentTime.label}`}
                      title={sentTime.label}
                    >
                      {sentTime.timeLabel}
                    </time>
                  )}
                </div>
              </div>
            </React.Fragment>
          );
        })}
        {renderActivity(messages.length)}
        {isWaitingForResponse && !hasActivity && (
          <div className="chat-message assistant-message">
            {messages.at(-1)?.role !== "assistant" && (
              <div className="message-identity">
                <span className="assistant-avatar">
                  <BrandMark />
                </span>
                <span>OpenPoke</span>
              </div>
            )}
            <LoadingIndicator
              label="Working on your request…"
              className="assistant-content"
            />
          </div>
        )}
      </div>
    </div>
  );
}

function EmptyState({ onSuggestion }: { onSuggestion?: (text: string) => void }) {
  return (
    <div className="chat-empty empty-welcome">
      <span className="welcome-mark">
        <BrandMark />
      </span>
      <p className="welcome-eyebrow">A LITTLE LESS ON YOUR PLATE</p>
      <h2>What can I take care of?</h2>
      <p>
        Make room for your day. I can help with your inbox,
        <br className="hidden sm:block" /> set reminders, and keep an eye on what matters.
      </p>
      {onSuggestion && (
        <div className="welcome-suggestions">
          {["Catch me up on my inbox", "Help me set a reminder", "What can you do?"].map((suggestion) => (
            <button
              type="button"
              key={suggestion}
              onClick={() => onSuggestion(suggestion)}
            >
              {suggestion}
              <span aria-hidden="true">↗</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
