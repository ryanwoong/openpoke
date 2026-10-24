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
  activeRequestIds?: string[];
  isWaitingForResponse: boolean;
  isLoadingHistory?: boolean;
  scrollContainerRef: RefObject<HTMLDivElement | null>;
  onScroll: () => void;
  onRetryRequest?: (requestId: string) => void;
  onSuggestion?: (text: string) => void;
}

export function ChatMessages({ messages, timeZone, activities = [], activeRequestIds, isWaitingForResponse, isLoadingHistory = false, scrollContainerRef, onScroll, onSuggestion, onRetryRequest }: ChatMessagesProps) {
  const activityPositions = chatActivityPositions(messages, activities);
  const positionedActivities = [...activityPositions.values()].flat();
  const hasActivity = activeRequestIds ? activeRequestIds.length > 0 && activeRequestIds.every((id) => positionedActivities.some((activity) => activity.requestId === id)) : activityPositions.has(messages.length);
  const renderActivity = (position: number) =>
    activityPositions.get(position)?.map((activity) => {
      const previous = messages[position - 1];
      const showIdentity = previous?.role !== "assistant" || previous.source === "scheduled" || (previous.requestId && previous.requestId !== activity.requestId);
      return (
        <React.Fragment key={activity.requestId}>
          {showIdentity && (
            <div className="message-identity">
              <span className="assistant-avatar">
                <BrandMark />
              </span>
              <span>OpenPoke</span>
            </div>
          )}
          <ChatActivitySteps
            steps={activity.steps}
            phase={activity.phase}
            outcome={activity.status}
            isPreparingReply={activeRequestIds ? activeRequestIds.includes(activity.requestId) : isWaitingForResponse && position === messages.length}
          />
        </React.Fragment>
      );
    });

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
          const isScheduled = message.source === "scheduled";
          const isDraft = message.role === "draft";
          const precedingUser = messages
            .slice(0, index)
            .reverse()
            .find((entry) => entry.role === "user");
          const originalQuestion = !isUser && !isScheduled && message.requestId && precedingUser?.requestId !== message.requestId ? messages.find((entry) => entry.role === "user" && entry.requestId === message.requestId)?.text : undefined;
          const showIdentity = !isUser && (index === 0 || messages[index - 1].role !== message.role || messages[index - 1].source !== message.source || messages[index - 1].requestId !== message.requestId);
          return (
            <React.Fragment key={message.id}>
              {renderActivity(index)}
              <div className={clsx("chat-message", isUser ? "user-message" : "assistant-message")}>
                {showIdentity && (
                  <div className="message-identity">
                    <span className="assistant-avatar">
                      <BrandMark />
                    </span>
                    <span>{isScheduled ? "Scheduled update" : isDraft ? "Email draft" : "OpenPoke"}</span>
                    {originalQuestion && (
                      <span
                        className="message-reply-context"
                        title={originalQuestion}
                      >
                        Reply to: {originalQuestion.length > 100 ? originalQuestion.slice(0, 100) + "…" : originalQuestion}
                      </span>
                    )}
                  </div>
                )}
                <div className="message-body">
                  <div
                    className={clsx(isUser ? "bubble-out" : "assistant-content assistant-bubble", isDraft && "draft-content", isScheduled && "scheduled-notification")}
                    tabIndex={sentTime ? 0 : undefined}
                    aria-describedby={sentTime ? timestampId : undefined}
                  >
                    {message.role === "assistant" ? <MarkdownMessage text={message.text} /> : <span className="whitespace-pre-wrap">{message.text}</span>}
                  </div>
                  {message.source === "failure" && message.retryable && message.requestId && onRetryRequest && (
                    <button
                      type="button"
                      className="chat-task-action message-retry"
                      onClick={() => onRetryRequest(message.requestId!)}
                    >
                      Retry with saved results
                    </button>
                  )}
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
            {(messages.at(-1)?.role !== "assistant" || messages.at(-1)?.source === "scheduled") && (
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
