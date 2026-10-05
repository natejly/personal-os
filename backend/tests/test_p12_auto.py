"""Auto model choice: the pure router, and a chat turn on model `auto` sending the chosen model.

Run: zsh tests/e2e/run-pytest.sh backend/tests/test_p12_auto.py
Built on the test_runs harness (TestClient + a scripted llm.stream_chat).
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_runs as T  # noqa: E402  (sets the data dir and scripts llm.stream_chat)

from personal_os import llm, router  # noqa: E402

client, j, drain = T.client, T.j, T.drain

try:
    import pytest
except ImportError:
    pytest = None
else:
    @pytest.fixture(scope="module", autouse=True)
    def _portal():  # type: ignore[no-untyped-def]
        with client:
            client.put("/settings", json={"autoLearn": False, "autoTitle": False, "baseUrl": ""})
            yield

FAST, BIG = "fast-m", "big-m"


def r(text: str, **kw: Any) -> tuple[str, str]:
    return router.route(text, fast_model=kw.pop("fast_model", FAST), default_model=BIG, **kw)


def test_router_decisions() -> None:
    assert r("thanks, and the time?") == (FAST, "short follow-up")
    assert r("a" * 300) == (BIG, "long message")
    assert r("hi", attachments=True) == (BIG, "attachments")
    assert r("fix ```x = 1```") == (BIG, "contains code")
    assert r("Why is the sky blue?") == (BIG, "asks for analysis")
    assert r("please compare these two") == (BIG, "asks for analysis")
    assert r("ok", prior_tools=True) == (BIG, "follows tool use")
    assert r("ok", effort="high")[0] == BIG and r("ok", effort="max")[0] == BIG
    assert r("ok", plan_mode=True) == (BIG, "plan mode")
    assert r("/research cats") == (BIG, "slash command")
    assert r("ok", fast_model="") == (BIG, "no fast model")
    assert r("ok", effort="low") == (FAST, "short follow-up")


def test_wanted_only_without_an_explicit_pick() -> None:
    assert router.wanted("auto", "", {})
    assert router.wanted("auto", "auto", {})
    assert not router.wanted(BIG, BIG, {"autoRoute": True, "defaultModel": BIG})
    assert router.wanted(BIG, "", {"autoRoute": True, "defaultModel": BIG})
    assert not router.wanted(BIG, "", {"autoRoute": False, "defaultModel": BIG})
    assert router.concrete("auto", {"defaultModel": BIG}) == BIG and router.concrete("x", {"defaultModel": BIG}) == "x"


seen: list[str] = []


async def _record(settings: dict[str, Any], model: str, messages: list[dict[str, Any]], *a: Any, **k: Any) -> Any:
    seen.append(model)
    yield {"type": "delta", "text": "ok"}
    yield {"type": "end", "finish_reason": "stop", "tool_calls": [], "usage": None}


def _turn(text: str, model: str = "auto") -> dict[str, Any]:
    llm.stream_chat = _record
    cid = T.new_conv()
    j("PATCH", f"/conversations/{cid}", {"model": model})
    j("POST", f"/conversations/{cid}/chat", {"content": text})
    drain(cid)
    llm.stream_chat = T._scripted_stream
    return j("GET", f"/conversations/{cid}")


def _setup(fast: str) -> None:
    j("PUT", "/settings", {"defaultModel": BIG, "fastModel": fast})


def test_auto_turn_streams_with_the_fast_model_and_records_why() -> None:
    _setup(FAST)
    seen.clear()
    conv = _turn("thanks")
    assert seen == [FAST]
    assert conv["model"] == "auto"
    am = conv["messages"][-1]
    assert am["model"] == FAST
    ctx = next(s for s in am["trace"] if s["kind"] == "context")
    assert ctx["meta"]["routed_from"] == "auto" and ctx["meta"]["reason"] == "short follow-up"


def test_auto_turn_with_heavy_message_uses_default() -> None:
    _setup(FAST)
    seen.clear()
    conv = _turn("Please analyse this")
    assert seen == [BIG] and conv["messages"][-1]["model"] == BIG


def test_auto_without_fast_model_falls_back() -> None:
    _setup("")
    seen.clear()
    conv = _turn("thanks")
    assert seen == [BIG]
    ctx = next(s for s in conv["messages"][-1]["trace"] if s["kind"] == "context")
    assert ctx["meta"]["reason"] == "no fast model"


def test_a_specific_model_is_never_routed() -> None:
    _setup(FAST)
    seen.clear()
    _turn("thanks", model="pinned-m")
    assert seen == ["pinned-m"]
