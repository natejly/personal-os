"""The model.client wrapper in llm.py and the model.router seam: a swapped provider's output is held to the shape the loop expects,
the core does the accounting, and a failing provider falls back only where that is safe.

Run: python -m pytest backend/tests/test_runtime_providers.py
"""
from __future__ import annotations

import asyncio
import inspect
import sys
from pathlib import Path
from typing import Any, AsyncIterator, Iterator

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from grain_sdk.testing import FakeModelClient, FakeRouter  # noqa: E402
from personal_os import llm, router, runtime  # noqa: E402
from personal_os.extensions.hooks import HookBus  # noqa: E402
from personal_os.runtime import checked  # noqa: E402
from personal_os.runtime.protocols import ModelReply, ModelRequest  # noqa: E402
from personal_os.runtime.slots import Provider, Runtime, Scopes  # noqa: E402

END = {"type": "end", "finish_reason": "stop", "tool_calls": [], "usage": None}
REMOTE = {"baseUrl": "https://api.example.com/v1", "apiKey": "SECRET-KEY"}
LOCAL = {"baseUrl": "http://localhost:11434/v1"}


def run(coro: Any) -> Any:
    return asyncio.run(coro)


async def collect(stream: AsyncIterator[dict[str, Any]]) -> list[dict[str, Any]]:
    return [ev async for ev in stream]


class Rig:
    """A runtime with the built-in providers and any number of extra ones, made the active registry for llm and router."""

    def __init__(self) -> None:
        self.bus = HookBus()
        self.rt = Runtime(self.bus, [])
        from personal_os.runtime.defaults import core_providers
        for p in core_providers():
            self.rt._providers[p.slot][p.id] = p
        self.selected: dict[str, Any] = {}
        self.rt.scopes = lambda: Scopes(global_=self.selected)
        self.events: list[tuple[str, Any]] = []
        for hook in ("model.request", "model.response", "runtime.fallback"):
            self.bus.subscribe(hook, lambda p, h=hook: self.events.append((h, p)), owner="t", order=0)

    def add(self, slot: str, name: str, impl: Any) -> str:
        pid = f"acme/{name}"
        self.rt._providers[slot][pid] = Provider(pid, slot, name.title(), "user", "acme", impl=impl)
        self.rt._remember(self.rt._providers[slot][pid])
        return pid

    def select(self, slot: str, pid: str) -> None:
        self.selected[slot] = pid


@pytest.fixture
def rig(monkeypatch: pytest.MonkeyPatch) -> Iterator[Rig]:
    r = Rig()
    monkeypatch.setattr(runtime, "active", r.rt)
    monkeypatch.setattr(llm, "bus", r.bus)
    monkeypatch.setattr(llm.default_client, "settings", lambda: REMOTE)
    llm.auth_breaker.reset()
    yield r
    llm.auth_breaker.reset()


def stream(**kw: Any) -> AsyncIterator[dict[str, Any]]:
    return llm.stream_chat(kw.pop("settings", REMOTE), "m", [{"role": "user", "content": "hi"}], kw.pop("tools", None), **kw)


