"use client";

import { useEffect, useId, useRef, useState, type KeyboardEvent } from "react";
import styles from "./Reminders.module.css";

export function ReminderActions({ label, onEdit, onDelete }: { label: string; onEdit?: () => void; onDelete: () => void }) {
  const [open, setOpen] = useState(false);
  const [present, setPresent] = useState(false);
  const menuId = useId();
  const containerRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const initialFocus = useRef(0);

  useEffect(() => {
    if (!open) return;
    menuRef.current?.querySelectorAll<HTMLButtonElement>("button")[initialFocus.current]?.focus();
    const dismiss = (event: PointerEvent) => {
      if (!containerRef.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", dismiss);
    return () => document.removeEventListener("pointerdown", dismiss);
  }, [open]);

  useEffect(() => {
    if (open) {
      setPresent(true);
      return;
    }
    if (!present) return;
    const timeout = window.setTimeout(() => setPresent(false), window.matchMedia("(prefers-reduced-motion: reduce)").matches ? 0 : 200);
    return () => window.clearTimeout(timeout);
  }, [open, present]);

  function close() {
    setOpen(false);
    triggerRef.current?.focus();
  }

  function navigate(event: KeyboardEvent<HTMLDivElement>) {
    const buttons = Array.from(menuRef.current?.querySelectorAll<HTMLButtonElement>("button") || []);
    const current = buttons.indexOf(document.activeElement as HTMLButtonElement);
    const index = event.key === "Home" ? 0 : event.key === "End" ? buttons.length - 1 : event.key === "ArrowDown" ? (current + 1) % buttons.length : event.key === "ArrowUp" ? (current - 1 + buttons.length) % buttons.length : null;
    if (index !== null) {
      event.preventDefault();
      buttons[index]?.focus();
    } else if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      close();
    }
  }

  return (
    <div
      ref={containerRef}
      className={styles.menuContainer}
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false);
      }}
    >
      <button
        ref={triggerRef}
        type="button"
        className={styles.menuTrigger}
        aria-label={`Actions for ${label}`}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        onClick={() => {
          initialFocus.current = 0;
          setOpen(!open);
        }}
        onKeyDown={(event) => {
          if (event.key === "ArrowDown" || event.key === "ArrowUp") {
            event.preventDefault();
            initialFocus.current = event.key === "ArrowUp" && onEdit ? 1 : 0;
            setOpen(true);
          }
        }}
      >
        <svg
          viewBox="0 0 24 24"
          width="20"
          height="20"
          fill="currentColor"
          aria-hidden="true"
        >
          <circle
            cx="5"
            cy="12"
            r="1.75"
          />
          <circle
            cx="12"
            cy="12"
            r="1.75"
          />
          <circle
            cx="19"
            cy="12"
            r="1.75"
          />
        </svg>
      </button>
      {(open || present) && (
        <div
          ref={menuRef}
          id={menuId}
          className={`${styles.menu} menu-popover`}
          data-state={open ? "open" : "closed"}
          aria-hidden={!open}
          onAnimationEnd={(event) => {
            if (event.target === event.currentTarget && !open) setPresent(false);
          }}
          role="menu"
          aria-label={`Actions for ${label}`}
          onKeyDown={navigate}
        >
          {onEdit && (
            <button
              type="button"
              role="menuitem"
              disabled={!open}
              tabIndex={-1}
              onClick={() => {
                close();
                onEdit();
              }}
            >
              Edit
            </button>
          )}
          <button
            type="button"
            role="menuitem"
            disabled={!open}
            tabIndex={-1}
            className={styles.deleteAction}
            onClick={() => {
              close();
              onDelete();
            }}
          >
            Delete
          </button>
        </div>
      )}
    </div>
  );
}
