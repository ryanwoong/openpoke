import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from server.agents.execution_agent.runtime import ExecutionAgentRuntime
from server.config import Settings
from server.execution_trace import traced, trace_store
from server.model_settings import CompletionTarget
from server.models.chat import ChatMessage, ChatRequest
from server.openrouter_client import client
from server.openrouter_client.context import ContextBudgetError
from server.services.conversation.chat_handler import handle_chat_request
from server.services.conversation.log import ConversationLog
from server.services.conversation.request_context import clear_requests, create_request_task, current_request, request_scope, stop_request


class TaskLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_overall_deadline_survives_initial_handoff_and_cancels_worker(self):
        started = asyncio.Event()
        log = Mock()

        @traced("execution", lambda: ("Worker", {}))
        async def worker():
            started.set()
            await asyncio.Future()

        with patch("server.services.conversation.get_conversation_log", return_value=log):
            with request_scope("deadline-task", "Search emails", timeout_seconds=0.02):
                task = create_request_task(worker())
            await started.wait()
            await asyncio.gather(task, return_exceptions=True)
            await asyncio.sleep(0)
        self.assertTrue(task.cancelled())
        self.assertFalse(stop_request("deadline-task"))
        log.record_task_failure.assert_called_once()
        self.assertIn("timed out", log.record_task_failure.call_args.args[0])
        span = next(span for span in reversed(trace_store.snapshot()) if span["label"] == "Worker")
        self.assertIn("timed out", span["output"]["error"])

    async def test_stop_cancels_only_the_selected_request_and_retains_ownership(self):
        started = asyncio.Event()
        contexts = []

        async def worker():
            contexts.append(current_request.get())
            if len(contexts) == 2:
                started.set()
            await asyncio.Future()

        with request_scope("first-stop", "First"):
            first = create_request_task(worker())
        with request_scope("second-stop", "Second"):
            second = create_request_task(worker())
        with patch("server.services.conversation.get_conversation_log", return_value=Mock()):
            await started.wait()
            self.assertTrue(stop_request("first-stop"))
            await asyncio.gather(first, return_exceptions=True)
            self.assertFalse(second.done())
            self.assertTrue(stop_request("second-stop"))
            await asyncio.gather(second, return_exceptions=True)
        self.assertEqual([context.request_id for context in contexts], ["first-stop", "second-stop"])

    async def test_clear_from_a_route_thread_cancels_workers_without_restoring_chat(self):
        started = asyncio.Event()
        with tempfile.TemporaryDirectory() as directory, patch("server.services.conversation.log._resolve_working_memory_log", return_value=Mock()), patch(
            "server.services.conversation.log.get_settings", return_value=Mock(summarization_enabled=False)
        ):
            log = ConversationLog(Path(directory) / "conversation.log")

            async def worker():
                started.set()
                try:
                    await asyncio.Future()
                except asyncio.CancelledError:
                    log.record_reply("Late reply after clearing")
                    log.record_task_failure("Late failure after clearing")
                    raise

            with request_scope("clear-task", "Count orders"):
                task = create_request_task(worker())
            await started.wait()
            await asyncio.to_thread(clear_requests)
            await asyncio.gather(task, return_exceptions=True)
            self.assertTrue(task.cancelled())
            self.assertEqual(log.to_chat_messages(), [])

    async def test_model_call_has_its_own_deadline_and_does_not_replay(self):
        upstream = AsyncMock()
        upstream.__aenter__.return_value = upstream

        async def blocked(*args, **kwargs):
            await asyncio.Future()

        upstream.post.side_effect = blocked
        settings = Settings().model_copy(update={"model_timeout_seconds": 0.01})
        target = CompletionTarget("Local", "test", "http://test/v1", None, 8192)
        with patch.object(client, "get_settings", return_value=settings), patch.object(client.httpx, "AsyncClient", return_value=upstream):
            with self.assertRaisesRegex(client.OpenRouterError, "Model request timed out"):
                await client._post_completion({"model": "test", "messages": []}, target, None, "http://test/v1")
        upstream.post.assert_awaited_once()

    async def test_compaction_has_a_separate_phase_deadline(self):
        async def blocked(*args):
            await asyncio.Future()

        settings = Settings().model_copy(update={"context_compaction_timeout_seconds": 0.01})
        target = CompletionTarget("Local", "deadline-test", "http://test/v1", None, 8192)
        send = AsyncMock()
        with patch.object(client, "get_settings", return_value=settings), patch.object(client, "completion_target", return_value=target), patch.object(
            client, "context_compactor", Mock(prepare=AsyncMock(side_effect=blocked))
        ), patch.object(client, "_post_completion", send):
            with self.assertRaisesRegex(ContextBudgetError, "compaction timed out"):
                await client.request_chat_completion(model="test", messages=[{"role": "user", "content": "Count orders"}])
        send.assert_not_awaited()
