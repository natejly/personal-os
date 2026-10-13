"""The simplify_settings migration: dead keys and constants leave the settings rows, hybridRetrieval folds into retrievalMode."""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

os.environ["GRAIN_SECRETS_BACKEND"] = "file"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os import llm, migrations  # noqa: E402

V = next(v for v, name, _ in migrations.MIGRATIONS if name == "simplify_settings")


def _run(rows: dict[str, str]) -> dict[str, object]:
    c = sqlite3.connect(":memory:", isolation_level=None)
    c.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    c.executemany("INSERT INTO settings VALUES (?, ?)", rows.items())
    c.execute(f"PRAGMA user_version = {V - 1}")
    migrations.run(c)
    return {k: json.loads(v) for k, v in c.execute("SELECT key, value FROM settings")}


def test_dead_keys_and_constants_are_removed() -> None:
    perm = {"version": 2, "permissionMode": "manual", "skipPermissions": True, "autoReview": "risky",
            "workspaceRoots": ["/x"]}
    out = _run({"mode": '"classic"', "stuckDetection": "true", "retainTraceDays": "7", "shellMaxBackground": "2",
                "telegramBotName": '"x"', "theme": '"light"', "permissions": json.dumps(perm)})
    assert out == {"theme": "light", "permissions": {"version": 2, "permissionMode": "manual"}}


def test_hybrid_retrieval_false_becomes_keyword_mode_unless_chosen() -> None:
    assert _run({"hybridRetrieval": "false"}) == {"retrievalMode": "bm25"}
    assert _run({"hybridRetrieval": "false", "retrievalMode": '"hybrid"'}) == {"retrievalMode": "bm25"}
    assert _run({"hybridRetrieval": "false", "retrievalMode": '"bm25"'}) == {"retrievalMode": "bm25"}
    assert _run({"hybridRetrieval": "true"}) == {}
    assert _run({"hybridRetrieval": "true", "retrievalMode": '"bm25"'}) == {"retrievalMode": "bm25"}


def test_no_removed_key_is_still_a_default() -> None:
    gone = set(migrations._CONSTANT_SETTINGS) | set(migrations._LEGACY_SETTINGS) | {"hybridRetrieval"}
    assert not gone & set(llm.DEFAULT_SETTINGS)
