"""Tool annotations: stored, shown, and never allowed to make a third-party tool less careful.

Run: cd backend && PYTHONPATH=. .venv/bin/python -m pytest personal_os/tests/test_mcp_annotations.py -q
"""
from __future__ import annotations

import os
import tempfile
from typing import Any

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="mcpann-"))
os.environ.setdefault("PERSONAL_OS_AUTH_TOKEN", "test-token")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from personal_os import app as appmod  # noqa: E402
from personal_os import mcp_servers  # noqa: E402
from personal_os.db import Database  # noqa: E402
from personal_os.mcp_servers import McpServers  # noqa: E402

PARAMS: dict[str, Any] = {"type": "object", "properties": {"q": {"type": "string"}}}


def spec(name: str, **annotations: Any) -> dict[str, Any]:
    return {"name": name, "description": f"{name} things.", "parameters": PARAMS, "annotations": annotations}


@pytest.fixture
def store(tmp_path: Any) -> McpServers:
    return McpServers(Database(tmp_path))


def test_hints_semantics() -> None:
    assert mcp_servers.hints({"readOnlyHint": True}) == {"read_only": True, "destructive": False}
    assert mcp_servers.hints({"destructiveHint": True}) == {"read_only": False, "destructive": True}
    both = mcp_servers.hints({"readOnlyHint": True, "destructiveHint": True})
    assert both == {"read_only": True, "destructive": False}, "destructive is false when readOnlyHint is also true"
    assert mcp_servers.hints({"destructiveHint": False}) == {"read_only": False, "destructive": False}
    assert mcp_servers.hints({"read_only_hint": True})["read_only"], "the SDK's snake_case spelling is understood"
    assert mcp_servers.hints(None) == {"read_only": False, "destructive": False}
    assert mcp_servers.hints({"destructiveHint": "yes"})["destructive"] is False, "only a real boolean counts"


def test_clean_annotations_keeps_only_known_hints() -> None:
    out = mcp_servers.clean_annotations({"readOnlyHint": True, "title": "  Look  ", "blob": "x" * 10_000, "openWorldHint": 1})
    assert out == {"readOnlyHint": True, "title": "Look"}


def test_annotations_are_stored_and_exposed_on_every_tool_dict(store: McpServers) -> None:
    sid = store.create_server("Records", command="x")["id"]
    store.sync_tools(sid, [spec("look", readOnlyHint=True), spec("wipe", destructiveHint=True), spec("plain")])
    look, wipe, plain = (store.tool(f"mcp__records__{n}") for n in ("look", "wipe", "plain"))
    assert look["annotations"] == {"readOnlyHint": True} and look["read_only"] and not look["destructive"]
    assert wipe["destructive"] and not wipe["read_only"]
    assert plain["annotations"] == {} and not plain["read_only"] and not plain["destructive"]
    assert [t["slug"] for t in store.tools() if t["destructive"]] == ["mcp__records__wipe"]


def test_changing_annotations_does_not_change_the_schema_hash_or_decay_a_grant(store: McpServers) -> None:
    sid = store.create_server("Records", command="x")["id"]
    store.sync_tools(sid, [spec("look", readOnlyHint=True)])
    slug = "mcp__records__look"
    h = store.tool(slug)["schema_hash"]
    store.set_grant(slug, "on")
    out = store.sync_tools(sid, [spec("look", destructiveHint=True)])
    assert out["unchanged"] == [slug] and out["changed"] == []
    t = store.tool(slug)
    assert t["schema_hash"] == h and t["destructive"], "the new annotations are stored without touching the hash"
    assert store.effective_mode(slug)["mode"] == "on" and not store.effective_mode(slug)["stale"]


def test_a_read_only_tool_still_defaults_to_ask(store: McpServers) -> None:
    sid = store.create_server("Records", command="x")["id"]
    store.sync_tools(sid, [spec("look", readOnlyHint=True)])
    assert store.effective_mode("mcp__records__look")["mode"] == "ask"
    assert store.tool("mcp__records__look")["danger"] == "external", "a hint never lowers the danger level"


