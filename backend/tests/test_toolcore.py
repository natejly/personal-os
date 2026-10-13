"""toolcore: the usage-ranked always-offered tools (ranking, pinned floor, hysteresis, order, budget, fallback)."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="toolcore-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os import subagents as sa, toolcore, tools  # noqa: E402
from personal_os.db import Database  # noqa: E402

STATIC = tools.CORE_ORDER
PINNED = toolcore.PINNED["chat"]
SLOTS = len(STATIC) - len(PINNED)
SIZE = lambda n: 100  # noqa: E731


def names(prefix: str, n: int) -> list[str]:
    return [f"{prefix}{i:02d}" for i in range(n)]


def test_pinned_first_in_fixed_order_then_dynamic_sorted_by_name() -> None:
    ranked = ["zeta", "alpha", "mid"] + names("t", 20)
    out = toolcore.pick("chat", ranked, (), STATIC, SIZE)
    assert out[:len(PINNED)] == PINNED and len(out) == len(STATIC)
    assert list(out[len(PINNED):]) == sorted(out[len(PINNED):]) and {"zeta", "alpha", "mid"} <= set(out)
    # the same tools with other counts (different rank order) give the same bytes
    assert toolcore.pick("chat", ["mid", "alpha", "zeta"] + names("t", 20), (), STATIC, SIZE) == out


def test_a_pinned_tool_is_never_dropped_or_double_counted() -> None:
    out = toolcore.pick("chat", [*PINNED, *names("t", 20)], (), STATIC, SIZE)
    assert set(PINNED) <= set(out) and len(set(out)) == len(out) == len(STATIC)


def test_hysteresis_keeps_a_slightly_dropped_tool_and_admits_a_clear_riser() -> None:
    base = names("t", 30)
    prev = toolcore.pick("chat", base, (), STATIC, SIZE)
    # t00 slips just outside the top slots (still within the slack): it stays while there is room
    slipped = base[1:SLOTS + 1] + ["t00"] + base[SLOTS + 1:]
    assert "t00" in toolcore.pick("chat", slipped, prev, STATIC, SIZE), "a one-place swap at the cut changes nothing"
    # a riser at rank 1 enters, displacing the lowest incumbent; the rest stay
    riser = ["new"] + base
    out = toolcore.pick("chat", riser, prev, STATIC, SIZE)
    assert "new" in out and "t00" in out and f"t{SLOTS - 1:02d}" not in out and len(out) == len(STATIC)
    # one far below the cut is dropped
    far = base[1:] + ["t00"]
    assert "t00" not in toolcore.pick("chat", far, prev, STATIC, SIZE)


def test_the_char_budget_skips_a_tool_that_would_not_fit() -> None:
    size = lambda n: 100_000 if n == "huge" else 100  # noqa: E731
    out = toolcore.pick("chat", ["huge", *names("t", 20)], (), STATIC, size)
    assert "huge" not in out and len(out) == len(STATIC)


def test_unknown_tools_are_skipped() -> None:
    size = lambda n: None if n == "gone" else 100  # noqa: E731
    assert "gone" not in toolcore.pick("chat", ["gone", *names("t", 20)], (), STATIC, size)


def _seed(db: Database, runs: list[tuple[str, str, list[str]]]) -> None:
    now = time.time()
    with db.tx() as c:
        for i, (run_id, kind, calls) in enumerate(runs):
            c.execute("INSERT INTO agent_runs(run_id, kind, status, started_at, updated_at) VALUES(?,?,?,?,?)", (run_id, kind, "done", now, now))
            for j, name in enumerate(calls):
                c.execute("INSERT INTO run_events(run_id, seq, type, data, ts) VALUES(?,?,?,?,?)",
                          (run_id, j, "tool_call", json.dumps({"name": name}), now))


def test_ranking_counts_distinct_runs_per_role_and_refresh_falls_back_on_thin_data() -> None:
    with tempfile.TemporaryDirectory() as d:
        db = Database(d)
        # a runaway run calls gmail_modify 100 times; three runs each call doc_edit once
        _seed(db, [("r0", "chat", ["gmail_modify"] * 100), ("r1", "chat", ["doc_edit"]), ("r2", "desk", ["doc_edit"]),
                   ("r3", "chat", ["doc_edit"]), ("w1", "worker", ["shell_run"] * 3), ("w2", "subagent", ["shell_run"])])
        chat, calls = toolcore.ranking(db, "chat")
        assert chat[:2] == ["doc_edit", "gmail_modify"] and calls == 103
        assert toolcore.ranking(db, "worker")[0] == ["shell_run"]
        # 103 chat calls is enough; the worker role has 4: it keeps the static list
        out = toolcore.refresh(db, lambda n: 100, {"chat": STATIC, "worker": sa.WORKER_CORE})
        assert "chat" in out and "worker" not in out and "gmail_modify" in out["chat"]
        toolcore.CURRENT.clear()


def test_refresh_saves_and_load_restores_the_core() -> None:
    with tempfile.TemporaryDirectory() as d:
        db = Database(d)
        _seed(db, [(f"r{i}", "chat", ["doc_edit"] * 2) for i in range(30)])
        out = toolcore.refresh(db, lambda n: 100, {"chat": STATIC, "worker": sa.WORKER_CORE})
        assert "doc_edit" in out["chat"] and "worker" not in out
        saved = dict(toolcore.CURRENT)
        toolcore.CURRENT.clear()
        toolcore.load(db)
        assert dict(toolcore.CURRENT) == saved
        toolcore.CURRENT.clear()


def test_a_worker_is_only_offered_core_tools_its_role_has() -> None:
    from personal_os import app as appmod
    tb = appmod.toolbox
    core = toolcore.pick("worker", ["fs_edit", "gmail_send", "shell_run", *sa.WORKER_CORE], (), sa.WORKER_CORE, toolcore.schema_chars(tb.specs))
    ch = type("C", (), {})()
    ch.modes = {"shell_run": "on", "read_tool_result": "on", "tool_search": "on"}
    ch.ctx = {"core": core, "tool_loaded": set(), "settings": {"toolDeferAbove": 1}}
    mgr = sa.Subagents.__new__(sa.Subagents)
    mgr.toolbox = tb
    offered = [s["function"]["name"] for s in mgr._schemas(ch)]
    assert set(offered) <= {"tool_search", "shell_run", "read_tool_result"} and "gmail_send" not in offered and "fs_edit" not in offered


def test_the_dynamic_chat_core_fits_the_static_core_char_budget() -> None:
    from personal_os import app as appmod
    size = toolcore.schema_chars(appmod.toolbox.specs)
    ranked = [n for n in appmod.toolbox.specs if n not in STATIC]
    out = toolcore.pick("chat", ranked, (), STATIC, size)
    assert sum(size(n) or 0 for n in out) <= sum(size(n) or 0 for n in STATIC)
