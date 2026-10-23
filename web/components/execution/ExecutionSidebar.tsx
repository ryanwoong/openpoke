"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import styles from "./ExecutionMonitor.module.css";

export type ExecutionPanel = "history" | "recent" | "details";

const LABELS: Record<ExecutionPanel, string> = { history: "Execution history", recent: "Recent execution activity", details: "Activity details" };

export function ExecutionSidebar({ view, parentView = null, onClose, children }: { view: ExecutionPanel | null; parentView?: "recent" | null; onClose: () => void; children: (view: ExecutionPanel) => ReactNode }) {
  const [retainedView, setRetainedView] = useState(view);
  const [detailsPresent, setDetailsPresent] = useState(false);
  const detailsContent = useRef<ReactNode>(null);
  const recentFocusRef = useRef<HTMLElement | null>(null);
  const nested = view === "details" && parentView === "recent";
  if (nested) detailsContent.current = children("details");
  const drawerRef = useRef<HTMLElement>(null);
  const triggerRef = useRef<HTMLElement | null>(null);
  const displayedView = view ?? retainedView;

  useEffect(() => {
    const drawer = drawerRef.current;
    const hadFocus = drawer?.contains(document.activeElement);
    if (drawer) drawer.inert = !view;
    if (view) {
      setRetainedView(view);
      if (document.activeElement instanceof HTMLElement && document.activeElement !== document.body && !drawer?.contains(document.activeElement)) triggerRef.current = document.activeElement;
      drawer?.focus({ preventScroll: true });
      return;
    }
    if (!drawer) return;
    if (hadFocus) triggerRef.current?.focus({ preventScroll: true });
    const timer = window.setTimeout(() => setRetainedView(null), window.matchMedia("(prefers-reduced-motion: reduce)").matches ? 0 : 240);
    return () => window.clearTimeout(timer);
  }, [view]);

  useEffect(() => {
    if (nested) {
      setDetailsPresent(true);
      return;
    }
    if (!detailsPresent) return;
    if (view === "recent") recentFocusRef.current?.focus({ preventScroll: true });
    const timer = window.setTimeout(() => setDetailsPresent(false), window.matchMedia("(prefers-reduced-motion: reduce)").matches ? 0 : 200);
    return () => window.clearTimeout(timer);
  }, [nested, detailsPresent, view]);

  if (!displayedView) return null;
  const baseView = nested || detailsPresent ? "recent" : displayedView;

  return (
    <div
      className={styles.sidebarShell}
      data-state={view ? "open" : "closed"}
      aria-hidden={!view}
      onAnimationEnd={(event) => {
        if (event.target === event.currentTarget && !view) setRetainedView(null);
      }}
    >
      <aside
        ref={drawerRef}
        className={styles.drawer}
        aria-label={LABELS[displayedView]}
        tabIndex={-1}
        onFocusCapture={(event) => {
          if (view === "recent" && event.target instanceof HTMLElement && event.target !== event.currentTarget) recentFocusRef.current = event.target;
        }}
        onKeyDown={(event) => {
          if (event.key !== "Escape") return;
          event.preventDefault();
          event.stopPropagation();
          onClose();
        }}
      >
        <div
          key={baseView}
          className={styles.drawerContent}
          aria-hidden={nested}
          ref={(element) => {
            if (element) element.inert = nested;
          }}
        >
          {children(baseView)}
        </div>
        {(nested || detailsPresent) && (
          <div
            className={`${styles.drawerContent} ${styles.detailLayer}`}
            data-state={nested ? "open" : "closed"}
            aria-hidden={!nested}
            ref={(element) => {
              if (element) element.inert = !nested;
            }}
            onAnimationEnd={(event) => {
              if (event.target === event.currentTarget && !nested) setDetailsPresent(false);
            }}
          >
            {detailsContent.current}
          </div>
        )}
      </aside>
    </div>
  );
}
