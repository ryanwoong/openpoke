export function ChatHeader({ isWaitingForResponse, highlightReply = false }: { isWaitingForResponse: boolean; highlightReply?: boolean }) {
  return (
    <header className={`chat-header${highlightReply ? " chat-header-new-reply" : ""}`}>
      <h1>Your assistant</h1>
      <p>
        <span className="assistant-status-dot" />
        {highlightReply ? "New message!" : isWaitingForResponse ? "Working on it…" : "Ready when you are"}
      </p>
    </header>
  );
}
