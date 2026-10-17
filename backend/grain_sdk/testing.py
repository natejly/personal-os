"""Drive an extension in a scratch backend: load its folder, fire hooks, run chat turns against a scripted model.

    from grain_sdk.testing import Harness

    def test_note_reaches_the_prompt():
        with Harness(Path(__file__).parents[1]) as h:
            h.chat("hello")
            h.chat("again")
            h.assert_context_contains("a note")
            h.assert_prefix_stable()

`FakeModelClient`, `FakeRouter`, `FakeToolSource` and `FakeSandbox` stand in for a runtime slot provider, and `slot_contract(slot,
factory)` runs the slot's contract suite against any provider; `Harness.select(slot, provider_id)` selects one for a chat turn.

The backend is the app singleton, so a test process needs a data dir of its own: the Harness makes a temporary one
unless PERSONAL_OS_DATA_DIR is already set (`grain ext test` sets it). One extension per Harness; a model call that is
not scripted answers "ok".
"""
from __future__ import annotations

import asyncio
import atexit
import json
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .types import HOOK_KINDS

SETTINGS: dict[str, Any] = {"autoLearn": False, "baseUrl": "http://127.0.0.1:9/v1", "provider": "custom", "autoTitle": False,
                            "followUps": False, "learnStyle": False, "permissionMode": "manual"}


@dataclass
class Turn:
    text: str  # the streamed reply
    requests: list[list[dict[str, Any]]]  # the messages of each model call this turn made
    tools: list[str]  # what the first call offered, as JSON: part of the cached prefix
    context_used: dict[str, Any]
    error: str | None = None
    events: list[tuple[str, Any]] = field(default_factory=list)


class Harness:
    def __init__(self, folder: Path | str, settings: dict[str, Any] | None = None) -> None:
        if "PERSONAL_OS_DATA_DIR" not in os.environ:
            os.environ["PERSONAL_OS_DATA_DIR"] = scratch = tempfile.mkdtemp(prefix="grain-harness-")
            atexit.register(shutil.rmtree, scratch, ignore_errors=True)  # the app singleton keeps using it until the process ends
        os.environ.setdefault("GRAIN_SECRETS_BACKEND", "file")
        from personal_os import app as appmod, llm
        self._app, self._llm = appmod, llm
        self._loop = asyncio.new_event_loop()
        self._script: list[Any] = []
        self._requests: list[list[dict[str, Any]]] = []
        self._tools: list[str] = []
        self._stream = llm._stream_openai
        self._runtime = appmod.db.get_settings().get("runtime") or {}
        self.turns: list[Turn] = []
        self.conversation_id: str | None = None
        appmod.db.set_settings({**SETTINGS, **(settings or {})})
        self.scope = self._run(appmod.host.load_dev(Path(folder).resolve(), appmod._ext_ctx))
        appmod.toolbox.set_extension_tools(self.scope)
        appmod.db.set_settings({"hiddenViews": [v for v in appmod.settings().get("hiddenViews") or () if v != self.scope.manifest.id]})

    def _run(self, coro: Any) -> Any:
        return self._loop.run_until_complete(coro)

    def __enter__(self) -> Harness:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._llm._stream_openai = self._stream
        self._app.db.set_settings({"runtime": self._runtime})
        self._app.toolbox.drop_extension_tools(self.scope.tools)
        self._run(self._app.host.unload(self.scope.manifest.id))
        self._loop.close()

    # ---- hooks ----
    def fire(self, hook: str, payload: Any = None) -> Any:
        """Fire a hook the way the host does and return what its handlers made of it: nothing for an emit, the merged
        list for a collect, the final value for a pipe, and None or the Deny/Ask for a gate."""
        bus = self._app.host.bus
        match HOOK_KINDS[hook]:
            case "emit":
                return self._run(bus.emit(hook, payload))
            case "collect":
                return self._run(bus.collect(hook, payload))
            case "pipe":
                return self._run(bus.pipe(hook, payload))
            case _:
                return self._run(bus.gate(hook, payload))

    def select(self, slot: str, provider: str | dict[str, list[str]]) -> None:
        """Select a provider (its full id, `<extension id>/<id>`; for `tools.source` an `{"enable": [...]}` object) for every chat until
        this Harness closes, as the user would in Settings."""
        self._app.db.set_settings({"runtime": {**(self._app.db.get_settings().get("runtime") or {}), slot: provider}})

    # ---- chat ----
    async def _fake(self, settings: dict[str, Any], model: str, messages: list[dict[str, Any]], tools: Any = None, **kw: Any) -> Any:
        self._requests.append([dict(m) for m in messages])
        if len(self._requests) == 1:
            self._tools = [json.dumps(t, sort_keys=True) for t in tools or ()]
        step = self._script.pop(0) if self._script else "ok"
        text, calls = (step, []) if isinstance(step, str) else (step.get("text", ""), step.get("tool_calls", []))
        calls = [{**c, "arguments": c["arguments"] if isinstance(c["arguments"], str) else json.dumps(c["arguments"])} for c in calls]
        if text:
            yield {"type": "delta", "text": text}
        yield {"type": "end", "finish_reason": "stop", "tool_calls": calls, "usage": None}

    def chat(self, text: str, model_script: list[Any] | None = None) -> Turn:
        """Send a message to the conversation this Harness keeps. `model_script` has one entry per model call: a reply
        string, or {"text": ..., "tool_calls": [{"id", "name", "arguments"}]}."""
        app = self._app
        self._llm._stream_openai = self._fake
        self._script, self._requests = list(model_script or []), []
        self.conversation_id = self.conversation_id or app.convos.create(None, "Harness", "test-model")["id"]
        events: list[tuple[str, Any]] = []

        async def go() -> None:
            async for ev, data in app._chat_stream(self.conversation_id, app.ChatIn(content=text), asyncio.Event()):
                events.append((ev, data))
        self._run(go())
        done = next((d for ev, d in events if ev == "done"), {})
        used = next((d["context_used"] for ev, d in reversed(events) if ev == "assistant_message" and d.get("context_used")), {})
        turn = Turn("".join(d["text"] for ev, d in events if ev == "delta"), self._requests, self._tools, used, done.get("error"), events)
        self.turns.append(turn)
        return turn

    def assert_context_contains(self, text: str) -> None:
        """The last turn's first model call carried `text` somewhere in its messages."""
        sent = "\n".join(str(m.get("content")) for m in self.turns[-1].requests[0])
        assert text in sent, f"{text!r} is not in the messages of the last turn's first model call"

    def assert_prefix_stable(self) -> None:
        """Every turn's first model call opened with the same system prompt and tool list, byte for byte: what a provider's
        prompt cache needs. Needs two turns, or it proves nothing."""
        assert len(self.turns) >= 2, "assert_prefix_stable needs at least two chat turns"
        first = self.turns[0]
        for n, turn in enumerate(self.turns[1:], start=2):
            assert turn.requests[0][0]["content"].encode() == first.requests[0][0]["content"].encode(), f"the system prompt changed on turn {n}"
            assert turn.tools == first.tools, f"the tool list changed on turn {n}"


