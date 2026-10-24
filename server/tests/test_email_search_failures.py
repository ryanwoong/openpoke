import asyncio
import importlib
import threading
import json
import ssl
import unittest
from unittest.mock import AsyncMock, Mock, patch

import httpx

from server.openrouter_client.client import OpenRouterError, request_chat_completion

search = importlib.import_module("server.agents.execution_agent.tasks.search_email.tool")


def completion(tool_calls=None):
    return {"choices": [{"message": {"content": "Done", "tool_calls": tool_calls or []}}]}


def tool_call(name, arguments):
    return {"id": "call_test", "function": {"name": name, "arguments": json.dumps(arguments)}}


class EmailSearchFailureTests(unittest.IsolatedAsyncioTestCase):
    async def run_search(self):
        return await search._run_email_search(search_query="today", composio_user_id="user_test", model="test/model", api_key="test-key")

    async def test_model_finishing_without_search_is_not_an_empty_result(self):
        with patch.object(search, "request_chat_completion", AsyncMock(return_value=completion())):
            with self.assertRaisesRegex(RuntimeError, "without running a Gmail search"):
                await self.run_search()

    async def test_completion_tool_without_search_is_not_an_empty_result(self):
        response = completion([tool_call(search.COMPLETE_TOOL_NAME, {"message_ids": []})])
        with patch.object(search, "request_chat_completion", AsyncMock(return_value=response)):
            with self.assertRaisesRegex(RuntimeError, "No Gmail search completed successfully"):
                await self.run_search()

    async def test_failed_gmail_search_cannot_be_reported_as_no_matches(self):
        responses = [
            completion([tool_call(search.SEARCH_TOOL_NAME, {"query": "newer_than:1d"})]),
            completion([tool_call(search.COMPLETE_TOOL_NAME, {"message_ids": []})]),
        ]
        with patch.object(search, "request_chat_completion", AsyncMock(side_effect=responses)), patch.object(
            search, "execute_gmail_tool", side_effect=RuntimeError("Gmail API unavailable")
        ), patch.object(search._LOG_STORE, "record_action"):
            with self.assertRaisesRegex(RuntimeError, "No Gmail search completed successfully.*Gmail API unavailable"):
                await self.run_search()

    async def test_successful_empty_gmail_search_remains_an_empty_result(self):
        responses = [
            completion([tool_call(search.SEARCH_TOOL_NAME, {"query": "newer_than:1d"})]),
            completion([tool_call(search.COMPLETE_TOOL_NAME, {"message_ids": []})]),
        ]
        with patch.object(search, "request_chat_completion", AsyncMock(side_effect=responses)), patch.object(
            search, "execute_gmail_tool", return_value={"data": {"messages": []}}
        ), patch.object(search, "parse_gmail_fetch_response", return_value=([], None)), patch.object(search._LOG_STORE, "record_action"):
            self.assertEqual(await self.run_search(), [])

    def email(self, identifier):
        return search.GmailSearchEmail(
            id=identifier,
            query="Chipotle after:2026/01/01",
            subject="Order receipt",
            sender="orders@example.com",
            recipient="user@example.com",
            timestamp="2026-10-23T12:00:00Z",
            clean_text="Order confirmed",
        )

    async def test_gmail_query_skips_nested_models_and_follows_pages_with_deduplication(self):
        first, second = self.email("first"), self.email("second")
        fetch = Mock(return_value={"data": {}})
        with patch.object(search, "_validate_gmail_connection", return_value="test-user"), patch.object(
            search, "request_chat_completion", AsyncMock()
        ) as model, patch.object(search, "execute_gmail_tool", fetch), patch.object(
            search, "parse_gmail_fetch_response", side_effect=[([first], "next-page"), ([first, second], None)]
        ), patch.object(
            search, "_processed_to_schema", side_effect=lambda email: email
        ), patch.object(
            search._LOG_STORE, "record_action"
        ):
            result = await search.task_email_search("Chipotle after:2026/01/01 before:2027/01/01")
        self.assertEqual([email["id"] for email in result], ["first", "second"])
        model.assert_not_awaited()
        self.assertEqual(fetch.call_count, 2)
        self.assertNotIn("page_token", fetch.call_args_list[0].kwargs["arguments"])
        self.assertEqual(fetch.call_args_list[1].kwargs["arguments"]["page_token"], "next-page")
        self.assertEqual(fetch.call_args_list[0].kwargs["arguments"]["max_results"], 100)

    async def test_later_page_failure_does_not_present_a_partial_count(self):
        with patch.object(search, "_validate_gmail_connection", return_value="test-user"), patch.object(
            search, "execute_gmail_tool", side_effect=[{"data": {}}, {"successful": False, "error": "Second page failed"}]
        ), patch.object(search, "parse_gmail_fetch_response", return_value=([self.email("first")], "next-page")), patch.object(
            search, "_processed_to_schema", side_effect=lambda email: email
        ), patch.object(
            search._LOG_STORE, "record_action"
        ):
            result = await search.task_email_search("Chipotle after:2026/01/01")
        self.assertIn("Second page failed", result["error"])
        self.assertNotIn("emails", result)

    async def test_repeated_page_tokens_and_page_limit_are_explicit_failures(self):
        for tokens in (("repeat", "repeat"), ("first", "second")):
            with patch.object(search, "MAX_SEARCH_PAGES", 2), patch.object(search, "_validate_gmail_connection", return_value="test-user"), patch.object(
                search, "execute_gmail_tool", return_value={}
            ), patch.object(search, "parse_gmail_fetch_response", side_effect=[([], token) for token in tokens]), patch.object(
                search._LOG_STORE, "record_action"
            ):
                result = await search.task_email_search("Chipotle after:2026/01/01")
            self.assertIn("error", result)

    async def test_gmail_does_not_block_the_event_loop(self):
        started, release = threading.Event(), threading.Event()

        def fetch(*args, **kwargs):
            started.set()
            release.wait(timeout=1)
            return {"data": {"messages": []}}

        with patch.object(search, "execute_gmail_tool", side_effect=fetch), patch.object(
            search, "parse_gmail_fetch_response", return_value=([], None)
        ), patch.object(search._LOG_STORE, "record_action"):
            task = asyncio.create_task(search._perform_search(arguments={"query": "Chipotle"}, queries=[], emails={}, composio_user_id="user"))
            try:
                await asyncio.wait_for(asyncio.to_thread(started.wait), timeout=1)
                self.assertFalse(task.done())
            finally:
                release.set()
                await task

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

    async def test_unsuccessful_composio_response_is_not_an_empty_search(self):
        responses = [
            completion([tool_call(search.SEARCH_TOOL_NAME, {"query": "newer_than:1d"})]),
            completion([tool_call(search.COMPLETE_TOOL_NAME, {"message_ids": []})]),
        ]
        with patch.object(search, "request_chat_completion", AsyncMock(side_effect=responses)), patch.object(
            search, "execute_gmail_tool", return_value={"successful": False, "error": "Service unavailable"}
        ), patch.object(search._LOG_STORE, "record_action"):
            with self.assertRaisesRegex(RuntimeError, "Gmail API search failed: Service unavailable"):
                await self.run_search()


if __name__ == "__main__":
    unittest.main()
