"""/providers: add, replace key, edit address and remove a provider. A key is saved only after the server accepted it.

Run: python -m pytest backend/tests/test_providers_api.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["GRAIN_SECRETS_BACKEND"] = "file"
os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="provapi-"))

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from personal_os import provider_keys, provider_route, setup  # noqa: E402
from personal_os.app import AUTH_TOKEN, app, db  # noqa: E402

client = TestClient(app, headers={"X-Personal-OS-Token": AUTH_TOKEN})
GOOD = {"sk-good", "sk-good-2"}
calls: list[httpx.Request] = []


def handler(req: httpx.Request) -> httpx.Response:
    calls.append(req)
    if "nolist" in req.url.host:
        if req.url.path.endswith("/models"):
            return httpx.Response(404, text="nope")
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})
    auth = req.headers.get("authorization", "") or req.headers.get("x-api-key", "")
    if "openai" in req.url.host or "anthropic" in req.url.host:
        if auth.removeprefix("Bearer ") not in GOOD:
            return httpx.Response(401, json={"error": {"message": "bad key"}})
    return httpx.Response(200, json={"data": [{"id": "m-a"}, {"id": "m-b"}]})


@pytest.fixture(autouse=True)
def fresh(monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setattr(setup, "_transport", httpx.MockTransport(handler))
    with db.tx() as c:
        c.execute("DELETE FROM settings WHERE key IN ('providers','defaultModel','onboardedAt','provider','baseUrl')")
    provider_keys.save(db.secrets, {})
    provider_route._live.clear()
    provider_route._cache.clear()
    calls.clear()
    yield


def add(**body):  # type: ignore[no-untyped-def]
    return client.post("/providers", json=body)


def test_list_has_presets_and_nothing_configured() -> None:
    r = client.get("/providers").json()
    assert {p["id"] for p in r["presets"]} >= {"fireworks", "openai", "anthropic", "custom", "litellm", "ollama"}
    assert r["providers"] == []


def test_add_ok_saves_key_entry_default_and_onboarded() -> None:
    r = add(kind="openai", apiKey="sk-good").json()
    assert r["ok"] is True and r["models"] == ["m-a", "m-b"] and r["provider"]["id"] == "openai"
    assert r["provider"]["hasKey"] and r["provider"]["status"] == "ok" and r["provider"]["needsKey"] is True
    s = client.get("/settings").json()
    assert s["defaultModel"] == "openai/gpt-6.1-sol" and s["onboardedAt"]
    assert [p["id"] for p in s["providers"]] == ["openai"] and s["apiKeySet"] is True
    assert provider_keys.load(db.secrets) == {"openai": "sk-good"}
    assert "sk-good" not in client.get("/settings").text and "sk-good" not in client.get("/providers").text
    assert client.get("/setup/status").json()["providers"] == ["openai"]


def test_add_with_a_rejected_key_stores_nothing() -> None:
    r = add(kind="openai", apiKey="sk-bad").json()
    assert r["ok"] is False and "rejected" in r["error"] and r["models"] is None and r["latencyMs"] is None
    assert client.get("/providers").json()["providers"] == [] and provider_keys.load(db.secrets) == {}
    assert not client.get("/settings").json().get("onboardedAt")


def test_add_needs_a_key_a_known_kind_and_a_custom_address() -> None:
    assert "needs an API key" in add(kind="openai").json()["error"]
    assert add(kind="nope").json()["ok"] is False
    assert add(kind="custom").json()["ok"] is False
    assert add(kind="custom", baseUrl="ftp://x").json()["ok"] is False


def test_duplicate_preset_refused_but_custom_repeats() -> None:
    assert add(kind="openai", apiKey="sk-good").json()["ok"]
    assert "already added" in add(kind="openai", apiKey="sk-good").json()["error"]
    assert add(kind="custom", baseUrl="http://one.test/v1", name="One").json()["provider"]["id"] == "custom"
    r = add(kind="custom", baseUrl="http://two.test/v1").json()
    assert r["provider"]["id"] == "custom-2" and r["provider"]["needsKey"] is False


def test_custom_server_that_cannot_list_models_takes_a_typed_one() -> None:
    assert "Enter a model" in add(kind="custom", baseUrl="http://nolist.test/v1").json()["error"]
    r = add(kind="custom", baseUrl="http://nolist.test/v1", model="my-model").json()
    assert r["ok"] and r["models"] is None
    s = client.get("/settings").json()
    assert s["providers"][0]["models"] == ["my-model"] and s["defaultModel"] == "custom/my-model"


def test_second_provider_does_not_change_the_default() -> None:
    add(kind="openai", apiKey="sk-good")
    add(kind="anthropic", apiKey="sk-good")
    assert client.get("/settings").json()["defaultModel"] == "openai/gpt-6.1-sol"


def test_replace_key_verifies_and_a_bad_key_keeps_the_old_one() -> None:
    add(kind="openai", apiKey="sk-good")
    bad = client.put("/providers/openai/key", json={"apiKey": "sk-bad"}).json()
    assert bad["ok"] is False and provider_keys.load(db.secrets) == {"openai": "sk-good"}
    ok = client.put("/providers/openai/key", json={"apiKey": "sk-good-2"}).json()
    assert ok["ok"] and provider_keys.load(db.secrets) == {"openai": "sk-good-2"}
    assert client.put("/providers/openai/key", json={"apiKey": "  "}).status_code == 422
    assert client.put("/providers/nope/key", json={"apiKey": "x"}).status_code == 404


def test_edit_address_only_for_own_servers_and_verified() -> None:
    add(kind="openai", apiKey="sk-good")
    assert client.put("/providers/openai", json={"baseUrl": "http://x.test"}).json()["ok"] is False
    add(kind="custom", baseUrl="http://one.test/v1")
    r = client.put("/providers/custom", json={"baseUrl": "http://two.test/v1"}).json()
    assert r["ok"] and r["provider"]["baseUrl"] == "http://two.test/v1"
    assert client.put("/providers/custom", json={"baseUrl": "ftp://bad"}).json()["ok"] is False
    assert [p for p in client.get("/providers").json()["providers"] if p["id"] == "custom"][0]["baseUrl"] == "http://two.test/v1"


def test_delete_reports_stale_selections_and_drops_the_key() -> None:
    add(kind="openai", apiKey="sk-good")
    add(kind="anthropic", apiKey="sk-good")
    client.put("/settings", json={"modelHigh": "openai/gpt-6-astra"})
    r = client.delete("/providers/openai").json()
    assert [p["id"] for p in r["providers"]] == ["anthropic"] and "defaultModel" in r["staleSelections"] and "modelHigh" in r["staleSelections"]
    assert provider_keys.load(db.secrets) == {"anthropic": "sk-good"}
    assert client.delete("/providers/openai").status_code == 404


def test_put_settings_ignores_provider_fields() -> None:
    add(kind="openai", apiKey="sk-good")
    client.put("/settings", json={"baseUrl": "http://evil.test", "apiKey": "stolen", "provider": "custom", "providers": []})
    s = client.get("/settings").json()
    assert s["baseUrl"] == "https://api.openai.com/v1" and [p["id"] for p in s["providers"]] == ["openai"]
    assert provider_keys.load(db.secrets) == {"openai": "sk-good"}


def test_models_lists_every_keyed_provider_with_refs(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    add(kind="openai", apiKey="sk-good")
    add(kind="anthropic", apiKey="sk-good")
    real = httpx.AsyncClient.__init__
    monkeypatch.setattr(httpx.AsyncClient, "__init__", lambda self, *a, **kw: real(self, *a, **{**kw, "transport": httpx.MockTransport(handler)}))
    ids = [m["id"] for m in client.get("/models").json()]
    assert {"openai/m-a", "openai/m-b", "anthropic/m-a"} <= set(ids)
