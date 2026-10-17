"""Telegram as an extension: the channel adapter it registers, `channel.inbound` and the chained `channel.outbound`, and the
`approval.requested` subscriber that texts a background worker's card. The transport is the fake API of test_telegram_api.py,
so nothing touches the network."""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import types
from pathlib import Path
from typing import Any

os.environ["GRAIN_SECRETS_BACKEND"] = "file"
os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="telegramext-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from personal_os import app as appmod  # noqa: E402
from personal_os.extensions.channels import Channels  # noqa: E402
from personal_os.extensions.hooks import Cancel, HookBus  # noqa: E402
from personal_os.extensions.host import ExtensionHost  # noqa: E402
from test_telegram_api import OWNER, _portal, _reset, bridge, fake, j, pair, target, wait_until  # noqa: E402,F401

bus = appmod.host.bus


@pytest.fixture
def subscribe():  # type: ignore[no-untyped-def]
    """Subscribe a test handler for the duration of one test."""
    made = []

    def add(hook: str, fn: Any, priority: int = 50) -> None:
        made.append(bus.subscribe(hook, fn, owner="test", order=99, priority=priority))
    yield add
    for d in made:
        d.dispose()


# ---- the registry ----
class _Adapter:
    def __init__(self, name: str, home: str | None = None) -> None:
        self.name, self.home, self.stopped = name, home, False

    def is_texts_conversation(self, conv_id: str | None) -> bool:
        return conv_id is not None and conv_id == self.home

    async def stop(self) -> None:
        self.stopped = True


def test_the_registry_finds_the_adapter_that_owns_a_conversation_and_stops_them_all() -> None:
    channels = Channels()
    a, b = _Adapter("a", home="chat-a"), _Adapter("b", home="chat-b")
    channels.register(a, lambda: True)  # type: ignore[arg-type]
    channels.register(b, lambda: True)  # type: ignore[arg-type]
    assert channels.owner_of("chat-b") is b and channels.owner_of("elsewhere") is None and channels.owner_of(None) is None
    assert channels["a"] is a
    asyncio.run(channels.stop())
    assert a.stopped and b.stopped


def test_a_channel_name_is_taken_once_and_free_again_after_dispose() -> None:
    channels = Channels()
    first = channels.register(_Adapter("a"), lambda: True)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        channels.register(_Adapter("a"), lambda: True)  # type: ignore[arg-type]
    first.dispose()
    assert list(channels) == []
    channels.register(_Adapter("a"), lambda: True)  # type: ignore[arg-type]


def test_the_extension_registers_the_bridge_as_a_channel_with_its_routes() -> None:
    scope = appmod.host.extensions["telegram"]
    assert appmod.host.channels["telegram"] is bridge and bridge.name == "telegram" and "Telegram" in bridge.hint
    assert scope.manifest.hooks.keys() == {"app.start", "settings.changed", "ext.changed", "approval.requested"}
    assert [r.prefix for r in scope.routers] == ["/telegram"]
    assert j("GET", "/telegram/status")["status"] == "no_token"


def test_a_channel_is_removed_when_its_extension_stops() -> None:
    async def nothing() -> None: ...
    host = ExtensionHost()
    scope = host.add_module(types.SimpleNamespace(key="mod", label="Mod", enabled=lambda: True, start=nothing, stop=nothing))  # type: ignore[arg-type]
    scope.channel(_Adapter("a", home="chat-a"))  # type: ignore[arg-type]
    assert host.channels.owner_of("chat-a") is not None
    asyncio.run(host.stop())
    assert list(host.channels) == [] and host.channels.owner_of("chat-a") is None


def test_turning_the_extension_off_stops_the_bridge_and_silences_its_routes_and_channel() -> None:
    pair()
    cid = target()
    assert appmod.host.channels.owner_of(cid) is bridge
    hidden = appmod.settings()["hiddenViews"]
    j("PUT", "/settings", {"hiddenViews": [*hidden, "telegram"]})
    try:
        wait_until(lambda: bridge._task is None, "the poller to stop")
        j("GET", "/telegram/status", expect=404)
        assert appmod.host.channels.owner_of(cid) is None and "telegram" not in {c.name for c in appmod.host.channels}
    finally:
        j("PUT", "/settings", {"hiddenViews": hidden})
    wait_until(lambda: bridge._task is not None, "the poller to start again")
    assert j("GET", "/telegram/status")["paired"] is True


# ---- hooks ----
def test_pipe_chains_every_handler_and_cancel_stops_the_chain() -> None:
    async def go() -> None:
        hooks = HookBus()
        seen: list[Any] = []

        def add(tag: str, priority: int, out: Any = None) -> None:
            def fn(v: Any) -> Any:
                seen.append((tag, v))
                return out(v) if callable(out) else out
            hooks.subscribe("channel.outbound", fn, owner="t", order=0, priority=priority)
        add("one", 10, lambda v: v + "1")
        add("two", 20, lambda v: v + "2")
        assert await hooks.pipe("channel.outbound", "x") == "x12"
        add("cancel", 30, Cancel())
        add("never", 40, "never")
        assert await hooks.pipe("channel.outbound", "x") == Cancel()
        assert ("never", "x12") not in seen
    asyncio.run(go())


