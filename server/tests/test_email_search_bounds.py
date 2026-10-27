import asyncio
import importlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from server.agents.execution_agent.email_evidence import EmailEvidence
from server.agents.execution_agent.runtime import ExecutionAgentRuntime
from server.openrouter_client.context import ContextCompactor

search = importlib.import_module("server.agents.execution_agent.tasks.search_email.tool")


def completion(*calls, content=""):
    return {"choices": [{"message": {"content": content, "tool_calls": [{"id": f"call-{i}", "function": {"name": name, "arguments": json.dumps(args)}} for i, (name, args) in enumerate(calls)]}}]}


def message(identifier="one", text="Please order part ABC-123. Dealer reply: on order.", thread="thread-one"):
    return {"messageId": identifier, "threadId": thread, "messageTimestamp": "2026-09-01T12:00:00Z", "subject": "Parts order", "sender": "parts@example.com", "to": "buyer@example.com", "messageText": text}


class SelectiveRetrievalTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = ContextCompactor(Path(self.directory.name))
        for item in [patch.object(search, "get_active_gmail_user_id", return_value="mailbox"), patch.object(search, "context_compactor", self.store)]:
            item.start()
            self.addCleanup(item.stop)

    async def test_headers_first_then_selected_full_thread(self):
        seen = []

        async def fetch(tool, mailbox, arguments):
            seen.append((tool, arguments))
            if tool == "GMAIL_FETCH_EMAILS":
                return {"messages": [{"id": "one", "threadId": "thread-one", "subject": "Parts order", "snippet": "Dealer reply"}]}
            return {"messages": [message()]}

        with patch.object(search, "_invoke", side_effect=fetch):
            discovery = await search.task_email_search("Subaru parts")
            bodies = await search.task_email_read(thread_id=discovery["messages"][0]["thread_id"])
        self.assertFalse(seen[0][1]["include_payload"])
        self.assertEqual(seen[0][1]["format"], "metadata")
        self.assertEqual(seen[1][0], "GMAIL_FETCH_MESSAGE_BY_THREAD_ID")
        self.assertEqual(seen[1][1], {"thread_id": "thread-one"})
        self.assertEqual(discovery["body_scope"], "headers")
        self.assertNotIn("clean_text", discovery["messages"][0])
        self.assertTrue(bodies["read_complete"])
        self.assertIn("on order", bodies["messages"][0]["clean_text"])

    async def test_fragments_resume_without_refetching_or_losing_body_text(self):
        text = "Order ABC-123: " + "Readable detail. " * 800
        evidence = EmailEvidence()
        pieces = []
        with patch.object(search, "_byte_limit", return_value=3000), patch.object(search, "_invoke", new=AsyncMock(return_value={"messages": [message(text=text)]})) as fetch:
            cursor = None
            for _ in range(100):
                result = await search.task_email_read(thread_id="thread-one", cursor=cursor)
                self.assertNotIn("error", result)
                self.assertLessEqual(len(json.dumps(result).encode()), 3000)
                evidence.observe("task_email_read", {"thread_id": "thread-one"}, True, result)
                pieces.extend(row["clean_text"] for row in result["messages"])
                cursor = result["next_cursor"]
                if cursor is None:
                    break
            else:
                self.fail("Read did not terminate")
        self.assertEqual(fetch.await_count, 1)
        self.assertEqual("".join(pieces), text.strip())
        self.assertEqual(evidence.records["one"]["clean_text"], text.strip())
        self.assertEqual(evidence.gaps, [])

    async def test_final_fragment_alone_does_not_prove_complete_read(self):
        with patch.object(search, "_byte_limit", return_value=3000), patch.object(search, "_invoke", new=AsyncMock(return_value={"messages": [message(text="Body " * 1000)]})):
            result = await search.task_email_read(thread_id="thread-one")
            while result.get("next_cursor"):
                result = await search.task_email_read(thread_id="thread-one", cursor=result["next_cursor"])
        evidence = EmailEvidence()
        evidence.observe("task_email_read", {"thread_id": "thread-one"}, True, result)
        self.assertTrue(result["read_complete"])
        self.assertTrue(evidence.gaps)
        self.assertEqual(evidence.records, {})

    async def test_thread_api_pagination_is_completed_before_snapshot(self):
        fetch = AsyncMock(side_effect=[{"messages": [message("one")], "nextPageToken": "page-two"}, {"messages": [message("two")]}])
        with patch.object(search, "_invoke", fetch):
            result = await search.task_email_read(thread_id="thread-one")
        self.assertEqual(fetch.await_args_list[1].args[2]["page_token"], "page-two")
        self.assertEqual(result["message_count"], 2)
        self.assertTrue(result["read_complete"])

    async def test_thread_page_failure_is_not_complete(self):
        with patch.object(search, "_invoke", new=AsyncMock(side_effect=[{"messages": [message()], "nextPageToken": "next"}, RuntimeError("page failed")])):
            result = await search.task_email_read(thread_id="thread-one")
        self.assertIn("error", result)
        self.assertFalse(list(self.store.directory.glob("*.json")))

    async def test_cursor_cannot_cross_mailboxes_or_conversations(self):
        with patch.object(search, "_byte_limit", return_value=3000), patch.object(search, "_invoke", new=AsyncMock(return_value={"messages": [message(text="Body " * 1000)]})):
            first = await search.task_email_read(thread_id="thread-one")
            cursor = first["next_cursor"]
            self.assertTrue(cursor)
            other = await search.task_email_read(thread_id="thread-two", cursor=cursor)
            self.assertIn("error", other)
            with patch.object(search, "get_active_gmail_user_id", return_value="another-mailbox"):
                other = await search.task_email_read(thread_id="thread-one", cursor=cursor)
            self.assertIn("error", other)
            self.store.clear()
            cleared = await search.task_email_read(thread_id="thread-one", cursor=cursor)
            self.assertIn("error", cleared)

    async def test_clear_during_remote_read_prevents_snapshot_repopulation(self):
        async def fetch(*args):
            self.store.clear()
            return {"messages": [message()]}

        with patch.object(search, "_invoke", side_effect=fetch):
            result = await search.task_email_read(thread_id="thread-one")
        self.assertIn("error", result)
        self.assertFalse(list(self.store.directory.glob("*.json")))

    async def test_missing_body_is_not_marked_as_read(self):
        raw = message()
        del raw["messageText"]
        with patch.object(search, "_invoke", new=AsyncMock(return_value={"messages": [raw]})):
            result = await search.task_email_read(thread_id="thread-one")
        evidence = EmailEvidence()
        evidence.observe("task_email_read", {"thread_id": "thread-one"}, True, result)
        self.assertFalse(result["read_complete"])
        self.assertIn("one", result["unread_message_ids"])
        self.assertTrue(evidence.gaps)

    async def test_individual_message_read_closes_a_known_missing_thread_body(self):
        evidence = EmailEvidence()
        raw = message()
        del raw["messageText"]
        with patch.object(search, "_invoke", new=AsyncMock(return_value={"messages": [raw]})):
            incomplete = await search.task_email_read(thread_id="thread-one")
        evidence.observe("task_email_read", {"thread_id": "thread-one"}, True, incomplete)
        self.assertTrue(evidence.gaps)
        with patch.object(search, "_invoke", new=AsyncMock(return_value=message())):
            recovered = await search.task_email_read(message_id="one")
        evidence.observe("task_email_read", {"message_id": "one"}, True, recovered)
        self.assertEqual(evidence.gaps, [])

    async def test_single_message_endpoint_and_wrong_ids(self):
        with patch.object(search, "_invoke", new=AsyncMock(return_value=message())) as fetch:
            result = await search.task_email_read(message_id="one")
            self.assertTrue(result["read_complete"])
            self.assertEqual(fetch.call_args.args[0], "GMAIL_FETCH_MESSAGE_BY_MESSAGE_ID")
            wrong = await search.task_email_read(message_id="another")
            self.assertIn("error", wrong)
        self.assertIn("error", await search.task_email_read(thread_id="one", message_id="one"))
        self.assertIn("error", await search.task_email_read(thread_id="thread-one", cursor="../invalid"))

    async def test_read_timeout_and_user_cancellation_do_not_claim_success(self):
        started = asyncio.Event()

        async def blocked(*args):
            started.set()
            await asyncio.Future()

        with patch.object(search, "SEARCH_TIMEOUT_SECONDS", 0.01), patch.object(search, "_invoke", side_effect=blocked):
            result = await search.task_email_read(thread_id="thread-one")
            self.assertIn("error", result)
        with patch.object(search, "_invoke", side_effect=blocked):
            started.clear()
            task = asyncio.create_task(search.task_email_read(thread_id="thread-one"))
            await started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

    async def test_mailbox_switch_during_fetch_does_not_return_previous_account_data(self):
        with patch.object(search, "get_active_gmail_user_id", side_effect=["mailbox", "another-mailbox"]), patch.object(search.asyncio, "to_thread", new=AsyncMock(return_value={"data": {"messages": [{"id": "one"}]}})):
            result = await search.task_email_search("from:parts@example.com")
        self.assertIn("account changed", result["error"])
        self.assertNotIn("messages", result)

    async def test_lookup_can_finish_despite_unread_discovery_pages(self):
        evidence = EmailEvidence()
        evidence.observe("task_email_search", {"search_query": "Subaru"}, True, {"body_scope": "headers", "messages": [{"id": "one", "thread_id": "thread-one"}], "next_page_token": "more", "search_complete": False})
        with patch.object(search, "_invoke", new=AsyncMock(return_value={"messages": [message()]})):
            result = await search.task_email_read(thread_id="thread-one")
        evidence.observe("task_email_read", {"thread_id": "thread-one"}, True, result)
        self.assertEqual(evidence.gaps, [])

    async def test_exhaustive_coverage_requires_all_pages_and_matching_bodies(self):
        evidence = EmailEvidence()
        arguments = {"search_query": "from:parts@example.com", "mode": "all"}
        first = {"body_scope": "headers", "coverage_required": True, "messages": [{"id": "one"}], "page_token": None, "next_page_token": "next"}
        evidence.observe("task_email_search", arguments, True, first)
        self.assertTrue(evidence.gaps)
        with patch.object(search, "_invoke", new=AsyncMock(return_value={"messages": [message()]})):
            result = await search.task_email_read(thread_id="thread-one")
        evidence.observe("task_email_read", {"thread_id": "thread-one"}, True, result)
        self.assertTrue(evidence.gaps)
        evidence.observe("task_email_search", {**arguments, "page_token": "next"}, True, {**first, "messages": [], "page_token": "next", "next_page_token": None})
        self.assertEqual(evidence.gaps, [])

    async def test_invalid_read_arguments_do_not_leave_a_phantom_scope(self):
        evidence = EmailEvidence()
        evidence.observe("task_email_read", {}, False, {"error": "Missing ID"})
        with patch.object(search, "_invoke", new=AsyncMock(return_value={"messages": [message()]})):
            result = await search.task_email_read(thread_id="thread-one")
        evidence.observe("task_email_read", {"thread_id": "thread-one"}, True, result)
        self.assertEqual(evidence.gaps, [])

    async def test_retry_read_can_close_a_specific_failed_scope(self):
        evidence = EmailEvidence()
        evidence.observe("task_email_read", {"thread_id": "thread-one"}, False, {"error": "temporary failure"})
        self.assertTrue(evidence.gaps)
        with patch.object(search, "_invoke", new=AsyncMock(return_value={"messages": [message()]})):
            result = await search.task_email_read(thread_id="thread-one")
        evidence.observe("task_email_read", {"thread_id": "thread-one"}, True, result)
        self.assertEqual(evidence.gaps, [])


