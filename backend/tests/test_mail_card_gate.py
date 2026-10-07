"""An agent never sends email by itself: gmail_send always stops at the user's email card, in every permission mode.

Drives the real chat loop (app._chat_stream) with a scripted model, the real gmail_send tool, the real outbox and a
fake Gmail. Only the user's Send click sends; Save as draft writes a draft; a discard sends nothing.

Reuses the harness of test_permrules_external_loop. Run: python backend/tests/test_mail_card_gate.py
"""
from __future__ import annotations

import asyncio
import base64
import email
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from personal_os import app as appmod  # noqa: E402

REAL = appmod.toolbox.specs["gmail_send"]
import test_permrules_external_loop as T  # noqa: E402  (swaps in a fake gmail_send; the real one goes back below)

appmod.toolbox.specs["gmail_send"] = REAL
from fake_google_api import FakeGoogle, FakeServer  # noqa: E402
from personal_os import autoreview, llm, permrules, verify  # noqa: E402
from personal_os.runs import Run  # noqa: E402

check = T.check
GOOD = {"to": "mira@example.com", "subject": "Hello", "body": "Original text"}
SERVER = FakeServer()


def setup(mode: str = "allow_all", rules: dict[str, list[str]] | None = None, hold: bool = False) -> str:
    cid = T.setup(rules, mode=None, permissionMode=mode, alwaysAsk=[], toolDeferAbove=0, gmailSendHold={"enabled": hold, "seconds": 60})
    SERVER.messages.clear()
    SERVER.drafts.clear()
    return cid


def send(i: int, **args: Any) -> dict[str, Any]:
    return {"id": f"c{i}", "name": "gmail_send", "arguments": json.dumps({**GOOD, **args})}


def drive(cid: str, rounds: list[list[dict[str, Any]]], answers: list[Any], run: Run | None = None) -> list[tuple[str, Any]]:
    """One reply. Each answer is a decision string or (decision, edited arguments, note)."""
    T.ROUNDS[:] = rounds
    queue = list(answers)

    async def answerer(uid: str) -> None:
        while uid not in appmod._approvals:
            await asyncio.sleep(0.01)
        a = queue.pop(0) if queue else "deny"
        dec, args, note = (a, None, None) if isinstance(a, str) else a
        await appmod.approve_tool_call(uid, appmod.ApprovalIn(decision=dec, arguments=args, note=note))

    async def go() -> list[tuple[str, Any]]:
        out, tasks = [], []
        async for ev in appmod._chat_stream(cid, appmod.ChatIn(content="go"), asyncio.Event(), run=run or Run(cid, appmod.run_store, kind="chat")):
            out.append(ev)
            if ev[0] == "tool_call" and ev[1].get("needs_approval"):
                tasks.append(asyncio.create_task(answerer(ev[1]["id"])))
        await asyncio.gather(*tasks)
        return out

    prev, appmod_ok = llm.stream_chat, appmod.toolbox._google_ok
    llm.stream_chat = T._scripted
    appmod.toolbox._google_ok = lambda: True  # type: ignore[method-assign]
    real_google, appmod.pim.google = appmod.pim.google, FakeGoogle(SERVER)
    d, s = verify.MAIL_RETRY_DELAYS, verify.SLEEP
    verify.MAIL_RETRY_DELAYS, verify.SLEEP = (0.0,), lambda s: None
    try:
        return asyncio.run(go())
    finally:
        llm.stream_chat, appmod.toolbox._google_ok, appmod.pim.google = prev, appmod_ok, real_google
        verify.MAIL_RETRY_DELAYS, verify.SLEEP = d, s


def last_tool_message() -> str:
    return next((m["content"] for m in reversed(T.SEEN[-1]) if m["role"] == "tool"), "")


def test_allow_all_still_raises_the_email_card_and_a_discard_sends_nothing() -> None:
    cid = setup("allow_all")
    ev = drive(cid, [[send(0)], []], ["deny"])
    cs = T.cards(ev)
    check(len(cs) == 1 and cs[0]["forced"] is True, "allow-all raised one forced card for the email")
    check(not SERVER.messages and not SERVER.drafts, "a discard sent nothing and saved no draft")
    check("discarded by the user on the email card" in last_tool_message(), "the model was told it was discarded")


