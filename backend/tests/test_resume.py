"""Resume an interrupted chat run: tape-derived note, cross-run idempotency, taint carried over.

Run: python backend/tests/test_resume.py
"""
from __future__ import annotations

import asyncio
import inspect
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="resumetest-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from personal_os import app as appmod  # noqa: E402
from personal_os import llm  # noqa: E402
from personal_os import resume as resume_mod  # noqa: E402
from personal_os.runs import args_digest  # noqa: E402

client = TestClient(appmod.app, headers={"X-Personal-OS-Token": appmod.AUTH_TOKEN})
store = appmod.run_store
passed = 0


def check(cond: Any, label: str) -> None:
    global passed
    assert cond, label
    passed += 1


SEEN: list[list[dict[str, Any]]] = []
ROUNDS: list[dict[str, Any]] = []


async def _scripted_stream(settings: dict[str, Any], model: str, messages: list[dict[str, Any]],
                           tools: list[dict[str, Any]] | None = None, kind: str = "chat",
                           effort: str = "default", tool_choice: str = "auto", fast: bool = False, cancel: asyncio.Event | None = None) -> Any:
    SEEN.append([dict(m) for m in messages])
    step = ROUNDS.pop(0) if ROUNDS else {"text": "all done", "calls": []}
    yield {"type": "delta", "text": step["text"]}
    yield {"type": "end", "finish_reason": "stop", "tool_calls": step["calls"], "usage": None}


_missing = set(inspect.signature(llm.stream_chat).parameters) - set(inspect.signature(_scripted_stream).parameters)
assert not _missing, f"_scripted_stream is missing {sorted(_missing)}"

SEND = {"to": "a@b.c", "subject": "hi", "body": "there"}
CALLS: list[str] = []
_real_call = appmod.toolbox.call


async def _counting_call(name: str, args: dict[str, Any], ctx: dict[str, Any]) -> Any:
    if name == "gmail_send":
        CALLS.append(name)
        return {"ok": True, "sent": True}
    return await _real_call(name, args, ctx)


def call(cid: str, name: str, args: dict[str, Any]) -> dict[str, Any]:
    return {"id": cid, "name": name, "arguments": json.dumps(args)}


def dead_run(*, taint: bool = False, journal: str | None = None, status: str = "interrupted", pending: bool = False) -> tuple[str, str]:
    """A run that died mid-reply, built by hand the way the tape and journal would have left it."""
    cid = appmod.convos.create(None, "t", "m")["id"]
    appmod.convos.add_message(cid, "user", "send the mail")
    am = appmod.convos.add_message(cid, "assistant", "", model="m")
    rid = "dead" + str(time.time_ns())
    store.create(rid, cid, "chat", {})
    store.update(rid, message_id=am["id"])
    seq = 0
    for ev, data in [("delta", {"id": am["id"], "text": "Working on the email"}),
                     ("tool_result", {"message_id": am["id"], "id": "t1", "name": "list_documents", "arguments": {}, "result_preview": "3 docs"})] + (
            [("taint", {"message_id": am["id"], "source": "fetch_url"})] if taint else []):
        seq += 1
        store.append(rid, seq, ev, data)
    if journal:
        async def go() -> None:
            async def fn() -> Any:
                if journal == "started":
                    raise asyncio.CancelledError()
                return {"ok": True, "sent": True, "n": "first"}
            try:
                await store.call_once(rid, 1000, "gmail_send", SEND, fn)
            except asyncio.CancelledError:
                pass
        asyncio.run(go())
    if pending:
        store.open_approval("pend1", rid, "gmail_draft", {"to": "x@y.z"}, conversation_id=cid)
    appmod.convos.finish_message(am["id"], "Working on the email", "Interrupted: backend restarted", None)
    store.update(rid, status=status)
    return cid, rid


def wait(rid_conv: str, until: tuple[str, ...] = ("done", "error", "awaiting_approval", "interrupted")) -> dict[str, Any]:
    for _ in range(400):
        time.sleep(0.05)
        r = store.latest(rid_conv)
        if r and r["status"] in until and r.get("resumed_from"):
            return r
    raise AssertionError("resumed run never settled")