def test_an_old_database_gains_the_new_columns(tmp_path: Any) -> None:
    db = Database(tmp_path)
    with db.tx() as c:
        c.executescript("""
        CREATE TABLE mcp_servers (id TEXT PRIMARY KEY, slug TEXT NOT NULL, name TEXT NOT NULL, transport TEXT NOT NULL DEFAULT 'stdio',
          command TEXT NOT NULL DEFAULT '', args TEXT NOT NULL DEFAULT '[]', cwd TEXT NOT NULL DEFAULT '', env TEXT NOT NULL DEFAULT '{}',
          secrets TEXT NOT NULL DEFAULT '{}', url TEXT NOT NULL DEFAULT '', headers TEXT NOT NULL DEFAULT '{}', description TEXT NOT NULL DEFAULT '',
          enabled INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL DEFAULT 'idle', status_detail TEXT NOT NULL DEFAULT '',
          last_connected_at REAL, created_at REAL NOT NULL, updated_at REAL NOT NULL);
        CREATE TABLE mcp_tools (id TEXT PRIMARY KEY, server_id TEXT NOT NULL, name TEXT NOT NULL, slug TEXT NOT NULL,
          description TEXT NOT NULL DEFAULT '', parameters TEXT NOT NULL DEFAULT '{}', schema_hash TEXT NOT NULL,
          danger TEXT NOT NULL DEFAULT 'external', first_seen_at REAL NOT NULL, last_seen_at REAL NOT NULL,
          schema_changed_at REAL, missing_since REAL, quarantined_at REAL, reviewed_hash TEXT NOT NULL DEFAULT '', UNIQUE(server_id, name));
        INSERT INTO mcp_servers(id,slug,name,created_at,updated_at) VALUES('s1','old','Old',1,1);
        INSERT INTO mcp_tools(id,server_id,name,slug,schema_hash,first_seen_at,last_seen_at) VALUES('t1','s1','t','mcp__old__t','h',1,1);
        """)
    store = McpServers(db)
    assert store.server("s1")["catalog_id"] == ""
    assert store.tool("mcp__old__t")["annotations"] == {} and store.tool("mcp__old__t")["destructive"] is False


# ---------- the app wiring ----------
@pytest.fixture
def api() -> Any:
    for row in appmod.mcp_store.servers():
        appmod.mcp_store.delete_server(row["id"])
    for g in appmod.mcp_store.grants():
        appmod.mcp_store.clear_grant(g["tool_slug"], g["scope"], g["scope_id"])
    server = appmod.mcp_store.create_server("Records", command="/bin/true")
    appmod.mcp_store.sync_tools(server["id"], [spec("look", readOnlyHint=True), spec("wipe", destructiveHint=True),
                                               spec("both", readOnlyHint=True, destructiveHint=True)])
    return TestClient(appmod.app, headers={"X-Personal-OS-Token": appmod.AUTH_TOKEN})


def test_a_destructive_tool_needs_a_confirmed_grant(api: TestClient) -> None:
    r = api.put("/mcp/tools/mcp__records__wipe/grant", json={"mode": "on"})
    assert r.status_code == 409 and r.json()["detail"] == "destructive: confirm required"
    assert appmod.mcp_store.effective_mode("mcp__records__wipe")["mode"] == "ask", "a refused grant changes nothing"
    assert api.put("/mcp/tools/mcp__records__wipe/grant", json={"mode": "ask"}).status_code == 200  # ask and off need no confirm
    assert api.put("/mcp/tools/mcp__records__wipe/grant", json={"mode": "off"}).status_code == 200
    ok = api.put("/mcp/tools/mcp__records__wipe/grant", json={"mode": "on", "confirm": True})
    assert ok.status_code == 200 and ok.json()["mode"] == "on"


def test_other_tools_grant_without_confirm(api: TestClient) -> None:
    for name in ("look", "both"):
        assert api.put(f"/mcp/tools/mcp__records__{name}/grant", json={"mode": "on"}).status_code == 200


def test_tool_lists_carry_the_hints(api: TestClient) -> None:
    tools = {t["name"]: t for t in api.get("/mcp/tools").json()["tools"]}
    assert tools["look"]["read_only"] and not tools["look"]["destructive"] and tools["look"]["annotations"] == {"readOnlyHint": True}
    assert tools["wipe"]["destructive"] and not tools["both"]["destructive"]
    sid = appmod.mcp_store.servers()[0]["id"]
    view = api.get("/mcp/servers").json()[0]
    assert view["id"] == sid and view["catalog_id"] == "" and {t["name"]: t["destructive"] for t in view["tools"]}["wipe"]
    assert view["live"]["resources"] == [] and view["live"]["prompts"] == []


def test_a_confirmed_grant_still_asks_when_the_run_is_tainted(api: TestClient) -> None:
    api.put("/mcp/tools/mcp__records__wipe/grant", json={"mode": "on", "confirm": True})
    assert appmod._gate("mcp__records__wipe", "on", {"tainted": False}) == "on"
    assert appmod._gate("mcp__records__wipe", "on", {"tainted": True}) == "ask"


def test_the_card_event_names_the_server_and_its_claims(api: TestClient) -> None:
    assert appmod._mcp_event("mcp__records__wipe") == {"server": "Records", "read_only": False, "destructive": True}
    assert appmod._mcp_event("mcp__records__look") == {"server": "Records", "read_only": True, "destructive": False}
    assert appmod._mcp_event("current_time") is None and appmod._mcp_event("mcp__nope__x") is None


def test_the_auto_reviewer_is_told_the_hints_are_unverified(api: TestClient) -> None:
    text = appmod._mcp_review_text("mcp__records__look")
    assert text.startswith("look things.") and "Server-declared hints (self-reported, unverified): read-only" in text
    assert "destructive" in appmod._mcp_review_text("mcp__records__wipe")
    assert "none declared" in mcp_servers.review_text({"description": "x", "annotations": {}})
    assert len(mcp_servers.review_text({"description": "d" * 5000, "annotations": {"destructiveHint": True}})) <= 600
    from personal_os import autoreview
    assert "self-reported and unverified" in autoreview.PROMPT
