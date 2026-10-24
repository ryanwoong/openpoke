from __future__ import annotations

import json
import ssl
from typing import Any, Dict, List, Optional

import httpx

from ..execution_trace import traced

from ..config import get_settings
from ..logging_config import logger
from ..model_settings import completion_target

OpenRouterBaseURL = "https://openrouter.ai/api/v1"


class OpenRouterError(RuntimeError):
    """Raised when the OpenRouter API returns an error response."""


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
    raise OpenRouterError(f"{label} request failed ({response.status_code}): {detail}") from exc


def _trace_details(**kwargs: Any):
    target = completion_target(kwargs["model"])
    return f"{target.label} · {target.model}", {"model": target.model, "provider": target.label, "message_count": len(kwargs["messages"]), "tools": [tool.get("function", {}).get("name") for tool in (kwargs.get("tools") or [])]}


@traced("model", _trace_details)
async def request_chat_completion(
    *,
    model: str,
    messages: List[Dict[str, str]],
    system: Optional[str] = None,
    api_key: Optional[str] = None,
    tools: Optional[List[Dict[str, Any]]] = None,
    base_url: str = OpenRouterBaseURL,
) -> Dict[str, Any]:
    """Request a chat completion and return the raw JSON payload."""

    target = completion_target(model)
    custom_endpoint = target.base_url is not None
    label = target.label
    payload: Dict[str, object] = {
        "model": target.model,
        "messages": _build_messages(messages, system),
        "stream": False,
    }
    if tools:
        payload["tools"] = tools

    url = f"{(target.base_url or base_url).rstrip('/')}/chat/completions"
    logger.info("%s request: model=%s, tools=%d", label, payload["model"], len(tools or []))

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


__all__ = ["OpenRouterError", "request_chat_completion", "OpenRouterBaseURL"]