def test_an_allow_rule_or_grant_never_stands_in_for_the_click() -> None:
    for mode in ("allow_all", "manual", "auto"):
        cid = setup(mode, {"allow": ["gmail_send(mira@example.com)", "gmail_send(*)"], "ask": [], "deny": []})
        for decision in ("always_chat", "always_global"):
            ev = drive(cid, [[send(0)], []], [decision])
            check(len(T.cards(ev)) == 1 and len(SERVER.messages) == 1, f"{mode}/{decision}: the click is what sent this one")
            SERVER.messages.clear()
            ev = drive(cid, [[send(0)], []], ["deny"])
            check(len(T.cards(ev)) == 1 and not SERVER.messages, f"{mode}/{decision}: the next email asks again and a no sends nothing")
            permrules.SESSION.clear()
    check((appmod.settings().get("tools") or {}).get("gmail_send") != "on", "no grant turned the tool on")


def test_approving_edits_sends_exactly_the_edited_email() -> None:
    cid = setup("allow_all")
    doc = appmod._store_upload(None, "notes.txt", "text/plain", b"attached text")
    edited = {**GOOD, "body": "Human wrote this", "cc": "ana@example.com", "attachments": [doc["id"]]}
    ev = drive(cid, [[send(0)], []], [("allow", edited, None)])
    check(len(T.cards(ev)) == 1 and len(SERVER.messages) == 1, "one card, one send")
    msg = email.message_from_bytes(base64.urlsafe_b64decode(next(iter(SERVER.messages.values()))["raw"]))
    check(msg["Cc"] == "ana@example.com" and msg["To"] == "mira@example.com", "the edited recipients went out")
    parts = [p for p in msg.walk() if not p.is_multipart()]
    check(parts[0].get_payload(decode=True).decode().strip() == "Human wrote this", "the edited body, not the model's, was sent")
    check([p.get_filename() for p in parts[1:]] == ["notes.txt"], "the edited attachment list went out")
    check("The user edited these arguments before approving" in last_tool_message(), "the result carries the edit note")
    check("Original text" not in "".join(p.get_payload(decode=True).decode(errors="ignore") for p in parts[:1]), "the model's body was replaced")


def test_save_as_draft_writes_a_draft_and_never_sends() -> None:
    cid = setup("allow_all")
    ev = drive(cid, [[send(0)], []], [("allow", {**GOOD, "as_draft": True}, None)])
    check(len(T.cards(ev)) == 1 and not SERVER.messages and len(SERVER.drafts) == 1, "a draft was saved, nothing was sent")


def test_the_hold_still_applies_after_the_click() -> None:
    cid = setup("allow_all", hold=True)
    drive(cid, [[send(0)], []], ["allow"])
    check(not SERVER.messages and [r["status"] for r in appmod.outbox.list() if r["to"] == GOOD["to"]][:1] == ["holding"], "the Send click queues it for the undo window")
    for r in appmod.outbox.list():
        appmod.outbox.cancel(r["id"])


def test_a_background_job_files_a_proposal_and_sends_nothing() -> None:
    cid = setup("allow_all")
    run = Run(cid, appmod.run_store, kind="job")
    ev = drive(cid, [[send(0)], []], [], run=run)
    mine = [p for p in appmod.proposals.list("pending", limit=500) if p["run_id"] == run.run_id]
    check(not T.cards(ev) and not SERVER.messages and len(mine) == 1 and mine[0]["tool"] == "gmail_send", "a job's email is a proposal, nothing was sent")


def test_the_pieces() -> None:
    tb = appmod.toolbox
    spec = tb.specs["gmail_send"]
    check(tb.ask_locked(spec) and tb.forces_ask("gmail_send", {}, {}) and tb.forces_card("gmail_send", {}, {}), "locked, forced and hard-forced")
    check(tb.always_ask().issuperset({"gmail_send"}), "listed even when the alwaysAsk setting is empty")
    check(tb.effective({"gmail_send": "on"}, None, None)["gmail_send"] == "ask", "never 'on'")
    for pmode in ("allow_all", "auto", "manual"):
        check(autoreview.route(pmode, mode="ask", danger="external", hard_forced=True, question="gmail_send" in permrules.STILL_ASK) == "card", f"{pmode}: card")
    r = permrules.resolve("gmail_send", {"to": "mira@example.com"}, "ask", True, rules=permrules.load_rules({"allow": ["gmail_send(mira@example.com)"]}))
    check(r.mode == "ask", "a forced ask survives an allow rule")
    check(permrules.lift_permission_ask("gmail_send", "ask", skip=True) == "ask", "skip-permissions does not lift it")


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f()
            print("ok  ", n)
    print(T.passed, "checks passed")
