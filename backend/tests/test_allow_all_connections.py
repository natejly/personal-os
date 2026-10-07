"""allowAllConnections: every host/connector restriction lifts, the SSRF and taint rules stay.

No network, no real server. Run: timeout 300 uv run pytest tests/test_allow_all_connections.py -x -q
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from pathlib import Path

os.environ["GRAIN_SECRETS_BACKEND"] = "file"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from personal_os import egress, mcp_drift, permissions, tools  # noqa: E402
from personal_os.db import Database  # noqa: E402
from personal_os.mcp_servers import McpServers  # noqa: E402

TAINTED = {"tainted": True, "allowed_urls": set()}
ON = {"allowAllConnections": True}


def test_validate_bool_only() -> None:
    assert permissions.DEFAULTS["allowAllConnections"] is False
    assert permissions.validate("allowAllConnections", True) is True
    with pytest.raises(ValueError):
        permissions.validate("allowAllConnections", "yes")


def test_fetch_taint_rule_lifts_but_ssrf_stays() -> None:
    with pytest.raises(tools.UrlBlocked):
        tools._check_url("https://example.com/a?x=1", TAINTED, {})
    assert tools._check_url("https://example.com/a?x=1", TAINTED, ON)[1] == "example.com"
    for private in ("http://127.0.0.1/", "http://169.254.169.254/latest", "http://10.0.0.5/"):
        with pytest.raises(tools.UrlBlocked):
            tools._check_url(private, TAINTED, ON)


def test_egress_wildcard() -> None:
    assert egress.allowed_set(True, ["example.com"]) != ("*",)
    assert egress.allowed_set(False, [], True) == ("*",)
    star = egress.allowed_set(True, [], True)
    assert egress.host_allowed("anything.example.org", star)
    assert not egress.host_allowed("93.184.216.34", star)  # an IP literal never passes
    assert not egress.host_allowed("93.184.216.34", ("example.com",))
    assert egress.addr_ok("93.184.216.34") and not egress.addr_ok("127.0.0.1")


def _store(allow_all: bool = False) -> tuple[McpServers, str]:
    store = McpServers(Database(Path(tempfile.mkdtemp(prefix="allowall-"))), lambda: allow_all)
    sid = store.create_server("alpha", command="x")["id"]
    store.sync_tools(sid, [{"name": "send", "description": "Send.", "parameters": {"type": "object", "properties": {}}}])
    return store, "mcp__alpha__send"


def test_mcp_default_on_explicit_off_wins_no_decay() -> None:
    store, slug = _store()
    assert store.effective_mode(slug)["mode"] == "ask"
    on = {"v": True}
    store.allow_all = lambda: on["v"]
    got = store.effective_mode(slug)
    assert (got["mode"], got["source"]) == ("on", "allow_all")
    store.set_grant(slug, "off", "global", "")
    assert store.effective_mode(slug)["mode"] == "off"
    store.set_grant(slug, "on", "global", "")
    with store.db.tx() as c:  # approved shape differs from the shape on offer: stale
        c.execute("UPDATE mcp_grants SET schema_hash='old' WHERE tool_slug=?", (slug,))
    assert store.effective_mode(slug)["mode"] == "on"
    on["v"] = False
    assert store.effective_mode(slug)["mode"] == "ask"  # the decay is back


def test_offerable_keeps_quarantined_only_when_on() -> None:
    rows = [{"slug": "a"}, {"slug": "b", "quarantined_at": 1.0}]
    assert [t["slug"] for t in mcp_drift.offerable(rows)] == ["a"]
    assert [t["slug"] for t in mcp_drift.offerable(rows, True)] == ["a", "b"]


def _stamp_24(path: Path) -> None:
    c = sqlite3.connect(path / "personal-os.db")
    c.execute("PRAGMA user_version = 24")
    c.commit()
    c.close()


def test_migration_fresh_stays_off_existing_turns_on() -> None:
    d = Path(tempfile.mkdtemp(prefix="allowall-mig-"))
    db = Database(d)
    assert permissions.get(permissions.load(db.get_settings()), "allowAllConnections") is False
    with db.tx() as c:
        c.execute("INSERT INTO conversations(id,title,model,created_at,updated_at) VALUES('c','t','m',1,1)")
    _stamp_24(d)
    assert permissions.get(permissions.load(Database(d).get_settings()), "allowAllConnections") is True
    # An empty database stamped at 24 (a fresh one) is not switched on.
    e = Path(tempfile.mkdtemp(prefix="allowall-mig-"))
    Database(e)
    _stamp_24(e)
    assert permissions.get(permissions.load(Database(e).get_settings()), "allowAllConnections") is False
