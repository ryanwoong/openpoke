import asyncio
import tempfile
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, Mock, patch

from server.agents.interaction_agent.runtime import InteractionAgentRuntime
from server.agents.interaction_agent.tools import ToolResult


def response(*calls, content=""):
    return {
        "choices": [
            {
                "message": {
                    "content": content,
                    "tool_calls": [
                        {"id": f"call_{index}", "function": {"name": name, "arguments": arguments}} for index, (name, arguments) in enumerate(calls)
                    ],
                }
            }
        ]
    }


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
        with patch("server.agents.interaction_agent.runtime.build_system_prompt", return_value="system"), patch(
            "server.agents.interaction_agent.runtime.prepare_message_with_history", return_value=[]
        ):
            return await self.runtime.execute("Check today's email")

    async def test_acknowledgement_then_delegation_does_not_generate_a_premature_answer(self):
        self.runtime._make_llm_call = AsyncMock(
            side_effect=[
                response(("send_message_to_user", {"message": "Checking your email."})),
                response(("send_message_to_agent", {"agent_name": "Email Checker"}), content="Invented email summary"),
                response(("send_message_to_user", {"message": "Six invented emails"})),
            ]
        )
        result = await self.execute()
        self.assertTrue(result.success)
        self.assertEqual(result.response, "Checking your email.")
        self.assertEqual(self.delivered, ["Checking your email."])
        self.assertEqual(self.runtime._make_llm_call.await_count, 2)
        self.runtime.conversation_log.record_reply.assert_not_called()

    async def test_wait_is_silent_and_stops_remaining_tools_and_model_calls(self):
        self.runtime._make_llm_call = AsyncMock(
            return_value=response(
                ("wait", {"reason": "Work pending"}), ("send_message_to_user", {"message": "Invented answer"}), content="Should not be displayed"
            )
        )
        result = await self.execute()
        self.assertTrue(result.success)
        self.assertEqual(result.response, "")
        self.assertEqual(self.delivered, [])
        self.assertEqual(self.runtime._execute_tool.call_count, 1)
        self.runtime._make_llm_call.assert_awaited_once()
        self.runtime.conversation_log.record_reply.assert_not_called()

    async def test_parallel_delegations_are_preserved_but_later_user_reply_is_skipped(self):
        self.runtime._make_llm_call = AsyncMock(
            return_value=response(
                ("send_message_to_agent", {"agent_name": "First"}),
                ("send_message_to_user", {"message": "Invented answer"}),
                ("send_message_to_agent", {"agent_name": "Second"}),
                content="Unverified result",
            )
        )
        result = await self.execute()
        self.assertEqual(result.execution_agents_used, 2)
        self.assertEqual(result.response, "")
        self.assertEqual(self.runtime._execute_tool.call_count, 2)
        self.runtime._make_llm_call.assert_awaited_once()
        self.assertEqual(self.delivered, [])

    async def test_failed_delegation_can_be_recovered_in_same_turn(self):
        self.runtime._execute_tool.side_effect = [ToolResult(success=False, payload={"error": "Could not submit"})]
        self.runtime._make_llm_call = AsyncMock(
            side_effect=[response(("send_message_to_agent", {"agent_name": "Email Checker"})), response(content="The search could not start.")]
        )
        result = await self.execute()
        self.assertTrue(result.success)
        self.assertEqual(result.execution_agents_used, 0)
        self.assertEqual(result.response, "The search could not start.")
        self.runtime.conversation_log.record_reply.assert_called_once_with(result.response)

    async def test_scheduled_delivery_does_not_replace_an_inflight_user_request(self):
        from server.services.conversation.log import ConversationLog
        from server.services.trigger_scheduler import TriggerScheduler

        started = asyncio.Event()
        release = asyncio.Event()
        with tempfile.TemporaryDirectory() as directory, patch("server.services.conversation.log._resolve_working_memory_log", return_value=Mock()), patch(
            "server.services.conversation.log.get_settings", return_value=Mock(summarization_enabled=False)
        ):
            log = ConversationLog(Path(directory) / "conversation.log")
            self.runtime.conversation_log = log

            async def complete(*args):
                started.set()
                await release.wait()
                return response(content="You placed three Chipotle orders.")

            self.runtime._make_llm_call = AsyncMock(side_effect=complete)
            with patch("server.agents.interaction_agent.runtime.build_system_prompt", return_value="system"), patch(
                "server.agents.interaction_agent.runtime.prepare_message_with_history", return_value=[]
            ):
                task = asyncio.create_task(self.runtime.execute("How many Chipotle orders this year?"))
                await started.wait()
                try:
                    with patch("server.services.trigger_scheduler.get_conversation_log", return_value=log):
                        await TriggerScheduler()._deliver_action({"kind": "reminder", "message": "Take a break"})
                    self.assertFalse(task.done())
                    notification = log.to_chat_messages()[-1]
                    self.assertEqual(notification.source, "scheduled")
                    self.assertEqual(notification.content, "Take a break")
                    self.assertIn("<scheduled_update", log.load_transcript())
                    self.assertIn("independent background notifications", self.runtime._make_llm_call.call_args.args[0])
                finally:
                    release.set()
                    result = await task
            self.assertTrue(result.success)
            self.assertEqual(log.to_chat_messages()[-1].content, "You placed three Chipotle orders.")
            self.assertIsNone(log.to_chat_messages()[-1].source)

    def test_scheduled_worker_replies_keep_their_source_without_affecting_chat_replies(self):
        from server.services.conversation.log import ConversationLog, scheduled_delivery

        with tempfile.TemporaryDirectory() as directory, patch("server.services.conversation.log._resolve_working_memory_log", return_value=Mock()), patch(
            "server.services.conversation.log.get_settings", return_value=Mock(summarization_enabled=False)
        ):
            log = ConversationLog(Path(directory) / "conversation.log")
            token = scheduled_delivery.set(True)
            try:
                log.record_reply("Scheduled research completed")
            finally:
                scheduled_delivery.reset(token)
            log.record_reply("Three orders this year")
            messages = log.to_chat_messages()
            self.assertEqual(messages[0].source, "scheduled")
            self.assertIsNone(messages[1].source)

    async def test_late_worker_reply_keeps_its_request_and_ignores_newer_chat_history(self):
        from server.services.conversation.log import ConversationLog
        from server.services.conversation.request_context import current_request, request_scope

        started = asyncio.Event()
        release = asyncio.Event()
        with tempfile.TemporaryDirectory() as directory, patch("server.services.conversation.log._resolve_working_memory_log", return_value=Mock()), patch(
            "server.services.conversation.log.get_settings", return_value=Mock(summarization_enabled=False)
        ):
            log = ConversationLog(Path(directory) / "conversation.log")
            self.runtime.conversation_log = log
            self.runtime._load_conversation_transcript = Mock(return_value="Stale answer: three orders. Latest user: hello")
            self.runtime._make_llm_call = AsyncMock(return_value=response(content="This search found four distinct orders."))

            async def worker():
                started.set()
                await release.wait()
                return await self.runtime.handle_agent_message("[SUCCESS] Search: four verified order IDs")

            with request_scope("orders-request", "Count my orders again"):
                log.record_user_message("Count my orders again")
                task = asyncio.create_task(worker())
            await started.wait()
            with request_scope("greeting-request", "hello"):
                log.record_user_message("hello")
                log.record_scheduled_update("Scheduled email sent")
                with patch("server.agents.interaction_agent.runtime.build_system_prompt", return_value="system"), patch(
                    "server.agents.interaction_agent.runtime.prepare_message_with_history", return_value=[]
                ) as prepare:
                    release.set()
                    result = await task
                self.assertEqual(current_request.get().request_id, "greeting-request")
            self.assertIsNone(current_request.get())
            self.assertTrue(result.success)
            self.assertIn("Count my orders again", prepare.call_args.args[0])
            self.assertEqual(prepare.call_args.args[1], "")
            self.runtime._load_conversation_transcript.assert_not_called()
            saved = log.to_chat_messages()
            self.assertEqual([message.request_id for message in saved], ["orders-request", "greeting-request", None, "orders-request"])
            self.assertEqual(saved[-1].content, "This search found four distinct orders.")

    async def test_result_delivery_cannot_restart_a_task_or_send_duplicate_answers(self):
        self.runtime._make_llm_call = AsyncMock(
            side_effect=[
                response(("send_message_to_agent", {"agent_name": "Search", "instructions": "Repeat"})),
                response(("send_message_to_user", {"message": "Four verified orders"}), ("send_message_to_user", {"message": "Duplicate"})),
            ]
        )
        with patch("server.agents.interaction_agent.runtime.build_system_prompt", return_value="system"), patch(
            "server.agents.interaction_agent.runtime.prepare_message_with_history", return_value=[]
        ):
            result = await self.runtime.handle_agent_message("[SUCCESS] Search: four verified orders")
        self.assertTrue(result.success)
        self.assertEqual(self.delivered, ["Four verified orders"])
        self.assertEqual(self.runtime._execute_tool.call_count, 1)
        self.assertEqual(self.runtime._make_llm_call.await_count, 2)

    async def test_result_delivery_cannot_silently_wait_instead_of_answering(self):
        self.runtime._make_llm_call = AsyncMock(
            side_effect=[response(("wait", {"reason": "Old answer already exists"})), response(content="Four verified orders in this search")]
        )
        with patch("server.agents.interaction_agent.runtime.build_system_prompt", return_value="system"), patch(
            "server.agents.interaction_agent.runtime.prepare_message_with_history", return_value=[]
        ):
            result = await self.runtime.handle_agent_message("[SUCCESS] Search: four order IDs")
        self.assertTrue(result.success)
        self.assertEqual(result.response, "Four verified orders in this search")
        self.runtime._execute_tool.assert_not_called()
        self.runtime.conversation_log.record_reply.assert_called_once_with(result.response)

    def test_failure_metadata_survives_reload_without_exposing_internal_instructions(self):
        from server.services.conversation.log import ConversationLog
        from server.services.conversation.request_context import request_scope, task_failure_message

        with tempfile.TemporaryDirectory() as directory, patch("server.services.conversation.log._resolve_working_memory_log", return_value=Mock()), patch(
            "server.services.conversation.log.get_settings", return_value=Mock(summarization_enabled=False)
        ):
            path = Path(directory) / "conversation.log"
            log = ConversationLog(path)
            request_id = 'client"\n&request'
            with request_scope(request_id, "Count my orders"):
                log.record_user_message("Count my orders")
                log.record_task_failure(task_failure_message("The model could not finish a context summary. INTERNAL TASK INSTRUCTIONS"))
            saved = ConversationLog(path).to_chat_messages()
            self.assertEqual(len(path.read_text().splitlines()), 2)
            self.assertEqual([message.request_id for message in saved], [request_id, request_id])
            self.assertEqual(saved[-1].source, "failure")
            self.assertNotIn("INTERNAL TASK INSTRUCTIONS", saved[-1].content)
            self.assertNotIn("larger context", saved[-1].content)

    async def test_agent_result_can_be_delivered_after_handoff(self):
        self.runtime._make_llm_call = AsyncMock(
            side_effect=[response(("send_message_to_user", {"message": "Seven verified emails."})), response(("wait", {"reason": "Results delivered"}))]
        )
        with patch("server.agents.interaction_agent.runtime.build_system_prompt", return_value="system"), patch(
            "server.agents.interaction_agent.runtime.prepare_message_with_history", return_value=[]
        ):
            result = await self.runtime.handle_agent_message("[SUCCESS] Email Checker: Seven verified emails.")
        self.assertTrue(result.success)
        self.assertEqual(result.response, "Seven verified emails.")
        self.assertEqual(self.delivered, ["Seven verified emails."])
        self.runtime.conversation_log.record_reply.assert_not_called()


if __name__ == "__main__":
    unittest.main()
