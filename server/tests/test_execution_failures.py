import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from server.agents.execution_agent.batch_manager import ExecutionBatchManager
from server.agents.execution_agent.runtime import ExecutionAgentRuntime, ExecutionResult
from server.execution_trace import traced


class ExecutionFailureTests(unittest.IsolatedAsyncioTestCase):
    async def test_timeout_delivers_request_specific_failure_without_model_rewriting(self):
        async def blocked(*args):
            await asyncio.Future()

        manager = ExecutionBatchManager(timeout_seconds=0.01)
        log = Mock()
        runtime = Mock(execute=AsyncMock(side_effect=blocked))
        with patch("server.agents.execution_agent.batch_manager.ExecutionAgentRuntime", return_value=runtime), patch(
            "server.services.conversation.get_conversation_log", return_value=log
        ), patch.object(manager, "_dispatch_to_interaction_agent", AsyncMock()) as dispatch:
            result = await manager.execute_agent("Email Search", "Count Chipotle orders this year")
        self.assertFalse(result.success)
        self.assertIn("timed out", result.error)
        self.assertNotIn("Count Chipotle orders this year", result.user_response)
        self.assertEqual(result.instructions, "Count Chipotle orders this year")
        self.assertIn("don't have a verified result", result.user_response)
        log.record_task_failure.assert_called_once_with(result.user_response)
        dispatch.assert_not_awaited()
        self.assertEqual(manager.get_pending_executions(), [])

    async def test_result_dispatch_waits_for_the_interaction_reply(self):
        manager = ExecutionBatchManager(timeout_seconds=1)
        started = asyncio.Event()
        release = asyncio.Event()

        async def deliver(payload):
            started.set()
            await release.wait()

        runtime = Mock(handle_agent_message=AsyncMock(side_effect=deliver))
        with patch("server.agents.interaction_agent.runtime.InteractionAgentRuntime", return_value=runtime):
            task = asyncio.create_task(manager._dispatch_to_interaction_agent("Task: Count Chipotle orders"))
            await started.wait()
            try:
                self.assertFalse(task.done())
            finally:
                release.set()
                await task
        runtime.handle_agent_message.assert_awaited_once_with("Task: Count Chipotle orders")

    async def test_different_user_runs_cannot_share_a_result_batch(self):
        manager = ExecutionBatchManager(timeout_seconds=1)
        registered = asyncio.Event()
        release = asyncio.Event()
        batches = []

        @traced("interaction", lambda name: (name, {"prompt": name}))
        async def register(name):
            batch = await manager._register_pending_execution(name, name, name)
            batches.append(batch)
            if len(batches) == 2:
                registered.set()
            await release.wait()
            return batch

        tasks = [asyncio.create_task(register(name)) for name in ("First request", "Second request")]
        await registered.wait()
        release.set()
        first, second = await asyncio.gather(*tasks)
        self.assertNotEqual(first, second)
        with patch.object(manager, "_dispatch_to_interaction_agent", AsyncMock()) as dispatch:
            for batch, name in ((first, "First request"), (second, "Second request")):
                await manager._complete_execution(batch, ExecutionResult(agent_name=name, success=True, response=name, instructions=name), name)
        self.assertEqual(dispatch.await_count, 2)
        self.assertIn("Task: First request", dispatch.await_args_list[0].args[0])
        self.assertNotIn("Second request", dispatch.await_args_list[0].args[0])
        self.assertEqual(manager._batch_states, {})

    async def test_partial_failure_cannot_be_rewritten_as_a_complete_success(self):
        manager = ExecutionBatchManager(timeout_seconds=1)

        @traced("interaction", lambda: ("Count orders", {}))
        async def register():
            return await asyncio.gather(
                manager._register_pending_execution("A", "Search receipts", "first"),
                manager._register_pending_execution("B", "Search delivery orders", "second"),
            )

        first, second = await register()
        log = Mock()
        with patch("server.services.conversation.get_conversation_log", return_value=log), patch.object(
            manager, "_dispatch_to_interaction_agent", AsyncMock()
        ) as dispatch:
            await manager._complete_execution(first, ExecutionResult(agent_name="A", success=True, response="Three receipts"), "A")
            await manager._complete_execution(second, ExecutionResult(agent_name="B", success=False, response="Compaction failed"), "B")
        dispatch.assert_not_awaited()
        log.record_task_failure.assert_called_once()
        self.assertIn("don't have a verified complete answer", log.record_task_failure.call_args.args[0])
        self.assertNotIn("Three receipts", log.record_task_failure.call_args.args[0])

    async def test_delegations_from_one_run_still_batch_together(self):
        manager = ExecutionBatchManager(timeout_seconds=1)

        @traced("interaction", lambda: ("One request", {}))
        async def register():
            return await asyncio.gather(
                manager._register_pending_execution("A", "First task", "first"), manager._register_pending_execution("B", "Second task", "second")
            )

        first, second = await register()
        self.assertEqual(first, second)
        with patch.object(manager, "_dispatch_to_interaction_agent", AsyncMock()) as dispatch:
            await manager._complete_execution(first, ExecutionResult(agent_name="A", success=True, response="First"), "A")
            dispatch.assert_not_awaited()
            await manager._complete_execution(second, ExecutionResult(agent_name="B", success=True, response="Second"), "B")
        dispatch.assert_awaited_once()

    async def test_failed_tool_cannot_be_rephrased_as_a_success(self):
        runtime = ExecutionAgentRuntime.__new__(ExecutionAgentRuntime)
        runtime.agent = Mock(name="Email Search")
        runtime._execute_tool = AsyncMock(return_value=(False, {"error": "Gmail unavailable"}))
        runtime._make_llm_call = AsyncMock(
            side_effect=[
                {"choices": [{"message": {"tool_calls": [{"id": "search", "function": {"name": "task_email_search", "arguments": "{}"}}]}}]},
                {"choices": [{"message": {"content": "No matching orders"}}]},
            ]
        )
        result = await runtime.execute("Count orders")
        self.assertFalse(result.success)
        self.assertIn("Gmail unavailable", result.response)
        self.assertNotIn("No matching orders", result.response)

    async def test_chat_worker_uses_current_instructions_without_previous_execution_results(self):
        from server.services.conversation.request_context import request_scope

        runtime = ExecutionAgentRuntime.__new__(ExecutionAgentRuntime)
        runtime.agent = Mock()
        runtime.agent.name = "Email Search"
        runtime.agent.build_system_prompt.return_value = "Current system rules"
        runtime.agent.build_system_prompt_with_history.return_value = "Old verified order total"
        runtime._make_llm_call = AsyncMock(return_value={"choices": [{"message": {"content": "Completed"}}]})
        with request_scope("current", "Search again"):
            await runtime.execute("Fresh search with current evidence")
        runtime.agent.build_system_prompt_with_history.assert_not_called()
        args = runtime._make_llm_call.call_args.args
        self.assertEqual(args[0], "Current system rules")
        self.assertEqual(args[1][0], {"role": "user", "content": "Fresh search with current evidence"})

    def test_default_timeout_uses_configuration(self):
        with patch("server.agents.execution_agent.batch_manager.get_settings", return_value=SimpleNamespace(execution_timeout_seconds=240)):
            self.assertEqual(ExecutionBatchManager().timeout_seconds, 240)
            self.assertEqual(ExecutionBatchManager(timeout_seconds=10).timeout_seconds, 10)


if __name__ == "__main__":
    unittest.main()
