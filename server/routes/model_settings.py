from __future__ import annotations

from typing import Any, Literal, Optional
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..config import get_settings
from ..model_settings import discover_endpoint_models, get_model_catalog, new_model_entry, saved_models

router = APIRouter(prefix="/settings/models", tags=["settings"])


class NewModel(BaseModel):
    kind: Literal["openrouter", "openai_compatible"]
    model: str = Field(min_length=1, max_length=256)
    name: str = Field(default="", max_length=120)
    base_url: Optional[str] = Field(default=None, max_length=2048)
    # None reuses a key already saved for the same endpoint.
    api_key: Optional[str] = Field(default=None, max_length=4096)
    activate: bool = True


class EndpointDiscovery(BaseModel):
    base_url: str = Field(min_length=1, max_length=2048)
    api_key: Optional[str] = Field(default=None, max_length=4096)


class ActiveModel(BaseModel):
    id: str = Field(min_length=1, max_length=64)


def _public(model: dict[str, Any]) -> dict[str, object]:
    view: dict[str, object] = {key: value for key, value in model.items() if key != "api_key"}
    if model["kind"] == "openai_compatible":
        view["has_api_key"] = bool(model.get("api_key"))
    return view


async def _catalog():
    try:
        return await get_model_catalog()
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(status_code=502, detail="OpenRouter returned an invalid model catalog") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Unable to load models from OpenRouter. Try again shortly.") from exc


def _endpoint_api_key(base_url: str, api_key: Optional[str]) -> str:
    if api_key is not None:
        return api_key.strip()
    base_url = base_url.strip().rstrip("/")
    return saved_models.api_key_for(base_url) or saved_models.api_key_for(f"{base_url}/v1")


async def _discover(base_url: str, api_key: str):
    parsed = urlparse(base_url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(status_code=422, detail="Enter a full server URL, like http://localhost:11434/v1")
    try:
        return await discover_endpoint_models(base_url, api_key)
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        hint = " Check the API key." if status in {401, 403} else ""
        raise HTTPException(status_code=502, detail=f"The server responded with {status} when listing models.{hint}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=f"{exc}. Check the URL points at an OpenAI-compatible API.") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Unable to reach {base_url.strip()}. Make sure the server is running.") from exc


def _listing():
    active, models = saved_models.snapshot()
    return {"active": active, "models": [_public(model) for model in models], "openrouter_key_configured": bool(get_settings().openrouter_api_key)}


@router.get("")
async def list_saved_models():
    return _listing()


@router.get("/openrouter")
async def list_openrouter_models():
    return {"models": await _catalog()}


@router.post("/discover")
async def discover_models(payload: EndpointDiscovery):
    base_url, models = await _discover(payload.base_url, _endpoint_api_key(payload.base_url, payload.api_key))
    return {"base_url": base_url, "models": models}


@router.post("")
async def add_model(payload: NewModel):
    if payload.kind == "openai_compatible":
        if not payload.base_url:
            raise HTTPException(status_code=422, detail="Enter the server URL")
        api_key = _endpoint_api_key(payload.base_url, payload.api_key)
        base_url, models = await _discover(payload.base_url, api_key)
        if not any(model["id"] == payload.model for model in models):
            raise HTTPException(status_code=400, detail="That model isn't served by this server. Discover models and choose one from the list.")
        match = next(model for model in models if model["id"] == payload.model)
        entry = new_model_entry(
            kind="openai_compatible",
            model=payload.model,
            name=payload.name.strip(),
            base_url=base_url,
            api_key=api_key,
            context_length=match.get("context_length"),
        )
    else:
        catalog = await _catalog()
        match = next((model for model in catalog if model["id"] == payload.model), None)
        if not match:
            raise HTTPException(status_code=400, detail="Choose an available OpenRouter model with tool calling support")
        entry = new_model_entry(kind="openrouter", model=payload.model, name=payload.name.strip() or match["name"], context_length=match.get("context_length"))
    try:
        saved_models.add(entry, activate=payload.activate)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _listing()


@router.put("/active")
async def set_active_model(payload: ActiveModel):
    try:
        saved_models.activate(payload.id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="That saved model no longer exists") from exc
    return _listing()


@router.delete("/{model_id}")
async def remove_model(model_id: str):
    try:
        saved_models.remove(model_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="That saved model no longer exists") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _listing()
