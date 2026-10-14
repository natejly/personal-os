"""Per-provider API keys: kept in one secret by provider id, never shown by /settings, filed at startup from an old
single-provider install, and used by the connection probe, which reports failures plainly.

Run: python backend/tests/test_provider_keys.py
"""
from __future__ import annotations

import asyncio
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
        db.set_settings({"apiKey": ""})
        with db.tx() as c:
            c.execute("DELETE FROM settings WHERE key IN ('providers','provider','baseUrl','defaultModel','onboardedAt')")
    reset()
    yield
    setup._transport = None
    reset()


def test_settings_never_expose_key_values() -> None:
    provider_keys.save(db.secrets, {"fireworks": FW_KEY, "openai": OAI_KEY})
    db.set_settings({"providers": [providers.new_entry("fireworks"), providers.new_entry("openai")]})
    body = client.get("/settings").text
    assert FW_KEY not in body and OAI_KEY not in body
    out = json.loads(body)
    assert out["providerKeysSet"] == {"fireworks": True, "openai": True}
    assert out["apiKey"] == "" and out["apiKeySet"] is True and out["provider"] == "fireworks"


def test_put_settings_cannot_touch_provider_keys() -> None:
    provider_keys.save(db.secrets, {"fireworks": FW_KEY})
    client.put("/settings", json={"providerKeys": {"fireworks": "evil"}, "providerKeysSet": {"x": True}, "apiKey": "evil"})
    assert provider_keys.load(db.secrets) == {"fireworks": FW_KEY} and not db.get_settings().get("apiKey")


def test_provider_keys_are_redacted_from_logs() -> None:
    from personal_os import logs
    provider_keys.save(db.secrets, {"fireworks": "fw-only-in-the-blob-1"})
    assert "fw-only-in-the-blob-1" not in logs.redact("sent fw-only-in-the-blob-1 upstream")


# ---- startup step ----
def test_migrate_seeds_active_provider() -> None:
    db.set_settings({"provider": "openai", "baseUrl": OAI, "apiKey": OAI_KEY})
    assert provider_keys.load(db.secrets) == {}
    provider_keys.migrate(db)
    assert provider_keys.load(db.secrets) == {"openai": OAI_KEY}
    s = db.get_settings()
    assert [e["id"] for e in s["providers"]] == ["openai"] and not s.get("apiKey") and "provider" not in s
    provider_keys.migrate(db)  # idempotent, and it never overwrites a key already saved
    assert provider_keys.load(db.secrets) == {"openai": OAI_KEY}


def test_migrate_infers_provider_from_base_url_alone() -> None:
    db.set_settings({"baseUrl": FW, "apiKey": FW_KEY})  # a base and a key, no provider
    provider_keys.migrate(db)
    assert provider_keys.load(db.secrets) == {"fireworks": FW_KEY}


def test_env_provider_vars_do_not_seed_settings(tmp_path: Path) -> None:
    """The old PERSONAL_OS_* provider variables are ignored: a fresh install stays unconfigured until a provider is added."""
    import subprocess
    env = {**os.environ, "PERSONAL_OS_DATA_DIR": str(tmp_path), "PERSONAL_OS_BASE_URL": OAI, "PERSONAL_OS_API_KEY": OAI_KEY,
           "PERSONAL_OS_DEFAULT_MODEL": "gpt-x", "PERSONAL_OS_EXTRACTION_MODEL": "gpt-y", "PERSONAL_OS_TEST_CHILD": "1"}
    env.pop("PERSONAL_OS_PACKAGED", None)
    code = ("from personal_os.app import db; s = db.get_settings(); "
            "print(sorted(k for k in ('baseUrl','apiKey','defaultModel','extractionModel') if s.get(k)))")
    out = subprocess.run([sys.executable, "-c", code], env=env, cwd=Path(__file__).resolve().parents[1],
                         capture_output=True, text=True, timeout=120)
    assert out.stdout.strip().endswith("[]"), out.stdout + out.stderr


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
    assert provider_keys.saved_for(fresh.secrets) == {"fireworks": True} and not fresh.secrets.get("apiKey")


