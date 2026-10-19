"""A coding session's REST/tool summary names the chat that started it (the Terminal tab lists a chat's own sessions).

Run: PYTHONPATH=. .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_coding_summary.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os.extensions.coding.sessions import CodingSessions, summary  # noqa: E402

ROW = {"id": "s1", "agent": "claude", "name": "n", "status": "working", "repo_path": "/r", "worktree": "/w", "created_at": 1.0, "updated_at": 1.0}


def test_summary_carries_conversation_id() -> None:
    assert summary({**ROW, "conversation_id": "c1"}, None)["conversation_id"] == "c1"
    assert summary(ROW, None)["conversation_id"] is None


def test_a_follow_up_job_with_the_same_output_size_is_not_skipped_as_already_seen() -> None:
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    s = object.__new__(CodingSessions)
    s.seen, s.jobs = {}, SimpleNamespace(jobs={})
    s._apply = lambda row, **kw: {**row, **kw}  # type: ignore[method-assign]
    s._job_events = MagicMock()  # type: ignore[method-assign]
    row = {"id": "s1", "agent": "pi", "external_id": "j1"}
    for job_id in ("j1", "j2"):
        s.jobs.jobs[job_id] = SimpleNamespace(id=job_id, total=5, status="exited", exit_code=0, buf="done\n", live=lambda: False)
        assert s._refresh_opencode({**row, "external_id": job_id}).get("status") == "done"


def test_waiting_on_a_string_is_refused_rather_than_read_as_one_id_per_character() -> None:
    import asyncio

    import pytest
    from personal_os.extensions.coding.sessions import CodingError

    with pytest.raises(CodingError, match="list"):
        asyncio.run(object.__new__(CodingSessions).wait("abc"))  # type: ignore[arg-type]
