"""Conditional exclusions: excludeRules and /regex/ entries in the plain exclusion lists."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_activity as ta  # noqa: E402
from personal_os import activity  # noqa: E402


def gate(**cfg):
    return activity.Gate(lambda: {**activity.DEFAULT_CONFIG, "excludeApps": [], "excludeTitlePatterns": [], **cfg})


def test_rules() -> None:
    g = gate(excludeRules=[{"app": "Safari", "title": "/bank|login/"}])
    assert g.excluded("Safari", "Bank login")
    assert not g.excluded("Safari", "News")
    assert not g.excluded("Chrome", "Bank login")
    assert gate(excludeRules=[{"app": "slack"}]).excluded("Slack", "x")
    assert gate(excludeRules=[{"url": "/\\.corp\\./"}]).excluded("A", "t", "https://x.corp.io")


def test_inert_and_invalid() -> None:
    assert not gate(excludeRules=[{}, "junk", {"app": ""}]).excluded("Safari", "x")
    assert not gate(excludeRules=[{"app": "Safari", "title": "/(/"}]).excluded("Safari", "x")
    assert not gate(excludeApps=["/(/"]).excluded("Safari", "x")


def test_regex_in_lists() -> None:
    g = gate(excludeApps=["/^sig/"], excludeTitlePatterns=["payroll", "/q[0-9] budget/"])
    assert g.excluded("Signal") and not g.excluded("Design")
    assert g.excluded("A", "Q3 Budget") and g.excluded("A", "Payroll run")


def test_record_everything_round_trip() -> None:
    rules = [{"app": "Safari", "title": "bank"}]
    m = ta._monitor(Path(tempfile.mkdtemp()))
    m.set_config({"excludeRules": rules})
    m.set_record_everything(True)
    assert m.config()["excludeRules"] == []
    m.set_record_everything(False)
    assert m.config()["excludeRules"] == rules


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f()
    print("ok")
