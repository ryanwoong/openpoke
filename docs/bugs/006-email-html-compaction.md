# BUG-006: Raw email HTML triggers excessive context compaction

Discovered: 2026-10-26. Status: implemented; regression tests and offline replay pass. Fresh live Gmail/model verification pending.

## Symptoms and reproduction

A retailer order search generated many context-compaction calls despite returning only 41 emails. Reproduce with a Gmail response whose top-level `payload` has `mimeType: "text/html"` and a base64url-encoded HTML body, or whose connector `textBody` contains HTML. Before the fix, `clean_email_content()` returned markup and CSS inside `clean_text`.

## Evidence and cause

The archived result was 585,977 bytes, about 204,951 tokens under the application's estimator. Eight rewards/cart emails contributed about 481 KB of body content. Several `clean_text` fields still contained complete HTML documents, styles, and tracking links. With the inspected 131,072-token model window, the normal input budget was 98,304 tokens; the source split into 38 chunks at the compactor's 18 KB chunk limit. These are an offline reconstruction using the inspected settings, not a persisted count of actual model calls or retries.

The extractor recognized HTML only in immediate MIME children or `htmlBody`. It decoded the top-level body as plain text without checking its MIME type, did not traverse nested multipart bodies, and applied only whitespace cleanup to `textBody`. Original raw MIME envelopes were not archived, so the exact input field responsible for each affected email is unconfirmed; the extraction defects are reproduced by synthetic fixtures.

## Fix

[Email processing](../../server/services/gmail/processing.py) now traverses top-level and nested MIME parts, prefers HTML body content, and selects plain text by MIME type. It skips named attachments and parts marked `Content-Disposition: attachment`, supports unpadded base64url, and falls back to connector body fields when parts are empty or malformed.

Recognizable HTML in plain-text or untyped fields uses the existing HTML cleaner before reaching the model. Ordinary text, comparisons, and angle-bracket email addresses remain plain text. Cleaning removes markup and styling without truncating the readable body; order details and email metadata remain available. Context budgets and compaction limits are unchanged.

## Verification and limits

[Regression tests](../../server/tests/test_gmail_processing.py) cover root/nested MIME bodies, misplaced HTML, invalid/empty parts, connector fallbacks, attachment exclusion, Unicode, order details, identifiers, pagination, and metadata. A large synthetic HTML result fits the model budget after cleaning and does not invoke model summarization.

The backend suite completed 162 tests with one opt-in live-model evaluation skipped; all remaining tests passed. Project lint passed. Markdown was formatted with Prettier; Python used Ruff because the configured Prettier installation has no working Python parser.

Offline replay through the fixed cleaner reduced the archived 41-email result to 54,404 bytes, about 19,147 estimated tokens, preserving all message IDs. This replay reused saved body strings rather than making a fresh Gmail request. Existing archived artifacts and already-compacted in-flight requests are not rewritten; rerun the search after the updated backend is loaded.

HTML detection in mislabeled fields is heuristic. This fix does not extract PDF attachment contents or establish whether the order search found every purchase. Large legitimate text results can still require compaction.
