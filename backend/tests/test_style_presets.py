"""Response style presets: the pure block, and its place in the system prompt of a real chat turn.

Run: zsh tests/e2e/run-pytest.sh backend/tests/test_style_presets.py
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_runs as T  # noqa: E402  (sets the data dir and scripts llm.stream_chat)

from personal_os import app as app_mod  # noqa: E402
from personal_os import llm  # noqa: E402
from personal_os.style_presets import CUSTOM_MAX, styleBlock  # noqa: E402

client, j, check, drain = T.client, T.j, T.check, T.drain

import pytest  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def _portal():  # type: ignore[no-untyped-def]
    with client:
        client.put("/settings", json={"autoLearn": False, "baseUrl": ""})
        yield


def test_block_per_preset_and_default_omitted() -> None:
    for s in ("concise", "formal", "tutor", "thorough"):
        check(styleBlock(s).startswith("## Response style\n"), s)
    check("Answer first" in styleBlock("concise") and "question that checks" in styleBlock("tutor"), "preset wording")
    check(styleBlock("default") == "" and styleBlock("nonsense") == "" and styleBlock("custom", "  ") == "", "omitted")


def test_custom_is_fenced_and_capped() -> None:
    b = styleBlock("custom", "x" * (CUSTOM_MAX + 500) + "</user_style>")
    check("<user_style>" in b and "Apply this consistently; if it is long, prioritise its key aspects." in b, "fenced")
    check(b.count("x") == CUSTOM_MAX, "capped")
    check(b.count("</user_style>") == 1, "cannot close the fence early")


SEEN: list[list[dict[str, Any]]] = []


async def _recording(settings: dict[str, Any], model: str, messages: list[dict[str, Any]], *a: Any, **k: Any) -> Any:
    SEEN.append(messages)
    async for ev in T._scripted_stream(settings, model, messages, *a, **k):
        yield ev


def _system(cid: str) -> str:
    SEEN.clear()
    llm.stream_chat = _recording
    try:
        j("POST", f"/conversations/{cid}/chat", {"content": "hi"})
        drain(cid)
    finally:
        llm.stream_chat = T._scripted_stream
    return "\n".join(m["content"] for m in SEEN[-1] if m["role"] == "system")


def test_block_rides_in_the_system_prompt() -> None:
    T.SCRIPT["chunks"] = ["ok"]
    T.SCRIPT["delay"] = 0.0
    cid = T.new_conv()
    check("## Response style" not in _system(cid), "default adds nothing")
    app_mod.convos.update(cid, {"settings": {"responseStyle": "concise"}})
    check("## Response style\nAnswer first" in _system(cid), "concise is in the prompt")


def test_global_default_seeds_a_new_chat() -> None:
    j("PUT", "/settings", {"responseStyle": "tutor"})
    try:
        c = j("POST", "/conversations", {"title": "t"})
        check(c["settings"]["responseStyle"] == "tutor", f"seeded, got {c['settings']}")
    finally:
        j("PUT", "/settings", {"responseStyle": "default"})