# ---- channel.inbound ----
def test_an_owner_text_fires_channel_inbound_before_the_turn_starts(subscribe) -> None:  # type: ignore[no-untyped-def]
    seen: list[dict[str, Any]] = []
    subscribe("channel.inbound", lambda ev: seen.append(ev))
    pair()
    fake.push(fake.update("ping"))
    wait_until(lambda: "Hello <b>there</b>" in fake.sent(), "the reply")
    assert seen == [{"channel": "telegram", "sender": "owner", "text": "ping", "files": []}]


def test_a_stranger_text_does_not_fire_channel_inbound(subscribe) -> None:  # type: ignore[no-untyped-def]
    seen: list[dict[str, Any]] = []
    subscribe("channel.inbound", lambda ev: seen.append(ev))
    pair()
    fake.push(fake.update("hi", chat=OWNER + 1, user=OWNER + 1))
    asyncio.run(asyncio.sleep(0.3))
    assert seen == []


# ---- channel.outbound ----
def test_a_reply_passes_through_every_outbound_handler_in_order(subscribe) -> None:  # type: ignore[no-untyped-def]
    subscribe("channel.outbound", lambda ev: {**ev, "text": ev["text"] + " [two]"}, priority=60)
    subscribe("channel.outbound", lambda ev: {**ev, "text": ev["text"] + " [one]"}, priority=40)
    pair()
    fake.push(fake.update("ping"))
    wait_until(lambda: any("[one] [two]" in t for t in fake.sent()), "the reworded reply")
    assert fake.sent() == ["Hello <b>there</b> [one] [two]"]


def test_a_handler_may_drop_the_files_of_a_reply(subscribe) -> None:  # type: ignore[no-untyped-def]
    subscribe("channel.outbound", lambda ev: {**ev, "files": []})
    ext_bridge = appmod.host.channels["telegram"]
    sent = asyncio.run(ext_bridge.deps.outbound("hi", [{"id": "d1", "name": "a.txt"}]))
    assert sent == ("hi", [])


def test_a_cancelled_reply_is_not_sent_and_the_next_one_is(subscribe) -> None:  # type: ignore[no-untyped-def]
    cancel = [True]
    subscribe("channel.outbound", lambda ev: Cancel() if cancel[0] else None)
    pair()
    fake.push(fake.update("ping"))
    wait_until(lambda: len(appmod.run_store.list(None, appmod.db.get_settings()["telegramState"]["textsConversationId"])) == 1, "the run")
    wait_until(lambda: not appmod.bus.live_ids(), "the run to end")
    assert fake.sent() == []
    cancel[0] = False
    fake.push(fake.update("again"))
    wait_until(lambda: "Hello <b>there</b>" in fake.sent(), "the second reply")


def test_a_pushed_worker_reply_goes_through_outbound_too(subscribe) -> None:  # type: ignore[no-untyped-def]
    subscribe("channel.outbound", lambda ev: {**ev, "text": ev["text"].upper()} if "loud" in ev["text"] else Cancel())
    pair()
    bridge.push("loud result")
    wait_until(lambda: "LOUD RESULT" in fake.sent(), "the reworded push")
    bridge.push("quiet result")
    asyncio.run(asyncio.sleep(0.2))
    assert fake.sent() == ["LOUD RESULT"]


def test_progress_and_the_from_grain_mirror_go_through_outbound_too(subscribe) -> None:  # type: ignore[no-untyped-def]
    subscribe("channel.outbound", lambda ev: {**ev, "text": ev["text"].replace("secret", "[redacted]")})
    pair()
    cid = target()
    assert bridge.send_update(cid, "the secret halfway")
    wait_until(lambda: "the [redacted] halfway" in fake.sent(), "the redacted progress")
    bridge.from_app(cid, "run-mirror", "my secret", [])
    wait_until(lambda: "From Grain: my [redacted]" in fake.sent(), "the redacted mirror")
    assert not any("secret" in t for t in fake.sent())


def test_a_cancelled_progress_update_and_mirror_are_not_sent(subscribe) -> None:  # type: ignore[no-untyped-def]
    subscribe("channel.outbound", lambda ev: Cancel())
    pair()
    cid = target()
    bridge.send_update(cid, "halfway")
    bridge.from_app(cid, "run-mirror", "typed in Grain", [])
    asyncio.run(asyncio.sleep(0.3))
    assert fake.sent() == []


# ---- approval.requested ----
def test_a_background_workers_card_is_texted_when_it_opens_and_other_cards_are_not(monkeypatch: pytest.MonkeyPatch) -> None:
    texted: list[str] = []
    monkeypatch.setattr(bridge, "notify_worker_approval", texted.append)
    monkeypatch.setattr(appmod.workers_mgr, "_owner", lambda call_id: object() if call_id.startswith("sa_w1:") else None)
    appmod.run_store.create("run-ext-1", None, "chat", {})
    appmod.run_store.create("sa_w1", None, "subagent", {})
    appmod.run_store.open_approval("call-ext-chat", "run-ext-1", "gmail_draft", {"to": "a@b.co"})
    assert texted == []
    appmod.run_store.open_approval("sa_w1:f1", "sa_w1", "fetch_url", {"url": "https://example.com"})
    assert texted == ["sa_w1:f1"]
    appmod.run_store.open_approval("sa_w1:f1", "sa_w1", "fetch_url", {"url": "https://example.com"})
    assert texted == ["sa_w1:f1"], "a card that was already open is not announced again"
