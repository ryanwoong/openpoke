import assert from "node:assert/strict";
import { test } from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { restoreChatActivity, updateChatActivity, type ChatActivity } from "../../lib/chatActivity";
import type { ExecutionSpan } from "../execution/executionGraph";
import { ChatMessages } from "./ChatMessages";

function span(id: string, overrides: Partial<ExecutionSpan> = {}): ExecutionSpan {
  return { id, run_id: "root", parent_id: "root", kind: "tool", label: "task_email_search", status: "running", started_at: "2026-10-23T12:00:00Z", ended_at: null, input: {}, output: null, ...overrides };
}
const activity: ChatActivity = { requestId: "request", userText: "Check email", occurrence: 2, steps: [] };
const root = span("root", { kind: "interaction", parent_id: null, input: { request_id: "request" } });

test("correlates by request ID, excludes background work, and suppresses nested Gmail calls", () => {
  const result = updateChatActivity([activity, { ...activity, requestId: "other" }], [root, span("search"), span("fetch", { kind: "access", parent_id: "search", label: "Gmail · GMAIL_FETCH_EMAILS" }), span("background", { kind: "access", run_id: "poll", parent_id: null, label: "Gmail · GMAIL_FETCH_EMAILS" })]);
  assert.deepEqual(result[0].steps, [{ id: "search", label: "Searching your inbox…", status: "running" }]);
  assert.deepEqual(result[1].steps, []);
});

test("updates a step in place and uses confirmed counts, including zero and truncated lists", () => {
  const running = updateChatActivity([activity], [root, span("search")]);
  for (const count of [0, 1, 3, 50]) {
    const completed = updateChatActivity(running, [root, span("search", { status: "completed", output: Array(count).fill({ id: "email" }) })]);
    assert.deepEqual(completed[0].steps, [{ id: "search", label: `Found ${count}${count === 50 ? "+" : ""} matching email${count === 1 ? "" : "s"}`, status: "completed" }]);
    assert.deepEqual(updateChatActivity(completed, [])[0].steps, completed[0].steps);
  }
});

test("does not present errors or cancellations as successful tool calls", () => {
  const result = updateChatActivity([activity], [root, span("error", { status: "completed", output: { status: "error", error: "Disconnected" } }), span("cancelled", { status: "cancelled" })]);
  assert.equal(result[0].steps[0].status, "failed");
  assert.equal(result[0].steps[1].status, "cancelled");
  assert.ok(!result[0].steps.some((step) => step.label.includes("Found")));
});

test("restores completed history and marks running steps interrupted until the server confirms them", () => {
  const running = updateChatActivity([activity], [root, span("search")]);
  const restored = restoreChatActivity(JSON.stringify(running));
  assert.equal(restored[0].steps[0].status, "interrupted");
  assert.equal(updateChatActivity(restored, [root, span("search")])[0].steps[0].status, "running");
  assert.deepEqual(restoreChatActivity("invalid"), []);
  assert.deepEqual(restoreChatActivity('[{"steps":[null]}]'), []);
});

test("renders activity at the bottom of the correct repeated turn", () => {
  const activities = updateChatActivity([activity], [root, span("search", { status: "completed", output: [] })]);
  const html = renderToStaticMarkup(
    <ChatMessages
      messages={[
        { id: "first", role: "user", text: "Check email" },
        { id: "reply1", role: "assistant", text: "First reply" },
        { id: "second", role: "user", text: "Check email" },
        { id: "reply2", role: "assistant", text: "Second reply" },
      ]}
      activities={activities}
      isWaitingForResponse={false}
      scrollContainerRef={{ current: null }}
      onScroll={() => {}}
    />,
  );
  assert.ok(html.indexOf("Found 0 matching emails") > html.indexOf("First reply"));
  assert.ok(html.indexOf("Found 0 matching emails") > html.indexOf("Second reply"));
  assert.equal((html.match(/Found 0 matching emails/g) || []).length, 1);
});

test("collapses completed groups and keeps running groups visible without a duplicate loader", () => {
  const activities = updateChatActivity([activity], [{ ...root, status: "completed" }, span("search", { status: "completed", output: [] }), span("reminder", { label: "createTrigger", status: "completed" })]);
  const render = () =>
    renderToStaticMarkup(
      <ChatMessages
        messages={[
          { id: "first", role: "user", text: "Check email" },
          { id: "second", role: "user", text: "Check email" },
        ]}
        activities={activities}
        isWaitingForResponse={false}
        scrollContainerRef={{ current: null }}
        onScroll={() => {}}
      />,
    );
  assert.ok(render().includes("Completed 2 steps"));
  activities[0].steps[1].status = "running";
  const html = render();
  assert.ok(!html.includes("<details>"));
  assert.ok(!html.includes("Working on your request"));
  assert.ok(html.includes('aria-live="polite"'));
});

