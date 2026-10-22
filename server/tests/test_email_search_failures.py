import ssl
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from server.openrouter_client.client import OpenRouterError, request_chat_completion


class EmailSearchFailureTests(unittest.IsolatedAsyncioTestCase):
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


if __name__ == "__main__":
    unittest.main()
