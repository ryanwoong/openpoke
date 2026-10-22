from __future__ import annotations

import threading
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Optional

import httpx

from .config import get_settings


class ModelStore:
    def __init__(self, path: Path):
        self._path = path
        self._lock = threading.Lock()
        try:
            self._model = path.read_text(encoding="utf-8").strip() or None
        except FileNotFoundError:
            self._model = None

    def get(self) -> Optional[str]:
        with self._lock:
            return self._model

    def set(self, model: str) -> None:
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            temporary_path = self._path.with_suffix(".tmp")
            temporary_path.write_text(model, encoding="utf-8")
            temporary_path.replace(self._path)
            self._model = model


model_store = ModelStore(Path(__file__).resolve().parent / "data" / "model.txt")
_catalog: list[dict[str, Any]] = []
_catalog_expires = 0.0


async def get_model_catalog() -> list[dict[str, Any]]:
    global _catalog, _catalog_expires
    if _catalog and time.monotonic() < _catalog_expires:
        return _catalog

    api_key = get_settings().openrouter_api_key
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    async with httpx.AsyncClient() as client:
        response = await client.get("https://openrouter.ai/api/v1/models", headers=headers, timeout=20.0)
        response.raise_for_status()
        payload = response.json()
    models = []
    for model in payload["data"]:
        if "tools" not in model.get("supported_parameters", []):
            continue
        architecture = model.get("architecture") or {}
        if "text" not in architecture.get("input_modalities", []) or "text" not in architecture.get("output_modalities", []):
            continue
        pricing = model.get("pricing") or {}
        try:
            prices = [Decimal(str(pricing[key])) for key in ("prompt", "completion")]
        except (KeyError, InvalidOperation):
            continue
        if any(not price.is_finite() or price < 0 for price in prices):
            continue
        models.append({"id": model["id"], "name": model["name"], "context_length": model.get("context_length"), "pricing": pricing})
    if not models:
        raise ValueError("OpenRouter returned no compatible models with published pricing")
    _catalog = sorted(models, key=lambda model: model["name"].casefold())
    _catalog_expires = time.monotonic() + 300
    return _catalog
