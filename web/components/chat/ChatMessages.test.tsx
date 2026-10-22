import assert from 'node:assert/strict';
import { test } from 'node:test';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

import { ChatMessages } from './ChatMessages';
import { MarkdownMessage } from './MarkdownMessage';

const renderMarkdown = (text: string) => renderToStaticMarkup(<MarkdownMessage text={text} />);

test('renders common Markdown and GitHub-flavored formatting', () => {
  const html = renderMarkdown('# Heading\n\n**bold** and *italic* and ~~removed~~\n\n- first\n  - nested\n\n1. ordered\n\n> quote\n\n---\n\n- [x] done\n\n| Name | Value |\n| --- | --- |\n| one | two |');

  for (const fragment of ['<h1>Heading</h1>', '<strong>bold</strong>', '<em>italic</em>', '<del>removed</del>', '<ul>', '<ol>', '<li>nested</li>', '<blockquote>', '<hr/>', 'type="checkbox"', '<table>', '<td>two</td>']) {
    assert.ok(html.includes(fragment), fragment);
  }
});

test('preserves code literally and separates paragraphs', () => {
  const html = renderMarkdown('First\nline\n\nSecond with `**literal**`\n\n```js\nconst text = "\\n";\n<script>alert(1)</script>\n```');

  assert.ok(html.includes('<p>First\nline</p>'));
  assert.ok(html.includes('<code>**literal**</code>'));
  assert.ok(html.includes('<pre><code class="language-js">'));
  assert.ok(html.includes('&quot;\\n&quot;'));
  assert.ok(html.includes('&lt;script&gt;alert(1)&lt;/script&gt;'));
});

test('keeps safe links and blocks executable URLs, raw HTML, and remote images', () => {
  const html = renderMarkdown('[safe](https://example.com "Example")\n\nhttps://example.org\n\n[mail](mailto:hello@example.com)\n\n[unsafe](javascript:alert%281%29)\n\n[data](data:text/html,hello)\n\n<script>alert(1)</script>\n\n<img src="x" onerror="alert(1)">\n\n![Description](https://example.com/tracking.png)');

  assert.ok(html.includes('href="https://example.com"'));
  assert.ok(html.includes('href="https://example.org"'));
  assert.ok(html.includes('href="mailto:hello@example.com"'));
  assert.ok(html.includes('target="_blank" rel="noopener noreferrer"'));
  assert.ok(html.includes('Description'));
  for (const fragment of ['javascript:', 'data:text/html', '<script', '<img', 'onerror=', 'tracking.png']) {
    assert.ok(!html.includes(fragment), fragment);
  }
});

test('only assistant messages render Markdown', () => {
  const html = renderToStaticMarkup(
    <ChatMessages
      messages={[
        { id: 'assistant', role: 'assistant', text: '**AI response**' },
        { id: 'user', role: 'user', text: '**User text**\nnext line' },
        { id: 'draft', role: 'draft', text: '**Draft text**' },
      ]}
      isWaitingForResponse={false}
      scrollContainerRef={{ current: null }}
      onScroll={() => {}}
    />,
  );

  assert.ok(html.includes('<strong>AI response</strong>'));
  assert.ok(html.includes('**User text**\nnext line</span>'));
  assert.ok(html.includes('**Draft text**</span>'));
  assert.equal((html.match(/class="markdown-message"/g) || []).length, 1);
});
