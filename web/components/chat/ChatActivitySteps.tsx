import React, { useEffect, useRef } from "react";
import type { ActivityStep } from "../../lib/chatActivity";

export function ChatActivitySteps({ steps, isPreparingReply = false }: { steps: ActivityStep[]; isPreparingReply?: boolean }) {
  if (!steps.length) return null;
  const running = steps.some((step) => step.status === "running");
  const visibleSteps: ActivityStep[] = isPreparingReply && !running ? [...steps, { id: "preparing-reply", label: "Preparing your reply…", status: "running" }] : steps;
  const failed = steps.some((step) => ["failed", "cancelled", "interrupted"].includes(step.status));
  const rows = (
    <ul
      className="chat-activity-list"
      aria-label="Agent activity"
    >
      {visibleSteps.map((step) => (
        <li
          key={step.id}
          className={`chat-activity-step chat-activity-${step.status}`}
        >
          <span
            className="chat-activity-icon"
            aria-hidden="true"
          >
            {step.status === "completed" ? "✓" : step.status === "running" ? "" : "!"}
          </span>
          {step.emails?.length ? (
            <EmailAudit step={step} />
          ) : (
            <span>
              <span className="sr-only">{step.status}: </span>
              {step.label}
            </span>
          )}
        </li>
      ))}
    </ul>
  );
  return (
    <div
      className="chat-activity"
      role="status"
      aria-live="polite"
    >
      {running || isPreparingReply || steps.length === 1 ? (
        rows
      ) : (
        <details>
          <summary>{failed ? "Activity needs attention" : `Completed ${steps.length} steps`}</summary>
          {rows}
        </details>
      )}
    </div>
  );
}

function EmailAudit({ step }: { step: ActivityStep }) {
  const detailsRef = useRef<HTMLDetailsElement>(null);
  const contentRef = useRef<HTMLUListElement>(null);
  const expandedRef = useRef(false);
  const animationsRef = useRef<Animation[]>([]);

  useEffect(() => () => animationsRef.current.forEach((animation) => animation.cancel()), []);

  const toggle = (event: React.MouseEvent<HTMLElement>) => {
    event.preventDefault();
    const details = detailsRef.current;
    const content = contentRef.current;
    if (!details || !content) return;
    const opening = !expandedRef.current;
    expandedRef.current = opening;
    const startHeight = details.getBoundingClientRect().height;
    animationsRef.current.forEach((animation) => animation.cancel());
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches || typeof details.animate !== "function") {
      details.open = opening;
      details.style.overflow = "";
      return;
    }
    details.open = true;
    const endHeight = opening ? details.getBoundingClientRect().height : event.currentTarget.getBoundingClientRect().height;
    details.style.overflow = "hidden";
    const options = { duration: 240, easing: "cubic-bezier(0.2, 0.7, 0.2, 1)", fill: "both" as FillMode };
    const heightAnimation = details.animate({ height: [`${startHeight}px`, `${endHeight}px`] }, options);
    const contentAnimation = content.animate({ opacity: opening ? [0, 1] : [1, 0], transform: opening ? ["translateY(-4px)", "translateY(0)"] : ["translateY(0)", "translateY(-4px)"] }, options);
    animationsRef.current = [heightAnimation, contentAnimation];
    heightAnimation.onfinish = () => {
      details.open = opening;
      details.style.overflow = "";
      animationsRef.current.forEach((animation) => animation.cancel());
      animationsRef.current = [];
    };
  };

  return (
    <details
      ref={detailsRef}
      className="chat-activity-email-details"
    >
      <summary onClick={toggle}>
        <span className="sr-only">{step.status}: </span>
        {step.label}
      </summary>
      <ul
        ref={contentRef}
        className="chat-activity-emails"
        aria-label="Matching emails"
      >
        {step.emails?.map((email, index) => (
          <li key={index}>
            <strong title={email.subject}>{email.subject}</strong>
            <span title={email.sender}>
              {email.sender
                .replace(/\s*<[^>]+>\s*$/, "")
                .replace(/^"(.*)"$/, "$1")
                .trim() || email.sender}
            </span>
          </li>
        ))}
      </ul>
    </details>
  );
}
