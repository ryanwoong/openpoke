"""Simplified Execution Agent Runtime."""

import asyncio
import inspect
import json
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass

from ...execution_trace import traced
from ...services.conversation.request_context import current_request
from ...services.conversation.read_checkpoints import read_checkpoints
from ...services.gmail import get_active_gmail_user_id

from .agent import ExecutionAgent
from .tools.scheduling import SCHEMA as SCHEDULING_SCHEMA
from .tools import get_tool_schemas, get_tool_registry
from ...config import get_settings
from ...openrouter_client import request_chat_completion
from ...model_settings import model_provider_configured
from ...logging_config import logger


@dataclass
class ExecutionResult:
    """Result from an execution agent."""

    agent_name: str
    success: bool
    response: str
    error: Optional[str] = None
    tools_executed: List[str] = None
    user_response: Optional[str] = None
    instructions: Optional[str] = None


class ExecutionAgentRuntime:
    """Manages the execution of a single agent request."""

    MAX_TOOL_ITERATIONS = 8

    # Initialize execution agent runtime with settings, tools, and agent instance
    def __init__(self, agent_name: str):
        settings = get_settings()
        self.agent = ExecutionAgent(agent_name)
        self.api_key = settings.openrouter_api_key
        self.model = settings.execution_agent_model
        self.tool_registry = get_tool_registry(agent_name=agent_name)
        self.tool_schemas = get_tool_schemas()

        if not model_provider_configured():
            raise ValueError("No model provider configured. Set OPENROUTER_API_KEY or add an OpenAI-compatible model in Settings → Model.")

    # Main execution loop for running agent with LLM calls and tool execution
    @traced(
        "execution",
        lambda self, instructions, scheduling=False: (
            self.agent.name,
            {"instructions": instructions, "access": ["Agent execution history", "Gmail connection status"]},
        ),
    )
    async def execute(self, instructions: str, scheduling: bool = False) -> ExecutionResult:
        """Execute the agent with given instructions."""
        try:
            # Build system prompt with history
            system_prompt = self.agent.build_system_prompt() if current_request.get() else self.agent.build_system_prompt_with_history()

            # Start conversation with the instruction
            messages = [{"role": "user", "content": instructions}]
            if scheduling:
                self.tool_schemas = [SCHEDULING_SCHEMA]
                self.tool_registry = {"scheduleTask": self.tool_registry["scheduleTask"]}
            tools_executed: List[str] = []
            tool_errors: Dict[str, str] = {}
            final_response: Optional[str] = None
            scheduling_attempted = scheduling
            scheduling_error = "The model did not provide a valid task and future time. Please specify both."

            for iteration in range(self.MAX_TOOL_ITERATIONS):
                logger.info(f"[{self.agent.name}] Requesting plan (iteration {iteration + 1})")
                response = await self._make_llm_call(system_prompt, messages, with_tools=True)
                assistant_message = response.get("choices", [{}])[0].get("message", {})

                if not assistant_message:
                    raise RuntimeError("LLM response did not include an assistant message")

                raw_tool_calls = assistant_message.get("tool_calls", []) or []
                parsed_tool_calls = self._extract_tool_calls(raw_tool_calls)

                assistant_entry: Dict[str, Any] = {
                    "role": "assistant",
                    "content": assistant_message.get("content", "") or "",
                }
                if raw_tool_calls:
                    assistant_entry["tool_calls"] = raw_tool_calls
                messages.append(assistant_entry)

                if not parsed_tool_calls:
                    final_response = assistant_entry["content"] or "No action required."
                    if scheduling and iteration == 0:
                        messages.append(
                            {
                                "role": "user",
                                "content": "The scheduling tool is available in this request. Use scheduleTask if the task and future time are clear. Otherwise explain which details are missing. Do not claim a reminder was scheduled without calling the tool.",
                            }
                        )
                        continue
                    if scheduling_attempted:
                        final_response = "Nothing was scheduled. " + scheduling_error
                    break

                verified_messages = []
                schedule_errors = []
                for tool_call in parsed_tool_calls:
                    tool_name = tool_call.get("name", "")
                    tool_args = tool_call.get("arguments", {})
                    call_id = tool_call.get("id")

                    if not tool_name:
                        logger.warning("Tool call missing name: %s", tool_call)
                        failure = {"error": "Tool call missing name; unable to execute."}
                        tool_message = {
                            "role": "tool",
                            "tool_call_id": call_id or "unknown_tool",
                            "content": self._format_tool_result(tool_name or "<unknown>", False, failure, tool_args),
                        }
                        messages.append(tool_message)
                        continue

                    tools_executed.append(tool_name)
                    scheduling_attempted = scheduling_attempted or tool_name in ("scheduleTask", "createTrigger", "updateTrigger")
                    logger.info(f"[{self.agent.name}] Executing tool: {tool_name}")

                    success, result = await self._execute_tool(tool_name, tool_args)

                    if success:
                        tool_errors.pop(tool_name, None)
                        logger.info(f"[{self.agent.name}] Tool {tool_name} completed successfully")
                        record_payload = self._safe_json_dump(result)
                    else:
                        error_detail = result.get("error") if isinstance(result, dict) else str(result)
                        logger.warning(f"[{self.agent.name}] Tool {tool_name} failed: {error_detail}")
                        record_payload = error_detail
                        scheduling_error = str(error_detail)
                        tool_errors[tool_name] = str(error_detail)
                        if tool_name in ("scheduleTask", "createTrigger", "updateTrigger"):
                            schedule_errors.append(scheduling_error)

                    self.agent.record_tool_execution(tool_name, self._safe_json_dump(tool_args), record_payload)

                    if success and tool_name in ("scheduleTask", "createTrigger", "updateTrigger"):
                        verified_messages.append(result["user_message"])

                    tool_message = {
                        "role": "tool",
                        "tool_call_id": call_id or tool_name,
                        "content": self._format_tool_result(tool_name, success, result, tool_args),
                    }
                    messages.append(tool_message)

                if verified_messages:
                    final_response = "\n\n".join(verified_messages + [f"A schedule could not be saved: {error}" for error in schedule_errors])
                    self.agent.record_response(final_response)
                    return ExecutionResult(
                        agent_name=self.agent.name,
                        success=not schedule_errors,
                        response=final_response,
                        error="; ".join(schedule_errors) or None,
                        tools_executed=tools_executed,
                        user_response=final_response,
                    )

            else:
                raise RuntimeError("Reached tool iteration limit without final response")

            if final_response is None:
                raise RuntimeError("LLM did not return a final response")

            if tool_errors and not scheduling_attempted:
                detail = "; ".join(f"{name}: {error}" for name, error in tool_errors.items())
                final_response = f"The task could not be completed: {detail}"
                self.agent.record_response(final_response)
                return ExecutionResult(agent_name=self.agent.name, success=False, response=final_response, error=detail, tools_executed=tools_executed)
            self.agent.record_response(final_response)

            return ExecutionResult(
                agent_name=self.agent.name,
                success=not scheduling_attempted,
                response=final_response,
                error=scheduling_error if scheduling_attempted else None,
                tools_executed=tools_executed,
            )

        except Exception as e:
            logger.error(f"[{self.agent.name}] Execution failed: {e}")
            error_msg = str(e)
            failure_text = f"Failed to complete task: {error_msg}"
            self.agent.record_response(f"Error: {error_msg}")

            return ExecutionResult(agent_name=self.agent.name, success=False, response=failure_text, error=error_msg)

    # Execute OpenRouter API call with system prompt, messages, and optional tool schemas
    async def _make_llm_call(self, system_prompt: str, messages: List[Dict], with_tools: bool) -> Dict:
        """Make an LLM call."""
        tools_to_send = self.tool_schemas if with_tools else None
        logger.info("[%s] Calling LLM, tools: %d", self.agent.name, len(tools_to_send or []))
        return await request_chat_completion(model=self.model, messages=messages, system=system_prompt, api_key=self.api_key, tools=tools_to_send)

    # Parse and validate tool calls from LLM response into structured format
    def _extract_tool_calls(self, raw_tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Extract tool calls from an assistant message."""
        tool_calls: List[Dict[str, Any]] = []

        for tool in raw_tools:
            function = tool.get("function", {})
            name = function.get("name", "")
            args = function.get("arguments", "")

            if isinstance(args, str):
                try:
                    args = json.loads(args) if args else {}
                except json.JSONDecodeError:
                    args = {}

            if name:
                tool_calls.append(
                    {
                        "id": tool.get("id"),
                        "name": name,
                        "arguments": args,
                    }
                )

        return tool_calls

    # Safely convert objects to JSON with fallback to string representation
    def _safe_json_dump(self, payload: Any) -> str:
        """Serialize payload to JSON, falling back to string representation."""
        try:
            return json.dumps(payload, default=str)
        except TypeError:
            return str(payload)

    # Format tool execution results into JSON structure for LLM consumption
    def _format_tool_result(
        self,
        tool_name: str,
        success: bool,
        result: Any,
        arguments: Dict[str, Any],
    ) -> str:
        """Build a structured string for tool responses."""
        if success:
            payload: Dict[str, Any] = {
                "tool": tool_name,
                "status": "success",
                "arguments": arguments,
                "result": result,
            }
        else:
            error_detail = result.get("error") if isinstance(result, dict) else str(result)
            payload = {
                "tool": tool_name,
                "status": "error",
                "arguments": arguments,
                "error": error_detail,
            }
        return self._safe_json_dump(payload)

    # Execute tool function from registry with error handling and async support
    @traced("tool", lambda self, tool_name, arguments: (tool_name, arguments))
    async def _execute_tool(self, tool_name: str, arguments: Dict) -> Tuple[bool, Any]:
        """Execute a tool. Returns (success, result)."""
        context = current_request.get()
        if context and context.stopped_reason:
            raise asyncio.CancelledError()
        mailbox = get_active_gmail_user_id() if tool_name == "task_email_search" and context else None
        if context and context.retry_of and tool_name != "task_email_search":
            return False, {"error": "Saved-result retries only permit email searches. Start a new request for actions that change data."}
        if context and tool_name != "task_email_search":
            context.read_only = False
            read_checkpoints.invalidate(context.request_id)
        if mailbox:
            found, cached = read_checkpoints.get(tool_name, arguments, mailbox)
            if found:
                read_checkpoints.put(tool_name, arguments, mailbox, cached)
                logger.info("Reusing completed email read for request %s", context.request_id)
                return True, cached
        tool_func = self.tool_registry.get(tool_name)
        if not tool_func:
            return False, {"error": f"Unknown tool: {tool_name}"}

        try:
            if inspect.iscoroutinefunction(tool_func):
                result = tool_func(**arguments)
            else:
                result = await asyncio.to_thread(tool_func, **arguments)
            if inspect.isawaitable(result):
                result = await result
            if isinstance(result, dict) and (result.get("error") or result.get("success") is False or result.get("successful") is False):
                return False, {"error": result.get("error") or "Tool reported failure"}
            if mailbox:
                read_checkpoints.put(tool_name, arguments, mailbox, result)
            return True, result
        except Exception as e:
            return False, {"error": str(e)}
