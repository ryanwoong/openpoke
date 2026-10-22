from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..config import get_settings
from ..model_settings import get_model_catalog, model_store

router = APIRouter(prefix="/settings/model", tags=["settings"])


class ModelSelection(BaseModel):
    model: str = Field(min_length=1, max_length=256)


async def _catalog():
    try:
        return await get_model_catalog()
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(status_code=502, detail="OpenRouter returned an invalid model catalog") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Unable to load models from OpenRouter. Try again shortly.") from exc


@router.get("")
async def get_model_settings():
    return {"model": model_store.get() or get_settings().interaction_agent_model, "models": await _catalog()}


@router.put("")
async def set_model_settings(payload: ModelSelection):
    models = await _catalog()
    if not any(model["id"] == payload.model for model in models):
        raise HTTPException(status_code=400, detail="Choose an available OpenRouter model with tool calling support")
    model_store.set(payload.model)
    return {"model": payload.model}
