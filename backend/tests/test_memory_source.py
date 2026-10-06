"""GET /memories/{id}/source: the quote of the user message a memory was learned from.

Run: pytest backend/tests/test_memory_source.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="memsourcetest-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from personal_os import app as appmod  # noqa: E402
from personal_os import memory_limits  # noqa: E402

client = TestClient(appmod.app, headers={"X-Personal-OS-Token": appmod.AUTH_TOKEN})


def _learned(text: str, title: str = "Trip planning") -> tuple[dict, dict, dict]:
    conv = appmod.convos.create(None, title, "m")
    msg = appmod.convos.add_message(conv["id"], "user", text)
    mem = appmod.memories.create(None, f"fact from {msg['id']}", source="auto",
                                 provenance={"conversation_id": conv["id"], "message_id": msg["id"]})
    return conv, msg, mem


def test_source_returns_ids_title_and_collapsed_quote() -> None:
    conv, msg, mem = _learned("  I always\n\n book   window seats\t on long flights ")
    r = client.get(f"/memories/{mem['id']}/source")
    assert r.status_code == 200
    assert r.json() == {"conversation_id": conv["id"], "message_id": msg["id"], "title": "Trip planning",
                        "quote": "I always book window seats on long flights"}


def test_long_message_is_cut_with_an_ellipsis() -> None:
    cap = memory_limits.SOURCE_QUOTE_CHARS
    _, _, mem = _learned("x" * (cap + 50))
    q = client.get(f"/memories/{mem['id']}/source").json()["quote"]
    assert q == "x" * cap + "…"
    _, _, exact = _learned("y" * cap)
    assert client.get(f"/memories/{exact['id']}/source").json()["quote"] == "y" * cap


def test_memory_without_a_source_is_404() -> None:
    mem = appmod.memories.create(None, "User likes tea", source="user")
    assert client.get(f"/memories/{mem['id']}/source").status_code == 404


def test_unknown_memory_is_404() -> None:
    assert client.get("/memories/nope/source").status_code == 404


def test_deleted_message_is_404() -> None:
    conv, msg, mem = _learned("remember I like tea")
    assert appmod.convos.delete_message(msg["id"], conv["id"])
    assert client.get(f"/memories/{mem['id']}/source").status_code == 404
