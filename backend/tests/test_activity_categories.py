"""Category rules: classification, rollup, per-day cats, mining, routes' helpers.

No network and no model: classification, stats, mining and the report never call one. A stub
complete_fn that raises proves it.

Runs under pytest, or directly: python backend/tests/test_activity_categories.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os import activity, activity_categories as ac, insights  # noqa: E402
from personal_os.db import Database  # noqa: E402

SETTINGS = {"baseUrl": "http://localhost:4000", "apiKey": "", "defaultModel": "m", "extractionModel": ""}


def _monitor() -> activity.Monitor:
    async def boom(*a, **k):
        raise AssertionError("categories must not call a model")
    return activity.Monitor(Database(Path(tempfile.mkdtemp())), lambda: dict(SETTINGS), boom)


def _at(day_offset: float, hour: int, minute: int = 0) -> float:
    base = datetime.now().replace(hour=hour, minute=minute, second=0, microsecond=0)
    return base.timestamp() - day_offset * 86400


def _focus(ts: float, app: str, seconds: float, title: str = "", url: str = "") -> dict:
    return {"ts": ts, "kind": "focus", "app": app, "title": title, "url": url, "text": "",
            "meta": {}, "duration_ms": int(seconds * 1000)}


def R(name, pattern=None, score=None, **kw):
    rule = {"type": "regex", "pattern": pattern, **kw} if pattern else {"type": "none"}
    out = {"name": name, "rule": rule}
    if score is not None:
        out["score"] = score
    return out


def test_deepest_match_wins_and_ties_go_to_list_order() -> None:
    e = ac.CategoryEngine([R(["Work"], "Cursor"), R(["Work", "Coding"], "Cursor"), R(["A"], "Cursor"), R(["B"], "Cursor")])
    assert e.classify("Cursor") == ("Work", "Coding")
    e = ac.CategoryEngine([R(["A"], "x"), R(["B"], "x")])
    assert e.classify("x") == ("A",)


def test_folders_never_match_and_unmatched_is_uncategorized() -> None:
    e = ac.CategoryEngine([R(["Work"]), R(["Work", "Coding"], "Cursor")])
    assert e.classify("Notes") == ("Uncategorized",)
    assert e.classify("Work") == ("Uncategorized",)


def test_invalid_regex_is_skipped_without_raising() -> None:
    e = ac.CategoryEngine([R(["Bad"], "("), R(["Good"], "Foo")])
    assert e.classify("Foo") == ("Good",)
    assert e.classify("(") == ("Uncategorized",)
    assert ac.CategoryEngine("garbage").classify("x") == ("Uncategorized",)   # type: ignore[arg-type]


def test_host_suffix_match() -> None:
    e = ac.CategoryEngine([R(["Reference"], "zzzz", hosts=["github.com"])])
    assert e.classify("Safari", "t", "https://docs.github.com/en/x") == ("Reference",)
    assert e.classify("Safari", "t", "https://notgithub.com/") == ("Uncategorized",)


def test_rollup_sums_children_into_every_ancestor() -> None:
    out = ac.CategoryEngine.rollup({("Work", "Coding"): 100, ("Work", "Writing"): 50, ("Social", "Media"): 10})
    assert out["Work"] == 150 and out["Work/Coding"] == 100 and out["Social"] == 10 and out["Social/Media"] == 10
    assert ac.CategoryEngine.rollup({"Work/Coding": 5})["Work"] == 5


def test_score_inheritance() -> None:
    e = ac.CategoryEngine([R(["Work"], None, 1), R(["Work", "Coding"], "x", 2), R(["Work", "Admin"], "y"), R(["Z"], "z")])
    assert e.score_of(("Work", "Coding")) == 2
    assert e.score_of(("Work", "Admin")) == 1      # inherited
    assert e.score_of(("Z",)) == 0
    assert e.score_of("Work/Admin") == 1


def test_defaults_classify_sensibly() -> None:
    e = ac.CategoryEngine()
    assert e.classify("Cursor", "x.py") == ("Work", "Coding")
    assert e.classify("Safari", "Funny cats - YouTube", "https://www.youtube.com/watch?v=1") == ("Social", "Media")
    assert e.score_of(("Social", "Media")) == -2
    assert e.classify("Mail") == ("Comms",)
    assert e.classify("Safari", "q", "https://github.com/a/b") == ("Reference",)
    assert e.classify("Preview") == ("Uncategorized",)


def test_day_stats_cats_only_paths_and_numbers() -> None:
    t = _at(0, 9)
    ev = [_focus(t, "Cursor", 600, "secret-plan.py"),
          _focus(t + 600, "Safari", 300, "Funny - YouTube", "https://youtube.com/watch?v=abc123")]
    d = insights.day_stats_from_events(ev)
    cats = next(iter(d.values()))["cats"]
    assert cats["Work"] == 600 and cats["Work/Coding"] == 600 and cats["Social/Media"] == 300 and cats["Social"] == 300
    blob = json.dumps(cats)
    assert "secret-plan" not in blob and "youtube.com" not in blob and "abc123" not in blob
    assert all(isinstance(v, float) for v in cats.values())


def test_merge_max_merge_idempotent_and_monotonic() -> None:
    ds = insights.DayStats(Database(Path(tempfile.mkdtemp())))
    ds.merge("2026-01-01", {"cats": {"Work": 100.0, "Work/Coding": 100.0}})
    ds.merge("2026-01-01", {"cats": {"Work": 100.0, "Work/Coding": 100.0}})
    assert ds.get("2026-01-01")["cats"] == {"Work": 100.0, "Work/Coding": 100.0}
    ds.merge("2026-01-01", {"cats": {"Work": 40.0, "Comms": 7.0}})        # a smaller view never shrinks history
    c = ds.get("2026-01-01")["cats"]
    assert c["Work"] == 100.0 and c["Comms"] == 7.0 and c["Work/Coding"] == 100.0


def test_migration_adds_cats_column_to_an_old_table() -> None:
    db = Database(Path(tempfile.mkdtemp()))
    with db.tx() as c:
        c.execute("""CREATE TABLE activity_day_stats (day TEXT PRIMARY KEY, apps TEXT NOT NULL DEFAULT '{}',
          hosts TEXT NOT NULL DEFAULT '{}', hours TEXT NOT NULL DEFAULT '{}', typing TEXT NOT NULL DEFAULT '{}',
          switches INTEGER NOT NULL DEFAULT 0, keys INTEGER NOT NULL DEFAULT 0, clicks INTEGER NOT NULL DEFAULT 0,
          scrolls INTEGER NOT NULL DEFAULT 0, focus_seconds REAL NOT NULL DEFAULT 0, idle_seconds REAL NOT NULL DEFAULT 0,
          first_ts REAL NOT NULL DEFAULT 0, last_ts REAL NOT NULL DEFAULT 0, updated_at REAL NOT NULL DEFAULT 0)""")
        c.execute("INSERT INTO activity_day_stats(day) VALUES('2025-12-31')")
    ds = insights.DayStats(db)
    assert ds.get("2025-12-31")["cats"] == {}
    ds.merge("2026-01-02", {"cats": {"Work": 5.0}})
    assert ds.get("2026-01-02")["cats"] == {"Work": 5.0}


def _days(n: int, cats_for) -> list[dict]:
    out = []
    for i in range(n):
        cats, hours = cats_for(i)
        out.append({"day": f"2026-01-{10 - i:02d}", "apps": {}, "hosts": {}, "hours": hours, "typing": {},
                    "cats": cats, "focus_seconds": sum(v for k, v in cats.items() if "/" not in k),
                    "switches": 0, "keys": 0, "clicks": 0, "scrolls": 0, "idle_seconds": 0,
                    "first_ts": 0, "last_ts": 0})
    return out


def test_mine_category_share_needs_recurrence() -> None:
    work = {"Work": 14400.0, "Work/Coding": 14400.0, "Comms": 3600.0}
    many = _days(4, lambda i: (work, {"10": 18000.0}))
    pats = insights.mine(events=[], days=many, summaries=[], min_days=2)["patterns"]
    share = [p for p in pats if p["kind"] == "category_share"]
    assert share and "Work 80%" in share[0]["detail"] and "4.0h/day" in share[0]["detail"]
    one = insights.mine(events=[], days=_days(1, lambda i: (work, {})), summaries=[], min_days=2)["patterns"]
    assert not [p for p in one if p["kind"] == "category_share"]


def test_mine_distraction_drift_for_distracted_evenings() -> None:
    bad = {"Work": 1800.0, "Work/Coding": 1800.0, "Social": 5400.0, "Social/Media": 5400.0}
    days = _days(3, lambda i: (bad, {"21": 3600.0, "22": 3600.0, "10": 1800.0}))
    pats = insights.mine(events=[], days=days, summaries=[], min_days=2)["patterns"]
    drift = [p for p in pats if p["kind"] == "distraction_drift"]
    assert drift and drift[0]["evidence"]["band"] == [21, 22] and "21:00-23:00" in drift[0]["detail"]
    good = _days(3, lambda i: ({"Work": 5000.0, "Work/Coding": 5000.0, "Social": 100.0, "Social/Media": 100.0}, {}))
    assert not [p for p in insights.mine(events=[], days=good, summaries=[])["patterns"] if p["kind"] == "distraction_drift"]


def test_fallback_and_digest_cover_new_kinds() -> None:
    bad = {"Work": 1800.0, "Work/Coding": 1800.0, "Social": 5400.0, "Social/Media": 5400.0}
    days = _days(3, lambda i: (bad, {"21": 3600.0}))
    pat = insights.mine(events=[], days=days, summaries=[], min_days=2)
    fb = insights.fallback(pat, set())
    assert any(s["key"] == "sug-guard-drift" for s in fb["suggestions"])
    assert any("category" in h["key"] for h in fb["habits"])
    text = insights.digest(pat)
    assert "Time by category:" in text and "distraction_drift" in text


def test_validation_rejects_bad_regex_and_shapes() -> None:
    ok, msg, idx = ac.validate_rules([R(["A"], "ok"), R(["B"], "(")])
    assert not ok and idx == 1 and "regex" in msg
    assert not ac.validate_rules([{"name": [], "rule": {"type": "none"}}])[0]
    assert not ac.validate_rules([R(["A"], "x", 5)])[0]
    assert not ac.validate_rules([R(["A"]) for _ in range(101)])[0]
    assert ac.validate_rules(ac.DEFAULT_CATEGORIES)[0]
    m = _monitor()
    try:
        ac.save(m, [R(["A"], "(")])
        raise AssertionError("should have raised")
    except ValueError as e:
        assert e.args[1] == 0


def test_save_changes_future_classification_not_history() -> None:
    m = _monitor()
    assert ac.effective(m.config())["default"] is True
    t = _at(0, 9)
    ev = [_focus(t, "Preview", 600)]
    m.insights.days.merge(insights._day_of(t), insights.day_stats_from_events(ev, ac.engine_for(None))[insights._day_of(t)])
    ac.save(m, [R(["Reading"], "Preview", 1)])
    assert ac.effective(m.config())["default"] is False
    eng = ac.engine_for(m.config().get("categories"))
    assert eng.classify("Preview") == ("Reading",)
    stats = insights.day_stats_from_events(ev, eng)[insights._day_of(t)]
    m.insights.days.merge(insights._day_of(t), stats)
    cats = m.insights.days.get(insights._day_of(t))["cats"]
    assert cats["Reading"] == 600 and cats["Uncategorized"] == 600          # history persists
    ac.save(m, None)
    assert ac.effective(m.config())["default"] is True


def test_report_productivity_in_range_and_uncategorized_apps() -> None:
    m = _monitor()
    t = _at(0, 9)
    for ev in ([_focus(t, "Cursor", 600), _focus(t + 600, "Mystery App", 900),
                _focus(t + 1500, "Safari", 300, "x - YouTube", "https://youtube.com/w")]):
        m.store.add("focus", app=ev["app"], title=ev["title"], url=ev["url"], duration_ms=ev["duration_ms"],
                    ts=ev["ts"], retention_hours=48)
    rep = ac.report_for(m, 7)
    assert -2 <= rep["productivity"] <= 2
    assert rep["totals"]["Work/Coding"] == 600 and rep["totals"]["Social"] == 300
    assert rep["top_uncategorized_apps"][0] == {"app": "Mystery App", "seconds": 900}
    assert rep["days"][-1]["cats"]["Uncategorized"] == 900
    empty = ac.report_for(_monitor(), 7)
    assert empty["productivity"] is None and empty["days"] == []


def test_mine_now_uses_engine_without_a_model() -> None:
    m = _monitor()
    t = _at(0, 9)
    m.store.add("focus", app="Cursor", title="a.py", duration_ms=1200 * 1000, ts=t, retention_hours=48)
    pat = m.insights.mine_now()
    assert any(c["path"] == "Work/Coding" for c in pat["categories"])
    digest, _ = m._digest(m.store.recent(limit=50))
    assert "Time by category:" in digest and "Work" in digest


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
