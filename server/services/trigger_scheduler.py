"""Background scheduler that watches trigger definitions and executes them."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Optional, Set

from ..agents.execution_agent.batch_manager import ExecutionBatchManager
from ..agents.execution_agent.runtime import ExecutionResult
from ..logging_config import logger
from .triggers import TriggerRecord, get_trigger_service
from .scheduling import PAYLOAD_PREFIX, decode_action
from .conversation import get_conversation_log
from .gmail import execute_gmail_tool, get_active_gmail_user_id

UTC = timezone.utc


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _isoformat(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


class TriggerScheduler:
    """Polls stored triggers and launches execution agents when due."""

    def __init__(self, poll_interval_seconds: float = 10.0) -> None:
        self._poll_interval = poll_interval_seconds
        self._service = get_trigger_service()
        self._task: Optional[asyncio.Task[None]] = None
        self._running = False
        self._in_flight: Set[int] = set()
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        async with self._lock:
            if self._task and not self._task.done():
                return
            loop = asyncio.get_running_loop()
            self._running = True
            self._task = loop.create_task(self._run(), name="trigger-scheduler")
            logger.info("Trigger scheduler started", extra={"interval": self._poll_interval})

    async def stop(self) -> None:
        async with self._lock:
            self._running = False
            if self._task:
                self._task.cancel()
                try:
                    await self._task
                except asyncio.CancelledError:
                    pass
                self._task = None
                logger.info("Trigger scheduler stopped")

    async def _run(self) -> None:
        try:
            while self._running:
                await self._poll_once()
                await asyncio.sleep(self._poll_interval)
        except asyncio.CancelledError:  # pragma: no cover - shutdown path
            raise
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("Trigger scheduler loop crashed", extra={"error": str(exc)})

    async def _poll_once(self) -> None:
        now = _utc_now()
        due_triggers = self._service.get_due_triggers(before=now)
        if not due_triggers:
            return

        for trigger in due_triggers:
            if trigger.id in self._in_flight:
                continue
            self._in_flight.add(trigger.id)
            asyncio.create_task(self._execute_trigger(trigger), name=f"trigger-{trigger.id}")

    async def _execute_trigger(self, trigger: TriggerRecord) -> None:
        try:
            current = self._service.get_trigger(trigger.id)
            if current is None or current != trigger or current.status != "active" or not current.next_trigger:
                return
            claimed = self._service.update_if_unchanged(current, {"status": "running"})
            if claimed is None:
                return
            trigger = claimed
            fired_at = _utc_now()
            action = decode_action(trigger.payload)
            if action is not None:
                await self._deliver_action(action)
                self._handle_success(trigger, fired_at)
                return
            instructions = self._format_instructions(trigger, fired_at)
            logger.info(
                "Dispatching trigger",
                extra={
                    "trigger_id": trigger.id,
                    "agent": trigger.agent_name,
                    "scheduled_for": trigger.next_trigger,
                },
            )
            execution_manager = ExecutionBatchManager()
            result = await execution_manager.execute_agent(
                trigger.agent_name,
                instructions,
            )
            if result.success:
                self._handle_success(trigger, fired_at)
            else:
                error_text = result.error or result.response
                self._handle_failure(trigger, fired_at, error_text)
        except Exception as exc:  # pragma: no cover - defensive
            self._handle_failure(trigger, _utc_now(), str(exc))
            logger.exception(
                "Trigger execution failed unexpectedly",
                extra={"trigger_id": trigger.id, "agent": trigger.agent_name},
            )
        finally:
            self._in_flight.discard(trigger.id)

    async def _deliver_action(self, action):
        if action["kind"] == "reminder":
            get_conversation_log().record_reply(action["message"])
            return
        if not action.get("approved_at"):
            raise ValueError("Scheduled email has not been approved; nothing was sent")
        user_id = get_active_gmail_user_id()
        if not user_id:
            raise ValueError("Gmail is not connected; scheduled email was not sent")
        draft = await asyncio.to_thread(
            execute_gmail_tool,
            "GMAIL_CREATE_EMAIL_DRAFT",
            user_id,
            arguments={"recipient_email": action["recipient_email"], "subject": action["subject"], "body": action["body"], "is_html": False},
        )
        self._check_gmail_result(draft)
        data = draft.get("data", draft)
        draft_id = data.get("draft_id") or data.get("id") or data.get("draft", {}).get("id")
        if not draft_id:
            raise ValueError("Gmail did not return a draft ID; scheduled email was not sent")
        result = await asyncio.to_thread(execute_gmail_tool, "GMAIL_SEND_DRAFT", user_id, arguments={"draft_id": draft_id})
        self._check_gmail_result(result)
        get_conversation_log().record_reply(f"Scheduled email sent to {action['recipient_email']}: {action['subject']}")

    @staticmethod
    def _check_gmail_result(result):
        if result.get("error") or result.get("successful") is False or result.get("success") is False:
            raise ValueError(f"Gmail scheduled email failed: {result.get('error') or 'Service reported failure'}")

    def _handle_success(self, trigger: TriggerRecord, fired_at: datetime) -> None:
        logger.info(
            "Trigger completed",
            extra={"trigger_id": trigger.id, "agent": trigger.agent_name},
        )
        self._service.schedule_next_occurrence(trigger, fired_at=fired_at)

    def _handle_failure(self, trigger: TriggerRecord, fired_at: datetime, error: str) -> None:
        logger.warning(
            "Trigger execution failed",
            extra={
                "trigger_id": trigger.id,
                "agent": trigger.agent_name,
                "error": error,
            },
        )
        if trigger.payload.startswith(PAYLOAD_PREFIX):
            get_conversation_log().record_reply(f"Scheduled task failed: {error}")
        if trigger.recurrence_rule:
            self._service.schedule_next_occurrence(trigger, fired_at=fired_at)
        else:
            self._service.update_if_unchanged(trigger, {"status": "paused", "next_trigger": None, "last_error": error})

    def _format_instructions(self, trigger: TriggerRecord, fired_at: datetime) -> str:
        scheduled_for = trigger.next_trigger or _isoformat(fired_at)
        metadata_lines = [f"Trigger ID: {trigger.id}"]
        if trigger.recurrence_rule:
            metadata_lines.append(f"Recurrence: {trigger.recurrence_rule}")
        if trigger.timezone:
            metadata_lines.append(f"Timezone: {trigger.timezone}")
        if trigger.start_time:
            metadata_lines.append(f"Start Time (UTC): {trigger.start_time}")

        metadata = "\n".join(f"- {line}" for line in metadata_lines)
        return f"Trigger fired at {_isoformat(fired_at)} (UTC).\nScheduled occurrence time: {scheduled_for}.\n\nMetadata:\n{metadata}\n\nPayload:\n{trigger.payload}"


_scheduler_instance: Optional[TriggerScheduler] = None


def get_trigger_scheduler() -> TriggerScheduler:
    global _scheduler_instance
    if _scheduler_instance is None:
        _scheduler_instance = TriggerScheduler()
    return _scheduler_instance


__all__ = ["TriggerScheduler", "get_trigger_scheduler"]
