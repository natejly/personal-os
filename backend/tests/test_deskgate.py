"""deskgate.py: the structural gate, the independent reviewer, and the desk_deliver / desk_ask changes in tools.py.
No model and no app: a real Database/Desks/Workspace in a temp dir behind a unit-level Toolbox, with the reviewer stubbed."""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from personal_os import deskgate  # noqa: E402
from personal_os.cowork import Desks  # noqa: E402
from personal_os.db import Database  # noqa: E402
from personal_os.tools import Toolbox  # noqa: E402
from personal_os.workspace import Workspace  # noqa: E402
from personal_os.working import Plans as WorkPlans  # noqa: E402


class FakePlans:
    def __init__(self) -> None:
        self.left: list[dict[str, Any]] = []

    def remaining(self, plan_id: str) -> list[dict[str, Any]]:
        return self.left if plan_id else []


class Rig:
    def __init__(self, tmp: Path, **settings: Any) -> None:
        self.settings: dict[str, Any] = {"deskDoneGate": True, "deskSelfReview": False, **settings}
        self.db = Database(tmp / "data")
        self.ws = Workspace(tmp / "ws")
        self.desks = Desks(self.db, self.ws)
        self.work = WorkPlans(self.db)
        with self.db.tx() as c:
            c.execute("INSERT INTO conversations(id, title, model, created_at, updated_at) VALUES(?,?,?,?,?)", ("c1", "t", "m", 0.0, 0.0))
        self.desk = self.desks.create(conversation_id="c1", brief="Write a pricing summary with three tiers.")
        self.id = self.desk["id"]
        self.ws.ensure(self.id)
        self.tb = Toolbox(None, None, None, lambda: self.settings, work_plans=self.work, desks=self.desks, workspace=self.ws)  # type: ignore[arg-type]
        self.tb.plans = FakePlans()
        self.ctx: dict[str, Any] = {"conversation_id": "c1", "desk_id": self.id, "settings": self.settings, "tainted": False,
                                    "taint_sources": [], "message_id": None}

    def run(self, name: str, **a: Any) -> Any:
        return asyncio.run(self.tb.call(name, a, self.ctx))

    def write(self, rel: str, text: str) -> Path:
        p = self.ws.desk_root(self.id) / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        return p

    def deliver(self, rel: str) -> Any:
        return self.run("desk_deliver", path=rel, title=rel)

    def problems(self) -> list[str]:
        return deskgate.problems(desks=self.desks, workspace=self.ws, desk_id=self.id, conversation_id="c1",
                                 work_plans=self.work, plans=self.tb.plans)


@pytest.fixture
def rig(tmp_path: Path) -> Rig:
    return Rig(tmp_path)


def test_clean_desk_has_no_problems(rig: Rig) -> None:
    assert rig.problems() == []
    assert rig.run("desk_done", summary="nothing to do")["status"] == "done"


def test_open_todos_and_remaining_plan_steps(rig: Rig) -> None:
    rig.work.set("c1", [{"text": "draft", "status": "done"}, {"text": "check figures", "status": "in_progress"}])
    got = rig.problems()
    assert len(got) == 1 and "check figures" in got[0] and "todo_write" in got[0]
    rig.work.set("c1", [{"text": "draft", "status": "done"}])
    assert rig.problems() == []
    rig.desks.set_status(rig.id, "working", reason="", plan_id="p1")
    rig.tb.plans.left = [{"tool": "gmail_draft"}]
    got = rig.problems()
    assert len(got) == 1 and "approved plan" in got[0] and "gmail_draft" in got[0]


def test_outputs_missing_empty_changed_and_undelivered(rig: Rig) -> None:
    p = rig.write("outputs/a.md", "# real\n")
    assert rig.deliver("outputs/a.md")["status"] == "awaiting_review"
    assert rig.problems() == []
    p.write_text("# real, then edited\n")
    assert "changed after you delivered" in rig.problems()[0]
    assert rig.deliver("outputs/a.md")["status"] == "awaiting_review"
    assert rig.problems() == []
    p.write_text("")
    assert "is empty" in rig.problems()[0]
    p.unlink()
    assert "no longer exists" in rig.problems()[0]
    rig.write("outputs/b.md", "never nominated")
    rig.write("work/scratch.md", "fine")
    rig.write("outputs/.hidden", "ignored")
    got = "\n".join(rig.problems())
    assert "outputs/b.md is in outputs/ but was never delivered" in got and "scratch" not in got and ".hidden" not in got


def test_refusal_cap_and_recorded_open_items(rig: Rig) -> None:
    rig.write("outputs/b.md", "x")
    for _ in range(2):
        out = rig.run("desk_done", summary="all done")
        assert "refused" in out["error"] and "outputs/b.md" in out["error"] and out["try_instead"]
    assert rig.desks.get(rig.id)["status"] != "done"
    third = rig.run("desk_done", summary="all done")
    assert third["status"] in ("done", "review")
    notes = [e["body"] for e in rig.desks.events(rig.id) if e["kind"] == "note"]
    assert any("Open items when the desk was closed" in n and "outputs/b.md" in n for n in notes)


def test_gate_off_lets_it_through(tmp_path: Path) -> None:
    r = Rig(tmp_path, deskDoneGate=False)
    r.write("outputs/b.md", "x")
    assert r.run("desk_done", summary="s")["status"] in ("done", "review")


