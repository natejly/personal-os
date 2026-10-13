"""Migration 38: an install on a provider that cannot serve the shipped embedding id gets that provider's own (or none)."""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="embmig-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os import migrations  # noqa: E402
from personal_os.db import Database  # noqa: E402


def _migrate(saved: dict) -> dict:
    d = Path(tempfile.mkdtemp(prefix="embmig-db-"))
    Database(d).set_settings(saved)
    con = sqlite3.connect(next(d.glob("*.db")))
    con.execute("PRAGMA user_version = 37")
    con.commit()
    assert migrations.run(con)[0] == 38
    out = {k: json.loads(v) for k, v in con.execute("SELECT key, value FROM settings")}
    con.close()
    return out


def test_wrong_provider_ids_are_replaced_and_user_picks_kept() -> None:
    anthropic = {"provider": "anthropic", "baseUrl": "https://api.anthropic.com/v1/"}
    assert _migrate({**anthropic, "embeddingModel": "accounts/fireworks/models/qwen3-embedding-8b"})["embeddingModel"] == ""
    assert _migrate({"provider": "openai", "baseUrl": "https://api.openai.com/v1"})["embeddingModel"] == "text-embedding-3-small"
    assert _migrate({**anthropic, "embeddingModel": "my-own-embedder"})["embeddingModel"] == "my-own-embedder"
    fw = {"provider": "fireworks", "baseUrl": "https://api.fireworks.ai/inference/v1",
          "embeddingModel": "accounts/fireworks/models/qwen3-embedding-8b"}
    assert _migrate(fw)["embeddingModel"] == fw["embeddingModel"]
