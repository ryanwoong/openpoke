"use client";

import { useId, useState, type FormEvent } from "react";
import type { Reminder, ReminderChange } from "./useReminders";
import styles from "./Reminders.module.css";
import { AnimatedDialog } from "../ui/AnimatedDialog";
import { ReminderActions } from "./ReminderActions";

const labels: Record<Reminder["status"], string> = { scheduled: "Scheduled", awaiting_approval: "Waiting for approval", paused: "Paused", running: "In progress", completed: "Completed", cancelled: "Cancelled", failed: "Failed" };

function dateLabel(value: string | null, timezone: string | null) {
  if (!value) return "No scheduled time";
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short", timeZone: timezone || "UTC" }).format(new Date(value));
}

function localDate(value: string | null, timezone: string | null) {
  if (!value) return "";
  const parts = new Intl.DateTimeFormat("en-CA", { timeZone: timezone || "UTC", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23" }).formatToParts(new Date(value));
  const part = (type: string) => parts.find((entry) => entry.type === type)?.value;
  return `${part("year")}-${part("month")}-${part("day")}T${part("hour")}:${part("minute")}:${part("second")}`;
}

function ItemDialog({ item, mode, onClose, onChange }: { item: Reminder; mode: "edit" | "cancel" | "approve"; onClose: () => void; onChange: (item: Reminder, change: ReminderChange) => Promise<Reminder> }) {
  const titleId = useId();
  const [startTime, setStartTime] = useState(localDate(item.next_trigger, item.timezone));
  const [message, setMessage] = useState(item.message);
  const [recipient, setRecipient] = useState(item.recipient_email);
  const [subject, setSubject] = useState(item.subject);
  const [body, setBody] = useState(item.body);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await onChange(item, mode === "edit" ? { action: "edit", start_time: startTime, message, recipient_email: recipient, subject, body } : { action: mode });
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save changes");
    } finally {
      setBusy(false);
    }
  }

  return (
    <AnimatedDialog
      open
      onClose={onClose}
      dismissible={!busy}
      labelledBy={titleId}
      panelClassName={mode === "cancel" ? "!max-w-md" : styles.dialog}
    >
      <form
        onSubmit={submit}
        className={mode === "cancel" ? styles.deleteForm : undefined}
      >
        {mode === "cancel" && (
          <div
            className={styles.deleteIcon}
            aria-hidden="true"
          >
            <svg
              width="24"
              height="24"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.75"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M3 6h18M9 6V4h6v2M5 6l1 14h12l1-14M10 10v6M14 10v6" />
            </svg>
          </div>
        )}
        <h2 id={titleId}>{mode === "edit" ? "Edit " + (item.kind === "email" ? "scheduled email" : item.kind === "task" ? "scheduled task" : "reminder") : mode === "approve" ? "Approve scheduled email" : "Delete this item?"}</h2>
        {mode === "edit" ? (
          <>
            {item.kind === "email" ? (
              <>
                <label>
                  To
                  <input
                    type="email"
                    value={recipient}
                    onChange={(event) => setRecipient(event.target.value)}
                    required
                    disabled={busy}
                  />
                </label>
                <label>
                  Subject
                  <input
                    value={subject}
                    onChange={(event) => setSubject(event.target.value)}
                    required
                    disabled={busy}
                  />
                </label>
                <label>
                  Body
                  <textarea
                    rows={6}
                    value={body}
                    onChange={(event) => setBody(event.target.value)}
                    required
                    disabled={busy}
                  />
                </label>
              </>
            ) : (
              <label>
                {item.kind === "task" ? "Task instructions" : "Reminder message"}
                <textarea
                  rows={5}
                  value={message}
                  onChange={(event) => setMessage(event.target.value)}
                  required
                  disabled={busy}
                />
              </label>
            )}
            <label>
              Date and time ({item.timezone || "UTC"})
              <input
                type="datetime-local"
                step="1"
                value={startTime}
                onChange={(event) => setStartTime(event.target.value)}
                required
                disabled={busy}
              />
            </label>
            {item.recurrence_rule && <p className={styles.hint}>This updates the remaining schedule. The repeat rule stays the same.</p>}
            {item.kind === "email" && <p className={styles.hint}>Saving pauses this email until you approve the updated draft and schedule.</p>}
          </>
        ) : mode === "approve" ? (
          <>
            <p className={styles.hint}>Approve this exact email and its schedule.</p>
            <dl className={styles.details}>
              <dt>To</dt>
              <dd>{item.recipient_email}</dd>
              <dt>Subject</dt>
              <dd>{item.subject}</dd>
              <dt>Send time</dt>
              <dd>
                {dateLabel(item.next_trigger, item.timezone)} ({item.timezone || "UTC"})
              </dd>
            </dl>
            <p className={styles.message}>{item.body}</p>
            {item.recurrence_rule && <p className={styles.hint}>Repeats: {item.recurrence_rule}</p>}
          </>
        ) : (
          <>
            <p>{item.kind === "email" ? item.subject : item.message}</p>
            <p className={styles.hint}>{item.recurrence_rule ? "This stops all future occurrences and removes the item from Upcoming. It will remain in History as cancelled." : "This stops the scheduled item and removes it from Upcoming. It will remain in History as cancelled."}</p>
          </>
        )}
        {error && (
          <p
            role="alert"
            className={styles.error}
          >
            {error}
          </p>
        )}
        <div className={styles.actions}>
          <button
            type="button"
            onClick={onClose}
            disabled={busy}
            autoFocus={mode === "cancel"}
          >
            {mode === "cancel" ? "Cancel" : "Close"}
          </button>
          <button
            type="submit"
            className={mode === "cancel" ? styles.deleteButton : styles.primary}
            disabled={busy}
          >
            {busy ? (mode === "cancel" ? "Deleting…" : "Saving…") : mode === "edit" ? "Save changes" : mode === "approve" ? "Approve email" : "Delete item"}
          </button>
        </div>
      </form>
    </AnimatedDialog>
  );
}