def test_deliver_refuses_empty_and_warns_on_placeholders(rig: Rig) -> None:
    rig.write("outputs/e.md", "")
    assert "empty" in rig.deliver("outputs/e.md")["error"]
    rig.write("outputs/t.md", "# Title\nPrice: TBD\nfine\nlorem ipsum dolor\nmaster todos tobdone [Placeholder]\n")
    out = rig.deliver("outputs/t.md")
    assert out["status"] == "awaiting_review"
    w = out["warning"]
    assert "TBD (line 2)" in w and "lorem ipsum (line 4)" in w and "[Placeholder] (line 5)" in w and "todos" not in w
    rig.write("outputs/ok.md", "a finished document\n")
    assert "warning" not in rig.deliver("outputs/ok.md")


def test_verdict_parser() -> None:
    assert deskgate.parse_verdict("looked fine\nVERDICT: pass") == ("pass", "")
    v, gaps = deskgate.parse_verdict("evidence...\nVERDICT: fail\n1. no third tier\n2. title missing")
    assert v == "fail" and "1. no third tier" in gaps and "2. title missing" in gaps
    assert deskgate.parse_verdict("verdict: FAIL 1. missing")[0] == "fail"
    assert deskgate.parse_verdict("it was great, I think")[0] == "none"
    assert deskgate.parse_verdict("The VERDICT: pass line should be alone")[0] == "none"
    assert deskgate.parse_verdict("")[0] == "none"
    assert deskgate.parse_verdict("VERDICT: pass\nVERDICT: fail\n1. x")[0] == "fail"  # the last one wins


def stub_review(monkeypatch: pytest.MonkeyPatch, replies: list[str | None]) -> list[str]:
    tasks: list[str] = []

    async def fake(tb: Any, ctx: dict[str, Any], task: str) -> str | None:
        tasks.append(task)
        return replies.pop(0)
    monkeypatch.setattr(deskgate, "run_review", fake)
    return tasks


def test_review_blocks_once_then_finishes_with_notes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    r = Rig(tmp_path, deskSelfReview=True)
    r.write("outputs/a.md", "# tiers\n")
    r.deliver("outputs/a.md")
    tasks = stub_review(monkeypatch, ["checked.\nVERDICT: fail\n1. only two tiers", "VERDICT: pass"])
    first = r.run("desk_done", summary="made the summary")
    assert "independent review" in first["error"] and "only two tiers" in first["error"]
    assert "Write a pricing summary with three tiers." in tasks[0] and "made the summary" in tasks[0] and "outputs/a.md" in tasks[0]
    assert "VERDICT: pass" in tasks[0] and "VERDICT: fail" in tasks[0]
    second = r.run("desk_done", summary="made the summary")
    assert second["status"] == "review" and len(tasks) == 1  # never reviewed twice
    notes = [e["body"] for e in r.desks.events(r.id) if e["kind"] == "note"]
    assert any("Reviewer notes" in n and "only two tiers" in n for n in notes)


@pytest.mark.parametrize("reply", ["VERDICT: pass", "rambling with no verdict line", None])
def test_review_pass_missing_or_unavailable_proceeds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reply: str | None) -> None:
    r = Rig(tmp_path, deskSelfReview=True)
    stub_review(monkeypatch, [reply])
    assert r.run("desk_done", summary="s")["status"] == "done"


def test_review_skipped_without_machinery(tmp_path: Path) -> None:
    r = Rig(tmp_path, deskSelfReview=True)  # no tb.subagents and no run_id: the real run_review declines
    assert asyncio.run(deskgate.run_review(r.tb, {**r.ctx, "run_id": "r1"}, "task")) is None
    assert asyncio.run(deskgate.run_review(r.tb, r.ctx, "task")) is None
    assert r.run("desk_done", summary="s")["status"] == "done"


def test_review_not_run_when_the_gate_gave_way(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    r = Rig(tmp_path, deskSelfReview=True)
    r.write("outputs/b.md", "x")
    tasks = stub_review(monkeypatch, ["VERDICT: fail\n1. no"])
    r.tb.deskgate_state = {r.id: {"refusals": 2, "reviewed": False, "notes": "", "open": []}}
    assert r.run("desk_done", summary="s")["status"] in ("done", "review")
    assert tasks == []


def test_desk_ask_options_validation(rig: Rig) -> None:
    for bad in (["only one"], ["a", "b", "c", "d", "e"], "a,b", ["a", ""], ["a", 3]):
        assert rig.run("desk_ask", question="Which?", options=bad)["field"] == "options"
    out = rig.run("desk_ask", question="Which?", options=["Dana", "Team"])
    assert out["status"] == "waiting_for_user" and out["options"] == ["Dana", "Team"]
    assert rig.desks.get(rig.id)["status"] == "blocked"
    plain = rig.run("desk_ask", question="Which?")  # backward compatible
    assert plain["status"] == "waiting_for_user" and "options" not in plain


def test_desk_ask_empty_note_does_not_block(rig: Rig) -> None:
    rig.desks.set_status(rig.id, "working", reason="")
    rig.ctx["modes"] = {"desk_ask": "ask"}  # a card gated this call, so reaching the body means approved with no answer
    out = rig.run("desk_ask", question="Which?")
    assert out["status"] == "no_answer" and "best judgement" in out["note"]
    assert rig.desks.get(rig.id)["status"] == "working"


def test_desk_ask_returns_choice_for_a_matching_answer(rig: Rig) -> None:
    rig.ctx["ask_note"] = "Team"
    out = rig.run("desk_ask", question="Which?", options=["Dana", "Team"])
    assert out["status"] == "answered" and out["choice"] == "Team"
    rig.ctx["ask_note"] = "something else"
    assert "choice" not in rig.run("desk_ask", question="Which?", options=["Dana", "Team"])