def test_live_install_survives_upgrade_untouched() -> None:
    """The key already in the secret store, a blank SQLite row, provider fireworks: the key and the models carry over."""
    d = Path(tempfile.mkdtemp(prefix="live-"))
    first = Database(d)
    first.secrets.set("apiKey", FW_KEY)
    rows = {"provider": "fireworks", "baseUrl": FW, "apiKey": "", "defaultModel": "accounts/fireworks/models/ember-1",
            "embeddingModel": "accounts/fireworks/models/qwen3-embedding-8b"}
    with first.tx() as c:
        c.execute("DELETE FROM settings WHERE key = 'providers'")
        for k, v in rows.items():
            c.execute("INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, json.dumps(v)))
    upgraded = Database(d)  # a restart on the new build
    import sqlite3
    migrations_con = sqlite3.connect(next(d.glob("*.db")))
    from personal_os import migrations
    migrations._provider_refs(migrations_con)
    migrations_con.commit()
    provider_keys.migrate(upgraded)
    got = upgraded.get_settings()
    assert provider_keys.load(upgraded.secrets) == {"fireworks": FW_KEY} and not got.get("apiKey")
    assert got["defaultModel"] == "fireworks/accounts/fireworks/models/ember-1"
    assert got["embeddingModel"] == rows["embeddingModel"]  # vectors are keyed by it: left bare
    assert [e["id"] for e in got["providers"]] == ["fireworks"]


def test_bad_json_blob_reads_as_empty() -> None:
    db.secrets.set(provider_keys.SECRET, "not json")
    assert provider_keys.load(db.secrets) == {}
    db.secrets.set(provider_keys.SECRET, "[1]")
    assert provider_keys.load(db.secrets) == {}


# ---- the connection probe ----
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
    return asyncio.run(setup.test_connection(setup.SetupIn(**body), db.secrets))


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


def test_resolve_key_uses_the_key_saved_under_the_provider_id() -> None:
    provider_keys.save(db.secrets, {"fireworks": FW_KEY})
    seen = mock(listing)
    check(provider="fireworks", baseUrl=FW, apiKey=None)
    assert seen[0].headers["authorization"] == f"Bearer {FW_KEY}"
    seen.clear()
    check(provider="fireworks", baseUrl=FW, apiKey="typed-key")  # a typed key always wins
    assert seen[0].headers["authorization"] == "Bearer typed-key"
    seen.clear()
    check(provider="openai", baseUrl=OAI, apiKey=None)  # nothing saved for OpenAI: no header, never Fireworks' key
    assert "authorization" not in seen[0].headers


def test_listing_401_lets_the_chat_probe_decide() -> None:
    mock(lambda req: httpx.Response(401, json={}) if req.url.path.endswith("/models") else listing(req))
    assert check(model="m")["ok"]


def test_blank_key_401_asks_for_a_key() -> None:
    mock(lambda req: httpx.Response(401, json={}))
    assert check(provider="litellm", baseUrl="http://localhost:4000", apiKey="", model="m")["error"] == "This server needs an API key."


def test_ollama_hints() -> None:
    mock(lambda req: httpx.Response(200, json={"data": [{"id": "qwen3"}]}) if req.url.path.endswith("/models")
         else httpx.Response(404, json={}))
    base = "http://localhost:11434/v1"
    assert check(provider="ollama", baseUrl=base, apiKey="", model="llama3.2")["error"] == \
        "Model llama3.2 not found. Run: ollama pull llama3.2. Pulled: qwen3."

    def refused(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")
    mock(refused)
    assert "ollama.com/download" in check(provider="ollama", baseUrl=base, apiKey="")["error"]


@pytest.mark.parametrize("bad", ["http://localhost:11x34/v1", "http://localhost:99999/v1", "http://[::1/v1", "http://host:abc"])
def test_a_stored_malformed_address_never_breaks_settings_or_startup(bad: str) -> None:
    db.set_settings({"baseUrl": bad})  # what an install that saved one before the check still holds
    assert providers.infer(bad) == "custom"
    provider_keys.migrate(db)
    assert client.get("/settings").status_code == 200
    assert client.get("/setup/status").status_code == 200
