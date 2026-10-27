"""Interaction Agent Runtime - handles LLM calls for user and agent turns."""

import asyncio
import json
from dataclasses import dataclass, field
from html import escape
from typing import Any, Dict, List, Optional, Set

from ...execution_trace import traced
from ...services.conversation.request_context import current_request, request_scope, task_failure_message
from ...services.scheduling import AGENT_NAME, is_scheduling_request, resolve_email_approval, scheduling_context

from .agent import build_system_prompt, prepare_message_with_history
from .tools import ToolResult, get_tool_schemas, handle_tool_call
from ...config import get_settings
from ...services.conversation import get_conversation_log, get_working_memory_log
from ...openrouter_client import request_chat_completion
from ...model_settings import model_provider_configured
from ...logging_config import logger


@dataclass
class InteractionResult:
    """Result from the interaction agent."""

    success: bool
    response: str
    error: Optional[str] = None
    execution_agents_used: int = 0


@dataclass
class _ToolCall:
    """Parsed tool invocation from an LLM response."""

    identifier: Optional[str]
    name: str
    arguments: Dict[str, Any]


@dataclass
class _LoopSummary:
    """Aggregate information produced by the interaction loop."""

    last_assistant_text: str = ""
    user_messages: List[str] = field(default_factory=list)
    tool_names: List[str] = field(default_factory=list)
    execution_agents: Set[str] = field(default_factory=set)
    yielded: bool = False


