"""Provider presets, the first-run status and the connection probe.

Run: python backend/tests/test_setup.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="setup-"))

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from personal_os import llm, provider_keys, providers, setup  # noqa: E402
from personal_os.app import AUTH_TOKEN, app, db  # noqa: E402

client = TestClient(app, headers={"X-Personal-OS-Token": AUTH_TOKEN})


def _clear() -> None:
    db.secrets.delete("providerKeys")  # a key saved by an earlier test would satisfy a later "needs a key" check
    db.set_settings({"providers": [], "onboardedAt": None, "defaultModel": ""})


class RegistryTests(unittest.TestCase):
    def test_contract_ids_and_defaults(self) -> None:
        self.assertEqual([p["id"] for p in providers.PROVIDERS], ["fireworks", "openai", "anthropic", "openrouter", "ollama", "litellm", "custom"])
        for p in providers.PROVIDERS:
            if p["id"] != "custom":
                self.assertIn(p["defaultModel"], p["models"])
        self.assertFalse(providers.get("ollama")["needsKey"])
        self.assertEqual(providers.get("anthropic")["baseUrl"], "https://api.anthropic.com/v1/")

    def test_infer(self) -> None:
        self.assertIsNone(providers.infer(""))
        self.assertEqual(providers.infer("http://localhost:4000"), "litellm")
        self.assertEqual(providers.infer("http://127.0.0.1:4000/"), "litellm")
        self.assertEqual(providers.infer("https://api.openai.com/v1"), "openai")
        self.assertEqual(providers.infer("https://api.fireworks.ai/inference"), "fireworks")
        self.assertEqual(providers.infer("http://my-box:8080"), "custom")

    def test_endpoint_adds_v1_once(self) -> None:
        self.assertEqual(providers.endpoint("http://localhost:4000", "/models"), "http://localhost:4000/v1/models")
        self.assertEqual(providers.endpoint("https://api.anthropic.com/v1/", "/models"), "https://api.anthropic.com/v1/models")
        self.assertEqual(providers.endpoint("https://openrouter.ai/api/v1", "/chat/completions"), "https://openrouter.ai/api/v1/chat/completions")

    def test_unconfigured_llm_fails_clearly(self) -> None:
        with self.assertRaises(llm.LLMError):
            llm._url({"baseUrl": ""}, "/models")
        with self.assertRaises(llm.LLMError):
            llm._url({"baseUrl": "http://x"}, "/chat/completions", "")
        self.assertFalse(llm.supports_service_tier({"baseUrl": "https://api.anthropic.com/v1/"}))
        self.assertTrue(llm.supports_service_tier({"baseUrl": "http://localhost:4000"}))


class StatusTests(unittest.TestCase):
    def setUp(self) -> None:
        _clear()

    def tearDown(self) -> None:
        _clear()

    def test_fresh_install_needs_onboarding(self) -> None:
        s = client.get("/setup/status").json()
        self.assertTrue(s["needsOnboarding"])
        self.assertFalse(s["hasApiKey"])
        self.assertEqual(s["providers"], [])
        self.assertIsNone(s["onboardedAt"])
        self.assertEqual(set(s), {"needsOnboarding", "hasApiKey", "providers", "googleConnected", "onboardedAt"})

    def test_a_usable_provider_or_the_stamp_skips_the_wizard(self) -> None:
        db.set_settings({"providers": [providers.new_entry("openai")]})
        self.assertTrue(client.get("/setup/status").json()["needsOnboarding"])  # listed, but no key yet
        provider_keys.save(db.secrets, {"openai": "sk-x"})
        s = client.get("/setup/status").json()
        self.assertFalse(s["needsOnboarding"])
        self.assertTrue(s["hasApiKey"])
        self.assertEqual(s["providers"], ["openai"])
        _clear()
        db.set_settings({"providers": [providers.new_entry("litellm")]})  # a proxy needs no key
        s = client.get("/setup/status").json()
        self.assertEqual((s["needsOnboarding"], s["hasApiKey"], s["providers"]), (False, False, ["litellm"]))
        _clear()
        db.set_settings({"onboardedAt": "2026-10-01T00:00:00+00:00"})
        self.assertFalse(client.get("/setup/status").json()["needsOnboarding"])

    def test_reset_clears_the_stamp(self) -> None:
        db.set_settings({"onboardedAt": "2026-10-01T00:00:00+00:00"})
        self.assertIsNone(client.post("/setup/reset").json()["onboardedAt"])

    def test_retired_routes_are_gone(self) -> None:
        for method, path in (("get", "/setup/providers"), ("post", "/setup/test"), ("post", "/setup/models"), ("post", "/setup/complete")):
            self.assertIn(getattr(client, method)(path).status_code, (404, 405), path)
        self.assertEqual(client.put("/provider-keys/openai", json={"apiKey": "x"}).status_code, 404)

    def test_requires_token(self) -> None:
        self.assertEqual(TestClient(app).get("/setup/status").status_code, 401)


def _mock(handler):  # type: ignore[no-untyped-def]
    setup._transport = httpx.MockTransport(handler)


class ProbeTests(unittest.TestCase):
    def tearDown(self) -> None:
        setup._transport = None

    def _post(self, **kw):  # type: ignore[no-untyped-def]
        body = {"provider": "openai", "baseUrl": "https://api.openai.com/v1", "apiKey": "sk-x", "model": "gpt-5-mini", **kw}
        return asyncio.run(setup.test_connection(setup.SetupIn(**body), db.secrets))

    def test_success(self) -> None:
        def h(req: httpx.Request) -> httpx.Response:
            self.assertEqual(req.headers["authorization"], "Bearer sk-x")
            if req.url.path.endswith("/models"):
                return httpx.Response(200, json={"data": [{"id": "b"}, {"id": "a"}]})
            return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})
        _mock(h)
        r = self._post()
        self.assertTrue(r["ok"])
        self.assertEqual(r["models"], ["a", "b"])
        self.assertIsInstance(r["latencyMs"], int)

    def test_models_listing_optional(self) -> None:
        _mock(lambda req: httpx.Response(404, json={}) if req.url.path.endswith("/models") else httpx.Response(200, json={"choices": []}))
        r = self._post()
        self.assertTrue(r["ok"])
        self.assertIsNone(r["models"])

    def test_max_completion_tokens_retry(self) -> None:
        def h(req: httpx.Request) -> httpx.Response:
            if req.url.path.endswith("/models"):
                return httpx.Response(200, json={"data": []})
            if b"max_completion_tokens" in req.content:
                return httpx.Response(200, json={"choices": []})
            return httpx.Response(400, json={"error": {"message": "Use 'max_completion_tokens' instead"}})
        _mock(h)
        self.assertTrue(self._post()["ok"])

    def test_bad_key(self) -> None:
        _mock(lambda req: httpx.Response(401, json={"error": {"message": "bad"}}))
        r = self._post()
        self.assertFalse(r["ok"])
        self.assertEqual(r["error"], "That key was rejected (401).")

    def test_connection_refused(self) -> None:
        def h(req: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused")
        _mock(h)
        r = self._post(baseUrl="http://localhost:11434/v1")
        self.assertFalse(r["ok"])
        self.assertIn("Couldn't reach http://localhost:11434/v1", r["error"])

    def test_unknown_model(self) -> None:
        def h(req: httpx.Request) -> httpx.Response:
            if req.url.path.endswith("/models"):
                return httpx.Response(200, json={"data": []})
            return httpx.Response(404, json={"error": {"message": "The model `nope` does not exist"}})
        _mock(h)
        r = self._post(model="nope")
        self.assertFalse(r["ok"])
        self.assertEqual(r["error"], "Model nope not found.")

    def test_blank_key_uses_the_key_saved_for_the_provider(self) -> None:
        provider_keys.save(db.secrets, {"openai": "sk-saved"})
        seen: list[str | None] = []

        def h(req: httpx.Request) -> httpx.Response:
            seen.append(req.headers.get("authorization"))
            return httpx.Response(200, json={"data": [], "choices": []})
        _mock(h)
        try:
            self.assertTrue(self._post(apiKey="")["ok"])
            self.assertEqual(set(seen), {"Bearer sk-saved"})
            seen.clear()
            self._post(apiKey=None, provider="anthropic")  # another provider id: that one's (absent) key
            self.assertEqual(set(seen), {None})
        finally:
            _clear()

    def test_missing_fields_never_500(self) -> None:
        self.assertFalse(self._post(baseUrl="")["ok"])


if __name__ == "__main__":
    unittest.main()


def test_effective_follows_edited_base_url():
    from personal_os import providers
    assert providers.effective({"provider": "openai", "baseUrl": "https://api.anthropic.com/v1/"}) == "anthropic"
    assert providers.effective({"provider": "openai", "baseUrl": "https://api.openai.com/v1"}) == "openai"
    assert providers.effective({"provider": "litellm", "baseUrl": "https://proxy.example.com"}) == "litellm"
    assert providers.effective({"provider": "custom", "baseUrl": "https://api.openai.com/v1"}) == "custom"
    assert providers.effective({"provider": None, "baseUrl": ""}) is None
