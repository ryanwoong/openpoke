export function ChatHeader({ isWaitingForResponse }: { isWaitingForResponse: boolean }) {
  return (
    <header className="chat-header">
      <h1>Your assistant</h1>
      <p>
        <span className="assistant-status-dot" />
        {isWaitingForResponse ? "Working on it…" : "Ready when you are"}
      </p>
    </header>
  );
}