export function Reminders({ items, loading, error, onChange }: { items: Reminder[]; loading: boolean; error: string; onChange: (item: Reminder, change: ReminderChange) => Promise<Reminder> }) {
  const [tab, setTab] = useState<"upcoming" | "history">("upcoming");
  const [dialog, setDialog] = useState<{ item: Reminder; mode: "edit" | "cancel" | "approve" } | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [actionError, setActionError] = useState("");
  const [notice, setNotice] = useState("");
  const visible = items.filter((item) => item.upcoming === (tab === "upcoming")).sort((a, b) => (tab === "upcoming" ? Date.parse(a.next_trigger || a.start_time || a.updated_at) - Date.parse(b.next_trigger || b.start_time || b.updated_at) : Date.parse(b.updated_at) - Date.parse(a.updated_at)));

  async function change(item: Reminder, update: ReminderChange) {
    const updated = await onChange(item, update);
    setNotice(update.action === "edit" && updated.kind === "email" ? "Email updated. Review and approve it before it can send." : update.action === "cancel" ? "Item cancelled and moved to history." : update.action === "approve" ? "Email approved and scheduled." : update.action === "resume" ? "Item resumed." : "Changes saved.");
    return updated;
  }

  async function resume(item: Reminder) {
    setBusyId(item.id);
    setActionError("");
    try {
      await change(item, { action: "resume" });
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Could not resume item");
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div className={styles.workspace}>
      <header className="chat-header">
        <h1>Reminders</h1>
        <p>Your reminders, scheduled tasks, and emails.</p>
      </header>
      <div className={styles.content}>
        <div className={styles.toolbar}>
          <div
            className={styles.tabs}
            aria-label="Reminder views"
          >
            <button
              type="button"
              aria-pressed={tab === "upcoming"}
              onClick={() => setTab("upcoming")}
            >
              Upcoming <span>{items.filter((item) => item.upcoming).length}</span>
            </button>
            <button
              type="button"
              aria-pressed={tab === "history"}
              onClick={() => setTab("history")}
            >
              History
            </button>
          </div>
        </div>
        {error && (
          <p
            role="alert"
            className={styles.error}
          >
            {error}
          </p>
        )}
        {actionError && (
          <p
            role="alert"
            className={styles.error}
          >
            {actionError}
          </p>
        )}
        <p
          role="status"
          className={styles.notice}
        >
          {notice}
        </p>
        {loading ? (
          <p className={styles.empty}>Loading reminders…</p>
        ) : !visible.length ? (
          <div className={styles.empty}>
            <h2>{tab === "upcoming" ? "Nothing coming up" : "No past items yet"}</h2>
            <p>{tab === "upcoming" ? "Ask in conversation to set a reminder or schedule a task." : "Completed, cancelled, and failed items will appear here."}</p>
          </div>
        ) : (
          <ul className={styles.list}>
            {visible.map((item) => (
              <li
                key={item.id}
                className={styles.card}
              >
                <div className={styles.cardHeading}>
                  <span className={styles.kind}>{item.kind === "email" ? "Scheduled email" : item.kind === "task" ? "Scheduled task" : "Reminder"}</span>
                  <div className={styles.headerActions}>
                    <span
                      className={styles.status}
                      data-status={item.status}
                    >
                      {labels[item.status]}
                    </span>
                    {item.editable && (
                      <ReminderActions
                        label={item.kind === "email" ? item.subject : item.message}
                        onEdit={() => setDialog({ item, mode: "edit" })}
                        onDelete={() => setDialog({ item, mode: "cancel" })}
                      />
                    )}
                  </div>
                </div>
                <h2>{item.kind === "email" ? item.subject : item.message}</h2>
                {item.kind === "email" && (
                  <>
                    <p className={styles.hint}>To: {item.recipient_email}</p>
                    <details className={styles.emailBody}>
                      <summary>View email body</summary>
                      <p className={styles.message}>{item.body}</p>
                    </details>
                  </>
                )}
                <p className={styles.time}>
                  {dateLabel(item.next_trigger || item.start_time, item.timezone)} <span>({item.timezone || "UTC"})</span>
                </p>
                {item.recurrence_rule && (
                  <details className={styles.hint}>
                    <summary>Repeating schedule</summary>
                    <pre>{item.recurrence_rule}</pre>
                  </details>
                )}
                {item.last_error && <p className={styles.error}>{item.last_error}</p>}
                {item.editable && (item.status === "awaiting_approval" || item.status === "paused") && (
                  <div className={`${styles.actions} ${styles.cardActions}`}>
                    {item.status === "awaiting_approval" && (
                      <button
                        type="button"
                        className={styles.primary}
                        onClick={() => setDialog({ item, mode: "approve" })}
                      >
                        Review &amp; approve
                      </button>
                    )}
                    {item.status === "paused" && (
                      <button
                        type="button"
                        disabled={busyId === item.id}
                        onClick={() => void resume(item)}
                      >
                        {busyId === item.id ? "Resuming…" : "Resume"}
                      </button>
                    )}
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
      {dialog && (
        <ItemDialog
          key={`${dialog.item.id}-${dialog.mode}`}
          item={dialog.item}
          mode={dialog.mode}
          onClose={() => setDialog(null)}
          onChange={change}
        />
      )}
    </div>
  );
}
