"""/btw: answered from the chat's context beside a live run; the stored note is never replayed to the model.

Run: PERSONAL_OS_TEST_CHILD=1 backend/.venv/bin/python -m pytest backend/tests/test_side_question.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="sidetest-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from personal_os import app as appmod  # noqa: E402
from personal_os import llm  # noqa: E402

appmod.db.set_settings({"autoLearn": False, "baseUrl": ""})
convos, run_store = appmod.convos, appmod.run_store


def test_side_question_beside_live_run() -> None:
    cid = convos.create(None, "t", "m")["id"]
    convos.add_message(cid, "user", "pick a file for X")
    run_store.create("r1", cid)
    run_store.update("r1", status="running")
    run_store.append("r1", 1, "delta", {"id": "a1", "text": "I chose config.toml"})
    seen: list[list[dict[str, Any]]] = []

    async def fake(settings: Any, model: str, messages: list[dict[str, Any]], kind: str = "other", **kw: Any) -> str:
        seen.append(messages)
        assert "tools" not in kw
        return "config.toml"

    prev, llm.complete = llm.complete, fake  # type: ignore[assignment]
    try:
        r = TestClient(appmod.app, headers={"X-Personal-OS-Token": appmod.AUTH_TOKEN}).post(f"/chats/{cid}/side-question", json={"question": "what file did you pick?"})
    finally:
        llm.complete = prev  # type: ignore[assignment]
    assert r.status_code == 200, r.text
    assert json.loads(r.json()["content"]) == {"q": "what file did you pick?", "a": "config.toml"}
    assert r.json()["kind"] == "side_note"
    blob = json.dumps(seen[0])
    assert "pick a file for X" in blob and "I chose config.toml" in blob
    assert run_store.get("r1")["status"] == "running"  # the run was not touched
    # The main agent's replay never contains the note.
    assert all("what file did you pick" not in m["content"] for m in convos.history(cid))
    assert all("what file did you pick" not in m["content"] for m in convos.history_rows(cid))
    assert any(m.get("kind") == "side_note" for m in convos.get(cid)["messages"])  # but the chat keeps it


if __name__ == "__main__":
    test_side_question_beside_live_run()
    print("ok")
