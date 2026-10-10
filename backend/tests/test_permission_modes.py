"""Permission modes: the pure routing helpers (autoreview.route / apply / parse), the reviewer's fail-closed behaviour,
its model choice and per-reply cache, and the migration that folds an existing install into "auto".

Run: backend/.venv/bin/python -m pytest backend/tests/test_permission_modes.py
"""
from __future__ import annotations

import asyncio
import itertools
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os import autoreview, llm, permissions  # noqa: E402

DANGERS = ("safe", "writes", "network", "executes", "external", "schedules")


def r(pmode: str, **kw: Any) -> str:
    base: dict[str, Any] = dict(mode="ask", danger="writes")
    return autoreview.route(pmode, **{**base, **kw})


# ---- route ----------------------------------------------------------------------------------------

def test_off_is_off_in_every_mode() -> None:
    for pm in autoreview.MODES:
        assert r(pm, mode="off") == "off"


def test_question_tools_keep_their_mode() -> None:
    for pm in autoreview.MODES:
        assert r(pm, mode="ask", question=True) == "card" and r(pm, mode="on", question=True) == "run"


def test_manual_is_the_per_tool_mode() -> None:
    for danger, soft, hard, fenced in itertools.product(DANGERS, (False, True), (False, True), (False, True)):
        kw = dict(danger=danger, soft_forced=soft, hard_forced=hard, fenced=fenced)
        assert r("manual", mode="ask", **kw) == "card" and r("manual", mode="on", **kw) == "run"


def test_allow_all_never_stops() -> None:
    for danger, soft, hard, ea in itertools.product(DANGERS, (False, True), (False, True), (False, True)):
        kw = dict(danger=danger, soft_forced=soft, hard_forced=hard, explicit_ask=ea)
        assert r("allow_all", mode="ask", **kw) == "run" and r("allow_all", mode="on", **kw) == "run"
        assert r("allow_all", mode="ask", fenced=True, **kw) == "run"


def test_auto_on_runs_only_what_is_known_safe() -> None:
    for danger in DANGERS:
        expect = "run" if danger == "safe" else "review"
        assert r("auto", mode="on", danger=danger) == expect
        assert r("auto", mode="on", danger=danger, explicit_on=True) == "run"
        assert r("auto", mode="on", danger=danger, covered=True) == "run"


def test_auto_ask_routing() -> None:
    for danger in DANGERS:
        assert r("auto", danger=danger) == "review"
        assert r("auto", danger=danger, soft_forced=True) == "review_strict"  # alwaysAsk / force_ask, untainted
        for flag in ("explicit_ask", "fenced", "hard_forced"):
            assert r("auto", danger=danger, **{flag: True}) == "card", flag
        # a hard force beats a soft one (tainted alwaysAsk is never reviewed away)
        assert r("auto", danger=danger, soft_forced=True, hard_forced=True) == "card"


def test_explicit_off_beats_everything() -> None:
    for pm in autoreview.MODES:
        assert r(pm, mode="off", explicit_on=True, covered=True) == "off"


# ---- apply ----------------------------------------------------------------------------------------

def test_apply() -> None:
    assert autoreview.apply("review", "allow", "low", True) == "run"
    assert autoreview.apply("review", "deny", "high", False) == "deny"
    assert autoreview.apply("review", "ask", "high", False) == "card"
    assert autoreview.apply("review_strict", "allow", "high", False) == "run"
    assert autoreview.apply("review_strict", "allow", "medium", False) == "card"
    assert autoreview.apply("review_strict", "allow", "low", False) == "card"
    assert autoreview.apply("review_strict", "allow", "high", True) == "card", "a tainted reply never lifts an alwaysAsk card"
    assert autoreview.apply("review_strict", "deny", "low", True) == "deny"
    assert autoreview.apply("review_strict", "ask", "high", False) == "card"


def test_mode_of() -> None:
    assert autoreview.mode_of({}) == "auto"
    assert autoreview.mode_of({"permissionMode": "manual"}) == "manual"
    assert autoreview.mode_of({"permissionMode": "allow_all"}) == "allow_all"
    assert autoreview.mode_of({"permissions": {"permissionMode": "manual"}}) == "manual"
    assert autoreview.mode_of({"permissionMode": "bogus"}) == "auto"


# ---- parse ----------------------------------------------------------------------------------------

def test_parse() -> None:
    assert autoreview.parse('ok {"verdict":"allow","confidence":"high","reason":"fine"}') == ("allow", "fine", "high")
    assert autoreview.parse('{"verdict":"deny","confidence":"medium","reason":"no"}') == ("deny", "no", "medium")
    assert autoreview.parse('{"verdict":"ask","reason":"hm"}') == ("ask", "hm", "low")
    assert autoreview.parse('{"verdict":"allow","confidence":"certain","reason":"x"}')[2] == "low"
    for bad in ("", "nonsense", '{"verdict":"maybe"}', "{not json}"):
        assert autoreview.parse(bad) == ("ask", "The reviewer's answer could not be read.", "low")


def test_digest_caps_recent_turns() -> None:
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "first task"}] + [
        {"role": "assistant" if i % 2 else "user", "content": "x" * 900} for i in range(10)]
    recent, first = autoreview.digest(msgs)
    assert first == "first task" and len(recent) <= autoreview.CONTEXT_CHARS and recent.count("\n") <= autoreview.CONTEXT_MESSAGES - 1


# ---- review ---------------------------------------------------------------------------------------