# ---- runtime slot providers ----
class FakeModelClient:
    """A scripted `model.client`: `script` is the event dicts `stream` yields; it raises after `fail_at` events when set."""
    kinds = frozenset({"chat", "complete"})

    def __init__(self, script: list[dict[str, Any]] | None = None, *, fail_at: int | None = None, locality: str = "remote",
                 text: str = "ok", id: str = "fake") -> None:
        self.id, self.locality, self.script, self.fail_at, self.text = id, locality, script, fail_at, text
        self.requests: list[Any] = []

    async def stream(self, req: Any) -> Any:
        self.requests.append(req)
        for n, ev in enumerate(self.script if self.script is not None else
                               [{"type": "delta", "text": self.text}, {"type": "end", "finish_reason": "stop", "tool_calls": [], "usage": None}]):
            if self.fail_at == n:
                raise RuntimeError("scripted failure")
            yield ev

    async def complete(self, req: Any) -> Any:
        from personal_os.runtime.protocols import ModelReply
        self.requests.append(req)
        if self.fail_at == 0:
            raise RuntimeError("scripted failure")
        return ModelReply(self.text)


class FakeRouter:
    """A `model.router` answering `choice`, or raising `error`, after sleeping `delay_s`."""

    def __init__(self, choice: dict[str, Any] | None = None, *, error: Exception | None = None, delay_s: float = 0.0, id: str = "fake") -> None:
        self.id, self.choice, self.error, self.delay_s = id, choice, error, delay_s

    def route(self, turn: dict[str, Any]) -> dict[str, Any]:
        import time
        time.sleep(self.delay_s)
        if self.error is not None:
            raise self.error
        return self.choice or {"model": turn["default_model"], "effort": turn["effort"], "reason": "fake router", "no_think": False}


class FakeToolSource:
    """A `tools.source` offering `tools` (dicts with name, description, schema); `call` fails once it has been called `fail_after` times."""

    def __init__(self, tools: list[dict[str, Any]], *, fail_after: int | None = None, id: str = "fake") -> None:
        self.id, self.tools, self.fail_after, self.calls = id, tools, fail_after, []

    def list(self) -> list[dict[str, Any]]:
        return self.tools

    async def call(self, name: str, args: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
        if self.fail_after is not None and len(self.calls) >= self.fail_after:
            raise RuntimeError("scripted failure")
        self.calls.append((name, args))
        return {"content": f"{name} done"}


class FakeSandbox:
    """A `sandbox.backend` listing `support`. It confines nothing (`wrap` returns the argv as it is), so a contract run catches a
    `support` that claims more than that."""

    def __init__(self, support: dict[str, dict[str, str]] | None = None, *, wrap_fails: bool = False, start_fails: bool = False,
                 id: str = "fake") -> None:
        self.id, self._support, self.wrap_fails, self.start_fails = id, support or {}, wrap_fails, start_fails
        self.wrapped: list[Any] = []

    def support(self) -> dict[str, dict[str, str]]:
        return self._support

    def wrap(self, req: Any, argv: list[str]) -> list[str]:
        if self.wrap_fails or req.mode not in self._support:
            raise OSError("scripted failure" if self.wrap_fails else f"{req.mode} is not supported")
        self.wrapped.append(req)
        return argv

    def failed_to_start(self, exit_code: int | None, output: str) -> bool:
        return self.start_fails


def slot_contract(slot: str, factory: Callable[[], Any]) -> None:
    """Run the contract suite of a runtime slot against the provider `factory()` makes; AssertionError says what it got wrong."""
    from personal_os.runtime.slots import SLOTS
    asyncio.run(SLOTS[slot].contract(factory()))
