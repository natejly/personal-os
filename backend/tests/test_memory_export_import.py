"""Memory export/import routes: a JSON round trip, scope handling, bad files."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="memexp-"))
os.environ.setdefault("PERSONAL_OS_AUTH_TOKEN", "test-token")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from personal_os.app import AUTH_TOKEN, app, memories  # noqa: E402

client = TestClient(app, headers={"X-Personal-OS-Token": AUTH_TOKEN})


def test_round_trip_and_dedupe():
    memories.create(None, "Likes tea", "preference", "user", True)
    old = memories.create(None, "Lives in Oslo", "fact", "user")
    memories.supersede(old["id"], "Lives in Bergen")  # history stays out of the export
    f = client.get("/memories/export", params={"project_id": "personal", "include_global": "false"}).json()
    assert f["grain_memories"] == 1
    assert {m["content"] for m in f["memories"]} == {"Likes tea", "Lives in Bergen"}
    assert all(set(m) == {"content", "kind", "pinned"} for m in f["memories"])
    # Importing into the same scope adds nothing; into a fresh project adds both.
    r = client.post("/memories/import", json={"file": f}).json()
    assert r == {"added": 0, "skipped": 2}
    p = client.post("/projects", json={"name": "Imp"}).json()
    r = client.post("/memories/import", json={"file": f, "project_id": p["id"]}).json()
    assert r["added"] == 2
    got = {m["content"]: m for m in memories.list(p["id"], "", False)}
    assert got["Likes tea"]["pinned"] and got["Likes tea"]["kind"] == "preference"


def test_bad_files_are_refused():
    assert client.post("/memories/import", json={"file": {"nope": 1}}).status_code == 400
    assert client.post("/memories/import", json={"file": {"grain_memories": 1, "memories": "x"}}).status_code == 400
    assert client.post("/memories/import", json={"file": {"grain_memories": 1, "memories": []}, "project_id": "all"}).status_code == 400
    r = client.post("/memories/import", json={"file": {"grain_memories": 1, "memories": [{"content": "  "}, 5, {"content": "ok", "kind": "weird"}]}}).json()
    assert r["added"] == 1
