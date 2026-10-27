import asyncio
import importlib
import json
import ssl
import unittest
from unittest.mock import AsyncMock, Mock, patch

import httpx

from server.openrouter_client.client import OpenRouterError, request_chat_completion
from server.model_settings import CompletionTarget

search = importlib.import_module("server.agents.execution_agent.tasks.search_email.tool")


class EmailSearchFailureTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        provider = patch("server.openrouter_client.client.completion_target", return_value=CompletionTarget("OpenRouter", "test/model", None, None))
        provider.start()
        self.addCleanup(provider.stop)

    async def test_inbox_check_is_a_bounded_metadata_preview_without_model_calls(self):
        raw = {"data": {"messages": [{"messageId": str(i), "subject": "x" * 1000, "sender": "sender@example.com", "snippet": "s" * 1000, "messageText": "body" * 100000} for i in range(12)], "nextPageToken": "next-page"}}
        with patch.object(search, "get_active_gmail_user_id", return_value="test-user"), patch.object(search, "execute_gmail_tool", return_value=raw) as fetch, patch("server.openrouter_client.request_chat_completion", AsyncMock()) as model:
            result = await search.task_email_search("is:unread newer_than:7d")
        fetch.assert_called_once()
        arguments = fetch.call_args.kwargs["arguments"]
        self.assertEqual(arguments["max_results"], 10)
        self.assertEqual(arguments["format"], "metadata")
        self.assertFalse(arguments["include_payload"])
        self.assertNotIn("page_token", arguments)
        self.assertEqual(result["returned_count"], 10)
        self.assertTrue(result["has_more"])
        self.assertIsNone(result["total_count"])
        self.assertEqual(len(result["messages"][0]["snippet"]), 280)
        self.assertNotIn("bodybody", json.dumps(result))
        self.assertLess(len(json.dumps(result)), 12000)
        model.assert_not_awaited()

    async def test_explicit_preview_supports_native_gmail_headers_and_missing_snippets(self):
        raw = {
            "data": {
                "messages": [
                    {
                        "id": "reply",
                        "payload": {
                            "headers": [
                                {"name": "From", "value": "sender@example.com"},
                                {"name": "Subject", "value": "Re: Hello"},
                                {"name": "Date", "value": "Sat, 24 Oct 2026 06:20:48 +0000"},
                            ]
                        },
                    }
                ]
            }
        }
        with patch.object(search, "get_active_gmail_user_id", return_value="test-user"), patch.object(search, "execute_gmail_tool", return_value=raw):
            result = await search.task_email_search("in:inbox from:sender@example.com", mode="preview")
        message = result["messages"][0]
        self.assertEqual(message["sender"], "sender@example.com")
        self.assertEqual(message["subject"], "Re: Hello")
        self.assertEqual(message["timestamp"], "2026-10-24T06:20:48+00:00")
        self.assertEqual(message["snippet"], "")
        self.assertFalse(result["has_more"])
        self.assertIsNone(result["total_count"])

    async def test_preview_errors_and_malformed_responses_are_not_empty_inboxes(self):
        for raw in [{"successful": False, "error": "413 too large"}, {"data": {}}, {"data": {"messages": [{}]}}]:
            with patch.object(search, "get_active_gmail_user_id", return_value="test-user"), patch.object(search, "execute_gmail_tool", return_value=raw):
                result = await search.task_email_search("is:unread")
            self.assertIn("error", result)
            self.assertNotIn("messages", result)

    async def test_empty_preview_is_successful_and_invalid_modes_do_not_fetch(self):
        with patch.object(search, "get_active_gmail_user_id", return_value="test-user"), patch.object(search, "execute_gmail_tool", return_value={"data": {"messages": []}}) as fetch:
            result = await search.task_email_search("in:inbox is:unread")
            self.assertEqual(result["messages"], [])
            self.assertEqual(result["returned_count"], 0)
            self.assertFalse(result["has_more"])
            fetch.reset_mock()
            self.assertIn("error", await search.task_email_search("is:unread", mode="unknown"))
            fetch.assert_not_called()

    async def test_raw_tls_failure_is_identified_as_openrouter_failure(self):
        upstream = AsyncMock()
        upstream.__aenter__.return_value = upstream
        upstream.post.side_effect = ssl.SSLError("SSLV3_ALERT_BAD_RECORD_MAC")
        with patch("server.openrouter_client.client.httpx.AsyncClient", return_value=upstream):
            with self.assertRaisesRegex(OpenRouterError, "OpenRouter request failed.*SSLV3_ALERT_BAD_RECORD_MAC"):
                await request_chat_completion(model="test/model", messages=[], api_key="test-key")
        self.assertEqual(upstream.post.await_count, 2)

    async def test_transient_tls_failure_is_retried_with_a_fresh_client(self):
        failed = AsyncMock()
        failed.__aenter__.return_value = failed
        failed.post.side_effect = ssl.SSLError("SSLV3_ALERT_BAD_RECORD_MAC")
        recovered = AsyncMock()
        recovered.__aenter__.return_value = recovered
        recovered.post.return_value = httpx.Response(200, json={"choices": []}, request=httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions"))
        with patch("server.openrouter_client.client.httpx.AsyncClient", side_effect=[failed, recovered]):
            response = await request_chat_completion(model="test/model", messages=[], api_key="test-key")
        self.assertEqual(response, {"choices": []})
        self.assertEqual(failed.post.await_count, 1)
        self.assertEqual(recovered.post.await_count, 1)

    async def test_failed_gmail_search_is_not_no_matches(self):
        with patch.object(search, "get_active_gmail_user_id", return_value="mailbox"), patch.object(search, "execute_gmail_tool", side_effect=RuntimeError("Gmail unavailable")):
            result = await search.task_email_search("from:parts@example.com")
        self.assertIn("Gmail unavailable", result["error"])
        self.assertNotIn("messages", result)

    async def test_successful_empty_query_is_explicitly_headers_only(self):
        with patch.object(search, "get_active_gmail_user_id", return_value="mailbox"), patch.object(search, "execute_gmail_tool", return_value={"data": {"messages": []}}):
            result = await search.task_email_search("from:parts@example.com")
        self.assertEqual(result["messages"], [])
        self.assertTrue(result["search_complete"])
        self.assertEqual(result["body_scope"], "headers")

    async def test_discovery_pagination_is_explicit_and_deduplicated(self):
        raw = {"data": {"messages": [{"id": "one"}, {"id": "one"}], "nextPageToken": "next"}}
        with patch.object(search, "get_active_gmail_user_id", return_value="mailbox"), patch.object(search, "execute_gmail_tool", return_value=raw) as fetch:
            result = await search.task_email_search("from:parts@example.com", mode="all")
            self.assertFalse(result["search_complete"])
            self.assertEqual(result["next_page_token"], "next")
            self.assertEqual(len(result["messages"]), 1)
            raw["data"]["nextPageToken"] = ""
            result = await search.task_email_search("from:parts@example.com", mode="all", page_token="next")
        self.assertEqual(fetch.call_args.kwargs["arguments"]["page_token"], "next")
        self.assertTrue(result["search_complete"])
        self.assertFalse(fetch.call_args.kwargs["arguments"]["include_payload"])

    async def test_repeated_token_and_later_page_failure_are_errors(self):
        with patch.object(search, "get_active_gmail_user_id", return_value="mailbox"), patch.object(search, "execute_gmail_tool", return_value={"data": {"messages": [], "nextPageToken": "next"}}):
            result = await search.task_email_search("from:parts@example.com", mode="all", page_token="next")
            self.assertIn("error", result)
        with patch.object(search, "get_active_gmail_user_id", return_value="mailbox"), patch.object(search, "execute_gmail_tool", side_effect=RuntimeError("Second page failed")):
            result = await search.task_email_search("from:parts@example.com", mode="all", page_token="next")
            self.assertIn("Second page failed", result["error"])
            self.assertNotIn("messages", result)

    async def test_no_gmail_connection_does_not_invoke_provider(self):
        with patch.object(search, "get_active_gmail_user_id", return_value=None), patch.object(search, "execute_gmail_tool") as fetch:
            result = await search.task_email_search("Subaru parts")
        self.assertIn("not connected", result["error"])
        fetch.assert_not_called()

    async def test_unsuccessful_connector_response_is_not_an_empty_search(self):
        with patch.object(search, "get_active_gmail_user_id", return_value="mailbox"), patch.object(search, "execute_gmail_tool", return_value={"successful": False, "error": "Service unavailable"}):
            result = await search.task_email_search("Subaru parts")
        self.assertIn("error", result)
        self.assertNotIn("messages", result)


if __name__ == "__main__":
    unittest.main()
