"use client";
import { useEffect, useRef, useState, type ReactNode } from "react";
import styles from "./AnimatedDialog.module.css";

export function AnimatedDialog({ open, onClose, dismissible = true, labelledBy, panelClassName = "", children }: { open: boolean; onClose: () => void; dismissible?: boolean; labelledBy: string; panelClassName?: string; children: ReactNode }) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const [present, setPresent] = useState(open);
  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (open) {
      setPresent(true);
      if (!dialog.open) dialog.showModal();
      return;
    }
    if (!dialog.open) return;
    // Keep the native focus trap active until the exit animation finishes.
    const timeout = window.setTimeout(
      () => {
        dialog.close();
        setPresent(false);
      },
      window.matchMedia("(prefers-reduced-motion: reduce)").matches ? 0 : 180,
    );
    return () => window.clearTimeout(timeout);
  }, [open]);
  return (
    <dialog
      ref={dialogRef}
      className={styles.dialog}
      data-state={open ? "open" : "closed"}
      aria-labelledby={labelledBy}
      onCancel={(event) => {
        // React bubbles cancel through nested dialogs; only the dialog that received Escape should close.
        if (event.target !== event.currentTarget) return;
        event.preventDefault();
        if (dismissible) onClose();
      }}
      onAnimationEnd={(event) => {
        if (event.target === event.currentTarget && !open) {
          event.currentTarget.close();
          setPresent(false);
        }
      }}
      onClick={(event) => {
        if (event.target === event.currentTarget && dismissible) onClose();
      }}
    >
      {(open || present) && <div className={`${styles.panel} card ${panelClassName}`}>{children}</div>}
    </dialog>
  );
}