def resume_it(rid: str) -> Any:
    return client.post(f"/runs/{rid}/resume")


def setup() -> None:
    client.put("/settings", json={"autoLearn": False, "baseUrl": "", "tools": {"gmail_send": "on"}})
    llm.stream_chat = _scripted_stream
    appmod.toolbox.call = _counting_call  # type: ignore[method-assign]


def test_note_and_resume() -> None:
    cid, rid = dead_run(pending=True)
    SEEN.clear()
    ROUNDS[:] = [{"text": "continuing", "calls": []}]
    r = resume_it(rid)
    check(r.status_code == 200, f"resume -> 200, got {r.status_code} {r.text}")
    new = wait(cid)
    check(new["resumed_from"] == rid and new["status"] == "done", "new run is linked and finished")
    note = [m for m in SEEN[0] if m["role"] == "system" and "Resuming an interrupted reply" in (m.get("content") or "")]
    check(len(note) == 1, "the model got the resume note")
    c = note[0]["content"]
    check("Working on the email" in c and "list_documents" in c and "3 docs" in c, "note carries partial text and finished calls")
    check("Do not redo completed steps" in c, "note ends with the instruction")
    check("gmail_draft" in c and "NOT run" in c, "pending approval is mentioned")
    check(store.approval("pend1")["status"] == "pending", "the old approval stays pending, never auto-approved")
    check(store.get(rid)["status"] == "interrupted", "the old run is untouched")
    d = client.get(f"/runs/{rid}").json()
    check(d["resumable"] is False and d["resumed_from"] is None, "resumed run is no longer resumable")
    check(client.get(f"/runs/{new['run_id']}").json()["resumed_from"] == rid, "GET exposes resumed_from")


def test_done_call_replays() -> None:
    cid, rid = dead_run(journal="done")
    CALLS.clear()
    ROUNDS[:] = [{"text": "", "calls": [call("c1", "gmail_send", SEND)]}, {"text": "sent", "calls": []}]
    SEEN.clear()
    check(resume_it(rid).status_code == 200, "resume ok")
    new = wait(cid)
    check(new["status"] == "done", "resumed run finished")
    check(CALLS == [], "the done call was replayed, the tool never ran again")
    tool_msgs = [m["content"] for m in SEEN[-1] if m["role"] == "tool"]
    check(tool_msgs and '"replayed": true' in tool_msgs[0] and "first" in tool_msgs[0], "model saw the recorded result marked replayed")


def test_started_call_is_unknown() -> None:
    cid, rid = dead_run(journal="started")
    CALLS.clear()
    ROUNDS[:] = [{"text": "", "calls": [call("c1", "gmail_send", SEND)]}, {"text": "ok", "calls": []}]
    SEEN.clear()
    check(resume_it(rid).status_code == 200, "resume ok")
    wait(cid)
    check(CALLS == [], "a started call is never run again")
    tool_msgs = [m["content"] for m in SEEN[-1] if m["role"] == "tool"]
    check(tool_msgs and "unknown" in tool_msgs[0].lower(), "model told the outcome is unknown")
    note = [m["content"] for m in SEEN[0] if m["role"] == "system" and "Resuming" in (m.get("content") or "")][0]
    check("do NOT repeat" in note, "note warns about the started call")


def test_taint_carries_over() -> None:
    cid, rid = dead_run(taint=True)
    CALLS.clear()
    ROUNDS[:] = [{"text": "", "calls": [call("c1", "gmail_send", {**SEND, "subject": "other"})]}, {"text": "x", "calls": []}]
    check(resume_it(rid).status_code == 200, "resume ok")
    new = wait(cid, ("awaiting_approval", "done", "error"))
    check(new["status"] == "awaiting_approval", f"tainted resume parks the external call, got {new['status']}")
    check(CALLS == [], "the external call did not run")
    ap = store.approvals(None, run_id=new["run_id"])
    check(ap and ap[0]["forced"], "the approval is forced")
    store.update(new["run_id"], status="done")  # let the test move on


