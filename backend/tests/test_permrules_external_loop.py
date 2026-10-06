"""External-danger cards inside the chat loop: no whole-tool grant, deny survives skip-permissions. Offline.

Harness copied from test_permrules_loop. Original doc: Permission rules inside the chat loop (app._chat_stream), driven with a scripted model and a fake `shell_run`.

The real shell tool is built elsewhere; the rules only look at its name and its `command`, so a stand-in that
records what it was asked to run is enough. Run: python backend/tests/test_permrules_loop.py
"""
from __future__ import annotations

import asyncio
import inspect
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="permloop-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os import llm  # noqa: E402
from personal_os import app as appmod  # noqa: E402
from personal_os import permrules  # noqa: E402
from personal_os.runs import Run  # noqa: E402
from personal_os.tools import ToolSpec, _obj  # noqa: E402

passed = 0
RAN: list[str] = []
SEEN: list[list[dict[str, Any]]] = []
ROUNDS: list[list[dict[str, Any]]] = []


def check(cond: Any, label: str) -> None:
    global passed
    assert cond, label
    passed += 1


async def _fake_shell(ctx: dict[str, Any], to: str) -> Any:
    RAN.append(to)
    return {"ok": True, "ran": to}


appmod.toolbox.specs["gmail_send"] = ToolSpec(
    "gmail_send", "fake send", _obj({"to": {"type": "string"}}, ["to"]), _fake_shell, "google", "external")


async def _scripted(settings: dict[str, Any], model: str, messages: list[dict[str, Any]], tools: Any = None, kind: str = "chat",
                    effort: str = "default", tool_choice: str = "auto", fast: bool = False, cancel: Any = None) -> Any:
    SEEN.append([dict(m) for m in messages])
    calls = ROUNDS.pop(0) if ROUNDS else []
    yield {"type": "delta", "text": "ok" if not calls else ""}
    yield {"type": "end", "finish_reason": "stop", "tool_calls": calls, "usage": None}


_missing = set(inspect.signature(llm.stream_chat).parameters) - set(inspect.signature(_scripted).parameters)
assert not _missing, f"_scripted is missing {sorted(_missing)}"


def sh(i: int, command: str) -> dict[str, Any]:
    return {"id": f"c{i}", "name": "gmail_send", "arguments": json.dumps({"to": command})}


def setup(rules: dict[str, list[str]] | None = None, mode: str = "ask", **settings: Any) -> str:
    appmod.db.set_settings({"autoLearn": False, "baseUrl": "", "stuckDetection": True, "workspaceRoots": [],
                            "permissionMode": "manual", "unattendedApprovals": "ask", "permissionRules": {"allow": [], "ask": [], "deny": [], **(rules or {})}, **settings})
    cid = appmod.convos.create(None, "t", "m")["id"]
    appmod.convos.update(cid, {"settings": {"tools": {"gmail_send": mode}}})
    RAN.clear()
    SEEN.clear()
    permrules.SESSION.clear()
    return cid


def drive(cid: str, calls_per_round: list[list[dict[str, Any]]], answers: list[Any] | None = None, run: Run | None = None) -> list[tuple[str, Any]]:
    """Run one reply. `answers` is consumed one per card: a decision string, or (decision, note)."""
    ROUNDS[:] = calls_per_round
    queue = list(answers or [])

    async def answerer(uid: str) -> None:
        while uid not in appmod._approvals:
            await asyncio.sleep(0.01)
        a = queue.pop(0) if queue else "allow"
        dec, note = a if isinstance(a, tuple) else (a, None)
        await appmod.approve_tool_call(uid, appmod.ApprovalIn(decision=dec, note=note))

    async def go() -> list[tuple[str, Any]]:
        out, tasks = [], []
        async for ev in appmod._chat_stream(cid, appmod.ChatIn(content="go"), asyncio.Event(), run=run):
            out.append(ev)
            if ev[0] == "tool_call" and ev[1].get("needs_approval"):
                tasks.append(asyncio.create_task(answerer(ev[1]["id"])))
        await asyncio.gather(*tasks)
        return out

    prev = llm.stream_chat
    llm.stream_chat = _scripted
    try:
        return asyncio.run(go())
    finally:
        llm.stream_chat = prev


def cards(events: list[tuple[str, Any]]) -> list[dict[str, Any]]:
    return [d for e, d in events if e == "tool_call" and d.get("needs_approval")]


def results(events: list[tuple[str, Any]]) -> list[dict[str, Any]]:
    return [d for e, d in events if e == "tool_result"]


def tool_messages() -> list[str]:
    return [m["content"] for r in SEEN[-1:] for m in r if m["role"] == "tool"]




def test_deny_survives_skip_permissions() -> None:
    cid = setup({"deny": ["gmail_send(bad@x.com)"]}, mode="ask", permissionMode="allow_all")
    ev = drive(cid, [[sh(0, "bad@x.com")], []])
    check(not RAN and not cards(ev) and results(ev)[0]["error"], "a denied recipient is refused in allow-all mode")
    cid = setup({"deny": ["gmail_send(bad@x.com)"]}, mode="ask", permissionMode="allow_all")
    ev = drive(cid, [[sh(0, "ok@x.com")], []])
    # Allow all lifts even an external tool's card; only the deny rule above stops a call.
    check(RAN == ["ok@x.com"] and not cards(ev), "another recipient runs without a card in allow-all mode")


def test_external_card_has_danger_and_no_whole_tool_grant() -> None:
    for dec in ("always_chat", "always_global"):
        cid = setup(mode="ask", skipPermissions=False)
        ev = drive(cid, [[sh(0, "a@x.com")], []], [dec])
        check(cards(ev)[0]["permission"]["danger"] == "external", "the card payload carries danger")
        check(RAN == ["a@x.com"], f"{dec} still sends this one call")
        check("gmail_send" not in (appmod.convos.get(cid)["settings"].get("tools") or {}) or appmod.convos.get(cid)["settings"]["tools"]["gmail_send"] == "ask",
              f"{dec} wrote no chat grant")
        check((appmod.settings().get("tools") or {}).get("gmail_send") != "on", f"{dec} wrote no global grant")
        ev = drive(cid, [[sh(0, "b@x.com")], []], ["deny"])
        check(len(cards(ev)) == 1, f"{dec}: the next send asks again")


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f()
    print(f"{passed} checks passed")
