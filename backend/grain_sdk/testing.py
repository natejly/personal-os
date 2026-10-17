"""Drive an extension in a scratch backend: load its folder, fire hooks, run chat turns against a scripted model.

    from grain_sdk.testing import Harness

    def test_note_reaches_the_prompt():
        with Harness(Path(__file__).parents[1]) as h:
            h.chat("hello")
            h.chat("again")
            h.assert_context_contains("a note")
            h.assert_prefix_stable()

The backend is the app singleton, so a test process needs a data dir of its own: the Harness makes a temporary one
unless PERSONAL_OS_DATA_DIR is already set (`grain ext test` sets it). One extension per Harness; a model call that is
not scripted answers "ok".
"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

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
        os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="grain-harness-"))
        os.environ.setdefault("GRAIN_SECRETS_BACKEND", "file")
        from personal_os import app as appmod, llm
        self._app, self._llm = appmod, llm
        self._loop = asyncio.new_event_loop()
        self._script: list[Any] = []
        self._requests: list[list[dict[str, Any]]] = []
        self._tools: list[str] = []
        self._stream = llm.stream_chat
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
        self._llm.stream_chat = self._stream
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
        self._llm.stream_chat = self._fake
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
