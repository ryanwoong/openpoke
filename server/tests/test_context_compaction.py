import asyncio
import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import httpx

from server import model_settings
from server.openrouter_client import client
from server.openrouter_client.context import ContextBudgetError, ContextCompactor, context_overflow, estimate_tokens, input_budget
from server.services.conversation.summarization import summarizer
from server.services.conversation.summarization.state import LogEntry, SummaryState


class ContextCompactionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.compactor = ContextCompactor(Path(directory.name) / "artifacts")
        self.summary = AsyncMock(return_value="Receipt A1: 2026-10-23, $14.12. Email approval is still pending. No final total is verified.")
        client._context_limits.clear()

    def payload(self, messages, tools=None):
        return {"model": "test", "messages": messages, "tools": tools or [], "max_tokens": 512}

    async def test_small_payload_needs_no_summary_or_artifact(self):
        payload = self.payload([{"role": "user", "content": "Hello"}])
        prepared, compacted = await self.compactor.prepare(payload, 2000, self.summary, "model")
        self.assertEqual(prepared, payload)
        self.assertFalse(compacted)
        self.summary.assert_not_awaited()
        self.assertFalse(self.compactor.directory.exists())

    def test_execution_history_defaults_to_recent_complete_tasks_with_an_explicit_scope(self):
        from server.agents.execution_agent.agent import ExecutionAgent

        transcript = "\n".join(f"<agent_request>Task {index:02}</agent_request>\n<agent_response>Result {index:02}</agent_response>" for index in range(12))
        store = Mock(load_transcript=Mock(return_value=transcript))
        with patch("server.agents.execution_agent.agent.get_execution_agent_logs", return_value=store):
            agent = ExecutionAgent("Worker")
            agent.build_system_prompt = Mock(return_value="Exact rules")
            prompt = agent.build_system_prompt_with_history()
        self.assertNotIn("Task 00", prompt)
        self.assertNotIn("Task 01", prompt)
        self.assertIn("Task 02", prompt)
        self.assertIn("Result 11", prompt)
        self.assertIn("not a complete audit", prompt)
        self.assertEqual(store.load_transcript.return_value, transcript)

    async def test_execution_history_compacts_without_changing_system_rules_or_current_task(self):
        history = "<agent_response>" + "Old observation. " * 2500 + "</agent_response>\n<agent_request>Latest exact request</agent_request>"
        rules = "Never send without approval."
        payload = self.payload(
            [{"role": "system", "content": rules + "\n\n# Execution History\n\n" + history}, {"role": "user", "content": "Check trigger 17; do not send it."}]
        )
        original = copy.deepcopy(payload)
        prepared, compacted = await self.compactor.prepare(payload, 5000, self.summary, "model")
        self.assertTrue(compacted)
        self.assertLessEqual(estimate_tokens(prepared), 5000)
        self.assertTrue(prepared["messages"][0]["content"].startswith(rules))
        self.assertIn("Latest exact request", prepared["messages"][0]["content"])
        self.assertEqual(prepared["messages"][-1], original["messages"][-1])
        self.assertEqual(payload, original)
        files = list(self.compactor.directory.glob("*.json"))
        self.assertTrue(files)
        self.assertIn("Old observation", json.loads(files[0].read_text())["source"])
        self.assertEqual(files[0].stat().st_mode & 0o777, 0o600)

    async def test_embedded_history_keeps_current_turn_and_recent_approval_verbatim(self):
        recent = "<poke_reply>Email draft 17 (awaiting approval): exact subject and body.</poke_reply>"
        content = (
            "<conversation_history><poke_reply>"
            + "Old email. " * 2500
            + "</poke_reply>"
            + recent
            + "</conversation_history><new_user_message>Is it approved?</new_user_message>"
        )
        prepared, _ = await self.compactor.prepare(self.payload([{"role": "user", "content": content}]), 4000, self.summary, "model")
        text = prepared["messages"][0]["content"]
        self.assertIn(recent, text)
        self.assertTrue(text.endswith("<new_user_message>Is it approved?</new_user_message>"))

    async def test_large_email_results_are_batched_without_changing_tool_calls(self):
        emails = [{"id": f"email-{index}", "clean_text": "Receipt A1, amount $14.12. " * 250} for index in range(8)]
        call = {
            "role": "assistant",
            "tool_calls": [{"id": "search", "function": {"name": "task_email_search", "arguments": '{"search_query":"receipts"}'}}],
            "content": "",
        }
        result = {"tool": "task_email_search", "status": "success", "arguments": {"search_query": "receipts"}, "result": emails}
        payload = self.payload(
            [{"role": "user", "content": "Count unique orders"}, call, {"role": "tool", "tool_call_id": "search", "content": json.dumps(result)}]
        )
        prepared, _ = await self.compactor.prepare(payload, 5000, self.summary, "model")
        self.assertEqual(prepared["messages"][1], call)
        self.assertEqual(prepared["messages"][2]["tool_call_id"], "search")
        compacted = json.loads(prepared["messages"][2]["content"])["result"]
        self.assertEqual(compacted["returned_email_count"], 8)
        self.assertIn("Deduplicate order IDs", compacted["warning"])
        self.assertGreater(self.summary.await_count, 1)
        evidence = "".join(json.loads(call.args[0])["source_excerpt"] for call in self.summary.await_args_list if "source_excerpt" in json.loads(call.args[0]))
        for email in emails:
            self.assertIn(email["id"], evidence)
        self.assertLessEqual(estimate_tokens(prepared), 5000)

    def test_large_email_fragments_retain_exact_ids_and_all_content(self):
        from server.openrouter_client.context import source_chunks

        email = {"id": "email-123", "subject": "Order A1", "sender": "receipt@example.com", "clean_text": 'Receipt A1 with "quotes", 漢字 and 🙂. ' * 500}
        chunks = source_chunks(json.dumps([email], ensure_ascii=False), 1024)
        fragments = [json.loads(chunk) for chunk in chunks]
        self.assertGreater(len(fragments), 1)
        self.assertTrue(all(fragment["id"] == "email-123" and fragment["subject"] == "Order A1" for fragment in fragments))
        self.assertEqual("".join(fragment["clean_text"] for fragment in fragments), email["clean_text"])
        self.assertTrue(all(len(chunk.encode("utf-8")) <= 1024 for chunk in chunks))

    async def test_search_subagent_results_without_wrapper_are_compacted(self):
        call = {"role": "assistant", "tool_calls": [{"id": "fetch", "function": {"name": "gmail_fetch_emails", "arguments": '{"query":"receipts"}'}}]}
        result = {"status": "success", "messages": [{"id": "one", "clean_text": "Receipt " * 4000}]}
        payload = self.payload([{"role": "user", "content": "Find receipts"}, call, {"role": "tool", "tool_call_id": "fetch", "content": json.dumps(result)}])
        prepared, _ = await self.compactor.prepare(payload, 4000, self.summary, "model")
        self.assertTrue(json.loads(prepared["messages"][-1]["content"])["messages"]["context_compacted"])

    async def test_old_tool_groups_are_replaced_as_whole_turns(self):
        old = [
            {"role": "user", "content": "Old task " * 1500},
            {"role": "assistant", "tool_calls": [{"id": "old", "function": {"name": "lookup", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "old", "content": "Old result"},
        ]
        latest = {"role": "user", "content": "Current exact question"}
        prepared, _ = await self.compactor.prepare(self.payload(old + [latest]), 2000, self.summary, "model")
        self.assertEqual(prepared["messages"][-1], latest)
        self.assertFalse(any(message.get("tool_call_id") == "old" for message in prepared["messages"]))
        self.assertFalse(any(message.get("tool_calls") for message in prepared["messages"]))

    async def test_current_user_text_that_looks_like_history_is_not_compacted(self):
        content = "<conversation_history>" + "Exact user-provided data " * 1000 + "</conversation_history>"
        payload = self.payload([{"role": "user", "content": content}])
        with self.assertRaises(ContextBudgetError):
            await self.compactor.prepare(payload, 2000, self.summary, "model")
        self.assertEqual(payload["messages"][0]["content"], content)
        self.summary.assert_not_awaited()

    async def test_oversized_mutation_arguments_are_never_shortened(self):
        payload = self.payload(
            [
                {"role": "system", "content": "Never send without approval"},
                {"role": "user", "content": "Prepare this email"},
                {
                    "role": "assistant",
                    "tool_calls": [
                        {"id": "draft", "function": {"name": "scheduleTask", "arguments": json.dumps({"kind": "email", "body": "Exact body " * 3000})}}
                    ],
                },
            ]
        )
        original = copy.deepcopy(payload)
        with self.assertRaises(ContextBudgetError):
            await self.compactor.prepare(payload, 2500, self.summary, "model")
        self.assertEqual(payload, original)
        self.summary.assert_not_awaited()

    async def test_summaries_are_cached_per_provider_and_source(self):
        source = "Historical fact. " * 1000
        await self.compactor.summarize(source, 4000, self.summary, "provider-a")
        count = self.summary.await_count
        await self.compactor.summarize(source, 4000, self.summary, "provider-a")
        self.assertEqual(self.summary.await_count, count)
        await self.compactor.summarize(source, 4000, self.summary, "provider-b")
        self.assertGreater(self.summary.await_count, count)

    async def test_clear_removes_artifacts_and_blocks_inflight_cache_repopulation(self):
        started, release = asyncio.Event(), asyncio.Event()

        async def summarize(source, output):
            started.set()
            await release.wait()
            return "Old memory"

        task = asyncio.create_task(self.compactor.summarize("Old source", 4000, summarize, "model"))
        await started.wait()
        self.compactor.clear()
        release.set()
        with self.assertRaises(ContextBudgetError):
            await task
        self.assertEqual(self.compactor.summaries, {})
        self.assertEqual(list(self.compactor.directory.glob("*.json")), [])

    async def test_context_error_is_retried_once_without_replaying_tools(self):
        target = model_settings.CompletionTarget("Local", "test", "http://test/v1", None, 8192)
        payloads = []

        async def send(payload, *args):
            payloads.append(copy.deepcopy(payload))
            if len(payloads) == 1:
                raise client.ContextWindowError("request exceeds context window", 4096)
            return {"choices": [{"message": {"content": "Done"}}]}

        with patch.object(client, "completion_target", return_value=target), patch.object(client, "context_compactor", self.compactor), patch.object(
            client, "_post_completion", side_effect=send
        ):
            response = await client.request_chat_completion(model="test", messages=[{"role": "user", "content": "Current question"}])
        self.assertEqual(len(payloads), 2)
        self.assertLess(payloads[1]["max_tokens"], payloads[0]["max_tokens"])
        self.assertEqual(response["choices"][0]["message"]["content"], "Done")
        self.assertEqual(client._context_limits[("http://test/v1", "test")].window, 4096)

    async def test_provider_overflow_compacts_history_before_retry(self):
        target = model_settings.CompletionTarget("Local", "test", "http://test/v1", None, 8192)
        source = (
            "<conversation_history><poke_reply>"
            + "Long older context. " * 500
            + "</poke_reply></conversation_history><new_user_message>Check trigger 17</new_user_message>"
        )
        regular, summaries = [], []

        async def send(payload, *args):
            if payload["messages"][0]["content"].startswith("Compact reference material"):
                summaries.append(payload)
                return {"choices": [{"message": {"content": "Trigger 17 is still waiting for approval"}, "finish_reason": "stop"}]}
            regular.append(copy.deepcopy(payload))
            if len(regular) == 1:
                raise client.ContextWindowError("context overflow", 4096)
            return {"choices": [{"message": {"content": "Final answer"}}]}

        with patch.object(client, "completion_target", return_value=target), patch.object(client, "context_compactor", self.compactor), patch.object(
            client, "_post_completion", side_effect=send
        ):
            await client.request_chat_completion(model="test", messages=[{"role": "user", "content": source}])
        self.assertEqual(len(regular), 2)
        self.assertTrue(summaries)
        self.assertLess(estimate_tokens(regular[1]), estimate_tokens(regular[0]))
        self.assertIn("Check trigger 17", regular[1]["messages"][-1]["content"])

    async def test_http_overflow_learns_a_budget_and_reuses_it_on_the_next_request(self):
        target = model_settings.CompletionTarget("Local", "test", "http://test/v1", None, 8192)
        source = (
            "<conversation_history><poke_reply>"
            + "Old reference. " * 700
            + "</poke_reply></conversation_history><new_user_message>Check trigger 17</new_user_message>"
        )
        regular, summaries = [], []

        def respond(request):
            payload = json.loads(request.content)
            self.assertNotIn("authorization", request.headers)
            if payload["messages"][0]["content"].startswith("Compact reference material"):
                summaries.append(payload)
                return httpx.Response(200, json={"choices": [{"message": {"content": "Trigger 17 remains paused"}, "finish_reason": "stop"}]})
            regular.append(payload)
            if len(regular) == 1:
                return httpx.Response(400, json={"error": {"message": "request exceeds context window; no room for answer in context (4096)"}})
            return httpx.Response(200, json={"choices": [{"message": {"content": "Done"}}]})

        real_client = httpx.AsyncClient

        def upstream():
            return real_client(transport=httpx.MockTransport(respond))

        with patch.object(client, "completion_target", return_value=target), patch.object(client, "context_compactor", self.compactor), patch.object(
            client.httpx, "AsyncClient", side_effect=upstream
        ):
            await client.request_chat_completion(model="test", messages=[{"role": "user", "content": source}])
            count = len(summaries)
            await client.request_chat_completion(model="test", messages=[{"role": "user", "content": source}])
        self.assertEqual(len(regular), 3)
        self.assertTrue(summaries)
        self.assertEqual(len(summaries), count)
        self.assertLess(estimate_tokens(regular[1]), estimate_tokens(regular[0]))
        self.assertLessEqual(estimate_tokens(regular[2]), client._context_limits[("http://test/v1", "test")].input_tokens)

    async def test_repeated_overflow_stops_after_one_retry(self):
        target = model_settings.CompletionTarget("Local", "test", "http://test/v1", None, 8192)
        send = AsyncMock(side_effect=client.ContextWindowError("too long", None))
        with patch.object(client, "completion_target", return_value=target), patch.object(client, "context_compactor", self.compactor), patch.object(
            client, "_post_completion", send
        ):
            with self.assertRaisesRegex(ContextBudgetError, "still rejected"):
                await client.request_chat_completion(model="test", messages=[{"role": "user", "content": "Hello"}])
        self.assertEqual(send.await_count, 2)

    async def test_shared_client_bounds_summary_requests_and_keeps_tool_schema(self):
        target = model_settings.CompletionTarget("Local", "test", "http://test/v1", None, 8192)
        source = (
            "<conversation_history><poke_reply>"
            + "Old memory " * 5000
            + "</poke_reply></conversation_history><new_user_message>Current exact task</new_user_message>"
        )
        tools = [{"type": "function", "function": {"name": "scheduleTask", "parameters": {"type": "object"}}}]
        sent = []

        async def send(payload, *args):
            sent.append(copy.deepcopy(payload))
            self.assertLess(estimate_tokens(payload) + payload["max_tokens"], 8192)
            return {"choices": [{"message": {"content": "Compact facts"}, "finish_reason": "stop"}]}

        with patch.object(client, "completion_target", return_value=target), patch.object(client, "context_compactor", self.compactor), patch.object(
            client, "_post_completion", side_effect=send
        ):
            await client.request_chat_completion(model="test", system="System rules stay exact", messages=[{"role": "user", "content": source}], tools=tools)
        self.assertGreater(len(sent), 1)
        self.assertEqual(sent[-1]["tools"], tools)
        self.assertEqual(sent[-1]["messages"][0]["content"], "System rules stay exact")
        self.assertIn("Current exact task", sent[-1]["messages"][-1]["content"])
        self.assertTrue(all("tools" not in payload for payload in sent[:-1]))

    async def test_summary_failure_does_not_send_uncompacted_payload(self):
        target = model_settings.CompletionTarget("Local", "test", "http://test/v1", None, 8192)
        send = AsyncMock(return_value={"choices": [{"message": {"content": ""}, "finish_reason": "length"}]})
        source = "<conversation_history>" + "Old memory " * 3000 + "</conversation_history><new_user_message>Current task</new_user_message>"
        with patch.object(client, "completion_target", return_value=target), patch.object(client, "context_compactor", self.compactor), patch.object(
            client, "_post_completion", send
        ):
            with self.assertRaisesRegex(ContextBudgetError, "context summary"):
                await client.request_chat_completion(model="test", messages=[{"role": "user", "content": source}])
        self.assertTrue(all("tools" not in call.args[0] for call in send.await_args_list))
        self.assertTrue(all(call.args[0]["messages"][0]["content"].startswith("Compact reference material") for call in send.await_args_list))

    async def test_compaction_concurrency_is_bounded_and_unicode_is_preserved(self):
        from server.openrouter_client.context import source_chunks

        source = "漢字🙂" * 4000
        chunks = source_chunks(source, 1024)
        self.assertEqual("".join(chunks).replace("\n", ""), source)
        self.assertTrue(all(len(chunk.encode("utf-8")) <= 1025 for chunk in chunks))
        active, peak = 0, 0

        async def summarize(source, output):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0)
            active -= 1
            return "Factual memory"

        await self.compactor.summarize(source, 5000, summarize, "model")
        self.assertLessEqual(peak, 3)
        self.assertGreater(peak, 1)

    def test_cleared_working_memory_rejects_a_stale_summary_write(self):
        from server.services.conversation.summarization.working_memory_log import WorkingMemoryLog

        memory = WorkingMemoryLog(self.compactor.directory.parent / "memory.log")
        generation = memory.generation
        memory.clear()
        self.assertFalse(memory.write_summary_state(SummaryState(summary_text="Old private memory", last_index=10), expected_generation=generation))
        self.assertEqual(memory.load_summary_state().summary_text, "")
        self.assertTrue(memory.write_summary_state(SummaryState(summary_text="New memory"), expected_generation=memory.generation))
        self.assertEqual(memory.load_summary_state().summary_text, "New memory")

    def test_clear_history_clears_context_artifacts_too(self):
        from server.routes.chat import clear_history

        self.compactor.archive("Original data")
        with patch("server.routes.chat.get_conversation_log", return_value=Mock()), patch("server.routes.chat.get_trigger_service", return_value=Mock()), patch(
            "server.services.get_execution_agent_logs", return_value=Mock()
        ), patch("server.services.get_agent_roster", return_value=Mock()), patch("server.openrouter_client.context.context_compactor", self.compactor):
            self.assertTrue(clear_history().ok)
        self.assertEqual(list(self.compactor.directory.glob("*.json")), [])

    async def test_auth_errors_are_not_context_retries(self):
        send = AsyncMock(side_effect=client.OpenRouterError("Unauthorized"))
        with patch.object(client, "_post_completion", send):
            with self.assertRaisesRegex(client.OpenRouterError, "Unauthorized"):
                await client.request_chat_completion(model="test", messages=[{"role": "user", "content": "Hello"}])
        self.assertEqual(send.await_count, 1)

    def test_overflow_parser_handles_local_error_and_ignores_other_bad_requests(self):
        self.assertEqual(
            context_overflow(400, "request exceeds the context window. prompt (149266 tokens) leaves no room for answer in context (131072)"), (True, 131072)
        )
        self.assertEqual(context_overflow(400, "maximum context length is 8192 tokens"), (True, 8192))
        self.assertEqual(context_overflow(401, "context exceeded"), (False, None))
        self.assertEqual(context_overflow(400, "Invalid tool_call_id"), (False, None))
        self.assertEqual(input_budget(32768, 4096), 24576)

    async def test_token_pressure_triggers_working_memory_before_message_threshold(self):
        settings = SimpleNamespace(
            summarization_enabled=True,
            conversation_summary_threshold=100,
            conversation_summary_tail_size=10,
            summarizer_model="test",
            context_window_tokens=8192,
            context_output_tokens=1024,
            openrouter_api_key=None,
        )
        entries = [LogEntry(tag="poke_reply", payload="Large prior message " * 2500, index=0), LogEntry(tag="user_message", payload="Latest question", index=1)]
        memory = Mock(load_summary_state=Mock(return_value=SummaryState.empty()))
        target = model_settings.CompletionTarget("Local", "test", "http://test/v1", None, 8192)
        with patch.object(summarizer, "get_settings", return_value=settings), patch.object(summarizer, "completion_target", return_value=target), patch.object(
            summarizer, "_resolve_conversation_log", return_value=Mock()
        ), patch.object(summarizer, "_collect_entries", return_value=entries), patch.object(
            summarizer, "get_working_memory_log", return_value=memory
        ), patch.object(
            summarizer, "_call_openrouter", AsyncMock(return_value="Compact memory")
        ):
            self.assertTrue(await summarizer.summarize_conversation())
        state = memory.write_summary_state.call_args.args[0]
        self.assertEqual(state.summary_text, "Compact memory")
        self.assertEqual(state.unsummarized_entries[-1].payload, "Latest question")
        self.assertEqual(state.last_index, 0)


if __name__ == "__main__":
    unittest.main()
