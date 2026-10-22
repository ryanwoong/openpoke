import unittest
from unittest.mock import AsyncMock, Mock, patch

from server.agents.interaction_agent.runtime import InteractionAgentRuntime
from server.agents.interaction_agent.tools import ToolResult


def response(*calls, content=""):
    return {"choices": [{"message": {"content": content, "tool_calls": [{"id": f"call_{index}", "function": {"name": name, "arguments": arguments}} for index, (name, arguments) in enumerate(calls)]}}]}


class InteractionHandoffTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.runtime = InteractionAgentRuntime.__new__(InteractionAgentRuntime)
        self.runtime.conversation_log = Mock()
        self.runtime._load_conversation_transcript = Mock(return_value="")
        self.delivered = []

        def execute(call):
            if call.name == "send_message_to_user":
                message = call.arguments["message"]
                self.delivered.append(message)
                return ToolResult(success=True, user_message=message, recorded_reply=True)
            return ToolResult(success=True)

        self.runtime._execute_tool = Mock(side_effect=execute)

    async def execute(self):
        with patch("server.agents.interaction_agent.runtime.build_system_prompt", return_value="system"), patch("server.agents.interaction_agent.runtime.prepare_message_with_history", return_value=[]):
            return await self.runtime.execute("Check today's email")

    async def test_acknowledgement_then_delegation_does_not_generate_a_premature_answer(self):
        self.runtime._make_llm_call = AsyncMock(side_effect=[
            response(("send_message_to_user", {"message": "Checking your email."})),
            response(("send_message_to_agent", {"agent_name": "Email Checker"}), content="Invented email summary"),
            response(("send_message_to_user", {"message": "Six invented emails"})),
        ])
        result = await self.execute()
        self.assertTrue(result.success)
        self.assertEqual(result.response, "Checking your email.")
        self.assertEqual(self.delivered, ["Checking your email."])
        self.assertEqual(self.runtime._make_llm_call.await_count, 2)
        self.runtime.conversation_log.record_reply.assert_not_called()

    async def test_wait_is_silent_and_stops_remaining_tools_and_model_calls(self):
        self.runtime._make_llm_call = AsyncMock(return_value=response(("wait", {"reason": "Work pending"}), ("send_message_to_user", {"message": "Invented answer"}), content="Should not be displayed"))
        result = await self.execute()
        self.assertTrue(result.success)
        self.assertEqual(result.response, "")
        self.assertEqual(self.delivered, [])
        self.assertEqual(self.runtime._execute_tool.call_count, 1)
        self.runtime._make_llm_call.assert_awaited_once()
        self.runtime.conversation_log.record_reply.assert_not_called()

    async def test_parallel_delegations_are_preserved_but_later_user_reply_is_skipped(self):
        self.runtime._make_llm_call = AsyncMock(return_value=response(("send_message_to_agent", {"agent_name": "First"}), ("send_message_to_user", {"message": "Invented answer"}), ("send_message_to_agent", {"agent_name": "Second"}), content="Unverified result"))
        result = await self.execute()
        self.assertEqual(result.execution_agents_used, 2)
        self.assertEqual(result.response, "")
        self.assertEqual(self.runtime._execute_tool.call_count, 2)
        self.runtime._make_llm_call.assert_awaited_once()
        self.assertEqual(self.delivered, [])

    async def test_failed_delegation_can_be_recovered_in_same_turn(self):
        self.runtime._execute_tool.side_effect = [ToolResult(success=False, payload={"error": "Could not submit"})]
        self.runtime._make_llm_call = AsyncMock(side_effect=[response(("send_message_to_agent", {"agent_name": "Email Checker"})), response(content="The search could not start.")])
        result = await self.execute()
        self.assertTrue(result.success)
        self.assertEqual(result.execution_agents_used, 0)
        self.assertEqual(result.response, "The search could not start.")
        self.runtime.conversation_log.record_reply.assert_called_once_with(result.response)

    async def test_agent_result_can_be_delivered_after_handoff(self):
        self.runtime._make_llm_call = AsyncMock(side_effect=[response(("send_message_to_user", {"message": "Seven verified emails."})), response(("wait", {"reason": "Results delivered"}))])
        with patch("server.agents.interaction_agent.runtime.build_system_prompt", return_value="system"), patch("server.agents.interaction_agent.runtime.prepare_message_with_history", return_value=[]):
            result = await self.runtime.handle_agent_message("[SUCCESS] Email Checker: Seven verified emails.")
        self.assertTrue(result.success)
        self.assertEqual(result.response, "Seven verified emails.")
        self.assertEqual(self.delivered, ["Seven verified emails."])
        self.runtime.conversation_log.record_reply.assert_not_called()


if __name__ == "__main__":
    unittest.main()