def call_review(cfg: dict[str, Any] | None = None, **kw: Any) -> dict[str, Any]:
    args = dict(name="doc_create", description="d", args={"t": 1}, danger="writes", user_text="u", mode="on", tainted=False)
    return asyncio.run(autoreview.review(cfg or {}, "chat-model", **{**args, **kw}))


def test_review_fails_closed_on_error(monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(*a: Any, **k: Any) -> str:
        raise RuntimeError("down")
    monkeypatch.setattr(llm, "complete", boom)
    res = call_review()
    assert res["verdict"] == "ask" and res["confidence"] == "low" and "RuntimeError" in res["reason"]
    assert set(res) >= {"verdict", "reason", "confidence", "model", "ms"}


def test_review_fails_closed_on_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    async def hang(*a: Any, **k: Any) -> str:
        await asyncio.sleep(5)
        return '{"verdict":"allow","confidence":"high"}'
    monkeypatch.setattr(llm, "complete", hang)
    monkeypatch.setattr(autoreview, "REVIEW_TIMEOUT_SECONDS", 0.05)
    res = call_review()
    assert res["verdict"] == "ask" and "Timeout" in res["reason"]


def test_review_model_choice(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    async def fake(settings: Any, model: str, *a: Any, **k: Any) -> str:
        seen.append(model)
        return '{"verdict":"allow","confidence":"high","reason":"ok"}'
    monkeypatch.setattr(llm, "complete", fake)
    cfg = {"autoReviewModel": "rev", "fastModel": "fast", "extractionModel": "ext"}
    for drop, want in ((None, "rev"), ("autoReviewModel", "fast"), ("fastModel", "ext"), ("extractionModel", "chat-model")):
        if drop:
            cfg.pop(drop)
        assert call_review(dict(cfg))["model"] == want and seen[-1] == want


def test_cache_hit_skips_the_second_call_and_only_allow_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    answers = ['{"verdict":"allow","confidence":"high","reason":"ok"}']
    calls: list[int] = []

    async def fake(*a: Any, **k: Any) -> str:
        calls.append(1)
        return answers[0]
    monkeypatch.setattr(llm, "complete", fake)
    cache: dict = {}
    first = call_review(cache=cache, conv_id="c1")
    again = call_review(cache=cache, conv_id="c1", args={"t": 1})
    assert len(calls) == 1 and again.get("cached") is True and again["verdict"] == "allow" and "cached" not in first
    call_review(cache=cache, conv_id="c1", args={"t": 2})
    call_review(cache=cache, conv_id="c2")
    assert len(calls) == 3, "another argument or conversation is a new review"
    answers[0] = '{"verdict":"ask","reason":"hm"}'
    cache.clear()
    call_review(cache=cache, conv_id="c1"), call_review(cache=cache, conv_id="c1")
    assert len(calls) == 5 and not cache, "an ask is never cached"


def test_the_deny_message_text() -> None:
    from personal_os import tools
    msg = json.dumps(tools.denied("doc_create", "refused by the safety reviewer: not asked. Do not retry the same call; change approach or ask the user."))
    assert "refused by the safety reviewer: not asked" in msg


# ---- store + migration ----------------------------------------------------------------------------

def settings_db() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    return c


def stored(c: sqlite3.Connection) -> dict[str, Any]:
    return json.loads(c.execute("SELECT value FROM settings WHERE key='permissions'").fetchone()[0])


def test_migration_sets_auto_and_keeps_everything_else() -> None:
    c = settings_db()
    row = {"version": 1, "skipPermissions": True, "autoReview": "risky", "workspaceRoots": ["/Users/x/Proj"],
           "tools": {"web_search": "off"}, "alwaysAsk": ["gmail_send"], "docEditMode": "apply"}
    c.execute("INSERT INTO settings VALUES ('permissions', ?)", (json.dumps(row),))
    permissions.migrate_mode(c)
    got = stored(c)
    assert got["permissionMode"] == "auto" and got["version"] == permissions.VERSION == 2
    assert {k: v for k, v in got.items() if k not in ("permissionMode", "version")} == {k: v for k, v in row.items() if k != "version"}
    permissions.migrate_mode(c)
    assert stored(c) == got, "idempotent"


def test_migration_does_not_overwrite_a_chosen_mode() -> None:
    c = settings_db()
    c.execute("INSERT INTO settings VALUES ('permissions', ?)", (json.dumps({"version": 2, "permissionMode": "manual"}),))
    permissions.migrate_mode(c)
    assert stored(c)["permissionMode"] == "manual"


def test_migration_on_a_fresh_store_and_a_legacy_row() -> None:
    c = settings_db()
    permissions.migrate_mode(c)
    assert stored(c) == {"version": 2, "permissionMode": "auto"}
    c = settings_db()
    c.execute("INSERT INTO settings VALUES ('skipPermissions', 'true')")  # a stray top-level legacy row folds in
    permissions.migrate_mode(c)
    assert stored(c)["skipPermissions"] is True and stored(c)["permissionMode"] == "auto"


def test_validate_permission_mode() -> None:
    for ok in ("auto", "manual", "allow_all"):
        assert permissions.validate("permissionMode", ok) == ok
    for bad in ("sometimes", "", None, 3):
        with pytest.raises(ValueError):
            permissions.validate("permissionMode", bad)
    assert permissions.DEFAULTS["permissionMode"] == "auto"
    # legacy keys still validate
    assert permissions.validate("skipPermissions", True) is True and permissions.validate("autoReview", "risky") == "risky"
