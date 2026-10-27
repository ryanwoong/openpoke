import base64
import json
import tempfile
import unittest
from dataclasses import asdict
from datetime import timezone
from pathlib import Path
from unittest.mock import AsyncMock

from server.openrouter_client.context import ContextCompactor, estimate_tokens
from server.services.gmail.processing import EmailTextCleaner, parse_gmail_fetch_response


def mime_part(content, mime_type="text/html", **fields):
    return {"mimeType": mime_type, "body": {"data": base64.urlsafe_b64encode(content.encode()).decode().rstrip("=")}, **fields}


ORDER_HTML = "<html><head><style>.banner { padding: 20px; }</style><script>tracking()</script></head><body><h1>Order #12345</h1><p>Short Shifter — SKU ABC-123, quantity 2</p><p>Total: CAD $199.50</p><p>Tracking: TRACK-6789</p></body></html>"


class EmailBodyCleaningTests(unittest.TestCase):
    def setUp(self):
        self.cleaner = EmailTextCleaner()

    def assert_order(self, text):
        for value in ("Order #12345", "Short Shifter", "ABC-123", "quantity 2", "CAD $199.50", "TRACK-6789"):
            self.assertIn(value, text)
        for value in ("<html", "<p>", "padding:", "tracking()"):
            self.assertNotIn(value, text)

    def test_top_level_html_payload_is_cleaned(self):
        self.assert_order(self.cleaner.clean_email_content({"payload": mime_part(ORDER_HTML, "Text/HTML; charset=UTF-8")}))

    def test_nested_multipart_prefers_html_and_ignores_attachments(self):
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                mime_part("<html><body>Attachment decoy</body></html>", filename="invoice.html"),
                {"mimeType": "multipart/alternative", "parts": [mime_part("Plain fallback", "text/plain"), {"mimeType": "multipart/related", "parts": [mime_part(ORDER_HTML), mime_part("binary", "image/png")]}]},
                mime_part("%PDF-1.7", "application/pdf", filename="invoice.pdf"),
            ],
        }
        text = self.cleaner.clean_email_content({"payload": payload})
        self.assert_order(text)
        self.assertNotIn("Attachment decoy", text)
        self.assertNotIn("Plain fallback", text)

    def test_nested_plaintext_preserves_email_addresses_and_comparisons(self):
        text = "Order #12345\nContact <parts@example.com>, <a@example.com>, <div@example.com>\n2 < 5 and 8 > 3\nTotal: £25.00"
        payload = {"mimeType": "multipart/mixed", "parts": [{"mimeType": "multipart/alternative", "parts": [mime_part(text, "text/plain")]}]}
        self.assertEqual(self.cleaner.clean_email_content({"payload": payload}), text)

    def test_html_in_plain_or_untyped_fields_is_cleaned(self):
        untyped = mime_part(ORDER_HTML)
        del untyped["mimeType"]
        for message in ({"messageText": ORDER_HTML}, {"textBody": ORDER_HTML}, {"payload": mime_part(ORDER_HTML, "text/plain")}, {"payload": untyped}, {"htmlBody": ORDER_HTML}):
            with self.subTest(fields=list(message)):
                self.assert_order(self.cleaner.clean_email_content(message))

    def test_invalid_or_empty_parts_do_not_hide_valid_siblings(self):
        payload = {
            "mimeType": "multipart/alternative",
            "parts": [None, {"mimeType": "text/html", "body": {"data": "%%%"}}, mime_part(" \n", "text/html"), {"mimeType": "text/html", "body": "invalid"}, mime_part(ORDER_HTML)],
        }
        self.assert_order(self.cleaner.clean_email_content({"payload": payload}))

    def test_invalid_base64_falls_back_to_connector_fields(self):
        payload = {"mimeType": "text/html", "body": {"data": "not base64☃"}}
        self.assert_order(self.cleaner.clean_email_content({"payload": payload, "htmlBody": ORDER_HTML}))
        self.assertEqual(self.cleaner.clean_email_content({"payload": payload, "textBody": "Plain fallback"}), "Plain fallback")

    def test_non_text_payload_and_attachment_disposition_are_not_message_bodies(self):
        for payload in (mime_part("binary", "application/octet-stream"), mime_part(ORDER_HTML, headers=[{"name": "Content-Disposition", "value": "attachment; filename=invoice.html"}]), mime_part("Attached notes", "text/plain", filename="notes.txt")):
            with self.subTest(mime_type=payload["mimeType"]):
                self.assertEqual(self.cleaner.clean_email_content({"payload": payload}), "")

    def test_connector_fallbacks_and_malformed_payloads(self):
        for payload in (None, [], "invalid", {"parts": "invalid"}, {"body": {"data": None}}):
            with self.subTest(payload=payload):
                self.assertEqual(self.cleaner.clean_email_content({"payload": payload, "htmlBody": " ", "textBody": "Order #12345"}), "Order #12345")
        self.assertEqual(self.cleaner.clean_email_content({"textBody": 42, "htmlBody": []}), "")

    def test_normalization_preserves_identifiers_metadata_and_attachments(self):
        raw = {
            "messageId": "email-123",
            "threadId": "thread-456",
            "subject": "Order #12345 confirmed",
            "sender": "parts@example.com",
            "to": "buyer@example.com",
            "messageTimestamp": "2026-09-01T12:00:00Z",
            "labelIds": ["INBOX"],
            "attachmentList": [{"filename": "invoice.pdf"}],
            "payload": mime_part(ORDER_HTML),
        }
        emails, next_page = parse_gmail_fetch_response({"data": {"messages": [raw], "nextPageToken": "next"}}, query="orders")
        email = emails[0]
        self.assert_order(email.clean_text)
        self.assertEqual((email.id, email.thread_id, email.subject, email.sender, email.recipient, email.query), ("email-123", "thread-456", raw["subject"], raw["sender"], raw["to"], "orders"))
        self.assertEqual(email.timestamp.astimezone(timezone.utc).isoformat(), "2026-09-01T12:00:00+00:00")
        self.assertEqual((email.label_ids, email.attachment_count, email.attachment_filenames, email.has_attachments, next_page), (["INBOX"], 1, ["invoice.pdf"], True, "next"))


class EmailCompactionRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_html_bloat_does_not_force_model_summarization(self):
        bloated = ORDER_HTML.replace("</style>", ".marketing { padding: 20px; font-size: 14px; }" * 1600 + "</style>")
        raw = [{"messageId": f"email-{index}", "messageTimestamp": "2026-09-01T12:00:00Z", "subject": "Order confirmation", "payload": mime_part(bloated)} for index in range(8)]
        original = [{"id": message["messageId"], "clean_text": bloated} for message in raw]
        self.assertGreater(estimate_tokens(original), 98304)
        emails, _ = parse_gmail_fetch_response({"data": {"messages": raw}}, query="orders")
        result = [asdict(email) for email in emails]
        payload = {"messages": [{"role": "user", "content": "Find all my orders"}, {"role": "tool", "content": json.dumps({"tool": "task_email_search", "status": "success", "result": result}, default=str)}]}
        summarize = AsyncMock(side_effect=AssertionError("Cleaned message bodies should fit without model summarization"))
        with tempfile.TemporaryDirectory() as directory:
            prepared, compacted = await ContextCompactor(Path(directory)).prepare(payload, 98304, summarize, "test")
        self.assertFalse(compacted)
        self.assertEqual(prepared, payload)
        summarize.assert_not_awaited()
        self.assertEqual(len(result), len(raw))
        for email in result:
            self.assertIn("Order #12345", email["clean_text"])
            self.assertIn("CAD $199.50", email["clean_text"])


if __name__ == "__main__":
    unittest.main()
