import asyncio
import json
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from server.agents.execution_agent.runtime import ExecutionAgentRuntime, ExecutionResult
from server.agents.execution_agent.batch_manager import ExecutionBatchManager
from server.agents.interaction_agent.runtime import InteractionAgentRuntime
from server.services import scheduling
from server.services.trigger_scheduler import TriggerScheduler
from server.services.triggers.service import TriggerService
from server.services.triggers.store import TriggerStore
from server.services.triggers.utils import utc_now


def completion(name=None, arguments=None, content="All set!"):
    calls = [{"id": "test", "function": {"name": name, "arguments": json.dumps(arguments)}}] if name else []
    return {"choices": [{"message": {"content": content, "tool_calls": calls}}]}


class MemoryConversation:
    def __init__(self):
        self.entries = []

    def record_user_message(self, content):
        self.entries.append(("user_message", "", content))

    def record_reply(self, content):
        self.entries.append(("poke_reply", "", content))

    def record_task_failure(self, content):
        self.entries.append(("task_failure", "", content))

    def record_scheduled_update(self, content):
        self.entries.append(("scheduled_update", "", content))

    def iter_entries(self):
        return iter(self.entries)


class SchedulingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.service = TriggerService(TriggerStore(Path(self.directory.name) / "triggers.db"))
        self.log = MemoryConversation()
        patches = [
            patch.object(scheduling, "get_trigger_service", return_value=self.service),
            patch.object(scheduling, "get_timezone_store", return_value=Mock(get_timezone=Mock(return_value="America/Edmonton"))),
            patch("server.services.trigger_scheduler.get_trigger_service", return_value=self.service),
            patch("server.services.trigger_scheduler.get_conversation_log", return_value=self.log),
            patch(
                "server.agents.execution_agent.runtime.get_settings",
                return_value=SimpleNamespace(openrouter_api_key="test", execution_agent_model="user/selected"),
            ),
            patch("server.agents.execution_agent.agent.get_execution_agent_logs", return_value=Mock()),
            patch("server.services.execution.get_execution_agent_logs", return_value=Mock()),
            patch("server.services.execution.get_agent_roster", return_value=Mock()),
            patch("server.agents.execution_agent.agent.ExecutionAgent.build_system_prompt_with_history", return_value="system"),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        self.runtime = InteractionAgentRuntime.__new__(InteractionAgentRuntime)
        self.runtime.conversation_log = self.log
        self.runtime._load_conversation_transcript = Mock(return_value="")
        self.runtime._make_llm_call = AsyncMock(side_effect=AssertionError("Scheduling must bypass chat model"))
        self.start = (utc_now() + timedelta(minutes=3)).isoformat()

    def records(self):
        return self.service.list_triggers(agent_name=scheduling.AGENT_NAME)

    async def schedule(self, arguments, request="remind me to take a desk break 3 mins from now"):
        with patch.object(ExecutionAgentRuntime, "_make_llm_call", AsyncMock(return_value=completion("scheduleTask", arguments))):
            return await self.runtime.execute(request)

    async def test_reminder_is_saved_confirmed_and_delivered_without_a_model(self):
        result = await self.schedule({"kind": "reminder", "message": "Take a desk break", "start_time": self.start})
        self.assertTrue(result.success)
        record = self.records()[0]
        self.assertEqual(record.status, "active")
        self.assertIn("MDT", result.response)
        self.assertIn("Reminder scheduled", result.response)
        scheduler = TriggerScheduler()
        with patch.object(scheduler, "_format_instructions", side_effect=AssertionError("No model needed")), patch(
            "server.services.trigger_scheduler._utc_now", return_value=utc_now() + timedelta(minutes=4)
        ):
            await scheduler._poll_once()
            await asyncio.gather(*[task for task in asyncio.all_tasks() if task.get_name() == f"trigger-{record.id}"])
        self.assertEqual(self.log.entries[-1][2], "Take a desk break")
        self.assertEqual(self.records()[0].status, "completed")
        await scheduler._poll_once()
        self.assertEqual(sum(entry[2] == "Take a desk break" for entry in self.log.entries), 1)

    async def test_refusal_or_fabricated_success_cannot_confirm_a_schedule(self):
        for text in ("I don't have a reminder tool", "Your reminder is scheduled"):
            with patch.object(ExecutionAgentRuntime, "_make_llm_call", AsyncMock(return_value=completion(content=text))):
                result = await self.runtime.execute("remind me in 3 minutes")
            self.assertFalse(result.success)
            self.assertTrue(result.response.startswith("Nothing was scheduled."))
            self.assertNotIn(text, result.response)
        self.assertEqual(self.records(), [])

    async def test_storage_failure_is_reported_without_false_confirmation(self):
        responses = [completion("scheduleTask", {"kind": "reminder", "message": "Break", "start_time": self.start}), completion()]
        with patch.object(self.service, "create_trigger", side_effect=RuntimeError("Database unavailable")), patch.object(
            ExecutionAgentRuntime, "_make_llm_call", AsyncMock(side_effect=responses)
        ):
            result = await self.runtime.execute("remind me in 3 minutes")
        self.assertFalse(result.success)
        self.assertEqual(result.response, "Nothing was scheduled. Database unavailable")
        self.assertEqual(self.records(), [])

    async def test_email_requires_exact_draft_approval_and_sends_only_when_due(self):
        args = {"kind": "email", "recipient_email": "test@example.com", "subject": "Hello!", "body": "Testing openpoke!", "start_time": self.start}
        with patch("server.services.trigger_scheduler.execute_gmail_tool") as gmail:
            result = await self.schedule(args, "Can you email test@example.com at 3pm?")
            self.assertTrue(result.success)
            self.assertIn("Subject: Hello!\n\nTesting openpoke!", result.response)
            self.assertEqual(self.records()[0].status, "paused")
            scheduler = TriggerScheduler()
            await scheduler._poll_once()
            gmail.assert_not_called()
            approved = await self.runtime.execute("yes")
            self.assertIn("Email scheduled", approved.response)
            gmail.assert_not_called()
            record = self.records()[0]
            self.assertEqual(record.status, "active")
            self.assertEqual(self.service.get_due_triggers(before=utc_now()), [])
            self.service.update_trigger(record.id, agent_name=scheduling.AGENT_NAME, start_time=(utc_now() - timedelta(seconds=1)).isoformat())
            gmail.side_effect = [{"successful": True, "data": {"id": "draft-test"}}, {"successful": True}]
            with patch("server.services.trigger_scheduler.get_active_gmail_user_id", return_value="test-user"):
                await scheduler._poll_once()
                await asyncio.gather(*[task for task in asyncio.all_tasks() if task.get_name() == f"trigger-{record.id}"])
            self.assertEqual(gmail.call_count, 2)
            self.assertEqual(gmail.call_args_list[0].kwargs["arguments"]["body"], args["body"])
            self.assertEqual(gmail.call_args_list[1].kwargs["arguments"], {"draft_id": "draft-test"})
            self.assertIn("Scheduled email sent", self.log.entries[-1][2])
            self.assertEqual(self.records()[0].status, "completed")

    async def test_unapproved_email_is_blocked_even_if_trigger_is_activated(self):
        scheduling.schedule_task(kind="email", recipient_email="test@example.com", subject="Hello", body="Body", start_time=self.start)
        record = self.records()[0]
        self.service.update_trigger(record.id, agent_name=scheduling.AGENT_NAME, status="active")
        with patch("server.services.trigger_scheduler.execute_gmail_tool") as gmail:
            await TriggerScheduler()._execute_trigger(self.records()[0])
            gmail.assert_not_called()
        self.assertIn("has not been approved", self.records()[0].last_error)
        self.assertIn("Scheduled task failed", self.log.entries[-1][2])

    async def test_email_cancel_and_expired_approval_never_activate(self):
        for answer in ("no", "yes"):
            result = scheduling.schedule_task(kind="email", recipient_email="test@example.com", subject=answer, body="Body", start_time=self.start)
            self.log.record_reply(result["user_message"])
            if answer == "yes":
                record = self.records()[-1]
                self.service.update_trigger(record.id, agent_name=scheduling.AGENT_NAME, start_time=(utc_now() - timedelta(minutes=1)).isoformat())
                self.log.entries[-1] = ("poke_reply", "", scheduling.confirmation(self.records()[0]))
            result = await self.runtime.execute(answer)
            self.assertNotIn("Email scheduled", result.response)
        self.assertTrue(all(record.status != "active" for record in self.records()))

    async def test_gmail_failure_is_visible_and_not_reported_as_sent(self):
        action = {"kind": "email", "recipient_email": "test@example.com", "subject": "Hello", "body": "Body", "approved_at": self.start}
        record = self.service.create_trigger(agent_name=scheduling.AGENT_NAME, payload=scheduling.PAYLOAD_PREFIX + json.dumps(action), start_time=self.start)
        with patch("server.services.trigger_scheduler.get_active_gmail_user_id", return_value="user"), patch(
            "server.services.trigger_scheduler.execute_gmail_tool", return_value={"successful": False, "error": "Gmail unavailable"}
        ) as gmail:
            await TriggerScheduler()._execute_trigger(record)
        gmail.assert_called_once()
        self.assertIn("Gmail unavailable", self.records()[0].last_error)
        self.assertIn("Scheduled task failed", self.log.entries[-1][2])

    async def test_delegated_schedule_confirmation_is_not_rewritten_by_chat_model(self):
        manager = ExecutionBatchManager()
        batch = await manager._register_pending_execution("test", "schedule", "request")
        result = ExecutionResult(agent_name="test", success=True, response="Saved", user_response="Verified schedule")
        with patch("server.services.conversation.get_conversation_log", return_value=self.log), patch.object(
            manager, "_dispatch_to_interaction_agent", AsyncMock()
        ) as dispatch:
            await manager._complete_execution(batch, result, "test")
        dispatch.assert_not_awaited()
        self.assertEqual(self.log.entries[-1][2], "Verified schedule")

    async def test_model_gets_one_chance_to_recover_from_an_initial_refusal(self):
        responses = [completion(content="No reminder tool"), completion("scheduleTask", {"kind": "reminder", "message": "Break", "start_time": self.start})]
        with patch.object(ExecutionAgentRuntime, "_make_llm_call", AsyncMock(side_effect=responses)) as model:
            result = await self.runtime.execute("remind me in 3 minutes")
        self.assertTrue(result.success)
        self.assertEqual(model.await_count, 2)
        self.assertEqual(len(self.records()), 1)

    async def test_multiple_schedule_calls_are_all_saved(self):
        first = completion("scheduleTask", {"kind": "reminder", "message": "First", "start_time": self.start})
        second = completion("scheduleTask", {"kind": "reminder", "message": "Second", "start_time": self.start})
        first["choices"][0]["message"]["tool_calls"].extend(second["choices"][0]["message"]["tool_calls"])
        with patch.object(ExecutionAgentRuntime, "_make_llm_call", AsyncMock(return_value=first)):
            result = await self.runtime.execute("remind me in 3 minutes")
        self.assertTrue(result.success)
        self.assertEqual(len(self.records()), 2)
        self.assertIn("First", result.response)
        self.assertIn("Second", result.response)

    async def test_recurring_reminder_advances_after_delivery(self):
        scheduling.schedule_task(kind="reminder", message="Daily break", start_time=self.start, recurrence_rule="FREQ=DAILY")
        first = self.records()[0]
        with patch("server.services.trigger_scheduler._utc_now", return_value=utc_now() + timedelta(minutes=4)):
            await TriggerScheduler()._execute_trigger(first)
        updated = self.records()[0]
        self.assertEqual(updated.status, "active")
        self.assertGreater(updated.next_trigger, first.next_trigger)
        self.assertEqual(self.log.entries[-1][2], "Daily break")

    def test_unrelated_yes_cannot_approve_an_earlier_draft(self):
        result = scheduling.schedule_task(kind="email", recipient_email="test@example.com", subject="Hello", body="Body", start_time=self.start)
        self.log.record_reply(result["user_message"])
        self.log.record_user_message("What time is it?")
        self.log.record_reply("It is afternoon.")
        self.log.record_user_message("yes")
        self.assertIsNone(scheduling.resolve_email_approval("yes", self.log))
        self.assertEqual(self.records()[0].status, "paused")

    def test_generic_tools_cannot_forge_or_approve_structured_email(self):
        from server.agents.execution_agent.tools import triggers

        scheduling.schedule_task(kind="email", recipient_email="test@example.com", subject="Hello", body="Body", start_time=self.start)
        record = self.records()[0]
        with patch.object(triggers, "_TRIGGER_SERVICE", self.service):
            forged = triggers._create_trigger_tool(agent_name=scheduling.AGENT_NAME, payload=record.payload)
            activated = triggers._update_trigger_tool(agent_name=scheduling.AGENT_NAME, trigger_id=record.id, status="active")
        self.assertIn("error", forged)
        self.assertIn("error", activated)
        self.assertEqual(self.records()[0].status, "paused")

    async def test_tool_error_object_is_a_failure(self):
        runtime = ExecutionAgentRuntime("test")
        runtime.tool_registry = {"failure": Mock(return_value={"error": "Rejected"})}
        success, result = await runtime._execute_tool("failure", {})
        self.assertFalse(success)
        self.assertEqual(result["error"], "Rejected")

    def test_missing_or_past_time_never_creates_a_trigger(self):
        for start in ("invalid", (utc_now() - timedelta(seconds=1)).isoformat()):
            with self.assertRaises(ValueError):
                scheduling.schedule_task(kind="reminder", message="Break", start_time=start)
        self.assertEqual(self.records(), [])

    def test_scheduling_routes_common_phrasings_without_hijacking_explanations(self):
        for request in (
            "could you remind me to wash my hand at 3pm?",
            "set a timer for 3 minutes",
            "schedule an email tomorrow",
            "send me an email in three hours",
            "email test@example.com at 3pm",
        ):
            self.assertTrue(scheduling.is_scheduling_request(request), request)
        for request in ("how do I set a reminder?", "don't remind me", "search my email", "what is a timer?"):
            self.assertFalse(scheduling.is_scheduling_request(request), request)


if __name__ == "__main__":
    unittest.main()
