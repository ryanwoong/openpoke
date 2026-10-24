import assert from "node:assert/strict";
import { test } from "node:test";
import { toBubbles } from "../../lib/chatHistory";
test("preserves optimistic and existing message identities when history is polled", () => {
  const previous = [{ id: "optimistic", role: "user", text: "Hello" }];
  const payload = {
    messages: [
      { role: "user", content: "Hello" },
      { role: "assistant", content: "Hi" },
    ],
  };
  const result = toBubbles(payload, previous);
  assert.equal(result[0].id, "optimistic");
  assert.deepEqual(toBubbles(payload, result), result);
});
test("keeps repeated messages distinct and filters empty history entries", () => {
  const payload = { messages: [{ role: "user", content: "Hello" }, { role: "user", content: "Hello" }, { role: "assistant", content: " " }, null] };
  const result = toBubbles(payload);
  assert.equal(result.length, 2);
  assert.notEqual(result[0].id, result[1].id);
  assert.deepEqual(toBubbles(payload, result), result);
  assert.deepEqual(toBubbles(null), []);
});

test("uses saved timestamps and preserves optimistic time until history provides one", () => {
  const previous = [{ id: "optimistic", role: "user", text: "Hello", timestamp: "2026-10-23T21:00:00Z" }];
  const withoutTimestamp = toBubbles({ messages: [{ role: "user", content: "Hello" }] }, previous);
  assert.equal(withoutTimestamp[0].timestamp, previous[0].timestamp);
  const saved = toBubbles({ messages: [{ role: "user", content: "Hello", timestamp: "2026-10-23 15:00:01" }] }, withoutTimestamp);
  assert.equal(saved[0].id, "optimistic");
  assert.equal(saved[0].timestamp, "2026-10-23 15:00:01");
  assert.equal(toBubbles({ messages: [{ role: "assistant", content: "Hi" }] })[0].timestamp, null);
});

test("repeated messages retain their individual send times through polling", () => {
  const payload = {
    messages: [
      { role: "assistant", content: "Take a break", timestamp: "2026-10-23 15:00:00" },
      { role: "assistant", content: "Take a break", timestamp: "2026-10-23 15:05:00" },
    ],
  };
  const first = toBubbles(payload);
  assert.notEqual(first[0].timestamp, first[1].timestamp);
  assert.deepEqual(toBubbles(payload, first), first);
});

test("keeps identical replies from different requests and scheduled updates distinct", () => {
  const payload = {
    messages: [
      { role: "assistant", content: "Done", request_id: "first" },
      { role: "assistant", content: "Done", source: "scheduled" },
      { role: "assistant", content: "Done", request_id: "second" },
    ],
  };
  const original = toBubbles(payload);
  const reordered = toBubbles({ messages: [...payload.messages].reverse() }, original);
  assert.equal(reordered[0].id, original[2].id);
  assert.equal(reordered[1].id, original[1].id);
  assert.equal(reordered[2].id, original[0].id);
  assert.equal(reordered[0].requestId, "second");
  assert.equal(reordered[1].source, "scheduled");
});