def test_409s() -> None:
    cid, rid = dead_run(status="done")
    check(resume_it(rid).status_code == 409, "done run -> 409")
    check(client.post("/runs/nope/resume").status_code == 404, "unknown run -> 404")
    cid, rid = dead_run()
    check(resume_it(rid).status_code in (200, 409), "first resume")
    wait(cid, ("done", "error", "awaiting_approval"))
    r2 = resume_it(rid)
    check(r2.status_code == 409, "second resume -> 409")
    cid, rid = dead_run()
    store.update(rid, status="interrupted")
    appmod.convos.create(None, "t", "m")
    newer = "newer" + str(time.time_ns())
    time.sleep(0.01)
    store.create(newer, cid, "chat", {})
    store.update(newer, status="done")
    check(resume_it(rid).status_code == 409, "a newer run in the conversation -> 409")
    cid, rid = dead_run()
    with appmod.db.tx() as c:
        c.execute("UPDATE agent_runs SET desk_id='d1' WHERE run_id=?", (rid,))
    check(resume_it(rid).status_code == 409, "desk run -> 409")


def test_note_unit() -> None:
    run = {"message_id": "m1", "run_id": "r"}
    ev = [(1, "delta", {"id": "m1", "text": "x" * 5000}),
          (2, "tool_result", {"name": "t", "arguments": {"q": "y" * 1000}, "result_preview": "p" * 1000, "error": "boom"})]
    a = resume_mod.build_resume_note(run, ev, [{"status": "started", "tool": "gmail_send"}], [])
    b = resume_mod.build_resume_note(run, ev, [{"status": "started", "tool": "gmail_send"}], [])
    check(a == b, "deterministic")
    check("x" * 1501 not in a and "y" * 301 not in a and "p" * 301 not in a, "truncated")
    check("(error)" in a and "gmail_send" in a, "errors and started calls listed")
    check(resume_mod.taint_from_tape([(1, "taint", {"source": "fetch_url"}), (2, "delta", {})]) == ["fetch_url"], "taint sources read from the tape")
    ok, why = resume_mod.resumable({"status": "interrupted", "kind": "chat", "desk_id": None, "run_id": "r"}, {"run_id": "r"}, False)
    check(ok and not why, "resumable happy path")


def test_prior_call_unit() -> None:
    async def go() -> None:
        n = 0

        async def fn() -> Any:
            nonlocal n
            n += 1
            return {"v": n}
        await store.call_once("pr-a", 5, "t", {"a": 1}, fn)
        row = store.prior_call("pr-a", "t", args_digest({"a": 1}))
        check(row and row["status"] == "done", "prior_call finds the row at any step")
        check(store.prior_call("pr-a", "t", args_digest({"a": 2})) is None, "different args -> none")
    cid = appmod.convos.create(None, "t", "m")["id"]
    store.create("pr-a", cid, "chat", {})
    store.create("pr-b", cid, "chat", {})
    asyncio.run(go())

    async def go2() -> None:
        calls = 0

        async def fn() -> Any:
            nonlocal calls
            calls += 1
            return {"v": "new"}
        res, rep = await store.call_once("pr-b", 9, "t", {"a": 1}, fn, inherit="pr-a")
        check(rep and res == {"v": 1} and calls == 0, "inherit replays the old run's done result")
        res, rep = await store.call_once("pr-b", 9, "t", {"a": 1}, fn)
        check(rep and calls == 0, "and it is recorded locally under the new key")
        res, rep = await store.call_once("pr-b", 9, "t", {"a": 2}, fn, inherit="pr-a")
        check(not rep and calls == 1, "a call the old run never made runs normally")
    asyncio.run(go2())


if __name__ == "__main__":
    failed = 0
    prev = (llm.stream_chat, appmod.toolbox.call)
    with client:
        setup()
        try:
            for t in [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]:
                try:
                    t()
                    print(f"ok   {t.__name__}")
                except Exception as e:  # noqa: BLE001
                    failed += 1
                    import traceback
                    traceback.print_exc()
                    print(f"FAIL {t.__name__}: {e}")
        finally:
            llm.stream_chat, appmod.toolbox.call = prev  # type: ignore[method-assign]
    print(f"\n{passed} assertions passed, {failed} test(s) failed")
    sys.exit(1 if failed else 0)
