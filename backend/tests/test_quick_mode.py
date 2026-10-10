"""Quick answer mode: the prompt block and the 'quick' mark ride only when the chat has it on; the background-work tools are in QUICK_HIDDEN."""
from __future__ import annotations

import os
import sys
import tempfile

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="quickmode-"))
os.environ.setdefault("PERSONAL_OS_AUTH_TOKEN", "test-token")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from personal_os import app as A  # noqa: E402
from personal_os.context import QUICK_RULES, build_context  # noqa: E402
from personal_os.tools import QUICK_HIDDEN  # noqa: E402


def ctx(conv_settings: dict) -> tuple[str, dict]:
    return build_context(memories=A.memories, graph=A.graph, documents=A.documents, project=None, project_id=None,
                         query="hi", settings={}, conv_settings=conv_settings, global_system_prompt="sys", style=A.style)


def test_rules_and_mark_only_when_on() -> None:
    system, used = ctx({"quick": True})
    assert QUICK_RULES in system and used["quick"] is True
    system, used = ctx({})
    assert QUICK_RULES not in system and "quick" not in used


def test_delegation_and_jobs_are_hidden() -> None:
    assert {"delegate", "check_worker", "agent_spawn", "schedule_task", "workflow_run", "opencode_run"} <= QUICK_HIDDEN
    assert QUICK_HIDDEN & set(A.toolbox.specs)  # the names track real tools
