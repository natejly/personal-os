"""Ship checklist (ship.py) with a fake command runner: gating, the push guards, the merge confirmation, retry, events."""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from personal_os import ship  # noqa: E402
from personal_os.db import Database  # noqa: E402

PR = "https://github.com/me/app/pull/7"


class Fake:
    """Answers each command by its first words; records every argv it was asked to run."""
    def __init__(self, fail: set[str] | None = None):
        self.fail = fail or set()
        self.calls: list[list[str]] = []

    async def __call__(self, argv: list[str], cwd: str, sandboxed: bool, timeout: float) -> tuple[bool, str]:
        self.calls.append(argv)
        key = "tests" if argv[0] == "/bin/zsh" else " ".join(argv[:3])
        if key in self.fail:
            return False, f"{key} failed"
        if key == "gh pr list":
            return True, ""
        if key == "gh pr create":
            return True, f"Creating pull request\n{PR}\n"
        if key == "gh pr view":
            return True, "abc1234def\n"
        return True, "ok"

    def ran(self, words: str) -> int:
        return sum(1 for a in self.calls if " ".join(a).startswith(words))


def make(tmp_path: Path, fake: Fake) -> tuple[ship.Ship, list[dict[str, Any]], Path]:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    events: list[dict[str, Any]] = []
    s = ship.Ship(Database(tmp_path / "data"), fake, lambda ev, row: events.append({"event": ev, "status": row["status"],
                                                                                   "steps": [x["status"] for x in row["steps"]]}))
    return s, events, repo


async def settle(s: ship.Ship, cid: str) -> dict[str, Any]:
    while cid in s.tasks:
        await asyncio.sleep(0.01)
    return s.get(cid)  # type: ignore[return-value]


def test_runs_to_the_merge_and_waits_for_confirm(tmp_path: Path) -> None:
    fake = Fake()
    s, events, repo = make(tmp_path, fake)

    async def go() -> None:
        row = s.create(repo_path=str(repo), branch="feature", test_command="make test", job_id="j1")
        s.start(row["id"])
        row = await settle(s, row["id"])
        assert row["status"] == "awaiting_confirm"
        assert [x["status"] for x in row["steps"]] == ["green", "green", "green", "awaiting_confirm"]
        assert row["pr_url"] == PR and row["steps"][2]["link"] == PR
        assert fake.ran("gh pr merge") == 0, "no merge without the user's confirmation"
        s.confirm(row["id"])
        with pytest.raises(ship.ShipError):
            s.confirm(row["id"])  # a second click cannot merge twice
        row = await settle(s, row["id"])
        assert row["status"] == "done" and row["merged_sha"] == "abc1234def"
        assert fake.ran("gh pr merge") == 1
        assert s.latest("j1")["id"] == row["id"]
    asyncio.run(go())
    assert all(e["event"] == "ship_checklist" for e in events)
    assert len(events) >= 9, "every transition is published"


def test_red_step_stops_and_skips_the_rest(tmp_path: Path) -> None:
    fake = Fake(fail={"git push -u"})
    s, _events, repo = make(tmp_path, fake)

    async def go() -> None:
        row = s.create(repo_path=str(repo), branch="feature", test_command="make test")
        s.start(row["id"])
        row = await settle(s, row["id"])
        assert row["status"] == "failed"
        assert [x["status"] for x in row["steps"]] == ["green", "red", "skipped", "skipped"]
        assert "failed" in row["steps"][1]["log_tail"]
        assert fake.ran("gh") == 0

        fake.fail.clear()
        s.retry(row["id"])
        row = await settle(s, row["id"])
        assert [x["status"] for x in row["steps"]] == ["green", "green", "green", "awaiting_confirm"]
        assert fake.ran("/bin/zsh") == 1, "retry resumes at the first non-green step, the tests are not re-run"
    asyncio.run(go())


