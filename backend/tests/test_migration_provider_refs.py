"""Migration 45 (models carry their provider) and the startup step that files keys and old conversations under it."""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

os.environ["GRAIN_SECRETS_BACKEND"] = "file"
os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="provmig-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os import migrations, provider_keys  # noqa: E402
from personal_os.db import Database  # noqa: E402

FW = "https://api.fireworks.ai/inference/v1"
DS = "accounts/fireworks/models/deepseek-v4p1-flash"


def _legacy(saved: dict) -> tuple[Database, sqlite3.Connection]:
    """A database as it was before migration 45: the old single-provider rows and no `providers`."""
    d = Path(tempfile.mkdtemp(prefix="provmig-"))
    db = Database(d)
    with db.tx() as c:
        c.execute("DELETE FROM settings WHERE key = 'providers'")
    db.set_settings(saved)
    con = sqlite3.connect(next(d.glob("*.db")))
    con.row_factory = None
    return db, con


def _run(con: sqlite3.Connection) -> dict:
    migrations._provider_refs(con)
    con.commit()
    return {k: json.loads(v) for k, v in con.execute("SELECT key, value FROM settings")}


def test_is_the_latest_step() -> None:
    assert migrations.MIGRATIONS[-1][:2] == (45, "provider_refs") or migrations.latest() >= 45


def test_real_install_shape() -> None:
    _, con = _legacy({"provider": "anthropic", "baseUrl": "https://api.anthropic.com/v1/", "defaultModel": "claude-sonnet-5-5", "modelLow": ""})
    s = _run(con)
    assert s["providers"] == [{"id": "anthropic", "kind": "anthropic", "name": "Anthropic", "baseUrl": "https://api.anthropic.com/v1/"}]
    assert s["defaultModel"] == "anthropic/claude-sonnet-5-5" and s["modelLow"] == ""
    assert s["provider"] == "anthropic"  # kept for the startup step


def test_own_server_keeps_its_address_and_owns_every_model() -> None:
    _, con = _legacy({"provider": "custom", "baseUrl": "http://my.lan:8080/v1", "defaultModel": "whatever", "workerModel": "other"})
    s = _run(con)
    assert s["providers"][0]["baseUrl"] == "http://my.lan:8080/v1" and s["providers"][0]["id"] == "custom"
    assert s["defaultModel"] == "custom/whatever" and s["workerModel"] == "custom/other"


def test_models_of_another_preset_are_prefixed_with_its_id_and_embedding_stays_bare() -> None:
    _, con = _legacy({"provider": "fireworks", "baseUrl": FW, "modelHigh": "claude-opus-5-5", "defaultModel": DS,
                      "embeddingModel": "qwen3-embedding-8b", "retrievalRerankModel": "accounts/fireworks/models/qwen3-reranker-8b"})
    s = _run(con)
    assert s["modelHigh"] == "anthropic/claude-opus-5-5" and s["defaultModel"] == "fireworks/" + DS
    assert s["embeddingModel"] == "qwen3-embedding-8b"
    assert s["retrievalRerankModel"] == "fireworks/accounts/fireworks/models/qwen3-reranker-8b"


def test_auto_review_model_is_converted_nested_and_flat() -> None:
    _, con = _legacy({"provider": "fireworks", "baseUrl": FW, "permissions": {"version": 1, "autoReviewModel": DS}})
    assert _run(con)["permissions"]["autoReviewModel"] == "fireworks/" + DS


def test_stale_legacy_models_are_dropped_or_folded_into_a_blank_tier() -> None:
    _, con = _legacy({"provider": "fireworks", "baseUrl": FW, "extractionModel": "accounts/fireworks/models/glm-5p3",
                      "fastModel": "deepseek-v4-flash", "modelMedium": ""})
    s = _run(con)
    assert "extractionModel" not in s and "fastModel" not in s
    assert s["modelLow"] == "fireworks/accounts/fireworks/models/glm-5p3"  # the active provider owns it: kept as the low tier


def test_fresh_install_is_left_alone_and_a_second_run_changes_nothing() -> None:
    _, con = _legacy({})
    assert "providers" not in _run(con)
    _, con = _legacy({"provider": "fireworks", "baseUrl": FW, "defaultModel": DS})
    once = _run(con)
    assert _run(con) == once


def test_startup_files_keys_adds_keyed_presets_canonicalises_chats_and_drops_old_rows() -> None:
    db, con = _legacy({"provider": "fireworks", "baseUrl": FW, "apiKey": "fw-key", "defaultModel": DS})
    provider_keys.save(db.secrets, {"anthropic": "an-key"})
    _run(con)  # the migration, on the same file
    db2 = Database(Path(con.execute("PRAGMA database_list").fetchone()[2]).parent)
    db2.secrets = db.secrets
    with db2.tx() as c:
        c.execute("INSERT INTO conversations(id, project_id, title, model, settings, created_at, updated_at) VALUES('c1',NULL,'t',?,'{}',1,1)", ("claude-opus-5-5",))
        c.execute("INSERT INTO conversations(id, project_id, title, model, settings, created_at, updated_at) VALUES('c2',NULL,'t','auto','{}',1,1)")
    provider_keys.migrate(db2)
    s = db2.get_settings()
    assert [e["id"] for e in s["providers"]] == ["fireworks", "anthropic"]
    assert provider_keys.load(db2.secrets) == {"anthropic": "an-key", "fireworks": "fw-key"}
    assert not s.get("apiKey") and "provider" not in s and "baseUrl" not in s
    with db2.tx() as c:
        assert dict(c.execute("SELECT id, model FROM conversations").fetchall()) == {"c1": "anthropic/claude-opus-5-5", "c2": "auto"}
    before = (db2.get_settings(), provider_keys.load(db2.secrets))
    provider_keys.migrate(db2)  # idempotent
    assert (db2.get_settings(), provider_keys.load(db2.secrets)) == before
