import assert from "node:assert/strict";
import { test } from "node:test";
import { messageTimestamp } from "../../lib/messageTimestamp";

test("legacy log timestamps keep their recorded wall time across browser timezones", () => {
  const legacy = messageTimestamp("2026-10-23 15:03:38", "Asia/Tokyo");
  assert.equal(legacy?.dateTime, "2026-10-23T15:03:38");
  assert.equal(legacy?.label, messageTimestamp("2026-10-23T15:03:38Z", "UTC")?.label);
});

test("timestamps with offsets display in the user's selected timezone", () => {
  assert.equal(messageTimestamp("2026-10-23T21:03:38Z", "America/Edmonton")?.label, messageTimestamp("2026-10-23 15:03:38")?.label);
  assert.equal(messageTimestamp("2026-10-23T15:03:38-06:00", "UTC")?.label, messageTimestamp("2026-10-23T21:03:38Z", "UTC")?.label);
});

test("missing and invalid timestamps do not invent a send time", () => {
  for (const value of [undefined, null, "", "unknown"]) assert.equal(messageTimestamp(value), null);
});
