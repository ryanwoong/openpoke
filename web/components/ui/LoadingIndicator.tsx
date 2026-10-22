import React from "react";
import clsx from "clsx";

export function Spinner({ className }: { className?: string }) {
  return (
    <svg
      className={clsx("loading-spinner", "text-brand-600", className)}
      viewBox="0 0 24 24"
      fill="none"
      aria-hidden="true"
    >
      <circle
        cx="12"
        cy="12"
        r="9"
        stroke="currentColor"
        strokeWidth="2.5"
        opacity="0.16"
      />
      <path
        d="M12 3a9 9 0 0 1 9 9"
        stroke="currentColor"
        strokeWidth="2.5"
        strokeLinecap="round"
      />
    </svg>
  );
}

export function LoadingIndicator({ label, className }: { label: string; className?: string }) {
  return (
    <div
      className={clsx("flex items-center gap-2.5 text-sm text-gray-500", className)}
      role="status"
    >
      <Spinner />
      <span>{label}</span>
    </div>
  );
}