def test_a_repo_may_be_anywhere_but_grains_own_folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    s, _events, repo = make(tmp_path, Fake())
    monkeypatch.setenv("PERSONAL_OS_DATA_DIR", str(tmp_path / "appdata"))  # `repo` is no longer inside it, and needs no grant
    assert s.create(repo_path=str(repo), branch="feature")["repo_path"] == str(repo.resolve())
    inner = tmp_path / "appdata" / "repo"
    (inner / ".git").mkdir(parents=True)
    with pytest.raises(ship.ShipError, match="off limits"):
        s.create(repo_path=str(inner), branch="feature")


def test_refuses_main_force_and_refspecs(tmp_path: Path) -> None:
    s, _events, repo = make(tmp_path, Fake())
    for bad in ("main", "master", "Main", "+feature", "feature:main", "refs/heads/main", "-f", "a..b"):
        with pytest.raises(ship.ShipError):
            s.create(repo_path=str(repo), branch=bad)
    with pytest.raises(ship.ShipError):
        s.create(repo_path=str(repo), branch="dev", base="dev")
    with pytest.raises(ship.ShipError):
        s.create(repo_path=str(repo), branch="feature", test_command="npm test && git push --force origin feature")
    with pytest.raises(ship.ShipError):
        s.create(repo_path="/", branch="feature")  # not a git repository
    for argv in (["git", "push", "--force", "origin", "x"], ["git", "push", "--force-with-lease"], ["git", "push", "origin", "+x"],
                 ["git", "push", "origin", "x:main"], ["git", "push", "origin", "refs/heads/x:refs/heads/master"]):
        with pytest.raises(ship.ShipError):
            ship.refuse_force(argv)
    ship.refuse_force(["git", "push", "-u", "origin", "refs/heads/x:refs/heads/x"])


def test_cancel_marks_the_rest_skipped(tmp_path: Path) -> None:
    s, _events, repo = make(tmp_path, Fake())

    async def go() -> None:
        row = s.create(repo_path=str(repo), branch="feature", test_command="make test")
        s.start(row["id"])
        row = await settle(s, row["id"])
        row = await s.cancel(row["id"])
        assert row["status"] == "cancelled" and row["steps"][3]["status"] == "skipped"
        with pytest.raises(ship.ShipError):
            s.confirm(row["id"])
    asyncio.run(go())


def test_restart_marks_a_running_step_red(tmp_path: Path) -> None:
    s, _events, repo = make(tmp_path, Fake())
    row = s.create(repo_path=str(repo), branch="feature")
    row["steps"][0]["status"] = "running"
    s._save(row)
    again = ship.Ship(s.db, Fake(), lambda *_: None)
    row = again.get(row["id"])
    assert row["status"] == "failed" and [x["status"] for x in row["steps"]] == ["red", "skipped", "skipped", "skipped"]


def test_detects_the_test_command(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text('{"scripts": {"test": "vitest"}}')
    assert ship.detect_test_command(tmp_path) == "npm test"
    (tmp_path / "package.json").write_text('{"scripts": {"test": "echo \\"Error: no test specified\\" && exit 1"}}')
    assert ship.detect_test_command(tmp_path) is None
    (tmp_path / "pyproject.toml").write_text("")
    assert ship.detect_test_command(tmp_path) == "python3 -m pytest"


def test_routes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """GET /ship/{id}, a 404 for an unknown one, and the confirm route's 409 when nothing waits."""
    os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="shiptest-"))
    from fastapi.testclient import TestClient
    from personal_os import app as appmod
    c = TestClient(appmod.app, headers={"X-Personal-OS-Token": appmod.AUTH_TOKEN})
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    row = appmod.ship_runner.create(repo_path=str(repo), branch="feature")
    assert c.get(f"/ship/{row['id']}").json()["branch"] == "feature"
    assert c.post(f"/ship/{row['id']}/confirm").status_code == 409
    assert c.get("/ship/nope").status_code == 404
