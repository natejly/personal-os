"""The MCP namespace guard: RESERVED_TOOL_NAMES really is every built-in tool.

mcp_servers.py lists the built-in names by hand rather than importing Toolbox, so this module
stays free of httpx, the sandbox runtime and the Google client. The comment there promises this
file keeps the list honest - until now it did not exist, and HEAD commit ebfa585 is literally
the fix-up for forgetting the calendar tools. A name missing from the list is not cosmetic: the
collision rules let an MCP server claim a slug that shadows a built-in.

Runs under pytest, or directly: python backend/tests/test_mcp_servers.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os import mcp_servers  # noqa: E402
from personal_os.tools import DEFAULT_MODE, Toolbox  # noqa: E402


class Stub:
    """Stands in for any collaborator Toolbox takes.

    Registration only closes over these objects - nothing is called until a tool runs - so a bare
    object is enough, and using one keeps the test free of a Database and a container runtime.
    """


def full_toolbox() -> Toolbox:
    """A Toolbox with every optional integration present, so every tool registers."""
    return Toolbox(Stub(), Stub(), Stub(), lambda: {},  # type: ignore[arg-type]
                   todos=Stub(), google=Stub(), boards=Stub(), sandboxes=Stub(),  # type: ignore[arg-type]
                   docs=Stub(), activity=Stub(), meetings=Stub())


def test_reserved_list_matches_registered_tools() -> None:
    registered = set(full_toolbox().specs)
    reserved = set(mcp_servers.RESERVED_TOOL_NAMES)
    missing = sorted(registered - reserved)
    stale = sorted(reserved - registered)
    assert not missing and not stale, (
        "mcp_servers.RESERVED_TOOL_NAMES is out of date.\n"
        f"  add to the list:      {missing or 'nothing'}\n"
        f"  remove from the list: {stale or 'nothing'}")


def test_meeting_tools_are_reserved() -> None:
    for name in ("meeting_list", "meeting_search", "meeting_read"):
        assert name in mcp_servers.RESERVED_TOOL_NAMES, f"{name} is registered but not reserved"


def test_no_builtin_can_shadow_an_mcp_slug() -> None:
    """The namespace invariant, from the other side: no built-in may carry RESERVED_PREFIX."""
    for name in mcp_servers.RESERVED_TOOL_NAMES:
        assert not name.startswith(mcp_servers.RESERVED_PREFIX), name
        assert len(name) <= mcp_servers.MAX_SLUG, name


def test_danger_levels_agree_across_modules() -> None:
    """mcp_servers mirrors tools.py's permission model; a tier it does not know falls back to 'external'."""
    assert set(mcp_servers.DANGER_LEVELS) == set(DEFAULT_MODE)
    for spec in full_toolbox().specs.values():
        assert spec.danger in mcp_servers.DANGER_LEVELS, f"{spec.name}: unknown danger {spec.danger!r}"
        assert spec.default_mode in mcp_servers.MODES, spec.name


def test_meeting_lifecycle_is_not_a_tool() -> None:
    """A recorder whose stop button is a tool has no integrity - see _register_meetings.

    `activity_pause` is registered "writes", which DEFAULT_MODE resolves to "on" with no approval
    card, so a prompt-injected model can switch capture off. Meetings deliberately registers
    nothing that starts, stops, enhances or writes, at any tier.
    """
    specs = full_toolbox().specs
    forbidden = ("meeting_start", "meeting_stop", "meeting_pause", "meeting_resume", "meeting_enhance",
                 "meeting_notes_append", "meeting_create", "meeting_delete", "meeting_audio",
                 "meeting_promote_action", "meeting_accept", "meeting_reject")
    for name in forbidden:
        assert name not in specs, f"{name} must never be a tool: lifecycle is a click or an HTTP route"
        assert name not in mcp_servers.RESERVED_TOOL_NAMES, f"{name} is reserved but not registered"
    for name, spec in specs.items():
        if spec.group == "meetings":
            assert spec.danger == "safe", f"{name} is group 'meetings' at danger {spec.danger!r}; read-only only"


def test_transcripts_taint_the_run() -> None:
    """A transcript is other people's speech, so it forces every external tool to ask (tools.gate)."""
    specs = full_toolbox().specs
    assert specs["meeting_search"].taints is True
    assert specs["meeting_read"].taints is True
    assert specs["meeting_list"].taints is False  # previews carry no notes body and no transcript


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
