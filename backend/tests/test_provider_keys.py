"""Per-provider API keys: switching provider keeps each key, /settings never shows them, /setup/test and
/setup/models resolve the right key and report failures plainly.

Run: python backend/tests/test_provider_keys.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["GRAIN_SECRETS_BACKEND"] = "file"
os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="provkeys-"))

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from personal_os import provider_keys, providers, setup  # noqa: E402
from personal_os.app import AUTH_TOKEN, app, db  # noqa: E402
from personal_os.db import Database  # noqa: E402

client = TestClient(app, headers={"X-Personal-OS-Token": AUTH_TOKEN})
FW, OAI = providers.get("fireworks")["baseUrl"], providers.get("openai")["baseUrl"]
FW_KEY, OAI_KEY = "fw-test-key", "oai-test-key"


@pytest.fixture(autouse=True)
def clean():  # type: ignore[no-untyped-def]
    def reset() -> None:
        db.secrets.delete(provider_keys.SECRET)
        db.set_settings({"provider": None, "baseUrl": "", "apiKey": "", "defaultModel": "", "onboardedAt": None})
    reset()
    setup._models_cache.clear()
    yield
    setup._transport = None
    reset()


def put(**patch):  # type: ignore[no-untyped-def]
    r = client.put("/settings", json=patch)
    assert r.status_code == 200, r.text
    return r.json()


def active() -> tuple[str | None, str, str]:
    s = db.get_settings()
    return s.get("provider"), s.get("baseUrl", ""), s.get("apiKey", "")


def start_on_fireworks() -> None:
    put(provider="fireworks", baseUrl=FW, apiKey=FW_KEY)


# ---- switching ----
def test_switch_stashes_old_key_and_clears_active() -> None:
    start_on_fireworks()
    assert provider_keys.load(db.secrets) == {"fireworks": FW_KEY}
    out = put(provider="openai")
    assert active() == ("openai", OAI, "")  # base filled from the preset, no Fireworks key sent to OpenAI
    assert out["apiKeySet"] is False and out["providerKeysSet"] == {"fireworks": True}


def test_switch_loads_saved_key_for_target() -> None:
    start_on_fireworks()
    put(provider="openai", apiKey=OAI_KEY)
    assert active() == ("openai", OAI, OAI_KEY)
    assert provider_keys.load(db.secrets) == {"fireworks": FW_KEY, "openai": OAI_KEY}  # the old key was stashed too


def test_round_trip_needs_no_repaste() -> None:
    start_on_fireworks()
    put(provider="openai", apiKey=OAI_KEY)
    put(provider="fireworks")
    assert active() == ("fireworks", FW, FW_KEY)
    put(provider="openai")
    assert active() == ("openai", OAI, OAI_KEY)


def test_explicit_base_wins_over_preset() -> None:
    start_on_fireworks()
    put(provider="custom", baseUrl="http://my-box:8080")
    assert active()[1] == "http://my-box:8080"


def test_blank_key_is_unchanged_and_null_removes_both() -> None:
    start_on_fireworks()
    put(apiKey="")
    assert active()[2] == FW_KEY
    out = put(apiKey=None)
    assert active()[2] == "" and provider_keys.load(db.secrets) == {}
    assert out["providerKeysSet"] == {}


def test_remove_keeps_other_providers_keys() -> None:
    start_on_fireworks()
    put(provider="openai", apiKey=OAI_KEY)
    put(apiKey=None)
    assert provider_keys.load(db.secrets) == {"fireworks": FW_KEY}


def test_put_settings_cannot_touch_provider_keys() -> None:
    start_on_fireworks()
    put(providerKeys={"fireworks": "evil"}, providerKeysSet={"x": True})
    assert provider_keys.load(db.secrets) == {"fireworks": FW_KEY}


# ---- what the renderer can see ----
def test_settings_never_expose_key_values() -> None:
    start_on_fireworks()
    put(provider="openai", apiKey=OAI_KEY)
    body = client.get("/settings").text
    assert FW_KEY not in body and OAI_KEY not in body
    out = json.loads(body)
    assert out["providerKeysSet"] == {"fireworks": True, "openai": True}
    assert out["apiKey"] == "" and out["apiKeySet"] is True and out["provider"] == "openai"


def test_provider_keys_are_redacted_from_logs() -> None:
    from personal_os import logs
    provider_keys.save(db.secrets, {"fireworks": "fw-only-in-the-blob-1"})
    assert "fw-only-in-the-blob-1" not in logs.redact("sent fw-only-in-the-blob-1 upstream")


# ---- startup migration ----
def test_migrate_seeds_active_provider() -> None:
    db.set_settings({"provider": "openai", "baseUrl": OAI, "apiKey": OAI_KEY})
    assert provider_keys.load(db.secrets) == {}
    provider_keys.migrate(db)
    assert provider_keys.load(db.secrets) == {"openai": OAI_KEY}
    provider_keys.migrate(db)  # idempotent, and it never overwrites a key already saved
    db.set_settings({"apiKey": "other"})
    provider_keys.migrate(db)
    assert provider_keys.load(db.secrets) == {"openai": OAI_KEY}


def test_migrate_infers_provider_from_base_url_alone() -> None:
    db.set_settings({"baseUrl": FW, "apiKey": FW_KEY})  # env seed: a base and a key, no provider
    provider_keys.migrate(db)
    assert provider_keys.load(db.secrets) == {"fireworks": FW_KEY}


def test_env_seed_then_migrate(monkeypatch: pytest.MonkeyPatch) -> None:
    from personal_os import app as app_mod
    monkeypatch.delenv("PERSONAL_OS_PACKAGED", raising=False)
    monkeypatch.setenv("PERSONAL_OS_BASE_URL", OAI)
    monkeypatch.setenv("PERSONAL_OS_API_KEY", OAI_KEY)
    with db.tx() as c:
        c.execute("DELETE FROM settings WHERE key IN ('baseUrl','apiKey')")
    db.secrets.delete("apiKey")
    app_mod._seed_settings_from_env()
    provider_keys.migrate(db)
    assert provider_keys.load(db.secrets) == {"openai": OAI_KEY}


def test_legacy_plaintext_sqlite_key_still_moves_to_the_store_and_blanks() -> None:
    d = Path(tempfile.mkdtemp(prefix="legacy-"))
    seed = Database(d)
    with seed.tx() as c:
        for k, v in {"provider": "fireworks", "baseUrl": FW, "apiKey": FW_KEY}.items():
            c.execute("INSERT INTO settings(key, value) VALUES(?, ?)", (k, json.dumps(v)))
    fresh = Database(d)
    assert fresh.secrets.get("apiKey") == FW_KEY
    with fresh.tx() as c:
        assert json.loads(c.execute("SELECT value FROM settings WHERE key='apiKey'").fetchone()["value"]) == ""
    provider_keys.migrate(fresh)
    assert provider_keys.saved_for(fresh.secrets) == {"fireworks": True}


def test_live_install_survives_upgrade_untouched() -> None:
    """The key already in the secret store, a blank SQLite row, provider fireworks: nothing the user set changes."""
    d = Path(tempfile.mkdtemp(prefix="live-"))
    first = Database(d)
    first.secrets.set("apiKey", FW_KEY)
    rows = {"provider": "fireworks", "baseUrl": FW, "apiKey": "",
            "defaultModel": "accounts/fireworks/models/ember-1",
            "extractionModel": "accounts/fireworks/models/deepseek-v4p1-flash",
            "embeddingModel": "accounts/fireworks/models/qwen3-embedding-8b"}
    with first.tx() as c:
        for k, v in rows.items():
            c.execute("INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, json.dumps(v)))
    upgraded = Database(d)  # a restart on the new build
    provider_keys.migrate(upgraded)
    got = upgraded.get_settings()
    assert got["apiKey"] == FW_KEY
    for k, v in rows.items():
        if k != "apiKey":
            assert got[k] == v
    assert provider_keys.saved_for(upgraded.secrets) == {"fireworks": True}
    # and the upgraded install can switch away and come back with no re-paste
    patch = provider_keys.apply(upgraded, {"provider": "openai"})
    assert patch == {"provider": "openai", "apiKey": None, "baseUrl": OAI}
    upgraded.set_settings(patch)
    assert upgraded.get_settings()["apiKey"] == ""
    upgraded.set_settings(provider_keys.apply(upgraded, {"provider": "fireworks"}))
    assert upgraded.get_settings()["apiKey"] == FW_KEY


def test_bad_json_blob_reads_as_empty() -> None:
    db.secrets.set(provider_keys.SECRET, "not json")
    assert provider_keys.load(db.secrets) == {}
    db.secrets.set(provider_keys.SECRET, "[1]")
    assert provider_keys.load(db.secrets) == {}


# ---- /setup/test and /setup/models ----
def mock(handler) -> list[httpx.Request]:  # type: ignore[no-untyped-def]
    seen: list[httpx.Request] = []

    def h(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return handler(req)
    setup._transport = httpx.MockTransport(h)
    return seen


def listing(req: httpx.Request) -> httpx.Response:
    if req.url.path.endswith("/models"):
        return httpx.Response(200, json={"data": [{"id": "b"}, {"id": "a"}]})
    return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})


def check(**kw):  # type: ignore[no-untyped-def]
    body = {"provider": "openai", "baseUrl": OAI, "apiKey": OAI_KEY, "model": "", **kw}
    return client.post("/setup/test", json=body).json()


def test_test_without_model_lists_models_only() -> None:
    seen = mock(listing)
    r = check()
    assert r["ok"] and r["models"] == ["a", "b"]
    assert [q.method for q in seen] == ["GET"]  # no chat probe
    assert seen[0].headers["authorization"] == f"Bearer {OAI_KEY}"


def test_test_with_model_probes_chat_too() -> None:
    seen = mock(listing)
    assert check(model="gpt-5-mini")["ok"]
    assert [q.method for q in seen] == ["GET", "POST"]


def test_test_rejected_key() -> None:
    mock(lambda req: httpx.Response(401, json={"error": {"message": "bad"}}))
    assert check()["error"] == "That key was rejected (401)."
    assert check(model="m")["error"] == "That key was rejected (401)."


def test_test_models_404_without_model_asks_for_one() -> None:
    mock(lambda req: httpx.Response(404, json={}))
    assert check()["error"] == "Enter a model to test with: this server does not list its models."


def test_test_models_404_falls_back_to_chat() -> None:
    mock(lambda req: httpx.Response(404, json={}) if req.url.path.endswith("/models") else httpx.Response(200, json={"choices": []}))
    r = check(model="m")
    assert r["ok"] and r["models"] is None


def test_test_both_404_is_an_address_error() -> None:
    mock(lambda req: httpx.Response(404, json={}))
    assert check(model="m")["error"] == f"Nothing answered at {OAI}: check the address."


def test_test_unknown_model_when_listing_works() -> None:
    mock(lambda req: httpx.Response(200, json={"data": []}) if req.url.path.endswith("/models")
         else httpx.Response(404, json={"error": {"message": "no such model"}}))
    assert check(model="nope")["error"] == "Model nope not found."


def test_test_connection_refused_and_timeout() -> None:
    def refused(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    def slow(req: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow")
    mock(refused)
    assert check(baseUrl="http://localhost:11434/v1")["error"] == "Couldn't reach http://localhost:11434/v1 — is it running?"
    mock(slow)
    assert check()["error"] == f"{OAI} took too long to answer."


def test_test_uses_the_timeout_it_promises() -> None:
    seen = mock(listing)
    check()
    assert seen[0].extensions["timeout"]["read"] == setup.CHECK_TIMEOUT == 10


def test_test_other_http_error_keeps_excerpt() -> None:
    mock(lambda req: httpx.Response(500, json={"error": {"message": "boom"}}))
    assert check()["error"] == "The provider returned 500: boom"


def test_resolve_key_uses_saved_key_for_the_provider() -> None:
    start_on_fireworks()
    put(provider="openai", apiKey=OAI_KEY)  # active is now openai; fireworks is saved
    seen = mock(listing)
    # fixed preset address: its saved key is used though it is not the active provider
    check(provider="fireworks", baseUrl=FW, apiKey=None)
    assert seen[0].headers["authorization"] == f"Bearer {FW_KEY}"
    # a typed key always wins
    seen.clear()
    check(provider="fireworks", baseUrl=FW, apiKey="typed-key")
    assert seen[0].headers["authorization"] == "Bearer typed-key"


def test_a_key_never_travels_to_another_server() -> None:
    start_on_fireworks()
    seen = mock(listing)
    check(provider="fireworks", baseUrl="http://elsewhere:9/v1", apiKey=None)
    check(provider="custom", baseUrl="http://elsewhere:9/v1", apiKey="")
    check(provider="openai", baseUrl=OAI, apiKey=None)  # nothing saved for OpenAI, the active key is Fireworks'
    assert all("authorization" not in q.headers for q in seen)


def test_custom_provider_reuses_active_key_for_the_stored_base_only() -> None:
    put(provider="custom", baseUrl="http://box:8080/v1", apiKey="box-key")
    seen = mock(listing)
    check(provider="custom", baseUrl="http://box:8080/v1/", apiKey=None)
    assert seen[0].headers["authorization"] == "Bearer box-key"
    seen.clear()
    check(provider="custom", baseUrl="http://other:8080/v1", apiKey=None)
    assert "authorization" not in seen[0].headers


def models(**kw):  # type: ignore[no-untyped-def]
    body = {"provider": "openai", "baseUrl": OAI, "apiKey": OAI_KEY, **kw}
    return client.post("/setup/models", json=body).json()


def test_models_route_lists_and_caches_for_a_minute() -> None:
    seen = mock(listing)
    assert models() == {"models": ["a", "b"], "error": None}
    assert models() == {"models": ["a", "b"], "error": None}
    assert len(seen) == 1  # second answer came from the cache
    models(baseUrl="http://box:8080/v1", provider="custom")
    assert len(seen) == 2  # another (provider, base) is its own entry
    setup._models_cache[("openai", OAI)] = (setup._models_cache[("openai", OAI)][0] - setup.LIST_TTL - 1, ["a", "b"])
    models()
    assert len(seen) == 3  # an expired entry is fetched again


def test_models_route_does_not_cache_failures() -> None:
    mock(lambda req: httpx.Response(401, json={}))
    assert models()["error"] == "That key was rejected (401)."
    seen = mock(listing)
    assert models()["models"] == ["a", "b"]
    assert len(seen) == 1


def test_models_route_error_texts() -> None:
    mock(lambda req: httpx.Response(404, json={}))
    assert models() == {"models": None, "error": "This server does not list its models."}

    def slow(req: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow")
    mock(slow)
    assert models()["error"] == f"{OAI} took too long to answer."
    assert models(baseUrl="")["error"] == "Enter a base URL first."


def test_models_route_skips_the_call_when_a_keyed_provider_has_no_key() -> None:
    seen = mock(listing)
    assert models(apiKey=None) == {"models": None, "error": None}
    assert seen == []
    # a keyless provider is asked anyway
    assert models(provider="ollama", baseUrl="http://localhost:11434/v1", apiKey=None)["models"] == ["a", "b"]


def test_models_route_uses_the_saved_key() -> None:
    start_on_fireworks()
    seen = mock(listing)
    models(provider="fireworks", baseUrl=FW, apiKey=None)
    assert seen[0].headers["authorization"] == f"Bearer {FW_KEY}"


# ---- the setup wizard goes through the same path ----
def test_setup_complete_switch_and_return() -> None:
    start_on_fireworks()
    r = client.post("/setup/complete", json={"provider": "openai", "baseUrl": OAI, "apiKey": OAI_KEY, "model": "gpt-5-mini"})
    assert r.status_code == 200
    assert provider_keys.load(db.secrets) == {"fireworks": FW_KEY, "openai": OAI_KEY}
    # the wizard on a provider with no key anywhere clears the active key rather than sending OpenAI's elsewhere
    r = client.post("/setup/complete", json={"provider": "ollama", "baseUrl": "", "apiKey": None, "model": "llama3.2"})
    assert r.status_code == 200
    assert active() == ("ollama", "http://localhost:11434/v1", "")
    assert provider_keys.load(db.secrets) == {"fireworks": FW_KEY, "openai": OAI_KEY}
