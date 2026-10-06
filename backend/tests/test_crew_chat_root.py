"""A chat id is a crew root: GET /crew/{chat} lists the subagents its replies spawned, nested by parent.

Run: PERSONAL_OS_DATA_DIR=/tmp/crewchat python backend/tests/test_crew_chat_root.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="crewchat-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os import app as appmod  # noqa: E402

conv = appmod.convos.create(None, "Research chat", "m")
cid = conv["id"]
rs = appmod.run_store
rs.create("turn1", cid, "chat", {})
rs.create("kid1", None, "subagent", {"role": "researcher", "task": "look"}, parent_run_id="turn1")
rs.create("kid2", None, "subagent", {"role": "worker", "task": "do"}, parent_run_id="kid1")

v = appmod.crew_view(cid)
assert v["root"]["kind"] == "chat" and v["root"]["id"] == cid, v["root"]
by = {a["id"]: a for a in v["agents"]}
assert set(by) == {"kid1", "kid2"}, by
assert by["kid1"]["parent_id"] is None and by["kid2"]["parent_id"] == "kid1", by
print("ok")
