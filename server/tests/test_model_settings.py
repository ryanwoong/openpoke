import json
import os
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


def endpoint_models(*ids):
    return [{"id": model_id, "name": model_id, "context_length": None, "pricing": {}} for model_id in ids]


class ModelSettingsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "models.json"
        self.store = model_settings.SavedModelStore(self.path, default_model="test/default")
        for target in ("server.routes.model_settings.saved_models", "server.model_settings.saved_models"):
            patcher = patch(target, self.store)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.app = FastAPI()
        self.app.include_router(router)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://test")
        self.addAsyncCleanup(self.client.aclose)

    async def complete(self, respond):
        upstream = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        with patch("server.openrouter_client.client.httpx.AsyncClient", return_value=upstream), self.assertLogs("openpoke.server", level="INFO") as logs:
            await request_chat_completion(model="test/fallback", messages=[{"role": "user", "content": "Hello"}], api_key="openrouter-key")
        return logs.output

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

    async def test_advertised_context_window_is_saved_and_used_by_completion_target(self):
        with patch("server.routes.model_settings.get_model_catalog", AsyncMock(return_value=[catalog_model("test/context", context_length=131072)])):
            response = await self.client.post("/settings/models", json={"kind": "openrouter", "model": "test/context"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(model_settings.completion_target("fallback").context_length, 131072)
        self.assertEqual(model_settings.SavedModelStore(self.path).active()["context_length"], 131072)

    async def test_local_context_metadata_survives_discovery_and_save(self):
        upstream = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"data": [{"id": "local-context", "max_model_len": 16384}]}))
        )
        with patch.object(model_settings.httpx, "AsyncClient", return_value=upstream):
            base_url, models = await model_settings.discover_endpoint_models("http://localhost:1234/v1")
        self.assertEqual(models[0]["context_length"], 16384)
        with patch("server.routes.model_settings.discover_endpoint_models", AsyncMock(return_value=(base_url, models))):
            response = await self.client.post("/settings/models", json={"kind": "openai_compatible", "base_url": base_url, "model": "local-context"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(model_settings.completion_target("fallback").context_length, 16384)

    async def test_store_starts_from_previously_selected_model(self):
        legacy = Path(self.directory.name) / "model.txt"
        legacy.write_text("test/legacy\n", encoding="utf-8")
        store = model_settings.SavedModelStore(Path(self.directory.name) / "fresh.json", legacy, "test/default")
        active, models = store.snapshot()
        self.assertEqual([(model["kind"], model["model"]) for model in models], [("openrouter", "test/legacy")])
        self.assertEqual(active, models[0]["id"])

        active, models = self.store.snapshot()
        self.assertEqual([(model["id"], model["model"]) for model in models], [("default", "test/default")])
        self.assertEqual(model_settings.SavedModelStore(self.path, default_model="test/default").snapshot()[0], active)
        self.assertFalse(self.path.exists())

    async def test_adding_openrouter_model_validates_and_activates_it(self):
        with patch("server.routes.model_settings.get_model_catalog", AsyncMock(return_value=[catalog_model("test/model", name="Test Model")])):
            response = await self.client.post("/settings/models", json={"kind": "openrouter", "model": "test/unknown"})
            self.assertEqual(response.status_code, 400)
            response = await self.client.post("/settings/models", json={"kind": "openrouter", "model": "test/model"})
            self.assertEqual(response.status_code, 200)
            duplicate = await self.client.post("/settings/models", json={"kind": "openrouter", "model": "test/model"})
        self.assertEqual(duplicate.status_code, 409)
        listing = response.json()
        added = listing["models"][-1]
        self.assertEqual((added["name"], listing["active"]), ("Test Model", added["id"]))
        reloaded = model_settings.SavedModelStore(self.path)
        self.assertEqual(reloaded.active()["model"], "test/model")

    async def test_openrouter_catalog_failure_returns_actionable_error(self):
        with patch("server.routes.model_settings.get_model_catalog", AsyncMock(side_effect=httpx.ConnectError("offline"))):
            response = await self.client.get("/settings/models/openrouter")
        self.assertEqual(response.status_code, 502)
        self.assertIn("Try again", response.json()["detail"])

    async def test_invalid_input_is_rejected(self):
        response = await self.client.post("/settings/models", json={"kind": "openrouter", "model": ""})
        self.assertEqual(response.status_code, 422)
        response = await self.client.post("/settings/models", json={"kind": "anthropic", "model": "x"})
        self.assertEqual(response.status_code, 422)

    async def test_discovery_falls_back_to_v1_and_sends_key(self):
        seen = []

        def respond(request):
            seen.append((request.url.path, request.headers.get("authorization")))
            if request.url.path == "/models":
                return httpx.Response(404)
            return httpx.Response(200, json={"object": "list", "data": [{"id": "qwen3:8b"}, {"id": "llama3.1:8b"}, {"id": "qwen3:8b"}]})

        upstream = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        with patch.object(model_settings.httpx, "AsyncClient", return_value=upstream):
            response = await self.client.post("/settings/models/discover", json={"base_url": "http://localhost:11434/", "api_key": "secret"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["base_url"], "http://localhost:11434/v1")
        self.assertEqual([model["id"] for model in response.json()["models"]], ["llama3.1:8b", "qwen3:8b"])
        self.assertEqual(seen, [("/models", "Bearer secret"), ("/v1/models", "Bearer secret")])

    async def test_discovery_reports_unreachable_server_and_bad_urls(self):
        upstream = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: (_ for _ in ()).throw(httpx.ConnectError("refused"))))
        with patch.object(model_settings.httpx, "AsyncClient", return_value=upstream):
            response = await self.client.post("/settings/models/discover", json={"base_url": "http://localhost:1234/v1"})
        self.assertEqual(response.status_code, 502)
        self.assertIn("Make sure the server is running", response.json()["detail"])
        response = await self.client.post("/settings/models/discover", json={"base_url": "localhost:1234"})
        self.assertEqual(response.status_code, 422)

    async def test_endpoint_model_keeps_key_private_and_reuses_it_for_the_same_server(self):
        discovery = AsyncMock(return_value=("https://api.example.com/v1", endpoint_models("model-a", "model-b")))
        with patch("server.routes.model_settings.discover_endpoint_models", discovery):
            response = await self.client.post(
                "/settings/models", json={"kind": "openai_compatible", "base_url": "https://api.example.com", "api_key": "secret", "model": "missing"}
            )
            self.assertEqual(response.status_code, 400)
            response = await self.client.post(
                "/settings/models", json={"kind": "openai_compatible", "base_url": "https://api.example.com", "api_key": "secret", "model": "model-a"}
            )
            self.assertEqual(response.status_code, 200)
            response = await self.client.post(
                "/settings/models", json={"kind": "openai_compatible", "base_url": "https://api.example.com", "model": "model-b", "activate": False}
            )
        self.assertEqual(discovery.await_args.args, ("https://api.example.com", "secret"))
        listing = response.json()
        self.assertNotIn("secret", json.dumps(listing))
        a, b = listing["models"][1:]
        self.assertEqual((a["base_url"], a["has_api_key"], listing["active"]), ("https://api.example.com/v1", True, a["id"]))
        self.assertTrue(b["has_api_key"])
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)

    async def test_activation_routes_completions_and_active_model_cannot_be_removed(self):
        endpoint = self.store.add(
            model_settings.new_model_entry(kind="openai_compatible", model="local-model", base_url="http://localhost:1234/v1", api_key="secret"), activate=False
        )
        openrouter_id = self.store.active()["id"]
        captured = []

        def respond(request):
            captured.append((str(request.url), request.headers.get("authorization"), json.loads(request.content)["model"]))
            return httpx.Response(200, json={"model": "served", "choices": []})

        logs = await self.complete(respond)
        self.assertIn("OpenRouter request: model=test/default", logs[0])

        response = await self.client.put("/settings/models/active", json={"id": endpoint["id"]})
        self.assertEqual(response.json()["active"], endpoint["id"])
        logs = await self.complete(respond)
        self.assertIn("localhost:1234 request: model=local-model", logs[0])
        self.assertIn("requested_model=local-model, returned_model=served", logs[1])
        self.assertEqual(
            captured,
            [
                ("https://openrouter.ai/api/v1/chat/completions", "Bearer openrouter-key", "test/default"),
                ("http://localhost:1234/v1/chat/completions", "Bearer secret", "local-model"),
            ],
        )

        response = await self.client.delete(f"/settings/models/{endpoint['id']}")
        self.assertEqual(response.status_code, 400)
        response = await self.client.delete(f"/settings/models/{openrouter_id}")
        self.assertEqual([model["id"] for model in response.json()["models"]], [endpoint["id"]])
        response = await self.client.put("/settings/models/active", json={"id": openrouter_id})
        self.assertEqual(response.status_code, 404)

    async def test_endpoint_without_key_sends_no_authorization_and_needs_no_openrouter_key(self):
        self.store.add(model_settings.new_model_entry(kind="openai_compatible", model="m", base_url="http://localhost:8080/v1"), activate=True)
        captured = []

        def respond(request):
            captured.append(request.headers.get("authorization"))
            return httpx.Response(200, json={"choices": []})

        upstream = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        with patch("server.openrouter_client.client.httpx.AsyncClient", return_value=upstream), patch("server.model_settings.get_settings") as settings:
            settings.return_value.openrouter_api_key = None
            self.assertTrue(model_settings.model_provider_configured())
            await request_chat_completion(model="test/fallback", messages=[{"role": "user", "content": "Hello"}])
            self.store.activate(self.store.snapshot()[1][0]["id"])
            self.assertFalse(model_settings.model_provider_configured())
        self.assertEqual(captured, [None])


if __name__ == "__main__":
    unittest.main()
