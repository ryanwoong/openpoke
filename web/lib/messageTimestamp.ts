export function messageTimestamp(timestamp: string | null | undefined, timeZone?: string) {
  if (!timestamp) return null;
  const dateTime = timestamp.replace(" ", "T");
  // Legacy logs contain user-local wall time without a timezone offset.
  const recordedLocalTime = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?$/.test(dateTime);
  const date = new Date(recordedLocalTime ? `${dateTime}Z` : dateTime);
  if (!Number.isFinite(date.getTime())) return null;
  const displayTimeZone = recordedLocalTime ? "UTC" : timeZone;
  const label = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short", timeZone: displayTimeZone }).format(date);
  const timeLabel = new Intl.DateTimeFormat(undefined, { timeStyle: "short", timeZone: displayTimeZone }).format(date);
  return { dateTime, label, timeLabel };
}
