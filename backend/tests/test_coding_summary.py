"""A coding session's REST/tool summary names the chat that started it (the Terminal tab lists a chat's own sessions).

Run: PYTHONPATH=. .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_coding_summary.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os.extensions.coding.sessions import summary  # noqa: E402

ROW = {"id": "s1", "agent": "claude", "name": "n", "status": "working", "repo_path": "/r", "worktree": "/w", "created_at": 1.0, "updated_at": 1.0}


def test_summary_carries_conversation_id() -> None:
    assert summary({**ROW, "conversation_id": "c1"}, None)["conversation_id"] == "c1"
    assert summary(ROW, None)["conversation_id"] is None
