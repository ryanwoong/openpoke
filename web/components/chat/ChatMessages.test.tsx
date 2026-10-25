import assert from "node:assert/strict";
import { test } from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { ChatMessages } from "./ChatMessages";
import { MarkdownMessage } from "./MarkdownMessage";
import { replyChoices } from "../../lib/replyChoices";

const renderMarkdown = (text: string) => renderToStaticMarkup(<MarkdownMessage text={text} />);

const approvalText = "Email draft for tomorrow (awaiting approval):\nTo: hello@example.com\nSubject: Hey!\n\nHello!\n\nReply yes to approve this exact email and its schedule, or no to cancel. Repeats: daily.";

test("approval choices preserve draft and recurrence details while replacing reply instructions", () => {
  const html = renderToStaticMarkup(
    <ChatMessages
      messages={[{ id: "approval", role: "assistant", text: approvalText }]}
      isWaitingForResponse={false}
      scrollContainerRef={{ current: null }}
      onScroll={() => {}}
      onReplyChoice={async () => {}}
    />,
  );
  for (const text of ["hello@example.com", "Subject: Hey!", "Hello!", "Repeats: daily.", "Click Yes or No.", ">Yes</button>", ">No</button>"]) assert.ok(html.includes(text), text);
  assert.ok(!html.includes("Reply yes"));
  assert.ok(!html.includes('disabled=""'));
});

test("older approval choices and choices during active requests cannot be submitted", () => {
  for (const waiting of [false, true]) {
    const html = renderToStaticMarkup(
      <ChatMessages
        messages={[{ id: "approval", role: "assistant", text: approvalText }, ...(!waiting ? [{ id: "answer", role: "user", text: "no" }] : [])]}
        isWaitingForResponse={waiting}
        scrollContainerRef={{ current: null }}
        onScroll={() => {}}
        onReplyChoice={async () => {}}
      />,
    );
    assert.equal((html.match(/disabled=""/g) || []).length, 2);
  }
});

test("ordinary questions, quoted instructions, and code examples stay intact", () => {
  for (const text of ["Should I send this email?", "> Reply yes to approve, or no to cancel.", "```\nReply yes to approve, or no to cancel.\n```", "Reply yes to approve, or no to cancel.\n\nAnother question follows."]) assert.equal(replyChoices(text), null);
  assert.equal(replyChoices("Continue?\n\nReply yes or no.")?.text, "Continue?");
});

test("renders common Markdown and GitHub-flavored formatting", () => {
  const html = renderMarkdown("# Heading\n\n**bold** and *italic* and ~~removed~~\n\n- first\n  - nested\n\n1. ordered\n\n> quote\n\n---\n\n- [x] done\n\n| Name | Value |\n| --- | --- |\n| one | two |");

  for (const fragment of ["<h1>Heading</h1>", "<strong>bold</strong>", "<em>italic</em>", "<del>removed</del>", "<ul>", "<ol>", "<li>nested</li>", "<blockquote>", "<hr/>", 'type="checkbox"', "<table>", "<td>two</td>"]) {
    assert.ok(html.includes(fragment), fragment);
  }
});

test("preserves code literally and separates paragraphs", () => {
  const html = renderMarkdown('First\nline\n\nSecond with `**literal**`\n\n```js\nconst text = "\\n";\n<script>alert(1)</script>\n```');

  assert.ok(html.includes("<p>First\nline</p>"));
  assert.ok(html.includes("<code>**literal**</code>"));
  assert.ok(html.includes('<pre><code class="language-js">'));
  assert.ok(html.includes("&quot;\\n&quot;"));
  assert.ok(html.includes("&lt;script&gt;alert(1)&lt;/script&gt;"));
});

test("keeps safe links and blocks executable URLs, raw HTML, and remote images", () => {
  const html = renderMarkdown('[safe](https://example.com "Example")\n\nhttps://example.org\n\n[mail](mailto:hello@example.com)\n\n[unsafe](javascript:alert%281%29)\n\n[data](data:text/html,hello)\n\n<script>alert(1)</script>\n\n<img src="x" onerror="alert(1)">\n\n![Description](https://example.com/tracking.png)');

  assert.ok(html.includes('href="https://example.com"'));
  assert.ok(html.includes('href="https://example.org"'));
  assert.ok(html.includes('href="mailto:hello@example.com"'));
  assert.ok(html.includes('target="_blank" rel="noopener noreferrer"'));
  assert.ok(html.includes("Description"));
  for (const fragment of ["javascript:", "data:text/html", "<script", "<img", "onerror=", "tracking.png"]) {
    assert.ok(!html.includes(fragment), fragment);
  }
});

test("only assistant messages render Markdown", () => {
  const html = renderToStaticMarkup(
    <ChatMessages
      messages={[
        { id: "assistant", role: "assistant", text: "**AI response**" },
        { id: "user", role: "user", text: "**User text**\nnext line" },
        { id: "draft", role: "draft", text: "**Draft text**" },
      ]}
      isWaitingForResponse={false}
      scrollContainerRef={{ current: null }}
      onScroll={() => {}}
    />,
  );

  assert.ok(html.includes("<strong>AI response</strong>"));
  assert.ok(html.includes("**User text**\nnext line</span>"));
  assert.ok(html.includes("**Draft text**</span>"));
  assert.equal((html.match(/class="markdown-message"/g) || []).length, 1);
});

test("scheduled updates stay distinct while an unrelated chat request is running", () => {
  const html = renderToStaticMarkup(
    <ChatMessages
      messages={[
        { id: "question", role: "user", text: "How many Chipotle orders this year?" },
        { id: "notification", role: "assistant", text: "Scheduled email sent", source: "scheduled" },
      ]}
      isWaitingForResponse={true}
      scrollContainerRef={{ current: null }}
      onScroll={() => {}}
    />,
  );
  assert.ok(html.includes("Scheduled update"));
  assert.ok(html.includes("scheduled-notification"));
  assert.ok(html.includes("Working on your request…"));
});

test("late replies identify the original question after a newer user message", () => {
  const html = renderToStaticMarkup(
    <ChatMessages
      messages={[
        { id: "question", role: "user", text: "Count my orders again", requestId: "orders" },
        { id: "greeting", role: "user", text: "hello", requestId: "hello" },
        { id: "answer", role: "assistant", text: "Four orders", requestId: "orders" },
      ]}
      isWaitingForResponse={false}
      scrollContainerRef={{ current: null }}
      onScroll={() => {}}
    />,
  );
  assert.ok(html.includes("Reply to: Count my orders again"));
});

test("running tasks omit Stop while checkpoint failures offer a saved-result retry", () => {
  const render = (failed: boolean) =>
    renderToStaticMarkup(
      <ChatMessages
        messages={[{ id: "question", role: "user", text: "Count orders", requestId: "orders" }, ...(failed ? [{ id: "failed", role: "assistant", text: "Task timed out", source: "failure" as const, requestId: "orders", retryable: true }] : [])]}
        activities={failed ? [] : [{ requestId: "orders", userText: "Count orders", occurrence: 1, steps: [], phase: "Writing answer…", status: "running" }]}
        activeRequestIds={failed ? [] : ["orders"]}
        isWaitingForResponse={!failed}
        onRetryRequest={() => {}}
        scrollContainerRef={{ current: null }}
        onScroll={() => {}}
      />,
    );
  assert.ok(!render(false).includes("Stop task"));
  assert.ok(render(true).includes("Retry with saved results"));
  assert.ok(!render(true).includes("Stop task"));
});
