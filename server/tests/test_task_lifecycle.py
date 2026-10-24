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
from server.services.conversation.read_checkpoints import ReadCheckpoints, read_checkpoints
from server.services.conversation.request_context import clear_requests, create_request_task, current_request, request_scope, stop_request


class TaskLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        read_checkpoints.clear()
        self.addCleanup(read_checkpoints.clear)

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

    async def test_explicit_retry_reuses_exact_completed_reads_and_never_replays_mutations(self):
        read = AsyncMock(return_value=[{"id": "receipt-1", "clean_text": "Order A1"}])
        write = AsyncMock(return_value={"success": True})
        runtime = ExecutionAgentRuntime.__new__(ExecutionAgentRuntime)
        runtime.tool_registry = {"task_email_search": read, "gmail_execute_draft": write}
        with patch("server.agents.execution_agent.runtime.get_active_gmail_user_id", return_value="mailbox-a"):
            with request_scope("original-read", "Count orders"):
                self.assertTrue((await runtime._execute_tool("task_email_search", {"search_query": "receipts"}))[0])
            with request_scope("retry-read", "Count orders", retry_of="original-read"):
                success, result = await runtime._execute_tool("task_email_search", {"search_query": "receipts"})
                self.assertTrue(success)
                self.assertEqual(result[0]["id"], "receipt-1")
                read.assert_awaited_once()
                write.assert_not_awaited()
                success, _ = await runtime._execute_tool("gmail_execute_draft", {"draft_id": "draft"})
                self.assertFalse(success)
                write.assert_not_awaited()
            with request_scope("new-request", "Read and send"):
                await runtime._execute_tool("task_email_search", {"search_query": "receipts"})
                await runtime._execute_tool("gmail_execute_draft", {"draft_id": "draft"})
                self.assertFalse(read_checkpoints.available("new-request"))
                self.assertFalse(current_request.get().read_only)
        write.assert_awaited_once()

    async def test_retry_checkpoints_are_isolated_expire_and_return_independent_copies(self):
        store = ReadCheckpoints(ttl_seconds=1)
        arguments = {"search_query": "receipts"}
        with request_scope("saved", "Count orders"):
            store.put("task_email_search", arguments, "mailbox-a", [{"id": "one"}])
        with request_scope("retry", "Different task", retry_of="saved"):
            self.assertFalse(store.get("task_email_search", arguments, "mailbox-a")[0])
        with request_scope("retry", "Count orders", retry_of="saved"):
            self.assertFalse(store.get("task_email_search", arguments, "mailbox-b")[0])
            found, result = store.get("task_email_search", arguments, "mailbox-a")
            self.assertTrue(found)
            result[0]["id"] = "changed"
            self.assertEqual(store.get("task_email_search", arguments, "mailbox-a")[1][0]["id"], "one")
            with patch("server.services.conversation.read_checkpoints.time.monotonic", return_value=10**12):
                self.assertFalse(store.get("task_email_search", arguments, "mailbox-a")[0])

    async def test_expired_retry_is_rejected_before_starting_work(self):
        request = ChatRequest(messages=[ChatMessage(role="user", content="Count orders")], retry_of="expired")
        with patch("server.services.conversation.chat_handler.get_active_gmail_user_id", return_value="mailbox-a"), patch(
            "server.services.conversation.chat_handler.InteractionAgentRuntime"
        ) as runtime:
            response = await handle_chat_request(request)
        self.assertEqual(response.status_code, 409)
        runtime.assert_not_called()

    async def test_failure_exposes_retry_only_when_completed_read_checkpoint_exists(self):
        with tempfile.TemporaryDirectory() as directory, patch("server.services.conversation.log._resolve_working_memory_log", return_value=Mock()), patch(
            "server.services.conversation.log.get_settings", return_value=Mock(summarization_enabled=False)
        ):
            log = ConversationLog(Path(directory) / "conversation.log")
            with request_scope("retryable-failure", "Count orders"):
                read_checkpoints.put("task_email_search", {"search_query": "receipts"}, "mailbox-a", [{"id": "one"}])
                log.record_task_failure("Task timed out")
            self.assertTrue(log.to_chat_messages()[0].retryable)
