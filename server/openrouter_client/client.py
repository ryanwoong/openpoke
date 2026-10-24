from __future__ import annotations

import json
from collections import OrderedDict
from dataclasses import dataclass
import ssl
from typing import Any, Dict, List, Optional

import httpx

from ..execution_trace import traced

from ..config import get_settings
from ..logging_config import logger
from ..model_settings import completion_target
from .context import ContextBudgetError, SUMMARY_SYSTEM, context_compactor, context_overflow, estimate_tokens, input_budget
from .errors import OpenRouterError

OpenRouterBaseURL = "https://openrouter.ai/api/v1"


class ContextWindowError(OpenRouterError):
    def __init__(self, message: str, limit: Optional[int]):
        super().__init__(message)
        self.limit = limit


@dataclass(frozen=True)
class LearnedContextLimit:
    window: int
    input_tokens: int


_context_limits: OrderedDict[tuple, LearnedContextLimit] = OrderedDict()


def _headers(*, api_key: Optional[str] = None, required: bool = True) -> Dict[str, str]:
    # Self-hosted servers often need no key, so only OpenRouter requests fall back to the env key.
    key = (api_key or (get_settings().openrouter_api_key if required else "") or "").strip()
    if not key and required:
        raise OpenRouterError("Missing OpenRouter API key")

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if key:
        headers["Authorization"] = f"Bearer {key}"

    return headers


def _build_messages(messages: List[Dict[str, str]], system: Optional[str]) -> List[Dict[str, str]]:
    if system:
        return [{"role": "system", "content": system}, *messages]
    return messages


def _handle_response_error(exc: httpx.HTTPStatusError, label: str) -> None:
    response = exc.response
    detail: str
    try:
        payload = response.json()
        detail = payload.get("error") or payload.get("message") or json.dumps(payload)
    except Exception:
        detail = response.text
    rejected, limit = context_overflow(response.status_code, str(detail))
    if rejected:
        raise ContextWindowError(f"{label} request failed ({response.status_code}): {detail}", limit) from exc
    raise OpenRouterError(f"{label} request failed ({response.status_code}): {detail}") from exc


def _trace_details(**kwargs: Any):
    target = completion_target(kwargs["model"])
    return f"{target.label} · {target.model}", {
        "model": target.model,
        "provider": target.label,
        "message_count": len(kwargs["messages"]),
        "tools": [tool.get("function", {}).get("name") for tool in (kwargs.get("tools") or [])],
    }


async def _post_completion(payload, target, api_key, base_url):
    custom_endpoint = target.base_url is not None
    label = target.label
    url = f"{(target.base_url or base_url).rstrip('/')}/chat/completions"
    logger.info("%s request: model=%s, tools=%d", label, payload["model"], len(payload.get("tools") or []))
    for attempt in range(2):
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    url,
                    headers=_headers(api_key=target.api_key, required=False) if custom_endpoint else _headers(api_key=api_key),
                    json=payload,
                    # Self-hosted models can take minutes on long prompts.
                    timeout=300.0 if custom_endpoint else 60.0,
                )
                response.raise_for_status()
                result = response.json()
                logger.info("%s response: requested_model=%s, returned_model=%s", label, payload["model"], result.get("model", "unknown"))
                return result
            except httpx.HTTPStatusError as exc:
                _handle_response_error(exc, label)
            except (httpx.ConnectError, ssl.SSLError) as exc:
                if attempt == 0 and not isinstance(exc, ssl.SSLCertVerificationError):
                    logger.warning("%s connection failed; retrying with a fresh connection: %s", label, exc)
                    continue
                raise OpenRouterError(f"{label} request failed: {exc}") from exc
            except httpx.HTTPError as exc:
                raise OpenRouterError(f"{label} request failed: {exc!r}") from exc

    raise OpenRouterError(f"{label} request failed: unknown error")


@traced("model", _trace_details)
async def request_chat_completion(
    *,
    model: str,
    messages: List[Dict[str, Any]],
    system: Optional[str] = None,
    api_key: Optional[str] = None,
    tools: Optional[List[Dict[str, Any]]] = None,
    base_url: str = OpenRouterBaseURL,
) -> Dict[str, Any]:
    target = completion_target(model)
    settings = get_settings()
    scope = (target.base_url or base_url, target.model)
    learned = _context_limits.get(scope)
    window = learned.window if learned else target.context_length or settings.context_window_tokens
    reserve = min(settings.context_output_tokens, window // 4)
    budget = min(input_budget(window, reserve), learned.input_tokens) if learned else input_budget(window, reserve)
    payload = {"model": target.model, "messages": _build_messages(messages, system), "stream": False, "max_tokens": reserve}
    if tools:
        payload["tools"] = tools

    @traced(
        "model",
        lambda source, output_tokens: (
            "Context compaction · " + target.model,
            {"estimated_source_tokens": estimate_tokens(source), "output_tokens": output_tokens},
        ),
    )
    async def summarize(source: str, output_tokens: int) -> str:
        summary_payload = {
            "model": target.model,
            "messages": [{"role": "system", "content": SUMMARY_SYSTEM}, {"role": "user", "content": source}],
            "stream": False,
            "max_tokens": output_tokens,
        }
        response = await _post_completion(summary_payload, target, api_key, base_url)
        choices = response.get("choices") or []
        content = (choices[0].get("message") or {}).get("content") if choices else None
        if not isinstance(content, str) or not content.strip() or response.get("choices", [{}])[0].get("finish_reason") == "length":
            raise ContextBudgetError("The model could not finish a context summary. Narrow the request or choose a model with a larger context window.")
        return content.strip()

    for attempt in range(2):
        try:
            prepared, compacted = await context_compactor.prepare(payload, budget, summarize, str(scope))
            logger.debug("Model context: model=%s estimated_tokens=%d input_budget=%d compacted=%s", target.model, estimate_tokens(prepared), budget, compacted)
            return await _post_completion(prepared, target, api_key, base_url)
        except ContextWindowError as exc:
            if attempt:
                raise ContextBudgetError(
                    "The model still rejected the request after context compaction. Narrow the request or select a larger context window."
                ) from exc
            window = min(window, exc.limit) if exc.limit else max(1024, window // 2)
            reserve = min(reserve, window // 4)
            payload["max_tokens"] = reserve
            budget = min(budget // 2, input_budget(window, reserve))
            _context_limits[scope] = LearnedContextLimit(window, budget)
            _context_limits.move_to_end(scope)
            while len(_context_limits) > 64:
                _context_limits.popitem(last=False)
            logger.warning("Model context rejected; compacting once more: model=%s input_budget=%d", target.model, budget)
    raise ContextBudgetError("Unable to fit the request in the model context window")


__all__ = ["OpenRouterError", "request_chat_completion", "OpenRouterBaseURL"]