class InteractionAgentRuntime:
    """Manages the interaction agent's request processing."""

    MAX_TOOL_ITERATIONS = 8

    # Initialize interaction agent runtime with settings and service dependencies
    def __init__(self) -> None:
        settings = get_settings()
        self.api_key = settings.openrouter_api_key
        self.model = settings.interaction_agent_model
        self.settings = settings
        self.conversation_log = get_conversation_log()
        self.working_memory_log = get_working_memory_log()
        self.tool_schemas = get_tool_schemas()

        if not model_provider_configured():
            raise ValueError("No model provider configured. Set OPENROUTER_API_KEY or add an OpenAI-compatible model in Settings → Model.")

    # Main entry point for processing user messages through the LLM interaction loop
    @traced(
        "interaction",
        lambda self, user_message, request_id=None, retry_of=None: (
            "Interaction agent",
            {"prompt": user_message, "request_id": request_id, "access": ["Conversation history", "Working memory", "Agent roster", "Gmail connection status"]},
        ),
    )
    async def execute(self, user_message: str, request_id: Optional[str] = None, retry_of: Optional[str] = None) -> InteractionResult:
        """Handle a user-authored message."""
        with request_scope(request_id, user_message, timeout_seconds=get_settings().execution_timeout_seconds, retry_of=retry_of):
            return await self._execute_user_message(user_message)

    async def _execute_user_message(self, user_message: str) -> InteractionResult:
        try:
            transcript_before = self._load_conversation_transcript()
            self.conversation_log.record_user_message(user_message)

            approval = resolve_email_approval(user_message, self.conversation_log)
            if approval is not None:
                self.conversation_log.record_reply(approval)
                return InteractionResult(success=True, response=approval)

            if is_scheduling_request(user_message):
                from ..execution_agent.runtime import ExecutionAgentRuntime
                from ...services.execution import get_agent_roster, get_execution_agent_logs

                acknowledgement = "I'll check the requested time and prepare it."
                self.conversation_log.record_reply(acknowledgement)
                runtime = ExecutionAgentRuntime(AGENT_NAME)
                get_agent_roster().record_dispatch(AGENT_NAME, current_request.get().request_id, "Prepare reminders and scheduled tasks")
                get_execution_agent_logs().record_request(AGENT_NAME, user_message)
                result = await runtime.execute(f"{scheduling_context()}\n\nUser request: {user_message}", scheduling=True)
                self.conversation_log.record_reply(result.response)
                return InteractionResult(success=result.success, response=result.response, error=result.error, execution_agents_used=1)

            system_prompt = build_system_prompt() + (
                "\nScheduled updates in <scheduled_update> are independent background notifications. "
                "They do not answer, cancel, or replace the current user request or execution-agent task. "
                "Finish the task identified in the current turn, even if an unrelated scheduled update appears later in history. "
                "The current user message is the only authorization for new work. Do not restart failed or historical tasks "
                "in response to greetings or unrelated messages. Earlier answers are not evidence for a fresh search."
            )
            context = current_request.get()
            if context and context.retry_of:
                system_prompt += " This is an explicit retry using saved email results when available. Do not describe reused results as a newly fetched search; identify them as saved results."
            messages = prepare_message_with_history(user_message, transcript_before, message_type="user")

            logger.info("Processing user message through interaction agent")
            summary = await self._run_interaction_loop(system_prompt, messages)

            final_response = self._finalize_response(summary)

            if final_response and not summary.user_messages:
                self.conversation_log.record_reply(final_response)

            return InteractionResult(
                success=True,
                response=final_response,
                execution_agents_used=len(summary.execution_agents),
            )

        except Exception as exc:
            logger.error("Interaction agent failed", extra={"error": str(exc)})
            if is_scheduling_request(user_message):
                response = f"I couldn't prepare that schedule: {exc}"
                self.conversation_log.record_reply(response)
                return InteractionResult(success=False, response=response, error=str(exc))
            response = task_failure_message(str(exc))
            self.conversation_log.record_task_failure(response)
            return InteractionResult(success=False, response=response, error=str(exc))

    # Handle incoming messages from execution agents and generate appropriate responses
    @traced(
        "interaction",
        lambda self, agent_message: (
            "Interaction agent · results",
            {"agent_message": agent_message, "access": ["Conversation history", "Working memory", "Agent roster", "Gmail connection status"]},
        ),
    )
    async def handle_agent_message(self, agent_message: str) -> InteractionResult:
        """Process a status update emitted by an execution agent."""

        try:
            request = current_request.get()
            self.conversation_log.record_agent_message(agent_message)
            task = f"<originating_user_request>{escape(request.prompt, quote=False)}</originating_user_request>\n" if request else ""
            system_prompt = build_system_prompt() + ("\nRespond only to the originating task using the execution results in the current turn. This is a result delivery, not a new user instruction. Do not restart the task or start unrelated work. Prior chat answers and scheduled updates are not evidence for this task. A failed or incomplete search cannot support a verified total. State uncertainty explicitly. Give one final answer grounded in these results.")
            messages = prepare_message_with_history(task + agent_message, "", message_type="agent")

            logger.info("Processing execution agent results")
            self.tool_schemas = [schema for schema in get_tool_schemas() if schema["function"]["name"] == "send_message_to_user"]
            summary = await self._run_interaction_loop(system_prompt, messages, result_delivery=True)

            final_response = self._finalize_response(summary)
            if not final_response:
                raise RuntimeError("The model did not produce an answer for the completed task.")

            if final_response and not summary.user_messages:
                self.conversation_log.record_reply(final_response)

            return InteractionResult(
                success=True,
                response=final_response,
                execution_agents_used=len(summary.execution_agents),
            )

        except Exception as exc:
            logger.error("Interaction agent (agent message) failed", extra={"error": str(exc)})
            response = task_failure_message(str(exc))
            self.conversation_log.record_task_failure(response)
            return InteractionResult(success=False, response=response, error=str(exc))

    # Core interaction loop that handles LLM calls and tool executions until completion
    async def _run_interaction_loop(
        self,
        system_prompt: str,
        messages: List[Dict[str, Any]],
        result_delivery: bool = False,
    ) -> _LoopSummary:
        """Iteratively query the LLM until it issues a final response."""

        summary = _LoopSummary()

        for iteration in range(self.MAX_TOOL_ITERATIONS):
            response = await self._make_llm_call(system_prompt, messages)
            assistant_message = self._extract_assistant_message(response)

            assistant_content = (assistant_message.get("content") or "").strip()
            if assistant_content:
                summary.last_assistant_text = assistant_content

            raw_tool_calls = assistant_message.get("tool_calls") or []
            parsed_tool_calls = self._parse_tool_calls(raw_tool_calls)

            assistant_entry: Dict[str, Any] = {
                "role": "assistant",
                "content": assistant_message.get("content", "") or "",
            }
            if raw_tool_calls:
                assistant_entry["tool_calls"] = raw_tool_calls
            messages.append(assistant_entry)

            if not parsed_tool_calls:
                break

            for tool_call in parsed_tool_calls:
                if summary.yielded and tool_call.name != "send_message_to_agent":
                    continue
                summary.tool_names.append(tool_call.name)

                if result_delivery and tool_call.name != "send_message_to_user":
                    result = ToolResult(
                        success=False,
                        payload={"error": "Result delivery must send one answer from the supplied task results. It cannot wait, start new work, or perform other actions."},
                    )
                else:
                    result = self._execute_tool(tool_call)

                if result.success and tool_call.name == "send_message_to_agent":
                    agent_name = tool_call.arguments.get("agent_name")
                    if isinstance(agent_name, str) and agent_name:
                        summary.execution_agents.add(agent_name)
                    summary.yielded = True

                if result.user_message:
                    summary.user_messages.append(result.user_message)
                    if result_delivery:
                        return summary

                tool_message = {
                    "role": "tool",
                    "tool_call_id": tool_call.identifier or tool_call.name,
                    "content": self._format_tool_result(tool_call, result),
                }
                messages.append(tool_message)

                if result.success and tool_call.name == "wait":
                    summary.yielded = True
                    break

            if summary.yielded:
                return summary
        else:
            raise RuntimeError("Reached tool iteration limit without final response")

        if not summary.user_messages and not summary.last_assistant_text:
            logger.warning("Interaction loop exited without assistant content")

        return summary

    # Load conversation history, preferring summarized version if available
    def _load_conversation_transcript(self) -> str:
        if self.settings.summarization_enabled:
            rendered = self.working_memory_log.render_transcript()
            if rendered.strip():
                return rendered
        return self.conversation_log.load_transcript()

    # Execute API call to OpenRouter with system prompt, messages, and tool schemas
    async def _make_llm_call(
        self,
        system_prompt: str,
        messages: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Make an LLM call via OpenRouter."""

        logger.debug(
            "Interaction agent calling LLM",
            extra={"tools": len(self.tool_schemas)},
        )
        return await request_chat_completion(
            model=self.model,
            messages=messages,
            system=system_prompt,
            api_key=self.api_key,
            tools=self.tool_schemas,
        )

    # Extract the assistant's message from the OpenRouter API response structure
    def _extract_assistant_message(self, response: Dict[str, Any]) -> Dict[str, Any]:
        """Return the assistant message from the raw response payload."""

        choice = (response.get("choices") or [{}])[0]
        message = choice.get("message")
        if not isinstance(message, dict):
            raise RuntimeError("LLM response did not include an assistant message")
        return message

    # Convert raw LLM tool calls into structured _ToolCall objects with validation
    def _parse_tool_calls(self, raw_tool_calls: List[Dict[str, Any]]) -> List[_ToolCall]:
        """Normalize tool call payloads from the LLM."""

        parsed: List[_ToolCall] = []
        for raw in raw_tool_calls:
            function_block = raw.get("function") or {}
            name = function_block.get("name")
            if not isinstance(name, str) or not name:
                logger.warning("Skipping tool call without name", extra={"tool": raw})
                continue

            arguments, error = self._parse_tool_arguments(function_block.get("arguments"))
            if error:
                logger.warning("Tool call arguments invalid", extra={"tool": name, "error": error})
                parsed.append(
                    _ToolCall(
                        identifier=raw.get("id"),
                        name=name,
                        arguments={"__invalid_arguments__": error},
                    )
                )
                continue

            parsed.append(_ToolCall(identifier=raw.get("id"), name=name, arguments=arguments))

        return parsed

    # Parse and validate tool arguments from various formats (dict, JSON string, etc.)
    def _parse_tool_arguments(self, raw_arguments: Any) -> tuple[Dict[str, Any], Optional[str]]:
        """Convert tool arguments into a dictionary, reporting errors."""

        if raw_arguments is None:
            return {}, None

        if isinstance(raw_arguments, dict):
            return raw_arguments, None

        if isinstance(raw_arguments, str):
            if not raw_arguments.strip():
                return {}, None
            try:
                parsed = json.loads(raw_arguments)
            except json.JSONDecodeError as exc:
                return {}, f"invalid json: {exc}"
            if isinstance(parsed, dict):
                return parsed, None
            return {}, "decoded arguments were not an object"

        return {}, f"unsupported argument type: {type(raw_arguments).__name__}"

    # Execute tool calls with error handling and logging, returning standardized results
    @traced("tool", lambda self, tool_call: (tool_call.name, {"query": "[redacted]"} if tool_call.name == "find_agents" else tool_call.arguments))
    def _execute_tool(self, tool_call: _ToolCall) -> ToolResult:
        """Execute a tool call and convert low-level errors into structured results."""

        context = current_request.get()
        if context and context.stopped_reason:
            raise asyncio.CancelledError()
        if "__invalid_arguments__" in tool_call.arguments:
            error = tool_call.arguments["__invalid_arguments__"]
            self._log_tool_invocation(tool_call, stage="rejected", detail={"error": error})
            return ToolResult(success=False, payload={"error": error})

        try:
            self._log_tool_invocation(tool_call, stage="start")
            result = handle_tool_call(tool_call.name, tool_call.arguments)
        except Exception as exc:  # pragma: no cover - defensive
            logger.error(
                "Tool execution crashed",
                extra={"tool": tool_call.name, "error": str(exc)},
            )
            self._log_tool_invocation(
                tool_call,
                stage="error",
                detail={"error": str(exc)},
            )
            return ToolResult(success=False, payload={"error": str(exc)})

        if not isinstance(result, ToolResult):
            logger.warning(
                "Tool did not return ToolResult; coercing",
                extra={"tool": tool_call.name},
            )
            wrapped = ToolResult(success=True, payload=result)
            self._log_tool_invocation(tool_call, stage="done", result=wrapped)
            return wrapped

        status = "success" if result.success else "error"
        logger.debug(
            "Tool executed",
            extra={
                "tool": tool_call.name,
                "status": status,
            },
        )
        self._log_tool_invocation(tool_call, stage="done", result=result)
        return result

    # Format tool execution results into JSON for LLM consumption
    def _format_tool_result(self, tool_call: _ToolCall, result: ToolResult) -> str:
        """Render a tool execution result back to the LLM."""

        payload: Dict[str, Any] = {
            "tool": tool_call.name,
            "status": "success" if result.success else "error",
            "arguments": {key: value for key, value in tool_call.arguments.items() if key != "__invalid_arguments__"},
        }

        if result.payload is not None:
            key = "result" if result.success else "error"
            payload[key] = result.payload

        return self._safe_json_dump(payload)

    # Safely serialize objects to JSON with fallback to string representation
    def _safe_json_dump(self, payload: Any) -> str:
        """Serialize payload to JSON, falling back to repr on failure."""

        try:
            return json.dumps(payload, default=str)
        except TypeError:
            return repr(payload)

    # Log tool execution stages (start, done, error) with structured metadata
    def _log_tool_invocation(
        self,
        tool_call: _ToolCall,
        *,
        stage: str,
        result: Optional[ToolResult] = None,
        detail: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Emit structured logs for tool lifecycle events."""

        cleaned_args = {key: value for key, value in tool_call.arguments.items() if key != "__invalid_arguments__"}
        if tool_call.name == "find_agents":
            cleaned_args = {"query": "[redacted]"}

        log_payload: Dict[str, Any] = {
            "tool": tool_call.name,
            "stage": stage,
            "arguments": cleaned_args,
        }

        if result is not None:
            log_payload["success"] = result.success
            if result.payload is not None:
                log_payload["payload"] = result.payload

        if detail:
            log_payload.update(detail)

        if stage == "done":
            logger.info(f"Tool '{tool_call.name}' completed")
        elif stage in {"error", "rejected"}:
            logger.warning(f"Tool '{tool_call.name}' {stage}")
        else:
            logger.debug(f"Tool '{tool_call.name}' {stage}")

    # Determine final user-facing response from interaction loop summary
    def _finalize_response(self, summary: _LoopSummary) -> str:
        """Decide what text should be exposed to the user as the final reply."""

        if summary.user_messages:
            return summary.user_messages[-1]

        if summary.yielded:
            return ""

        return summary.last_assistant_text