test("live activity stays below acknowledgements through tool completion and final delivery", () => {
  const messages = [
    { id: "user", role: "user", text: "Check email" },
    { id: "ack", role: "assistant", text: "Checking your emails now." },
  ];
  let activities = updateChatActivity([{ ...activity, occurrence: 1 }], [root, span("search")]);
  let waiting = true;
  const render = () =>
    renderToStaticMarkup(
      <ChatMessages
        messages={messages}
        activities={activities}
        isWaitingForResponse={waiting}
        scrollContainerRef={{ current: null }}
        onScroll={() => {}}
      />,
    );
  let html = render();
  assert.ok(html.indexOf("Searching your inbox") > html.indexOf("Checking your emails now."));
  const completedTool = span("search", { status: "completed", ended_at: "2026-10-23T12:00:01Z", output: [{ id: "one" }, { id: "two" }] });
  activities = updateChatActivity(activities, [root, completedTool]);
  html = render();
  assert.ok(html.indexOf("Found 2 matching emails") > html.indexOf("Checking your emails now."));
  assert.ok(html.indexOf("Writing answer") > html.indexOf("Found 2 matching emails"));
  assert.equal((html.match(/message-identity/g) || []).length, 1);
  activities = updateChatActivity(activities, [{ ...root, status: "completed" }, completedTool, span("deliver", { label: "send_message_to_user", status: "completed", started_at: "2026-10-23T12:00:02Z", input: { message: "Here are your emails." } })]);
  messages.push({ id: "final", role: "assistant", text: "Here are your emails." });
  waiting = false;
  html = render();
  assert.ok(html.indexOf("Found 2 matching emails") > html.indexOf("Checking your emails now."));
  assert.ok(html.indexOf("Found 2 matching emails") > html.indexOf("Here are your emails."));
  assert.ok(!html.includes("Preparing your reply"));
  assert.ok(!html.includes("Working on your request"));
});

test("historical activity stays at the end of its turn before a later user message", () => {
  const activities = updateChatActivity([{ ...activity, occurrence: 1 }], [root, span("search", { status: "completed", output: [] })]);
  const html = renderToStaticMarkup(
    <ChatMessages
      messages={[
        { id: "user", role: "user", text: "Check email" },
        { id: "reply", role: "assistant", text: "Here are your emails." },
        { id: "next", role: "user", text: "Next question" },
      ]}
      activities={activities}
      isWaitingForResponse={false}
      scrollContainerRef={{ current: null }}
      onScroll={() => {}}
    />,
  );
  assert.ok(html.indexOf("Found 0 matching emails") > html.indexOf("Here are your emails."));
  assert.ok(html.indexOf("Found 0 matching emails") < html.indexOf("Next question"));
});

test("counts the emails inside the execution result envelope instead of its two wrapper entries", () => {
  for (const count of [0, 1, 2, 4, 50]) {
    const payload = Array.from({ length: count }, (_, index) => ({ id: `email-${index}`, subject: `Subject ${index + 1}`, sender: `Sender ${index + 1}`, clean_text: "Private email body" }));
    const result = updateChatActivity([activity], [{ ...root, status: "completed" }, span("search", { status: "completed", output: [true, payload] })]);
    const step = result[0].steps[0];
    assert.equal(step.label, `Found ${count}${count === 50 ? "+" : ""} matching email${count === 1 ? "" : "s"}`);
    assert.equal(step.emails?.length || 0, count);
    assert.ok(!JSON.stringify(result).includes("Private email body"));
    assert.deepEqual(restoreChatActivity(JSON.stringify(result)), JSON.parse(JSON.stringify(result)));
  }
});

test("wrapped failures never produce a successful email count or email details", () => {
  for (const output of [
    [false, { error: "Unavailable" }],
    [true, { error: "Unavailable" }],
    [true, { status: "error" }],
  ]) {
    const result = updateChatActivity([activity], [root, span("search", { status: "completed", output })]);
    assert.equal(result[0].steps[0].status, "failed");
    assert.equal(result[0].steps[0].emails, undefined);
    assert.ok(!result[0].steps[0].label.startsWith("Found"));
  }
});

