"""The backend API object of an extension (design doc 3.4): data_dir, settings, secrets, the tables-only db, slash commands,
interval jobs, notify and inbox, trash handlers; and the hook bus guards of 5.0 and 7.4: HookCtx and the via_hook depth,
the cap on the text a handler adds, the note raised when a handler is muted.

Run: python -m pytest backend/tests/test_extension_api.py
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="extapi-"))
os.environ.setdefault("PERSONAL_OS_AUTH_TOKEN", "test-token")
os.environ.setdefault("GRAIN_SECRETS_BACKEND", "file")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from personal_os import commands, trash as trash_mod  # noqa: E402
from personal_os.context import Block  # noqa: E402
from personal_os.db import Database  # noqa: E402
from personal_os.dispatch import dispatch_tool  # noqa: E402
from personal_os.docs import Docs  # noqa: E402
from personal_os.extensions import host as host_mod  # noqa: E402
from personal_os.extensions.hooks import MAX_ADDED_TEXT, Ask, Deny, HookBus, HookCtx, ToolCallRefused  # noqa: E402
from personal_os.extensions.host import ExtensionContext, ExtensionHost  # noqa: E402
from personal_os.extensions.manifest import Manifest, ManifestError  # noqa: E402
from personal_os.extensions.notes import ExtNotes, InboxItem  # noqa: E402
from personal_os.extensions.trashkinds import TrashHandler  # noqa: E402
from personal_os.todos import Todos  # noqa: E402


def run(coro: Any) -> Any:
    return asyncio.run(coro)


@pytest.fixture
def work() -> Any:
    d = Path(tempfile.mkdtemp(prefix="extapi-work-"))
    yield d
    shutil.rmtree(d, ignore_errors=True)


class Rig:
    """A host with one folder extension loaded, and the app context it was lent."""

    def __init__(self, work: Path, manifest: dict[str, Any] | None = None, code: str = "def activate(api):\n    pass\n") -> None:
        self.root = work / "demo"
        self.root.mkdir()
        (self.root / "extension.json").write_text(json.dumps({"id": "demo", "name": "Demo", "version": "1.0.0", "apiVersion": 1,
                                                              "permissions": ["db:own"], **(manifest or {})}), "utf-8")
        (self.root / "extension.py").write_text(code, "utf-8")
        self.events: list[tuple[str, Any]] = []
        self.values: dict[str, Any] = {}
        self.hidden: list[str] = []
        self.ctx = ExtensionContext(db=Database(Path(tempfile.mkdtemp(prefix="extapi-db-", dir=work))), hidden_views=lambda: self.hidden,
                                    services={}, settings=lambda: self.values, set_settings=lambda v: None, sid=lambda p: p, wsid=lambda p: p,
                                    publish=lambda name, data: self.events.append((name, data)))
        self.host = ExtensionHost()
        self.scope = self.host.load_folder(self.root, self.ctx)


# ---- data_dir, settings, secrets ----
def test_data_dir_is_the_extensions_own_folder_beside_the_code(work: Path) -> None:
    rig = Rig(work)
    assert rig.scope.data_dir == rig.ctx.db.data_dir / "ext" / "demo" and rig.scope.data_dir.is_dir()
    assert rig.root not in rig.scope.data_dir.parents


def test_settings_are_read_by_declared_name_and_a_secret_field_comes_from_the_store(work: Path) -> None:
    rig = Rig(work, {"settings": {"limit": {"type": "integer"}, "token": {"type": "secret"}}})
    settings = rig.scope.settings
    assert settings.get("limit") is None
    rig.values["ext.demo.limit"] = 3
    assert settings.get("limit") == 3
    with pytest.raises(KeyError, match="not declared"):
        settings.get("other")
    rig.scope.secrets.set("token", "s3cret")
    assert settings.get("token") == "s3cret" and rig.ctx.db.secrets.get("ext.demo.token") == "s3cret"
    rig.scope.secrets.delete("token")
    assert settings.get("token") is None


def test_an_unset_setting_reads_as_its_declared_default(work: Path) -> None:
    rig = Rig(work, {"settings": {"limit": {"type": "integer", "default": 7}}})
    assert rig.scope.settings.get("limit") == 7
    rig.values["ext.demo.limit"] = 3
    assert rig.scope.settings.get("limit") == 3


def test_on_change_hears_only_its_own_settings_and_not_while_disabled(work: Path) -> None:
    rig = Rig(work, {"settings": {"limit": {"type": "integer"}}})
    heard: list[str] = []
    rig.scope.settings.on_change(heard.append)
    changed = {"keys": ["theme", "ext.demo.limit", "ext.other.limit"], "old": {}, "new": {}}
    run(rig.host.bus.emit("settings.changed", changed))
    assert heard == ["limit"]
    rig.hidden.append("demo")
    run(rig.host.bus.emit("settings.changed", changed))
    assert heard == ["limit"]


# ---- the tables-only db ----
def test_the_db_of_a_folder_extension_reaches_only_its_tables(work: Path) -> None:
    rig = Rig(work, {"data": {"tables": ["demo_notes"]}})
    with rig.ctx.db.tx() as c:
        c.execute("CREATE TABLE demo_notes (id INTEGER PRIMARY KEY, text TEXT)")
        c.execute("CREATE TABLE demo_other (id INTEGER PRIMARY KEY)")
    with rig.scope.db.tx() as c:
        c.execute("INSERT INTO demo_notes (text) VALUES ('kept')")
    with rig.scope.db.tx() as c:
        assert c.execute("SELECT COUNT(*), MAX(text) FROM demo_notes").fetchone()[:] == (1, "kept")
    for sql in ("SELECT * FROM demo_other", "SELECT * FROM settings", "SELECT * FROM demo_notes JOIN demo_other",
                "SELECT name FROM sqlite_master", "INSERT INTO demo_other DEFAULT VALUES", "DROP TABLE demo_notes",
                "CREATE TABLE demo_new (id INTEGER)", "PRAGMA table_info(demo_notes)", "ATTACH DATABASE ':memory:' AS x"):
        with pytest.raises(sqlite3.DatabaseError, match="prohibited|not authorized"), rig.scope.db.tx() as c:
            c.execute(sql)


def test_a_failed_block_rolls_back_the_scoped_db(work: Path) -> None:
    rig = Rig(work, {"data": {"tables": ["demo_notes"]}})
    with rig.ctx.db.tx() as c:
        c.execute("CREATE TABLE demo_notes (id INTEGER PRIMARY KEY, text TEXT)")
    with pytest.raises(RuntimeError), rig.scope.db.tx() as c:
        c.execute("INSERT INTO demo_notes (text) VALUES ('lost')")
        raise RuntimeError("boom")
    with rig.scope.db.tx() as c:
        assert c.execute("SELECT COUNT(*) FROM demo_notes").fetchone()[0] == 0


# ---- slash commands ----
CODE_COMMAND = "def activate(api):\n    api.command('ping', lambda args: f'pong {args}')\n"


def test_a_registered_command_lays_its_text_under_the_turn_and_follows_the_enabled_state(work: Path) -> None:
    rig = Rig(work, {"contributes": {"commands": [{"name": "ping", "hint": "say pong"}]}}, CODE_COMMAND)
    assert rig.host.slash.expand("ping", "a b") == "pong a b"
    assert rig.host.slash.listing() == [{"name": "ping", "hint": "say pong", "owner": "demo"}]
    expanded = commands.expand("/ping a b", None, registered=rig.host.slash.expand)
    assert expanded == "/ping a b\n\npong a b"
    assert commands.expand("/ping a b", None) == "/ping a b"
    rig.hidden.append("demo")
    assert rig.host.slash.expand("ping", "a b") is None and rig.host.slash.listing() == []


def test_a_command_the_manifest_does_not_list_is_refused_and_so_is_an_app_name(work: Path) -> None:
    rig = Rig(work)
    with pytest.raises(ValueError, match="did not declare command"):
        rig.scope.command("ping", lambda args: "x")
    for name in ("research", "Bad Name"):
        with pytest.raises(ManifestError, match="contributes.commands"):
            Manifest.from_dict({"id": "x", "name": "X", "version": "1.0.0", "apiVersion": 1, "contributes": {"commands": [{"name": name}]}})


def test_a_failing_command_leaves_the_turn_as_typed(work: Path) -> None:
    rig = Rig(work, {"contributes": {"commands": [{"name": "ping"}]}})
    rig.scope.command("ping", lambda args: 1 / 0)
    assert rig.host.slash.expand("ping", "") is None


# ---- interval jobs ----
def test_a_job_runs_every_interval_while_started_and_records_its_failure(work: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(host_mod, "MIN_JOB_INTERVAL_S", 0.0)
    rig = Rig(work)
    calls: list[int] = []

    def tick() -> None:
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("second one fails")

    async def go() -> None:
        rig.scope.job("tick", timedelta(milliseconds=20), tick)
        await rig.scope.start()
        await asyncio.sleep(0.3)
        assert len(calls) >= 3
        await rig.scope.stop()
        after = len(calls)
        await asyncio.sleep(0.1)
        assert len(calls) == after
    run(go())
    assert rig.scope.jobs == []  # stopping the extension disposed the registration


def test_a_job_failure_is_kept_for_the_dev_panel(work: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(host_mod, "MIN_JOB_INTERVAL_S", 0.0)
    rig = Rig(work)

    async def go() -> Any:
        rig.scope.job("bad", timedelta(milliseconds=10), lambda: 1 / 0)
        await rig.scope.start()
        await asyncio.sleep(0.1)
        job = rig.scope.jobs[0]
        await rig.scope.stop()
        return job
    job = run(go())
    assert job.runs >= 1 and job.last_error == "ZeroDivisionError: division by zero"


def test_a_job_is_not_shorter_than_the_minimum_and_not_named_twice(work: Path) -> None:
    rig = Rig(work)
    with pytest.raises(ValueError, match="shortest interval"):
        rig.scope.job("fast", timedelta(seconds=5), lambda: None)
    rig.scope.job("slow", timedelta(minutes=5), lambda: None)
    with pytest.raises(ValueError, match="already has a job"):
        rig.scope.job("slow", timedelta(minutes=5), lambda: None)


# ---- notify and inbox ----
def test_inbox_leaves_a_card_until_dismissed_and_notify_only_an_event(work: Path) -> None:
    rig = Rig(work)
    rig.scope.inbox(InboxItem("Backup stale", "Last ran 9 days ago", "inbox"))
    rig.scope.notify("Done", "All synced", target="run:c1")
    notes = ExtNotes(rig.ctx.db)
    cards = notes.open()
    assert [(c["ext_id"], c["title"], c["body"]) for c in cards] == [("demo", "Backup stale", "Last ran 9 days ago")]
    events = notes.notifications(0)
    assert [(e["title"], e["target"], e["kind"]) for e in events] == [("Done", "run:c1", "ext_note")]
    assert notes.notifications(time.time() + 1) == []
    assert [name for name, _ in rig.events] == ["inbox_note", "inbox_note"]
    assert notes.dismiss(cards[0]["id"]) and notes.open() == [] and not notes.dismiss(cards[0]["id"])


def test_notify_fires_the_notify_hook(work: Path) -> None:
    rig = Rig(work)
    got: list[Any] = []
    rig.host.bus.subscribe("notify", got.append, owner="x", order=0)

    async def go() -> None:
        rig.host.bus.loop = asyncio.get_running_loop()
        rig.scope.notify("Done", "All synced", target="run:c1")
        rig.scope.inbox(InboxItem("Stale", "9 days"))
        await asyncio.sleep(0.1)
    run(go())
    assert got == [{"title": "Done", "body": "All synced", "target": "run:c1"}], "a card is not a notification"


def test_a_note_is_trimmed(work: Path) -> None:
    rig = Rig(work)
    rig.scope.inbox(InboxItem("t" * 500, "b" * 5000))
    card = ExtNotes(rig.ctx.db).open()[0]
    assert len(card["title"]) == 120 and len(card["body"]) == 500


CODE_BOOM = "def activate(api):\n    def boom(payload):\n        raise RuntimeError('secret detail')\n    api.on('chat.created', boom)\n"


def test_a_muted_handler_raises_one_inbox_note_naming_the_hook_but_not_the_error_text(work: Path) -> None:
    rig = Rig(work, {"hooks": {"chat.created": {}}}, CODE_BOOM)
    for _ in range(8):
        run(rig.host.bus.emit("chat.created", {"id": "c1", "project_id": None}))
    cards = ExtNotes(rig.ctx.db).open()
    assert len(cards) == 1
    assert "Demo" in cards[0]["title"] and "chat.created" in cards[0]["body"] and "RuntimeError" in cards[0]["body"]
    assert "secret detail" not in cards[0]["body"]


# ---- trash handlers ----
def test_a_trash_handler_lists_restores_and_erases_its_own_kind(work: Path) -> None:
    rig = Rig(work)
    rows = {"a": {"id": "a", "title": "Old note", "deleted_at": time.time()},
            "b": {"id": "b", "title": "Ancient note", "deleted_at": time.time() - 40 * trash_mod.DAY}}
    rig.scope.trash_handler("demo:note", TrashHandler("Demo notes", lambda: list(rows.values()),
                                                      lambda i: rows.pop(i, None) is not None, lambda i: rows.pop(i, None) is not None))
    db = rig.ctx.db
    trash = trash_mod.Trash(db, Todos(db), Docs(db), HookBus(), rig.host.trash_kinds)
    listing = trash.list()
    assert listing["total"] == 2 and listing["extensions"][0]["label"] == "Demo notes"
    assert [i["type"] for i in listing["extensions"][0]["items"]] == ["demo:note"] * 2
    assert trash.purge_old() == 1 and list(rows) == ["a"]
    assert trash.restore("demo:note", "a")["ok"] and trash.restore("demo:note", "a") is None
    rows["c"] = {"id": "c", "title": "", "deleted_at": time.time()}
    assert trash.list()["extensions"][0]["items"][0]["title"] == "Untitled"
    assert trash.empty() == 1 and trash.list()["total"] == 0
    rows["d"] = {"id": "d", "title": "x", "deleted_at": time.time()}
    rig.hidden.append("demo")
    assert trash.list()["total"] == 0  # a disabled extension's trash is left alone


def test_a_trash_kind_is_named_after_its_extension_and_a_failing_handler_hides_only_itself(work: Path) -> None:
    rig = Rig(work)
    handler = TrashHandler("Demo", lambda: 1 / 0, lambda i: False, lambda i: False)  # type: ignore[arg-type, return-value]
    with pytest.raises(ValueError, match="must be named demo:<name>"):
        rig.scope.trash_handler("note", handler)
    rig.scope.trash_handler("demo:note", handler)
    db = rig.ctx.db
    trash = trash_mod.Trash(db, Todos(db), Docs(db), HookBus(), rig.host.trash_kinds)
    assert trash.list()["extensions"] == []


# ---- HookCtx and the nesting of hook-made tool calls ----
def sub(bus: HookBus, hook: str, fn: Any, owner: str = "x", priority: int = 50, **kw: Any) -> None:
    bus.subscribe(hook, fn, owner=owner, order=0, priority=priority, **kw)


TOOL_CTX = {"project_id": "p", "conversation_id": "c", "run_id": "r", "agent_id": "a", "tainted": True, "taint_sources": []}


def fake_toolbox(**specs: str) -> SimpleNamespace:
    async def call(name: str, args: dict[str, Any], ctx: dict[str, Any]) -> Any:
        return {"ran": name}
    kinds = {"t": "safe", **specs}
    return SimpleNamespace(bus=HookBus(), call=call, specs={n: SimpleNamespace(danger=d, taints=False) for n, d in kinds.items()},
                           settings=dict, proposes=lambda n: False, floor_action=lambda *a, **k: None, floor_kind=lambda *a, **k: None)


def dispatch(box: Any, name: str = "t", ctx: dict[str, Any] | None = None) -> Any:
    return run(dispatch_tool(box, dict(TOOL_CTX) if ctx is None else ctx, name, {"a": 1}, call_id="id1"))


def test_a_handler_that_asks_for_ctx_gets_the_turns_ids_and_one_that_does_not_is_unchanged() -> None:
    box = fake_toolbox()
    stop = asyncio.Event()
    seen: list[HookCtx] = []
    plain: list[str] = []

    def with_ctx(ev: dict[str, Any], ctx: HookCtx) -> None:
        seen.append(ctx)

    sub(box.bus, "tool.pre", with_ctx)
    sub(box.bus, "tool.pre", lambda ev: plain.append(ev["name"]))
    dispatch(box, ctx={**TOOL_CTX, "stop": stop})
    (ctx,) = seen
    assert (ctx.run_id, ctx.conversation_id, ctx.project_id, ctx.agent_id, ctx.tainted) == ("r", "c", "p", "a", True)
    assert ctx.stop is stop and ctx.via_hook == 1 and plain == ["t"]


def test_a_hook_made_call_skips_the_handlers_it_is_nested_in_and_sees_the_others() -> None:
    box = fake_toolbox()
    own: list[str] = []
    other: list[int] = []
    results: list[Any] = []

    async def first(ev: dict[str, Any], ctx: HookCtx) -> None:
        own.append(ev["call_id"])
        if len(own) == 1:
            results.append(await ctx.call_tool("t", {}))

    sub(box.bus, "tool.pre", first, owner="a", priority=10)
    sub(box.bus, "tool.pre", lambda ev, ctx: other.append(ctx.via_hook), owner="b", priority=20)
    dispatch(box)
    assert results == [{"ran": "t"}]
    assert len(own) == 1  # the nested call did not re-enter the handler that made it
    assert other == [2, 1]  # but the other extension's handler saw it, one level deeper


def test_hook_made_calls_are_refused_past_two_levels() -> None:
    box = fake_toolbox()
    outcomes: set[tuple[int, str]] = set()

    def level(owner: str, priority: int) -> None:
        async def handler(ev: dict[str, Any], ctx: HookCtx) -> None:
            try:
                await ctx.call_tool("t", {})
                outcomes.add((ctx.via_hook, "called"))
            except ToolCallRefused:
                outcomes.add((ctx.via_hook, "refused"))
        sub(box.bus, "tool.pre", handler, owner=owner, priority=priority)

    for i, owner in enumerate("abc"):
        level(owner, 10 * (i + 1))
    dispatch(box)
    assert outcomes == {(1, "called"), (2, "called"), (3, "refused")}  # three handlers deep, the third is turned away


def test_a_hook_can_call_only_read_only_built_in_tools() -> None:
    box = fake_toolbox(w="writes")
    box.specs["s"] = SimpleNamespace(danger="safe", taints=True)
    got: dict[str, Any] = {}

    async def handler(ev: dict[str, Any], ctx: HookCtx) -> None:
        if ev["name"] == "t":
            for name in ("w", "s", "mcp__x__y"):
                got[name] = await ctx.call_tool(name, {})

    sub(box.bus, "tool.pre", handler)
    dispatch(box)
    assert all("cannot be called from a hook" in v["error"] for v in got.values()) and set(got) == {"w", "s", "mcp__x__y"}


def test_a_hook_without_tool_access_cannot_call_one() -> None:
    with pytest.raises(ToolCallRefused, match="cannot call tools"):
        run(HookCtx().call_tool("t", {}))


# ---- the cap on the text a handler adds ----
BIG = "x" * (2 * MAX_ADDED_TEXT)


def test_a_collect_return_over_the_cap_is_dropped_like_a_failure() -> None:
    bus = HookBus()
    sub(bus, "context.contribute", lambda ev: Block(BIG, "volatile", "x"), owner="wordy")
    sub(bus, "context.contribute", lambda ev: Block("x" * (MAX_ADDED_TEXT - 20), "volatile", "y"), owner="fits")
    sub(bus, "context.contribute", lambda ev: Block(BIG, "volatile", "core"), owner="core")
    skipped: list[str] = []
    out = run(bus.collect("context.contribute", {"conversation_id": "c", "project_id": None}, skipped=skipped))
    assert [b.owner for b in out] == ["y", "core"] and skipped == ["wordy"]
    (wordy,) = bus.subscriptions("wordy")
    assert "HookOutputTooLong" in wordy.errors[-1] and "over the cap" in wordy.errors[-1]


def test_a_pipe_may_pass_a_long_value_on_but_not_add_a_long_text_to_it() -> None:
    bus = HookBus()
    payload = {"name": "t", "call_id": "1", "run_id": None, "result": {"text": BIG}, "tainted": False}
    sub(bus, "tool.result", lambda ev: {**ev, "result": {**ev["result"], "extra": "y" * 100}}, owner="small")
    assert run(bus.pipe("tool.result", payload))["result"]["extra"] == "y" * 100
    sub(bus, "tool.result", lambda ev: {**ev, "result": {**ev["result"], "more": BIG}}, owner="wordy")
    out = run(bus.pipe("tool.result", payload))
    assert "more" not in out["result"] and "extra" in out["result"]


def test_a_gate_reason_over_the_cap_turns_into_a_question() -> None:
    bus = HookBus()
    sub(bus, "tool.pre", lambda ev: Deny(BIG), owner="wordy")
    ctx = {"run_id": None, "conversation_id": None, "project_id": None, "agent_id": None, "desk_id": None, "tainted": False}
    verdict = run(bus.gate("tool.pre", {"name": "t", "args": {}, "call_id": "1", "ctx": ctx}))
    assert isinstance(verdict, Ask) and "wordy" in verdict.reason


def test_a_sync_pipe_over_the_cap_is_dropped_and_counts_toward_the_mute() -> None:
    bus = HookBus()
    sub(bus, "memory.before_write", lambda ev: {**ev, "content": BIG}, owner="wordy")
    value = {"content": "short", "kind": "fact", "source": "user", "project_id": None}
    for _ in range(5):
        assert bus.pipe_sync("memory.before_write", value) == value
    (wordy,) = bus.subscriptions("wordy")
    assert wordy.muted and "HookOutputTooLong" in wordy.errors[-1]


# ---- activation events and tier ----
def lazy(work: Path, code: str = CODE_COMMAND, **over: Any) -> Rig:
    return Rig(work, {"activation": ["onCommand:ping"], "contributes": {"commands": [{"name": "ping", "hint": "say pong"}]},
                      "hooks": {"app.start": {}}, **over}, code)


def test_a_folder_extension_is_user_tier_and_a_packaged_one_builtin(work: Path) -> None:
    assert Rig(work).scope.tier == "user"
    assert ExtensionHost().add_module(SimpleNamespace(key="m", label="M", enabled=lambda: True)).tier == "builtin"


def test_code_waits_for_its_activation_event_and_its_command_is_listed_meanwhile(work: Path) -> None:
    rig = lazy(work)
    assert rig.scope.dormant and rig.scope.module is None
    assert rig.host.slash.listing() == [] and rig.host.slash.expand("ping", "a") is None
    assert rig.host.slash_listing() == [{"name": "ping", "hint": "say pong", "owner": "demo"}]
    run(rig.host.activate("onCommand:other"))
    run(rig.host.activate("onView:ping"))
    assert rig.scope.dormant
    run(rig.host.activate("onCommand:ping"))
    assert rig.scope.dormant is None and rig.host.slash.expand("ping", "a b") == "pong a b"
    assert rig.host.slash_listing() == [{"name": "ping", "hint": "say pong", "owner": "demo"}]


def test_activation_starts_the_extension_once(work: Path) -> None:
    code = "def activate(api):\n    api.on('app.start', lambda p: (api.data_dir / 'starts').open('a').write('x'))\n"
    rig = lazy(work, code)
    run(rig.host.start())
    assert not (rig.scope.data_dir / "starts").exists()  # app start passes it by
    run(rig.host.activate("onCommand:ping"))
    run(rig.host.activate("onCommand:ping"))
    assert (rig.scope.data_dir / "starts").read_text() == "x"


def test_a_disabled_extension_is_not_activated(work: Path) -> None:
    rig = lazy(work)
    rig.hidden.append("demo")
    run(rig.host.activate("onCommand:ping"))
    assert rig.scope.dormant and rig.host.slash_listing() == []
    rig.hidden.clear()
    run(rig.host.activate("onCommand:ping"))
    assert rig.scope.dormant is None


def test_a_failing_activation_unloads_the_extension_and_lists_it_as_failed(work: Path) -> None:
    rig = lazy(work, "def activate(api):\n    raise RuntimeError('nope')\n")
    run(rig.host.activate("onCommand:ping"))
    assert "demo" not in rig.host.extensions and "RuntimeError: nope" in rig.host.failed[rig.root]


def test_slash_name_is_the_command_a_turn_calls() -> None:
    assert [commands.slash_name(t) for t in ("/ping a b", "/ping", "hello", "/Bad", "", None)] == ["ping", "ping", None, None, None, None]
