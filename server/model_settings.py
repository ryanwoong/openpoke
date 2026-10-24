from __future__ import annotations

import json
import os
import threading
import time
import uuid
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

import httpx

from .config import get_settings


def new_model_entry(*, kind: str, model: str, name: str = "", base_url: str = "", api_key: str = "", context_length: Optional[int] = None) -> dict[str, Any]:
    entry = {"id": uuid.uuid4().hex[:12], "kind": kind, "model": model, "name": name}
    if kind == "openai_compatible":
        entry.update(base_url=base_url, api_key=api_key)
    if isinstance(context_length, int) and not isinstance(context_length, bool) and context_length >= 1024:
        entry["context_length"] = context_length
    return entry


class SavedModelStore:
    """Persists the user's saved models, including endpoint API keys, and which one is active."""

    def __init__(self, path: Path, legacy_model_path: Optional[Path] = None, default_model: str = ""):
        self._path = path
        self._lock = threading.Lock()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            self._models: list[dict[str, Any]] = data["models"]
            self._active: Optional[str] = data.get("active")
        except (FileNotFoundError, ValueError, KeyError, TypeError):
            # Start from the model chosen before saved models existed so upgrades keep working.
            try:
                legacy_model = legacy_model_path.read_text(encoding="utf-8").strip() if legacy_model_path else ""
            except FileNotFoundError:
                legacy_model = ""
            model = legacy_model or default_model
            # A fixed id keeps the seed addressable across restarts until something is saved.
            seed = {**new_model_entry(kind="openrouter", model=model), "id": "default"} if model else None
            self._models = [seed] if seed else []
            self._active = seed["id"] if seed else None

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = self._path.with_suffix(".tmp")
        temporary_path.write_text(json.dumps({"active": self._active, "models": self._models}), encoding="utf-8")
        os.chmod(temporary_path, 0o600)
        temporary_path.replace(self._path)

    def snapshot(self) -> tuple[Optional[str], list[dict[str, Any]]]:
        with self._lock:
            return self._active, [dict(model) for model in self._models]

    def active(self) -> Optional[dict[str, Any]]:
        with self._lock:
            return next((dict(model) for model in self._models if model["id"] == self._active), None)

    def api_key_for(self, base_url: str) -> str:
        """Return the key saved with an endpoint, so adding another model from it needs no re-entry."""
        with self._lock:
            return next((model["api_key"] for model in self._models if model.get("base_url") == base_url and model.get("api_key")), "")

    def add(self, entry: dict[str, Any], activate: bool) -> dict[str, Any]:
        with self._lock:
            identity = (entry["kind"], entry["model"], entry.get("base_url", ""))
            if any((model["kind"], model["model"], model.get("base_url", "")) == identity for model in self._models):
                raise ValueError("This model is already saved")
            self._models.append(entry)
            if activate or self._active is None:
                self._active = entry["id"]
            self._save()
            return dict(entry)

    def activate(self, model_id: str) -> None:
        with self._lock:
            if not any(model["id"] == model_id for model in self._models):
                raise KeyError(model_id)
            self._active = model_id
            self._save()

    def remove(self, model_id: str) -> None:
        with self._lock:
            if model_id == self._active:
                raise ValueError("Switch to another model before removing the active one")
            if not any(model["id"] == model_id for model in self._models):
                raise KeyError(model_id)
            self._models = [model for model in self._models if model["id"] != model_id]
            self._save()


@dataclass(frozen=True)
class CompletionTarget:
    label: str
    model: str
    base_url: Optional[str]
    api_key: Optional[str]
    context_length: Optional[int] = None


_data_directory = Path(__file__).resolve().parent / "data"
saved_models = SavedModelStore(_data_directory / "models.json", _data_directory / "model.txt", get_settings().interaction_agent_model)


def endpoint_label(base_url: str) -> str:
    return urlparse(base_url).netloc or base_url


def completion_target(default_model: str) -> CompletionTarget:
    """Resolve where the next completion goes; base_url/api_key of None mean the OpenRouter defaults."""
    active = saved_models.active()
    if active and active["kind"] == "openai_compatible":
        return CompletionTarget(endpoint_label(active["base_url"]), active["model"], active["base_url"], active["api_key"], active.get("context_length"))
    return CompletionTarget("OpenRouter", active["model"] if active else default_model, None, None, active.get("context_length") if active else None)


def model_provider_configured() -> bool:
    return completion_target("").base_url is not None or bool(get_settings().openrouter_api_key)


async def discover_endpoint_models(base_url: str, api_key: str = "") -> tuple[str, list[dict[str, Any]]]:
    """List models from an OpenAI-compatible server, returning the base URL that answered."""
    base_url = base_url.strip().rstrip("/")
    candidates = [base_url] if base_url.endswith("/v1") else [base_url, f"{base_url}/v1"]
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    async with httpx.AsyncClient() as client:
        for index, candidate in enumerate(candidates):
            response = await client.get(f"{candidate}/models", headers=headers, timeout=10.0)
            last = index == len(candidates) - 1
            if response.status_code == 404 and not last:
                continue
            response.raise_for_status()
            try:
                data = response.json().get("data")
            except (ValueError, AttributeError):
                data = None
            if not isinstance(data, list):
                if last:
                    raise ValueError("Server did not return an OpenAI-compatible model list")
                continue
            ids = sorted({model["id"] for model in data if isinstance(model, dict) and isinstance(model.get("id"), str) and model["id"]}, key=str.casefold)
            if not ids:
                raise ValueError("Server returned no models")
            lengths = {}
            for item in data:
                if isinstance(item, dict) and isinstance(item.get("id"), str):
                    length = item.get("context_length") or item.get("max_model_len")
                    if isinstance(length, int) and not isinstance(length, bool) and length >= 1024:
                        lengths[item["id"]] = length
            return candidate, [{"id": model_id, "name": model_id, "context_length": lengths.get(model_id), "pricing": {}} for model_id in ids]
    raise ValueError("Server did not return an OpenAI-compatible model list")


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
