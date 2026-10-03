"""Reasoning effort: new chats start at low, and Kimi K3 only hears a value it accepts.

K3 rejects medium and xhigh, and a missing field is its own max. The dropdown can still say
Medium; the wire value for that model is high.

Runs under pytest, or directly: python backend/tests/test_reasoning_effort.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os.db import Database  # noqa: E402
from personal_os.llm import effort_param  # noqa: E402
from personal_os.repos import Conversations, DEFAULT_EFFORT  # noqa: E402


def test_new_chat_starts_at_low() -> None:
    with tempfile.TemporaryDirectory() as d:
        conv = Conversations(Database(d)).create(None, "hi", "kimi-k3")
    assert conv["settings"]["effort"] == "low"
    assert DEFAULT_EFFORT == "low"


def test_kimi_k3_medium_is_sent_as_high() -> None:
    assert effort_param("kimi-k3", "medium") == "high"
    assert effort_param("accounts/fireworks/models/kimi-k3", "medium") == "high"
    assert effort_param("kimi-k3-fast", "medium") == "high"
    assert effort_param("kimi-k3", "low") == "low"
    assert effort_param("kimi-k3", "high") == "high"
    assert effort_param("kimi-k3", "xhigh") == "max"
    assert effort_param("kimi-k3", "max") == "max"


def test_omitted_effort_stays_off_the_wire() -> None:
    """'default' means send nothing, including on Kimi, where that is the model's own max."""
    assert effort_param("kimi-k3", "default") is None
    assert effort_param("deepseek-v4-flash", "default") is None
    assert effort_param("kimi-k3", "") is None


def test_other_models_keep_medium() -> None:
    assert effort_param("deepseek-v4-flash", "medium") == "medium"
    assert effort_param("glm-5.3", "xhigh") == "xhigh"


def test_kimi_k2_rejects_the_field() -> None:
    assert effort_param("kimi-k2.7-code", "medium") is None
    assert effort_param("kimi-k2.7-code", "high") is None


def test_caps_without_reasoning_leave_the_field_off() -> None:
    assert effort_param("gpt-x", "medium", {"reasoning": False}) is None
    assert effort_param("gpt-x", "medium", {"reasoning": True}) == "medium"
    assert effort_param("gpt-x", "medium", {}) == "medium"
    # The Kimi mapping is decided before the capability check.
    assert effort_param("kimi-k3", "medium", {"reasoning": False}) == "high"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"  ok  {fn.__name__}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"FAIL  {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
