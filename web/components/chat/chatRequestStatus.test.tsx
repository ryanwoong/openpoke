import assert from "node:assert/strict";
import { test } from "node:test";
import { chatRequestStatus, hasActiveChatRequest } from "../../lib/chatRequestStatus";
import type { ExecutionSpan } from "../execution/executionGraph";

function root(id: string, requestId: string, status = "running"): ExecutionSpan {
  return { id, run_id: id, parent_id: null, kind: "interaction", label: "Interaction agent", status, started_at: "2026-10-22T12:00:00Z", ended_at: status === "running" ? null : "2026-10-22T12:00:01Z", input: { request_id: requestId, prompt: "Check email" }, output: { success: true } };
}

test("an accepted request stays unresolved until its own root appears", () => {
  assert.equal(chatRequestStatus([root("old", "other", "completed")], "new-request"), null);
});

test("a completed interaction and an acknowledgement do not end a running worker", () => {
  const interaction = root("task", "request", "completed");
  const worker = { ...root("worker", "request"), run_id: "task", parent_id: "task", kind: "execution" };
  assert.deepEqual(chatRequestStatus([interaction, worker], "request"), { status: "running" });
  assert.equal(hasActiveChatRequest([interaction, worker]), true);
  worker.status = "completed";
  assert.deepEqual(chatRequestStatus([interaction, worker], "request"), { status: "completed" });
});

test("identical prompts are correlated by request id rather than an old reply", () => {
  const first = root("first", "request-1", "completed");
  const second = root("second", "request-2");
  assert.deepEqual(chatRequestStatus([first, second], "request-1"), { status: "completed" });
  assert.deepEqual(chatRequestStatus([first, second], "request-2"), { status: "running" });
});

test("background Gmail polling does not make the conversation busy", () => {
  const poll = { ...root("poll", "poll"), kind: "access", input: {} };
  assert.equal(hasActiveChatRequest([poll]), false);
});

test("an accepted request failure is surfaced after the last interaction ends", () => {
  const task = root("task", "request", "completed");
  const reply = { ...root("reply", "request", "failed"), parent_id: "task", run_id: "task", output: { success: false, error: "Model unavailable" } };
  assert.deepEqual(chatRequestStatus([task, reply], "request"), { status: "failed", error: "Model unavailable" });
});

test("a recovered tool failure does not turn a successful final reply into a chat error", () => {
  const task = root("task", "request", "completed");
  const tool = { ...root("tool", "request", "failed"), parent_id: "task", run_id: "task", kind: "tool" };
  const reply = { ...root("reply", "request", "completed"), parent_id: "task", run_id: "task" };
  assert.deepEqual(chatRequestStatus([task, tool, reply], "request"), { status: "completed" });
});

test("timed-out execution remains failed after the initial acknowledgement completed", () => {
  const task = root("task", "request", "completed");
  const worker = { ...root("worker", "request", "cancelled"), parent_id: "task", run_id: "task", kind: "execution", output: { error: "" } };
  assert.equal(chatRequestStatus([task, worker], "request")?.status, "failed");
  assert.equal(hasActiveChatRequest([task, worker]), false);
});

test("a cancelled worker's lingering synchronous Gmail call does not keep the request busy", () => {
  const task = root("task", "request", "completed");
  const worker = { ...root("worker", "request", "cancelled"), parent_id: "task", run_id: "task", kind: "execution" };
  const gmail = { ...root("gmail", "request"), parent_id: "worker", run_id: "task", kind: "access" };
  assert.equal(chatRequestStatus([task, worker, gmail], "request")?.status, "failed");
  assert.equal(hasActiveChatRequest([task, worker, gmail]), false);
});