test("email activity expands to brief subjects and senders without bodies or raw tool data", () => {
  const result = updateChatActivity(
    [{ ...activity, occurrence: 1 }],
    [
      root,
      span("search", {
        status: "completed",
        output: [
          true,
          [
            { id: "a", subject: "<Subject one>", sender: "First sender", clean_text: "Private content" },
            { id: "b", subject: "", sender: "Second sender", clean_text: "Private content" },
          ],
        ],
      }),
    ],
  );
  const html = renderToStaticMarkup(
    <ChatMessages
      messages={[{ id: "user", role: "user", text: "Check email" }]}
      activities={result}
      isWaitingForResponse={false}
      scrollContainerRef={{ current: null }}
      onScroll={() => {}}
    />,
  );
  assert.ok(html.includes("Found 2 matching emails</summary>"));
  assert.ok(html.includes('class="chat-activity-email-details"'));
  assert.ok(html.includes("&lt;Subject one&gt;"));
  assert.ok(html.includes("First sender"));
  assert.ok(html.includes("(No subject)"));
  assert.ok(!html.includes("Private content"));
  assert.ok(!html.includes("clean_text"));
  const corrupted = JSON.stringify([{ ...result[0], steps: [{ ...result[0].steps[0], emails: [null] }] }]);
  assert.deepEqual(restoreChatActivity(corrupted), []);
});

test("request IDs keep late activity with its reply rather than a repeated question", () => {
  const html = renderToStaticMarkup(
    <ChatMessages
      messages={[
        { id: "first", role: "user", text: "Check email", requestId: "request" },
        { id: "second", role: "user", text: "Check email", requestId: "other" },
        { id: "reply", role: "assistant", text: "Late result for the first request", requestId: "request" },
        { id: "third", role: "user", text: "hello", requestId: "greeting" },
      ]}
      activities={[{ ...activity, occurrence: 99, steps: [{ id: "search", label: "Searched original request", status: "completed" }] }]}
      activeRequestIds={["request"]}
      isWaitingForResponse={true}
      scrollContainerRef={{ current: null }}
      onScroll={() => {}}
    />,
  );
  assert.ok(html.indexOf("Searched original request") > html.indexOf("Late result for the first request"));
  assert.ok(html.indexOf("Searched original request") < html.indexOf(">hello<"));
  assert.ok(html.includes("Preparing your reply…"));
  assert.ok(!html.includes("Working on your request…"));
});

test("standalone activity shows OpenPoke before the first assistant bubble arrives", () => {
  const render = (withReply: boolean) =>
    renderToStaticMarkup(
      <ChatMessages
        messages={[{ id: "question", role: "user", text: "Check email", requestId: "request" }, ...(withReply ? [{ id: "reply", role: "assistant", text: "Checking your inbox", requestId: "request" }] : [])]}
        activities={[{ ...activity, steps: [{ id: "search", label: "Found 24 matching emails", status: "completed" }] }]}
        activeRequestIds={["request"]}
        isWaitingForResponse={true}
        scrollContainerRef={{ current: null }}
        onScroll={() => {}}
      />,
    );
  for (const withReply of [false, true]) {
    const html = render(withReply);
    assert.equal((html.match(/>OpenPoke</g) || []).length, 1);
    assert.ok(html.indexOf(">OpenPoke<") < html.indexOf("Found 24 matching emails"));
    assert.ok(html.includes("Preparing your reply…"));
  }
});

test("compaction and timeout reflect the task outcome even after all search steps finish", () => {
  const completedSearch = span("search", { status: "completed", output: [{ id: "one" }] });
  const worker = span("worker", { kind: "execution", label: "Order counter" });
  const compaction = span("compact", { parent_id: "worker", kind: "model", label: "Context compaction · local model" });
  const started = updateChatActivity([activity], [root, worker, completedSearch, compaction]);
  assert.equal(started[0].phase, "Compacting results…");
  const stopped = updateChatActivity(started, [{ ...root, status: "completed" }, { ...worker, status: "cancelled", output: { error: "Task timed out at its overall deadline" } }, completedSearch, { ...compaction, status: "cancelled" }]);
  assert.equal(stopped[0].status, "timed_out");
  assert.equal(stopped[0].phase, undefined);
  const html = renderToStaticMarkup(
    <ChatMessages
      messages={[{ id: "question", role: "user", text: "Check email", requestId: "request" }]}
      activities={stopped}
      activeRequestIds={[]}
      isWaitingForResponse={false}
      scrollContainerRef={{ current: null }}
      onScroll={() => {}}
    />,
  );
  assert.ok(html.includes("Task timed out · 1 step completed"));
  assert.ok(!html.includes("Compacting results…"));
  assert.ok(!html.includes("Writing answer…"));
  assert.equal(restoreChatActivity(JSON.stringify(stopped))[0].status, "timed_out");
});