# ---- the default is untouched ----
def test_without_a_selection_the_built_in_client_answers_directly(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[Any] = []

    async def fake(settings: Any, model: str, messages: Any, tools: Any = None, **kw: Any) -> AsyncIterator[dict[str, Any]]:
        calls.append((settings, model, kw))
        yield {"type": "delta", "text": "built-in"}
        yield END

    monkeypatch.setattr(llm, "_stream_openai", fake)
    assert [e["type"] for e in run(collect(stream()))] == ["delta", "end"] and calls[0][0] is REMOTE
    rig.add("model.client", "c", FakeModelClient())
    assert len(run(collect(stream()))) == 2 and len(calls) == 2  # an available alternative that nobody selected changes nothing


def test_the_built_in_client_derives_its_locality_from_the_configured_hosts() -> None:
    client = llm.OpenAICompatible()
    for settings, expected in ((LOCAL, "local"), (REMOTE, "remote"), ({"baseUrl": "http://192.168.1.20:8000/v1"}, "local"), ({}, "remote"),
                               ({"providers": [{"id": "a", "kind": "ollama", "baseUrl": "http://127.0.0.1:11434/v1"},
                                               {"id": "b", "kind": "custom", "baseUrl": "https://api.example.com/v1"}]}, "remote")):
        client.settings = lambda s=settings: s
        assert client.locality == expected, settings
    proxy = {"providers": [{"id": "p", "kind": "litellm", "baseUrl": "http://localhost:4000"}]}
    client.settings = lambda: proxy
    assert client.locality == "remote"  # the proxy forwards to whatever model its config names
    client.settings = lambda: {"provider": "litellm", "baseUrl": "http://127.0.0.1:4000"}
    assert client.locality == "remote"


# ---- model.client: shape, accounting, secrets ----
def test_a_malformed_stream_never_reaches_the_loop_malformed(rig: Rig) -> None:
    script = [{"type": "mystery"}, {"type": "delta", "text": "he"}, {"type": "delta", "text": 5}, {"type": "reasoning", "text": "hm"},
              {"type": "end", "finish_reason": "tool_calls", "usage": {"prompt_tokens": 3, "completion_tokens": 2},
               "tool_calls": [{"id": "c1", "name": "web_search", "arguments": {"q": "x"}}, {"name": "nameless"}, {"id": "c2", "name": "", "arguments": "{}"},
                              {"id": "c3", "name": "not_offered", "arguments": "{}"}]},
              {"type": "end", "finish_reason": "stop", "tool_calls": []}, {"type": "delta", "text": "after"}]
    rig.select("model.client", rig.add("model.client", "c", FakeModelClient(script=script)))
    events = run(collect(stream(tools=[{"type": "function", "function": {"name": "web_search"}}])))
    assert [e["type"] for e in events] == ["delta", "reasoning", "end"]
    assert checked.problems(events) == []
    end = events[-1]
    assert [c["name"] for c in end["tool_calls"]] == ["web_search", "not_offered"]  # an unknown name goes down the loop's own invalid-call path
    assert end["tool_calls"][0]["arguments"] == '{"q": "x"}' and end["usage"] == {"prompt_tokens": 3, "completion_tokens": 2}
    assert set(end["usage_est"]) == {"prompt_tokens", "completion_tokens"} and end["incomplete"] is False


def test_a_stream_without_an_end_gets_an_incomplete_one(rig: Rig) -> None:
    rig.select("model.client", rig.add("model.client", "c", FakeModelClient(script=[{"type": "delta", "text": "cut"}])))
    events = run(collect(stream()))
    assert [e["type"] for e in events] == ["delta", "end"] and events[-1]["incomplete"] is True and events[-1]["tool_calls"] == []


def test_the_core_fires_the_model_hooks_and_the_usage_row_for_a_swapped_client(rig: Rig) -> None:
    rig.select("model.client", rig.add("model.client", "c", FakeModelClient(text="hello")))
    run(collect(stream(kind="title")))
    kinds = [h for h, _ in rig.events]
    assert kinds == ["model.request", "model.response"]
    request, response = (p for _, p in rig.events)
    assert request["model"] == "m" and request["message_count"] == 1
    assert response["model"] == "m" and response["kind"] == "title" and response["estimated"] is True and response["completion_tokens"] >= 1


def test_the_sdk_exports_the_reply_a_client_returns_from_complete() -> None:
    import grain_sdk
    assert grain_sdk.ModelReply is ModelReply and grain_sdk.ModelRequest is ModelRequest


def test_the_client_is_handed_the_request_and_nothing_else(rig: Rig) -> None:
    client = FakeModelClient()
    rig.select("model.client", rig.add("model.client", "c", client))
    run(collect(stream(settings={**REMOTE, "providers": [{"id": "p", "kind": "x", "baseUrl": "https://x"}], "providerKeys": "ANOTHER-KEY"})))
    [req] = client.requests
    assert isinstance(req, ModelRequest) and set(req.__dataclass_fields__) == {"model", "messages", "tools", "kind", "effort", "tool_choice", "fast"}
    assert "SECRET-KEY" not in repr(req) and "ANOTHER-KEY" not in repr(req)
    assert list(inspect.signature(FakeModelClient.stream).parameters) == ["self", "req"]


def test_a_client_serves_only_the_kinds_it_declares(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakeModelClient(text="from the client")
    client.kinds = frozenset({"chat"})
    rig.select("model.client", rig.add("model.client", "c", client))

    async def default_complete(settings: Any, model: str, messages: Any, kind: str = "other", **kw: Any) -> str:
        return "from the default"

    monkeypatch.setattr(llm, "_complete_openai", default_complete)
    assert run(llm.complete(REMOTE, "m", [{"role": "user", "content": "t"}], "title")) == "from the default"
    client.kinds = frozenset({"complete"})
    assert run(llm.complete(REMOTE, "m", [{"role": "user", "content": "t"}], "title")) == "from the client"
    assert [h for h, _ in rig.events] == ["model.request", "model.response"]


# ---- model.client: failure ----
def broken_default(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    used: list[str] = []

    async def fake(settings: Any, model: str, messages: Any, tools: Any = None, **kw: Any) -> AsyncIterator[dict[str, Any]]:
        used.append("default")
        yield {"type": "delta", "text": "default answer"}
        yield END

    monkeypatch.setattr(llm, "_stream_openai", fake)
    return used


def test_a_local_client_failing_before_its_first_event_is_not_retried_on_a_remote_default(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    used = broken_default(monkeypatch)
    rig.select("model.client", rig.add("model.client", "c", FakeModelClient(locality="local", fail_at=0)))
    with pytest.raises(llm.LLMError, match="failed"):
        run(collect(stream()))
    assert used == [] and not [p for h, p in rig.events if h == "runtime.fallback"]


def test_a_remote_client_failing_before_its_first_event_is_retried_once_on_the_default(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    used = broken_default(monkeypatch)
    rig.select("model.client", rig.add("model.client", "c", FakeModelClient(locality="remote", fail_at=0)))
    events = run(collect(stream()))
    assert used == ["default"] and events[0]["text"] == "default answer"
    assert "default client answered" in events[-1]["notice"]
    [fell] = [p for h, p in rig.events if h == "runtime.fallback"]
    assert (fell["slot"], fell["wanted"], fell["used"], fell["refused"]) == ("model.client", "acme/c", "core/openai-compatible", False)


def test_a_client_refused_its_credentials_twice_is_held_and_not_called_again(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    used = broken_default(monkeypatch)
    client = FakeModelClient(fail_at=0, error=PermissionError)
    rig.select("model.client", rig.add("model.client", "c", client))
    for _ in range(2):
        assert run(collect(stream()))[0]["text"] == "default answer"
    assert len(client.requests) == 2
    held = run(collect(stream()))
    assert len(client.requests) == 2, "the third call is refused before the client is reached"
    assert held[0]["text"] == "default answer" and "refused its credentials" in held[-1]["notice"]
    assert used == ["default"] * 3


def test_a_held_client_fails_the_turn_when_the_default_is_less_private_and_a_reset_reopens_it(rig: Rig) -> None:
    client = FakeModelClient(fail_at=0, error=PermissionError, locality="local")
    rig.select("model.client", rig.add("model.client", "c", client))
    for _ in range(2):
        with pytest.raises(llm.LLMError, match="PermissionError") as raised:
            run(collect(stream()))
        assert raised.value.kind == "auth"
    with pytest.raises(llm.LLMError, match="'C' was refused its credentials"):
        run(collect(stream()))
    assert len(client.requests) == 2
    llm.auth_breaker.reset()
    client.fail_at = None
    assert run(collect(stream()))[-1]["type"] == "end" and len(client.requests) == 3


def test_a_local_client_may_fall_back_to_a_local_default(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    used = broken_default(monkeypatch)
    monkeypatch.setattr(llm.default_client, "settings", lambda: LOCAL)
    rig.select("model.client", rig.add("model.client", "c", FakeModelClient(locality="local", fail_at=0)))
    run(collect(stream(settings=LOCAL)))
    assert used == ["default"]


def test_a_failure_after_events_is_not_replayed_on_another_client(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    used = broken_default(monkeypatch)
    rig.select("model.client", rig.add("model.client", "c", FakeModelClient(locality="remote", fail_at=1)))
    seen: list[dict[str, Any]] = []

    async def go() -> None:
        async for ev in stream():
            seen.append(ev)

    with pytest.raises(llm.LLMError, match="failed: RuntimeError"):
        run(go())
    assert [e["type"] for e in seen] == ["delta"] and used == []


def test_a_complete_that_fails_falls_back_only_to_a_default_that_is_no_less_private(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    async def default_complete(settings: Any, model: str, messages: Any, kind: str = "other", **kw: Any) -> str:
        return "default"

    monkeypatch.setattr(llm, "_complete_openai", default_complete)
    rig.select("model.client", rig.add("model.client", "far", FakeModelClient(locality="remote", fail_at=0)))
    assert run(llm.complete(REMOTE, "m", [], "title")) == "default"
    rig.select("model.client", rig.add("model.client", "near", FakeModelClient(locality="local", fail_at=0)))
    with pytest.raises(llm.LLMError):
        run(llm.complete(REMOTE, "m", [], "title"))


def test_a_selected_client_that_is_gone_while_the_default_is_less_private_fails_the_call(rig: Rig) -> None:
    pid = rig.add("model.client", "c", FakeModelClient(locality="local"))
    rig.select("model.client", pid)
    rig.rt.resolve("model.client")  # seen while it was there
    rig.rt._providers["model.client"][pid].impl = None  # gone
    with pytest.raises(llm.LLMError, match="nothing was run") as e:
        run(collect(stream()))
    assert e.value.kind == "unavailable"


def test_a_silent_client_is_cut_off_after_the_idle_limit(rig: Rig) -> None:
    class Hang(FakeModelClient):
        async def stream(self, req: Any) -> AsyncIterator[dict[str, Any]]:
            await asyncio.sleep(30)
            yield END

    rig.select("model.client", rig.add("model.client", "c", Hang(locality="local")))  # a local client is never retried on the remote default
    with pytest.raises(llm.LLMError, match="stopped responding") as e:
        run(collect(stream(settings={**REMOTE, "llmIdleSeconds": 0.05})))
    assert e.value.kind == "timeout"


def test_cancelling_the_turn_cancels_the_clients_pending_read(rig: Rig) -> None:
    ended: list[str] = []

    class Slow(FakeModelClient):
        async def stream(self, req: Any) -> AsyncIterator[dict[str, Any]]:
            try:
                await asyncio.sleep(30)
                yield END
            except asyncio.CancelledError:
                ended.append("cancelled")
                raise

    rig.select("model.client", rig.add("model.client", "c", Slow()))

    async def go() -> None:
        turn = asyncio.ensure_future(collect(stream()))
        await asyncio.sleep(0.05)
        turn.cancel()
        await asyncio.gather(turn, return_exceptions=True)
        await asyncio.sleep(0)

    run(go())
    assert ended == ["cancelled"]


def test_a_well_formed_stream_passes_through_the_wrapper_unchanged(rig: Rig) -> None:
    call = {"id": "c1", "name": "t", "arguments": "{}"}
    sent = [{"type": "reasoning", "text": "hm"}, {"type": "delta", "text": "a"}, {"type": "delta", "text": "b"},
            {"type": "end", "finish_reason": "tool_calls", "tool_calls": [call], "usage": {"prompt_tokens": 3, "completion_tokens": 2}}]
    rig.select("model.client", rig.add("model.client", "c", FakeModelClient(script=sent)))
    got = run(collect(stream()))
    assert got[:3] == sent[:3]
    assert {k: got[3][k] for k in ("finish_reason", "tool_calls", "usage")} == {k: sent[3][k] for k in ("finish_reason", "tool_calls", "usage")}


def test_stop_and_the_deadline_end_a_swapped_clients_stream_like_a_providers(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    class Slow(FakeModelClient):
        async def stream(self, req: Any) -> AsyncIterator[dict[str, Any]]:
            yield {"type": "delta", "text": "x"}
            await asyncio.sleep(30)
            yield END

    rig.select("model.client", rig.add("model.client", "c", Slow()))

    async def stopped() -> list[dict[str, Any]]:
        cancel = asyncio.Event()
        asyncio.get_running_loop().call_later(0.05, cancel.set)
        return await collect(stream(cancel=cancel))

    events = run(stopped())
    assert events[-1]["finish_reason"] == "cancelled" and events[-1]["tool_calls"] == []

    async def late() -> list[dict[str, Any]]:
        import time
        llm.stream_deadline.set(time.monotonic() + 0.05)
        return await collect(stream())

    assert run(late())[-1]["finish_reason"] == "timeout"


def test_complete_returns_text_and_a_client_that_returns_something_else_is_an_error(rig: Rig) -> None:
    client = FakeModelClient(text="the text")
    rig.select("model.client", rig.add("model.client", "c", client))
    assert run(llm.complete(REMOTE, "m", [{"role": "user", "content": "x"}], "learn")) == "the text"
    assert isinstance(run(client.complete(ModelRequest("m", []))), ModelReply)

    class Wrong(FakeModelClient):
        async def complete(self, req: Any) -> Any:
            return "bare string"

    rig.select("model.client", rig.add("model.client", "wrong", Wrong(locality="local")))
    with pytest.raises(llm.LLMError, match="did not return a ModelReply"):
        run(llm.complete(REMOTE, "m", [], "learn"))


# ---- model.router ----
TURN = {"text": "hi", "attachments": False, "prior_tools": False, "effort": "high", "plan_mode": False, "fast_model": "fast",
        "default_model": "big", "conversation_id": None, "project_id": None}


def choose(rig: Rig, served: Any = lambda m: True) -> tuple[dict[str, Any], str | None]:
    return run(router.choose(rig.rt, TURN, served))


def test_the_default_router_is_the_heuristic_unchanged(rig: Rig) -> None:
    choice, notice = choose(rig)
    assert (choice["model"], choice["reason"], choice["no_think"], notice) == ("fast", "short follow-up", False, None)
    assert router.route("hi", effort="high", fast_model="fast", default_model="big") == ("fast", "short follow-up", False)
    quiet = router.Heuristic().route({**TURN, "fast_model": ""})
    assert quiet["no_think"] is True and quiet["effort"] == "none"


def test_a_selected_router_decides(rig: Rig) -> None:
    rig.select("model.router", rig.add("model.router", "r", FakeRouter({"model": "big", "effort": "low", "reason": "mine", "no_think": False})))
    assert choose(rig) == ({"model": "big", "effort": "low", "reason": "mine", "no_think": False}, None)


@pytest.mark.parametrize("fake,why", [
    (FakeRouter(error=RuntimeError("boom")), "boom"),
    (FakeRouter(delay_s=0.5), "TimeoutError"),
    (FakeRouter({"model": "unconfigured", "effort": "high", "reason": "x", "no_think": False}), "not served by any configured provider"),
    (FakeRouter({"model": "big"}), "a route is"),
])
def test_a_router_that_fails_is_replaced_by_the_default_for_that_turn(rig: Rig, fake: FakeRouter, why: str) -> None:
    rig.select("model.router", rig.add("model.router", "r", fake))
    choice, notice = choose(rig, served=lambda m: m != "unconfigured")
    assert choice["model"] == "fast" and notice and why in notice and "default router was used" in notice
    [fell] = [p for h, p in rig.events if h == "runtime.fallback"]
    assert (fell["slot"], fell["wanted"], fell["used"]) == ("model.router", "acme/r", "core/heuristic")


def test_five_failures_in_a_row_mute_a_router_for_the_session(rig: Rig) -> None:
    fake = FakeRouter(error=RuntimeError("boom"))
    rig.select("model.router", rig.add("model.router", "r", fake))
    for _ in range(router.MUTE_AFTER):
        assert choose(rig)[1]
    fake.error = None
    assert choose(rig)[1] is None and choose(rig)[0]["reason"] == "short follow-up"  # skipped, so the working router is not asked either


def test_a_success_resets_the_failure_count(rig: Rig) -> None:
    fake = FakeRouter(error=RuntimeError("boom"))
    rig.select("model.router", rig.add("model.router", "r", fake))
    for _ in range(router.MUTE_AFTER - 1):
        choose(rig)
    fake.error = None
    assert choose(rig)[0]["reason"] == "fake router"
    fake.error = RuntimeError("boom")
    for _ in range(router.MUTE_AFTER - 1):
        assert choose(rig)[1]
    assert router.wanted("auto", "", {}) and not router.wanted("m", "explicit", {"autoRoute": True, "defaultModel": "m"})


def test_the_safety_reviewer_is_never_served_by_an_extension_client(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    ext = FakeModelClient(text='{"verdict": "allow"}')
    rig.select("model.client", rig.add("model.client", "c", ext))
    seen: list[str] = []

    async def builtin(settings: Any, model: str, messages: Any, kind: str = "other", **kw: Any) -> str:
        seen.append(kind)
        return "core"

    monkeypatch.setattr(llm, "_complete_openai", builtin)
    assert run(llm.complete(REMOTE, "m", [{"role": "user", "content": "x"}], kind="review")) == "core"
    assert run(llm.complete(REMOTE, "m", [{"role": "user", "content": "x"}], kind="title")) == '{"verdict": "allow"}'
    assert seen == ["review"]
