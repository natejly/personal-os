"""Trashing a doc (alone or with its folder) sweeps its Space windows.

# Regression: trashing a doc left its Space windows pointing at a missing doc (404 in the widget)
# Found by /qa on 2026-10-06
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from typing import Any

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="doctrashwin-"))
os.environ.setdefault("PERSONAL_OS_AUTH_TOKEN", "test-token")
os.environ.setdefault("GRAIN_SECRETS_BACKEND", "file")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from personal_os.app import AUTH_TOKEN, app  # noqa: E402

client = TestClient(app, headers={"X-Personal-OS-Token": AUTH_TOKEN})


def j(method: str, path: str, body: Any = None) -> Any:
    r = client.request(method, path, json=body)
    assert r.status_code == 200, f"{method} {path} -> {r.status_code} {r.text[:300]}"
    return r.json()


def _space_with(doc_id: str) -> str:
    cv = j("POST", "/canvases", {"name": "trash-win"})["id"]
    j("POST", f"/canvases/{cv}/windows", {"kind": "doc", "ref_id": doc_id})
    j("POST", f"/canvases/{cv}/windows", {"kind": "todos"})
    return cv


def _kinds(cv: str) -> list[str]:
    return [w["kind"] for w in j("GET", f"/canvases/{cv}")["windows"]]


def test_trashing_a_doc_removes_its_windows() -> None:
    doc = j("POST", "/docs", {"title": "Gone"})
    cv = _space_with(doc["id"])
    assert sorted(_kinds(cv)) == ["doc", "todos"]
    j("DELETE", f"/docs/{doc['id']}")
    assert _kinds(cv) == ["todos"]


def test_trashing_a_folder_with_its_docs_removes_their_windows() -> None:
    doc = j("POST", "/docs", {"title": "Filed", "folder": "Old"})
    cv = _space_with(doc["id"])
    j("DELETE", "/docs/folders?path=Old&delete_docs=true")
    assert _kinds(cv) == ["todos"]
