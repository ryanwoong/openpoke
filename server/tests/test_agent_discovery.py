import asyncio
import json
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from html import escape
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from server.agents.interaction_agent import agent, tools
from server.agents.interaction_agent.runtime import InteractionAgentRuntime, _ToolCall
from server.agents.interaction_agent.tools import ToolResult
from server.execution_trace import trace_store
from server.openrouter_client.context import framed_history
from server.services.conversation.request_context import current_request, request_scope
from server.services.execution.roster import AgentRoster


class DirectoryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "roster.json"
        self.roster = AgentRoster(self.path)

    def test_migration_preserves_names_and_backup(self):
        raw = json.dumps(["Tokyo Restaurant Search", "Email to Alice", "Tokyo Restaurant Search"])
        self.path.write_text(raw)
        self.assertEqual(self.roster.get_agents(), ["Tokyo Restaurant Search", "Email to Alice"])
        data = json.loads(self.path.read_text())
        self.assertEqual(data["version"], 1)
        self.assertIsNone(data["agents"][0]["last_used_at"])
        self.assertEqual(data["agents"][0]["purpose"], "Tokyo Restaurant Search")
        self.assertEqual(self.path.with_suffix(".legacy.json").read_text(), raw)
        self.roster.load()
        self.assertEqual(self.path.with_suffix(".legacy.json").read_text(), raw)

    def test_ranking_unicode_reuse_and_empty_results(self):
        self.roster.record_dispatch("Tokyo", "old", "restaurants vegetarian")
        self.roster.record_dispatch("Tokyo restaurants", "new", "travel")
        self.roster.record_dispatch("Dining", "other", "Tokyo restaurants")
        self.assertEqual([a["agent_name"] for a in self.roster.find_agents("TOKYO")["agents"]], ["Tokyo", "Tokyo restaurants", "Dining"])
        self.assertEqual(self.roster.find_agents("missing"), {"agents": [], "truncated": False})
        self.assertFalse(self.roster.record_dispatch("Tokyo", "followup", "replacement"))
        self.assertEqual(self.roster.find_agents("Tokyo")["agents"][0]["purpose"], "restaurants vegetarian")
        self.roster.record_dispatch("Straße", "unicode")
        self.assertEqual(self.roster.find_agents("STRASSE")["agents"][0]["agent_name"], "Straße")
        for query in ["", " ", "x" * 257, None, 42]:
            with self.assertRaises(ValueError):
                self.roster.find_agents(query)

    def test_recency_then_name_break_ties(self):
        self.path.write_text(json.dumps(["B Tokyo", "A Tokyo"]))
        self.assertEqual([a["agent_name"] for a in self.roster.find_agents("Tokyo")["agents"]], ["A Tokyo", "B Tokyo"])
        self.roster.record_dispatch("B Tokyo")
        self.assertEqual(self.roster.find_agents("Tokyo")["agents"][0]["agent_name"], "B Tokyo")

    def test_concurrent_updates_and_bounded_associations(self):
        def dispatch(index):
            return AgentRoster(self.path).record_dispatch(f"Agent {index}", f"request-{index}")

        with ThreadPoolExecutor(max_workers=8) as executor:
            self.assertTrue(all(executor.map(dispatch, range(40))))
        self.assertEqual(len(self.roster.get_agents()), 40)
        self.assertEqual(len(json.loads(self.path.read_text())["recent_dispatches"]), 20)
        self.assertEqual(len(self.roster.recent_dispatches()), 5)
        self.roster.record_dispatch("Agent 39", "followup")
        self.assertEqual(self.roster.recent_dispatches()[0]["request_id"], "followup")
        self.assertEqual(len({r["agent_name"] for r in self.roster.recent_dispatches()}), 5)

    def test_result_limits_and_exact_identifiers(self):
        names = ["Tokyo " + str(i) for i in range(1000)] + ["Oversized " + "雪" * 9000]
        self.path.write_text(json.dumps(names))
        result = self.roster.find_agents("Tokyo")
        self.assertEqual(len(result["agents"]), 5)
        self.assertTrue(result["truncated"])
        self.assertLessEqual(len(json.dumps(result).encode()), 8192)
        self.assertTrue(all(a["agent_name"] in names for a in result["agents"]))
        self.assertEqual(self.roster.find_agents("Oversized")["agents"], [])

    def test_payload_limit_includes_false_truncation_flag(self):
        record = {"agent_name": "Edge ", "purpose": "Edge", "last_used_at": None}
        overhead = len(json.dumps({"agents": [record], "truncated": False}).encode())
        record["agent_name"] += "x" * (8193 - overhead)
        self.path.write_text(json.dumps({"version": 1, "agents": [record], "recent_dispatches": []}))
        result = self.roster.find_agents("Edge")
        self.assertEqual(result, {"agents": [], "truncated": True})

    def test_corrupt_storage_is_not_replaced_and_clear_recovers(self):
        for raw in ["{", '{"version": 2}', "[42]", '{"version":1,"agents":[{}],"recent_dispatches":[]}']:
            self.path.write_text(raw)
            with self.assertRaises((ValueError, TypeError)):
                self.roster.record_dispatch("New")
            self.assertEqual(self.path.read_text(), raw)
        self.roster.clear()
        self.assertEqual(self.roster.get_agents(), [])
        self.assertEqual(self.roster.recent_dispatches(), [])

    def test_oversized_recent_reference_is_omitted_without_truncating_name(self):
        self.roster.record_dispatch("Agent " + "雪" * 9000, "large")
        self.assertEqual(self.roster.recent_dispatches(), [])
        with patch.object(agent, "get_agent_roster", return_value=self.roster):
            self.assertEqual(agent._render_recent_dispatches(), "None")

    def test_references_do_not_leak_extra_storage_fields(self):
        self.roster.record_dispatch("Tokyo", "request")
        data = json.loads(self.path.read_text())
        data["agents"][0]["private_note"] = "not searchable or returned"
        data["recent_dispatches"][0]["private_note"] = "not returned"
        self.path.write_text(json.dumps(data))
        self.assertNotIn("private_note", json.dumps(self.roster.find_agents("Tokyo")))
        self.assertNotIn("private_note", json.dumps(self.roster.recent_dispatches()))

    def test_clear_removes_migration_backup(self):
        self.path.write_text('["Old"]')
        self.roster.load()
        self.roster.clear()
        self.assertFalse(self.path.with_suffix(".legacy.json").exists())

    def test_prompt_excludes_full_roster_and_escapes_references(self):
        self.path.write_text(json.dumps([f"Dormant {i}" for i in range(1000)]))
        self.roster.record_dispatch('Tokyo "<search>"', 'request-"1')
        with patch.object(agent, "get_agent_roster", return_value=self.roster):
            content = agent.prepare_message_with_history("Continue", "<user_message>Previous</user_message>")[0]["content"]
        self.assertNotIn("Dormant", content)
        self.assertNotIn("<active_agents>", content)
        self.assertIn('request_id="request-&quot;1"', content)
        self.assertIn("&lt;search&gt;", content)
        self.assertIsNotNone(framed_history(content))
        self.path.write_text("broken")
        with patch.object(agent, "get_agent_roster", return_value=self.roster):
            self.assertIn("Directory unavailable", agent.prepare_message_with_history("Hi", "")[0]["content"])


class DiscoveryRoutingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.roster = AgentRoster(Path(self.directory.name) / "roster.json")
        self.patch = patch.object(tools, "get_agent_roster", return_value=self.roster)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def runtime(self):
        runtime = InteractionAgentRuntime.__new__(InteractionAgentRuntime)
        runtime._make_llm_call = AsyncMock()
        return runtime

    def response(self, name, arguments):
        return {"choices": [{"message": {"tool_calls": [{"id": name, "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}]}}]}

    async def test_lookup_then_dispatch_and_direct_followup(self):
        self.roster.record_dispatch("Tokyo", "original", "restaurants")
        runtime = self.runtime()
        runtime._make_llm_call.side_effect = [self.response("find_agents", {"query": "Tokyo restaurants"}), self.response("send_message_to_agent", {"agent_name": "Tokyo", "instructions": "Continue"})]
        with patch.object(tools, "get_execution_agent_logs", return_value=Mock()), patch.object(tools._EXECUTION_BATCH_MANAGER, "execute_agent", new=AsyncMock(return_value=SimpleNamespace(success=True))):
            with request_scope("followup", "Continue Tokyo"):
                summary = await runtime._run_interaction_loop("system", [])
                self.assertEqual(summary.tool_names, ["find_agents", "send_message_to_agent"])
                self.assertEqual(summary.execution_agents, {"Tokyo"})
                self.assertEqual(self.roster.recent_dispatches()[0]["request_id"], "followup")
                await asyncio.sleep(0)
            runtime._make_llm_call.side_effect = [self.response("send_message_to_agent", {"agent_name": "Tokyo", "instructions": "Make shorter"})]
            with request_scope("direct", "Make shorter"):
                summary = await runtime._run_interaction_loop("system", [])
                self.assertEqual(summary.tool_names, ["send_message_to_agent"])
                await asyncio.sleep(0)

    async def test_no_match_then_creation_and_purpose(self):
        runtime = self.runtime()
        runtime._make_llm_call.side_effect = [self.response("find_agents", {"query": "Budget"}), self.response("send_message_to_agent", {"agent_name": "Budget", "instructions": "Analyze", "purpose": "Q3 expense analysis"})]
        with patch.object(tools, "get_execution_agent_logs", return_value=Mock()), patch.object(tools._EXECUTION_BATCH_MANAGER, "execute_agent", new=AsyncMock(return_value=SimpleNamespace(success=True))):
            with request_scope("budget", "Analyze expenses"):
                await runtime._run_interaction_loop("system", [])
                self.assertEqual(self.roster.find_agents("expenses")["agents"], [])
                self.assertEqual(self.roster.find_agents("expense")["agents"][0]["purpose"], "Q3 expense analysis")
                await asyncio.sleep(0)

    async def test_lookup_failure_is_explicit_and_query_is_redacted(self):
        self.roster._roster_path.write_text("broken")
        runtime = self.runtime()
        result = runtime._execute_tool(_ToolCall("lookup", "find_agents", {"query": "secret Tokyo"}))
        self.assertFalse(result.success)
        spans = [s for s in trace_store.snapshot() if s["kind"] == "tool" and s["label"] == "find_agents"]
        self.assertNotIn("secret Tokyo", json.dumps(spans))
        self.assertFalse(tools.handle_tool_call("find_agents", {"query": ""}).success)

    async def test_result_delivery_does_not_allow_discovery(self):
        runtime = self.runtime()
        runtime._make_llm_call.side_effect = [self.response("find_agents", {"query": "Tokyo"}), self.response("send_message_to_user", {"message": "Completed"})]
        messages = []
        with patch.object(tools, "find_agents") as lookup, patch.object(tools, "get_conversation_log", return_value=Mock()):
            summary = await runtime._run_interaction_loop("system", messages, result_delivery=True)
        lookup.assert_not_called()
        self.assertEqual(summary.user_messages, ["Completed"])
        self.assertIn("Result delivery", messages[1]["content"])

    async def test_scheduling_shortcut_records_request_association(self):
        from server.agents.execution_agent.runtime import ExecutionAgentRuntime
        from server.services.scheduling import AGENT_NAME

        runtime = self.runtime()
        runtime.conversation_log = Mock()
        runtime._load_conversation_transcript = Mock(return_value="")
        with patch("server.services.execution.get_agent_roster", return_value=self.roster), patch("server.services.execution.get_execution_agent_logs", return_value=Mock()), patch.object(ExecutionAgentRuntime, "__init__", return_value=None), patch.object(ExecutionAgentRuntime, "execute", new=AsyncMock(return_value=SimpleNamespace(success=True, response="Scheduled", error=None))):
            result = await runtime.execute("remind me in 3 minutes", request_id="schedule-request")
        self.assertTrue(result.success)
        self.assertEqual(self.roster.recent_dispatches()[0]["request_id"], "schedule-request")
        self.assertEqual(self.roster.recent_dispatches()[0]["agent_name"], AGENT_NAME)
        runtime._make_llm_call.assert_not_awaited()

    async def test_clear_prevents_stale_directory_repopulation(self):
        with request_scope("cancelled", "Work"):
            current_request.get().stopped_reason = "cleared"
            self.roster.clear()
            with self.assertRaises(asyncio.CancelledError):
                self.roster.record_dispatch("Late", "cancelled")
        self.assertEqual(self.roster.get_agents(), [])

    async def test_failed_submission_does_not_record_dispatch(self):
        with patch.object(self.roster, "record_dispatch", side_effect=OSError("unavailable")), patch.object(tools._EXECUTION_BATCH_MANAGER, "execute_agent", new=AsyncMock()) as execute:
            result = tools.handle_tool_call("send_message_to_agent", {"agent_name": "New", "instructions": "Work"})
            self.assertFalse(result.success)
            await asyncio.sleep(0)
            execute.assert_not_awaited()
        self.assertEqual(self.roster.recent_dispatches(), [])


@unittest.skipUnless(os.environ.get("OPENPOKE_EVALUATE_AGENT_DISCOVERY") == "1", "Opt-in evaluation requires a reachable model endpoint")
class LiveTaskOwnershipTests(unittest.IsolatedAsyncioTestCase):
    async def test_independent_email_tasks_and_followups(self):
        with tempfile.TemporaryDirectory() as directory:
            roster = AgentRoster(Path(directory) / "roster.json")
            roster.record_dispatch("Email Search Agent", "legacy-chipotle", "Search emails for Chipotle orders in the last year")
            runtime = InteractionAgentRuntime()
            transcript = ""
            dispatches = []
            calls = []
            tasks = [
                ("How many times did I order Chipotle in the last year?", "Chipotle orders"),
                ("What email did I get on September 1, 2026?", "September 1 emails"),
                ("Find the email from Jai.", "Jai correspondence"),
                ("Find the email from the Subaru dealership for parts I ordered.", "Subaru parts order"),
                ("For that Subaru parts-order email, show me the order number too.", "Subaru follow-up"),
                ("Limit that Chipotle order analysis to the last three months.", "Chipotle follow-up"),
            ]

            def execute(call):
                calls.append(call.name)
                if call.name == "find_agents":
                    return ToolResult(success=True, payload=roster.find_agents(**call.arguments))
                if call.name == "send_message_to_agent":
                    name = call.arguments["agent_name"]
                    purpose = call.arguments.get("purpose")
                    is_new = name not in roster.get_agents()
                    if is_new:
                        self.assertTrue(purpose and purpose.strip(), "New task needs a searchable purpose")
                        self.assertNotIn(name.casefold(), {"email search agent", "email search", "gmail agent", "research agent"})
                    roster.record_dispatch(name, current_request.get().request_id, purpose)
                    dispatches.append(name)
                    return ToolResult(success=True, payload={"status": "submitted", "agent_name": name})
                if call.name == "send_message_to_user":
                    return ToolResult(success=True, user_message=call.arguments["message"])
                return ToolResult(success=False, payload={"error": "Tool unavailable in this synthetic evaluation"})

            runtime._execute_tool = execute
            with patch.object(agent, "get_agent_roster", return_value=roster):
                for index, (prompt, task) in enumerate(tasks):
                    with request_scope(f"routing-{index}", prompt):
                        before = len(dispatches)
                        calls.clear()
                        messages = agent.prepare_message_with_history(prompt, transcript)
                        system = agent.SYSTEM_PROMPT + "\nGmail is connected. All search and email capabilities are available for delegation."
                        await asyncio.wait_for(runtime._run_interaction_loop(system, messages), timeout=90)
                        self.assertEqual(len(dispatches), before + 1, "Expected one task delegation")
                        selected = dispatches[-1]
                        if index == 0:
                            self.assertIn("find_agents", calls)
                            self.assertEqual(selected, "Email Search Agent", "Legacy generic agent can still own its original Chipotle task")
                        elif 1 <= index <= 3:
                            self.assertIn("find_agents", calls, "Independent tasks must search for their own task owner")
                            self.assertNotIn(selected, dispatches[:-1], "Shared email tools do not justify cross-task reuse")
                        elif index == 4:
                            self.assertEqual(selected, dispatches[3], "Subaru follow-up must retain its task owner")
                        elif index == 5:
                            self.assertEqual(selected, dispatches[0], "Chipotle follow-up must not use the most recent Subaru agent")
                        transcript += f'<user_message request_id="routing-{index}">{escape(prompt)}</user_message>\n'
                        transcript += f'<poke_reply request_id="routing-{index}">Synthetic result delivered for {escape(task)}.</poke_reply>\n'
            self.assertEqual(len(set(dispatches[:4])), 4)
            self.assertEqual(roster.find_agents("Email Search Agent")["agents"][0]["purpose"], "Search emails for Chipotle orders in the last year")


if __name__ == "__main__":
    unittest.main()
