"""Migration 33 moves an install onto DeepSeek V4.1 Flash at high effort and leaves explicit picks and other keys alone."""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="deepseek-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os import migrations, providers, repos  # noqa: E402
from personal_os.db import Database  # noqa: E402

DS = "accounts/fireworks/models/deepseek-v4p1-flash"


def _db(saved: dict) -> tuple[Database, sqlite3.Connection]:
    d = Path(tempfile.mkdtemp(prefix="deepseek-mig-"))
    db = Database(d)
    db.set_settings(saved)
    return db, sqlite3.connect(next(d.glob("*.db")))


def _chat(con: sqlite3.Connection, cid: str, model: str, settings: dict) -> None:
    con.execute("INSERT INTO conversations(id, project_id, title, model, settings, created_at, updated_at) VALUES(?,?,?,?,?,1,1)",
                (cid, None, cid, model, json.dumps(settings)))


def _settings(con: sqlite3.Connection) -> dict:
    return {k: json.loads(v) for k, v in con.execute("SELECT key, value FROM settings")}


def test_fresh_database_resolves_to_deepseek_at_high() -> None:
    db, con = _db({})
    assert migrations.current(con) == migrations.latest()
    cfg = {"provider": "fireworks", "baseUrl": "https://api.fireworks.ai/inference/v1"}
    assert providers.default_model(cfg) == DS
    assert {providers.tier_model(cfg, t) for t in providers.TIERS} == {DS}
    assert {providers.tier_model({"provider": "litellm"}, t) for t in providers.TIERS} == {"deepseek-v4-flash"}
    assert repos.DEFAULT_EFFORT == "high"
    con.close()


def test_upgraded_fireworks_install_moves_to_deepseek_high() -> None:
    db, con = _db({"provider": "fireworks", "baseUrl": "https://api.fireworks.ai/inference/v1",
                   "defaultModel": "accounts/fireworks/models/ember-1", "modelHigh": "accounts/fireworks/models/ember-1",
                   "extractionModel": DS, "fastModel": "accounts/fireworks/models/glm-5p3",
                   "embeddingModel": "accounts/fireworks/models/qwen3-embedding-8b", "retrievalRerankModel": "x-reranker",
                   "visionModel": "some-vl", "uiZoom": 110})
    perms_before = con.execute("SELECT value FROM settings WHERE key = 'permissions'").fetchone()
    _chat(con, "a", "accounts/fireworks/models/ember-1", {})
    _chat(con, "b", "ember-1", {"effort": "medium", "fast": True})
    _chat(con, "c", "glm-5.3", {"effort": "low"})
    _chat(con, "d", "kimi-k3", {"effort": "max"})
    con.execute("PRAGMA user_version = 32")
    con.commit()
    assert migrations.run(con) == [33]
    s = _settings(con)
    assert not {"defaultModel", "modelHigh", "extractionModel", "fastModel"} & set(s)
    assert s["embeddingModel"] == "accounts/fireworks/models/qwen3-embedding-8b"
    assert s["retrievalRerankModel"] == "x-reranker" and s["visionModel"] == "some-vl" and s["uiZoom"] == 110
    assert con.execute("SELECT value FROM settings WHERE key = 'permissions'").fetchone() == perms_before
    rows = {r[0]: (r[1], json.loads(r[2])) for r in con.execute("SELECT id, model, settings FROM conversations")}
    assert rows["a"] == (DS, {})
    assert rows["b"] == ("deepseek-v4-flash", {"effort": "high", "fast": True})
    assert rows["c"] == ("glm-5.3", {"effort": "low"})
    assert rows["d"] == ("kimi-k3", {"effort": "max"})
    # Idempotent: a second pass changes nothing.
    migrations._deepseek_default(con)
    assert {r[0]: (r[1], json.loads(r[2])) for r in con.execute("SELECT id, model, settings FROM conversations")} == rows
    assert _settings(con) == s
    con.close()


def test_other_provider_keeps_its_saved_models() -> None:
    db, con = _db({"provider": "openai", "baseUrl": "https://api.openai.com/v1", "defaultModel": "gpt-5", "modelLow": "gpt-5-nano"})
    con.execute("PRAGMA user_version = 32")
    con.commit()
    migrations.run(con)
    s = _settings(con)
    assert s["defaultModel"] == "gpt-5" and s["modelLow"] == "gpt-5-nano"
    con.close()
