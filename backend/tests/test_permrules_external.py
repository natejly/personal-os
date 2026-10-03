"""Patterned allow/deny for mail recipients and calendars, and no whole-tool grant for external writes. Offline."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="permext-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from personal_os import app as appmod  # noqa: E402
from personal_os import permrules as pr  # noqa: E402

client = TestClient(appmod.app, headers={"X-Personal-OS-Token": appmod.AUTH_TOKEN})


def rs(allow=(), deny=()):
    return {"allow": list(allow), "deny": list(deny), "ask": []}


def res(args, mode="ask", forced=False, tool="gmail_send", **rules):
    return pr.resolve(tool, args, mode, forced, rules=rs(**rules))


def mail(to):
    return {"to": to, "subject": "s", "body": "b"}


def test_allow_one_recipient_leaves_another_at_ask():
    assert res(mail("a@x.com"), allow=["gmail_send(a@x.com)"]).mode == "on"
    assert res(mail("b@x.com"), allow=["gmail_send(a@x.com)"]).mode == "ask"
    assert res(mail("A@X.com"), allow=["gmail_send(a@x.com)"]).mode == "on"  # case-insensitive
    # every recipient needs an allow
    assert res(mail("a@x.com, Bo <b@x.com>"), allow=["gmail_send(a@x.com)"]).mode == "ask"
    assert res(mail("a@x.com, b@x.com"), allow=["gmail_send(*@x.com)"]).mode == "on"


def test_deny_beats_allow_on_and_skip():
    d = dict(allow=["gmail_send(*@x.com)"], deny=["gmail_send(bad@x.com)"])
    assert res(mail("ok@x.com, bad@x.com"), **d).refusal
    assert res(mail("bad@x.com"), mode="on", **d).refusal
    assert not res(mail("ok@x.com"), mode="on", **d).refusal
    # skip-permissions runs after this refusal and never reads it (app.py: `not perm.refusal`)
    assert res(mail("bad@x.com"), **d).refusal


def test_calendar_subject():
    c = {"summary": "x", "start": "2026-10-07T10:00", "calendar_id": "work@x.com"}
    assert res(c, tool="calendar_create", allow=["calendar_create(work@x.com)"]).mode == "on"
    assert res({"event_id": "e"}, tool="calendar_delete", allow=["calendar_delete(work@x.com)"]).mode == "ask"  # primary
    assert res(c, tool="calendar_create", deny=["calendar_create(work@x.com)"]).refusal
    p = {"changes": [{"op": "delete", "event_id": "e", "calendar_id": "work@x.com"}, {"op": "delete", "event_id": "f"}]}
    assert res(p, tool="calendar_propose", allow=["calendar_propose(work@x.com)"]).mode == "ask"
    assert res(p, tool="calendar_propose", allow=["calendar_propose(work@x.com)", "calendar_propose(primary)"]).mode == "on"


def test_validate_rejects_blanket_and_foreign_subject():
    a = mail("a@x.com")
    assert pr.validate_saved_rules("gmail_send", a, ["gmail_send(a@x.com)"]) == ["gmail_send(a@x.com)"]
    assert pr.validate_saved_rules("gmail_send", a, ["gmail_send(*@x.com)"])
    for bad in ("gmail_send(*)", "gmail_send(b@x.com)", "gmail_send"):
        with pytest.raises(ValueError):
            pr.validate_saved_rules("gmail_send", a, [bad])


def test_evaluate_route_and_always_rule():
    client.put("/settings", json={"permissionRules": rs(deny=["gmail_send(bad@x.com)"])})
    v = client.post("/permissions/evaluate", json={"tool": "gmail_send", "args": mail("bad@x.com")}).json()
    assert v["action"] == "deny"
    v = client.post("/permissions/evaluate", json={"tool": "gmail_send", "args": mail("a@x.com")}).json()
    assert v["suggestions"] == ["gmail_send(a@x.com)"]

    store = appmod.run_store
    store.open_approval("c1", None, "gmail_send", mail("a@x.com"), danger="external")
    assert client.post("/approvals/c1", json={"decision": "always_rule", "rules": ["gmail_send(*)"]}).status_code == 400
    assert client.post("/approvals/c1", json={"decision": "always_rule", "rules": ["gmail_send(b@x.com)"]}).status_code == 400
    client.post("/approvals/c1", json={"decision": "always_rule", "rules": ["gmail_send(a@x.com)"]})
    saved = appmod.settings()["permissionRules"]
    assert "gmail_send(a@x.com)" in saved["allow"]
    assert pr.resolve("gmail_send", mail("a@x.com"), "ask", False, rules=saved).mode == "on"
    assert pr.resolve("gmail_send", mail("b@x.com"), "ask", False, rules=saved).mode == "ask"

    # a forced card writes no standing rule
    store.open_approval("c2", None, "gmail_send", mail("z@x.com"), forced=True, danger="external")
    client.post("/approvals/c2", json={"decision": "always_rule", "rules": ["gmail_send(z@x.com)"]})
    assert "gmail_send(z@x.com)" not in appmod.settings()["permissionRules"]["allow"]


def test_card_offers_recipient_rule():
    r = pr.resolve("gmail_send", mail("a@x.com"), "ask", False, rules=None)
    assert r.card()["subject"] == "gmail_send(a@x.com)" and r.card()["suggestions"] == ["gmail_send(a@x.com)"]
