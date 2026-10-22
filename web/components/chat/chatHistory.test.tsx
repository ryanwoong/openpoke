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
