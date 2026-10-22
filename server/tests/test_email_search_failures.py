import importlib
import json
import ssl
import unittest
from unittest.mock import AsyncMock, patch

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
        responses = [completion([tool_call(search.SEARCH_TOOL_NAME, {"query": "newer_than:1d"})]), completion([tool_call(search.COMPLETE_TOOL_NAME, {"message_ids": []})])]
        with patch.object(search, "request_chat_completion", AsyncMock(side_effect=responses)), patch.object(search, "execute_gmail_tool", side_effect=RuntimeError("Gmail API unavailable")), patch.object(search._LOG_STORE, "record_action"):
            with self.assertRaisesRegex(RuntimeError, "No Gmail search completed successfully.*Gmail API unavailable"):
                await self.run_search()

    async def test_successful_empty_gmail_search_remains_an_empty_result(self):
        responses = [completion([tool_call(search.SEARCH_TOOL_NAME, {"query": "newer_than:1d"})]), completion([tool_call(search.COMPLETE_TOOL_NAME, {"message_ids": []})])]
        with patch.object(search, "request_chat_completion", AsyncMock(side_effect=responses)), patch.object(search, "execute_gmail_tool", return_value={"data": {"messages": []}}), patch.object(search, "parse_gmail_fetch_response", return_value=([], None)), patch.object(search._LOG_STORE, "record_action"):
            self.assertEqual(await self.run_search(), [])

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
        responses = [completion([tool_call(search.SEARCH_TOOL_NAME, {"query": "newer_than:1d"})]), completion([tool_call(search.COMPLETE_TOOL_NAME, {"message_ids": []})])]
        with patch.object(search, "request_chat_completion", AsyncMock(side_effect=responses)), patch.object(search, "execute_gmail_tool", return_value={"successful": False, "error": "Service unavailable"}), patch.object(search._LOG_STORE, "record_action"):
            with self.assertRaisesRegex(RuntimeError, "Gmail API search failed: Service unavailable"):
                await self.run_search()


if __name__ == "__main__":
    unittest.main()
