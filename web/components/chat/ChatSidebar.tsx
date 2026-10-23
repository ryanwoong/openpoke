import { BrandMark } from "./BrandMark";

export function ChatSidebar({ email, onOpenSettings, activeView, onNavigate }: { email: string; onOpenSettings: () => void; activeView: "conversation" | "execution"; onNavigate: (view: "conversation" | "execution") => void }) {
  const name = email ? email.split("@")[0].replace(/[._-]/g, " ") : "Your profile";

  return (
    <aside
      className="chat-sidebar"
      aria-label="Main navigation"
    >
      <a
        className="chat-brand"
        href="/"
        aria-label="OpenPoke home"
      >
        <BrandMark />
        <span>OpenPoke</span>
      </a>
      <nav aria-label="Workspace">
        <a
          aria-label="Conversation"
          className="conversation-link"
          href="#conversation"
          aria-current={activeView === "conversation" ? "page" : undefined}
          onClick={() => onNavigate("conversation")}
        >
          <svg
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.6"
            aria-hidden="true"
          >
            <path d="M20.5 11.5a8.5 8.5 0 0 1-8.5 8.5H5l-3 2v-6a8.5 8.5 0 1 1 18.5-4.5Z" />
          </svg>
          <span>Conversation</span>
        </a>
        <a
          aria-label="Execution"
          className="conversation-link"
          href="#execution"
          aria-current={activeView === "execution" ? "page" : undefined}
          onClick={() => onNavigate("execution")}
        >
          <svg
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.6"
            aria-hidden="true"
          >
            <rect
              x="3"
              y="3"
              width="7"
              height="7"
              rx="2"
            />
            <rect
              x="14"
              y="14"
              width="7"
              height="7"
              rx="2"
            />
            <path d="M10 6.5h7.5V14m-3-3 3 3 3-3M6.5 10v7.5H14" />
          </svg>
          <span>Execution</span>
        </a>
      </nav>
      <button
        className="profile-button"
        onClick={onOpenSettings}
        aria-label="Open profile and settings"
      >
        <span className="profile-avatar">
          {email ? (
            name.charAt(0).toUpperCase()
          ) : (
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.6"
              aria-hidden="true"
            >
              <circle
                cx="12"
                cy="8"
                r="3.5"
              />
              <path d="M5 21v-2a7 7 0 0 1 14 0v2" />
            </svg>
          )}
        </span>
        <span className="profile-name">{name}</span>
        <svg
          className="profile-settings"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.6"
          aria-hidden="true"
        >
          <path d="m9 3-.5 2-2 1-2-.5-2 3 1.5 1.5v3L2.5 15l2 3 2-.5 2 1 .5 2h4l.5-2 2-1 2 .5 2-3-1.5-2v-3L20 8.5l-2-3-2 .5-2-1-.5-2Z" />
          <circle
            cx="11.5"
            cy="12"
            r="3"
          />
        </svg>
      </button>
    </aside>
  );
}
