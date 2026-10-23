import asyncio
import unittest
from unittest.mock import AsyncMock, Mock, patch

from server.models.chat import ChatMessage, ChatRequest
from server.services.conversation.chat_handler import handle_chat_request
from server.agents.interaction_agent.runtime import InteractionAgentRuntime
from server.execution_trace import trace_store


class ChatRequestStatusTests(unittest.IsolatedAsyncioTestCase):
    async def test_submission_preserves_the_client_request_id(self):
        runtime = Mock(execute=AsyncMock())
        with patch("server.services.conversation.chat_handler.InteractionAgentRuntime", return_value=runtime):
            response = await handle_chat_request(ChatRequest(messages=[ChatMessage(role="user", content="Check email")], request_id="client-request"))
            await asyncio.sleep(0)
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.headers["X-Request-ID"], "client-request")
        runtime.execute.assert_awaited_once_with(user_message="Check email", request_id="client-request")

    async def test_legacy_clients_receive_a_generated_request_id(self):
        runtime = Mock(execute=AsyncMock())
        with patch("server.services.conversation.chat_handler.InteractionAgentRuntime", return_value=runtime):
            response = await handle_chat_request(ChatRequest(messages=[ChatMessage(role="user", content="Check email")]))
            await asyncio.sleep(0)
        request_id = response.headers["X-Request-ID"]
        self.assertTrue(request_id)
        runtime.execute.assert_awaited_once_with(user_message="Check email", request_id=request_id)

    async def test_request_id_is_attached_to_the_interaction_trace(self):
        trace_store.clear()
        runtime = InteractionAgentRuntime.__new__(InteractionAgentRuntime)
        runtime.conversation_log = Mock()
        runtime._load_conversation_transcript = Mock(return_value="")
        runtime._make_llm_call = AsyncMock(return_value={"choices": [{"message": {"content": "Done"}}]})
        with patch("server.agents.interaction_agent.runtime.build_system_prompt", return_value="system"), patch("server.agents.interaction_agent.runtime.prepare_message_with_history", return_value=[]):
            result = await runtime.execute("Check email", request_id="correlated-request")
        self.assertTrue(result.success)
        span = trace_store.snapshot()[0]
        self.assertEqual(span["input"]["request_id"], "correlated-request")
        self.assertEqual(span["status"], "completed")
