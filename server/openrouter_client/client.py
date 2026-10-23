from __future__ import annotations

import json
import ssl
from typing import Any, Dict, List, Optional

import httpx

from ..execution_trace import traced

from ..config import get_settings
from ..logging_config import logger
from ..model_settings import model_store

OpenRouterBaseURL = "https://openrouter.ai/api/v1"


class OpenRouterError(RuntimeError):
    """Raised when the OpenRouter API returns an error response."""


def _headers(*, api_key: Optional[str] = None) -> Dict[str, str]:
    settings = get_settings()
    key = (api_key or settings.openrouter_api_key or "").strip()
    if not key:
        raise OpenRouterError("Missing OpenRouter API key")

    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    return headers


def _build_messages(messages: List[Dict[str, str]], system: Optional[str]) -> List[Dict[str, str]]:
    if system:
        return [{"role": "system", "content": system}, *messages]
    return messages


def _handle_response_error(exc: httpx.HTTPStatusError) -> None:
    response = exc.response
    detail: str
    try:
        payload = response.json()
        detail = payload.get("error") or payload.get("message") or json.dumps(payload)
    except Exception:
        detail = response.text
    raise OpenRouterError(f"OpenRouter request failed ({response.status_code}): {detail}") from exc


@traced("model", lambda **kwargs: ("OpenRouter · " + (model_store.get() or kwargs["model"]), {"model": model_store.get() or kwargs["model"], "message_count": len(kwargs["messages"]), "tools": [tool.get("function", {}).get("name") for tool in (kwargs.get("tools") or [])]}))
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

    payload: Dict[str, object] = {
        "model": model_store.get() or model,
        "messages": _build_messages(messages, system),
        "stream": False,
    }
    if tools:
        payload["tools"] = tools

    url = f"{base_url.rstrip('/')}/chat/completions"
    logger.info("OpenRouter request: model=%s, tools=%d", payload["model"], len(tools or []))

    for attempt in range(2):
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    url,
                    headers=_headers(api_key=api_key),
                    json=payload,
                    timeout=60.0,
                )
                response.raise_for_status()
                result = response.json()
                logger.info("OpenRouter response: requested_model=%s, returned_model=%s", payload["model"], result.get("model", "unknown"))
                return result
            except httpx.HTTPStatusError as exc:
                _handle_response_error(exc)
            except (httpx.ConnectError, ssl.SSLError) as exc:
                if attempt == 0 and not isinstance(exc, ssl.SSLCertVerificationError):
                    logger.warning("OpenRouter connection failed; retrying with a fresh connection: %s", exc)
                    continue
                raise OpenRouterError(f"OpenRouter request failed: {exc}") from exc
            except httpx.HTTPError as exc:
                raise OpenRouterError(f"OpenRouter request failed: {exc}") from exc

    raise OpenRouterError("OpenRouter request failed: unknown error")


__all__ = ["OpenRouterError", "request_chat_completion", "OpenRouterBaseURL"]