class ExecutionEvidenceTests(unittest.IsolatedAsyncioTestCase):
    def runtime(self):
        runtime = ExecutionAgentRuntime.__new__(ExecutionAgentRuntime)
        runtime.agent = Mock()
        runtime.agent.name = "Parts"
        runtime.agent.build_system_prompt_with_history.return_value = "System"
        runtime._execute_tool = AsyncMock(return_value=(True, [{"id": "one", "clean_text": "Dealer confirmed part ABC-123 on order", "subject": "Parts order"}]))
        runtime._make_llm_call = AsyncMock()
        return runtime

    async def test_four_search_calls_allow_final_answer_without_blanket_warning(self):
        runtime = self.runtime()
        runtime._make_llm_call.side_effect = [completion(*[("task_email_search", {"search_query": str(i)}) for i in range(4)]), completion(content="The dealership confirmed part ABC-123 was on order.")]
        result = await runtime.execute("Find my parts")
        self.assertTrue(result.success)
        self.assertNotIn("incomplete", result.response.lower())
        self.assertIn("ABC-123", result.response)

    async def test_read_still_allowed_after_search_limit(self):
        runtime = self.runtime()
        discovery = {"body_scope": "headers", "messages": [{"id": "one", "thread_id": "thread-one"}], "search_complete": True}
        runtime._execute_tool.side_effect = [(True, discovery)] * 4 + [(True, [{"id": "one", "clean_text": "On order"}])]
        runtime._make_llm_call.side_effect = [completion(*[("task_email_search", {"search_query": str(i)}) for i in range(4)]), completion(("task_email_read", {"thread_id": "thread-one"})), completion(content="The dealer confirmed it was on order.")]
        result = await runtime.execute("Find my parts")
        self.assertEqual(runtime._execute_tool.await_count, 5)
        self.assertNotIn("incomplete", result.response.lower())

    async def test_large_tool_batch_is_bounded_without_forcing_incomplete_status(self):
        runtime = self.runtime()
        runtime._make_llm_call.side_effect = [completion(*[("task_email_search", {"search_query": str(i)}) for i in range(9)]), completion(content="Found the confirmed order.")]
        result = await runtime.execute("Find my parts")
        self.assertEqual(runtime._execute_tool.await_count, 4)
        self.assertNotIn("incomplete", result.response.lower())

    async def test_actual_missing_scope_retains_specific_warning(self):
        runtime = self.runtime()
        runtime._execute_tool.side_effect = [(True, [{"id": "one", "clean_text": "On order"}]), (False, {"error": "Selected thread unavailable"})]
        runtime._make_llm_call.side_effect = [completion(("task_email_search", {"search_query": "parts"})), completion(("task_email_read", {"thread_id": "missing-thread"})), completion(content="Found one order confirmation.")]
        result = await runtime.execute("Find my parts")
        self.assertIn("selected evidence remains unread", result.response)
        self.assertNotIn("Search coverage is incomplete", result.response)
        self.assertEqual(result.user_response, result.response)

    async def test_synthesis_failure_preserves_retrieved_evidence(self):
        runtime = self.runtime()
        runtime._make_llm_call.side_effect = [completion(("task_email_search", {"search_query": "parts"})), RuntimeError("Model unavailable")]
        result = await runtime.execute("Find my parts")
        self.assertTrue(result.success)
        self.assertIn("ABC-123", result.response)
        self.assertNotIn("incomplete", result.response.lower())

    async def test_reserve_stops_more_retrieval_without_false_coverage_warning(self):
        runtime = self.runtime()
        runtime._make_llm_call.side_effect = [completion(("task_email_search", {"search_query": "parts"})), completion(content="Found the order confirmation.")]
        with patch("server.agents.execution_agent.runtime.remaining_seconds", side_effect=[300, 300, 110, 110]):
            result = await runtime.execute("Find my parts")
        self.assertEqual(runtime._execute_tool.await_count, 1)
        self.assertNotIn("incomplete", result.response.lower())

    async def test_thread_reads_remain_read_only_on_explicit_retry(self):
        from server.services.conversation.request_context import request_scope, current_request

        runtime = self.runtime()
        runtime.tool_registry = {"task_email_read": AsyncMock(return_value={"messages": [], "read_complete": False, "next_cursor": "saved"})}
        with request_scope("read-only-retry", "Find parts", retry_of="original"), patch("server.agents.execution_agent.runtime.get_active_gmail_user_id", return_value="mailbox"), patch("server.agents.execution_agent.runtime.read_checkpoints") as checkpoints:
            checkpoints.get.return_value = (False, None)
            success, result = await ExecutionAgentRuntime._execute_tool(runtime, "task_email_read", {"thread_id": "thread-one"})
            self.assertTrue(success)
            self.assertTrue(current_request.get().read_only)
            checkpoints.put.assert_called_once()


if __name__ == "__main__":
    unittest.main()
