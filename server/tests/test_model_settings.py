import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI

from server import model_settings
from server.openrouter_client.client import request_chat_completion
from server.routes.model_settings import router


def catalog_model(model_id="test/model", **overrides):
    return {
        "id": model_id,
        "name": model_id,
        "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
        "supported_parameters": ["tools"],
        "pricing": {"prompt": "0.000003", "completion": "0.000015"},
        **overrides,
    }


class ModelSettingsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = model_settings.ModelStore(Path(self.directory.name) / "model.txt")
        self.app = FastAPI()
        self.app.include_router(router)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://test")
        self.addAsyncCleanup(self.client.aclose)

    async def test_catalog_filters_unsupported_models_and_preserves_pricing(self):
        models = [
            catalog_model(),
            catalog_model("test/free", pricing={"prompt": "0", "completion": "0"}),
            catalog_model("test/no-tools", supported_parameters=[]),
            catalog_model("test/image", architecture={"input_modalities": ["text"], "output_modalities": ["image"]}),
            catalog_model("test/dynamic", pricing={"prompt": "-1", "completion": "-1"}),
            catalog_model("test/invalid", pricing={"prompt": "NaN", "completion": "0"}),
        ]
        transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"data": models}))
        upstream = httpx.AsyncClient(transport=transport)
        with patch.object(model_settings, "_catalog", []), patch.object(model_settings.httpx, "AsyncClient", return_value=upstream):
            result = await model_settings.get_model_catalog()
            cached = await model_settings.get_model_catalog()
        self.assertEqual([model["id"] for model in result], ["test/free", "test/model"])
        self.assertEqual(result[1]["pricing"]["prompt"], "0.000003")
        self.assertIs(result, cached)

    async def test_setting_is_persisted_and_returned(self):
        with patch("server.routes.model_settings.model_store", self.store), patch("server.routes.model_settings.get_model_catalog", AsyncMock(return_value=[catalog_model()])):
            response = await self.client.put("/settings/model", json={"model": "test/model"})
            self.assertEqual(response.status_code, 200)
            response = await self.client.get("/settings/model")
        self.assertEqual(response.json()["model"], "test/model")
        reloaded = model_settings.ModelStore(Path(self.directory.name) / "model.txt")
        self.assertEqual(reloaded.get(), "test/model")

    async def test_unknown_model_does_not_replace_selection(self):
        self.store.set("test/original")
        with patch("server.routes.model_settings.model_store", self.store), patch("server.routes.model_settings.get_model_catalog", AsyncMock(return_value=[catalog_model()])):
            response = await self.client.put("/settings/model", json={"model": "test/unknown"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.store.get(), "test/original")

    async def test_upstream_failure_returns_actionable_error(self):
        with patch("server.routes.model_settings.get_model_catalog", AsyncMock(side_effect=httpx.ConnectError("offline"))):
            response = await self.client.get("/settings/model")
        self.assertEqual(response.status_code, 502)
        self.assertIn("Try again", response.json()["detail"])

    async def test_invalid_input_is_rejected(self):
        response = await self.client.put("/settings/model", json={"model": ""})
        self.assertEqual(response.status_code, 422)

    async def test_completion_uses_saved_model_and_preserves_default_without_selection(self):
        captured = []

        def respond(request):
            captured.append(json.loads(request.content))
            return httpx.Response(200, json={"choices": []})

        for selected in (None, "test/selected"):
            upstream = httpx.AsyncClient(transport=httpx.MockTransport(respond))
            with patch("server.openrouter_client.client.model_store.get", return_value=selected), patch("server.openrouter_client.client.httpx.AsyncClient", return_value=upstream):
                await request_chat_completion(model="test/default", messages=[{"role": "user", "content": "Hello"}], api_key="test-key")
        self.assertEqual([payload["model"] for payload in captured], ["test/default", "test/selected"])

    async def test_settings_save_changes_next_request_and_logs_actual_models(self):
        captured = []

        def respond(request):
            captured.append(json.loads(request.content))
            return httpx.Response(200, json={"model": "test/served-model", "choices": []})

        with patch("server.routes.model_settings.model_store", self.store), patch("server.openrouter_client.client.model_store", self.store), patch("server.routes.model_settings.get_model_catalog", AsyncMock(return_value=[catalog_model("openrouter/free")])):
            for expected in ("test/startup-default", "openrouter/free"):
                upstream = httpx.AsyncClient(transport=httpx.MockTransport(respond))
                with patch("server.openrouter_client.client.httpx.AsyncClient", return_value=upstream), self.assertLogs("openpoke.server", level="INFO") as logs:
                    await request_chat_completion(model="test/startup-default", messages=[{"role": "user", "content": "Hello"}], api_key="test-key")
                self.assertIn(f"OpenRouter request: model={expected}", logs.output[0])
                self.assertIn(f"requested_model={expected}, returned_model=test/served-model", logs.output[1])
                if expected == "test/startup-default":
                    response = await self.client.put("/settings/model", json={"model": "openrouter/free"})
                    self.assertEqual(response.status_code, 200)
        self.assertEqual([payload["model"] for payload in captured], ["test/startup-default", "openrouter/free"])


if __name__ == "__main__":
    unittest.main()
