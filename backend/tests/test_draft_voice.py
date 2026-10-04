"""The voice block is draft-only, untainted-only and volatile; off Draft mode a one-line hint points at the tool. Run: PYTHONPATH=backend python backend/tests/test_draft_voice.py"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="draftvoice-"))
os.environ.setdefault("PERSONAL_OS_AUTH_TOKEN", "test-token")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from personal_os import app as A  # noqa: E402
from personal_os.context import build_context  # noqa: E402
from personal_os.style import STYLE_HEADER, STYLE_HINT, voice_wanted  # noqa: E402

FIXTURE = "SHORTSENTENCEVOICE"
A.style.save_profile(None, {"summary": FIXTURE, "guidelines": ["open with the ask"]})


def ctx(conv_settings: dict, draft: bool) -> tuple[str, dict]:
    return build_context(memories=A.memories, graph=A.graph, documents=A.documents, project=None, project_id=None,
                         query="hi", settings={}, conv_settings=conv_settings, global_system_prompt="sys",
                         style=A.style, draft=draft)


def tool(conv_settings: dict, tainted: bool = False) -> dict:
    fn = A.toolbox.specs["writing_style"].fn
    return asyncio.run(fn({"project_id": None, "conv_settings": conv_settings, "tainted": tainted}))


def test_non_draft_carries_a_hint_and_the_tool_returns_the_voice() -> None:
    system, used = ctx({"useStyle": True}, draft=False)
    assert STYLE_HEADER not in system and FIXTURE not in system and used["style"] is None
    assert STYLE_HINT in system and STYLE_HINT in used["stable_system"]
    assert FIXTURE in str(tool({"useStyle": True}))  # calling the tool is the draft intent
    system, _ = ctx({"useStyle": True, "useTools": False}, draft=False)
    assert STYLE_HINT not in system  # nothing to call


def test_no_profile_no_hint() -> None:
    saved = A.style.profile(None)
    A.style.save_profile(None, {"enabled": False})
    try:
        system, _ = ctx({"useStyle": True}, draft=False)
        assert STYLE_HINT not in system and tool({"useStyle": True})["profile"] is None
    finally:
        A.style.save_profile(None, {"enabled": saved["enabled"]})


def test_draft_is_volatile_then_gone() -> None:
    cs = {"useStyle": True, "draftMode": True}
    system, used = ctx(cs, draft=True)
    assert STYLE_HEADER in system and FIXTURE in system and STYLE_HINT not in system
    assert FIXTURE not in used["stable_system"]
    assert any(STYLE_HEADER in b and FIXTURE in b for b in used["volatile_blocks"])
    assert FIXTURE in str(tool(cs))
    system2, _ = ctx(cs, draft=False)
    assert FIXTURE not in system2


def test_taint_and_toggle_win() -> None:
    cs = {"useStyle": True, "draftMode": True, "tainted": True}
    system, _ = ctx(cs, draft=True)
    assert STYLE_HEADER not in system and tool(cs, tainted=True)["profile"] is None
    assert tool({"useStyle": True, "draftMode": True}, tainted=True)["profile"] is None
    system, _ = ctx({"useStyle": True, "tainted": True}, draft=False)
    assert STYLE_HINT not in system and tool({"useStyle": True}, tainted=True)["profile"] is None
    system, _ = ctx({"useStyle": False}, draft=True)
    assert STYLE_HEADER not in system and tool({"useStyle": False, "draftMode": True})["profile"] is None
    system, _ = ctx({"useStyle": False}, draft=False)
    assert STYLE_HINT not in system and tool({"useStyle": False})["profile"] is None


def test_pure_function() -> None:
    assert voice_wanted({}, draft=True, tainted=False)
    assert not voice_wanted({}, draft=False, tainted=False)
    assert not voice_wanted({}, draft=True, tainted=True)
    assert not voice_wanted({"useStyle": False}, draft=True, tainted=False)


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f()
    print("ok")
