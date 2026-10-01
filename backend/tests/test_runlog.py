"""The run tape: canonicalisation, delta coalescing, call_once, the `unknown` rule, recovery.

CANON_FIXTURES below is the contract between this file and src/renderer/src/lib/planDigest.test.ts.
`canon()` is what args_digest hashes, so if the Python and TypeScript canonical strings ever differ
by one byte, ActionPlans.claim() silently never matches and every approved step re-prompts. Both
test files pin the same fixture set for that reason.

Run: PERSONAL_OS_DATA_DIR=/tmp/x python backend/tests/test_runlog.py
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="runlogtest-"))
os.environ.setdefault("PERSONAL_OS_AUTH_TOKEN", "test-token")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os import runlog  # noqa: E402
from personal_os.db import Database, new_id, now  # noqa: E402
from personal_os.runlog import RunStore, args_digest, call_key, canon, sse  # noqa: E402

passed = 0

# (args, the exact canonical string JSON.stringify-with-sorted-keys must also produce)
CANON_FIXTURES: list[tuple[dict[str, Any], str]] = [
    ({}, "{}"),
    ({"a": 1, "b": 2}, '{"a":1,"b":2}'),
    ({"b": 2, "a": 1}, '{"a":1,"b":2}'),
    ({"z": {"b": 1, "a": 2}}, '{"z":{"a":2,"b":1}}'),
    ({"l": [3, 1, {"b": 1, "a": 2}]}, '{"l":[3,1,{"a":2,"b":1}]}'),
    ({"f": False, "n": None, "t": True}, '{"f":false,"n":null,"t":true}'),
    ({"s": "hi there"}, '{"s":"hi there"}'),
    ({"e": ""}, '{"e":""}'),
    ({"q": 'a"b\\c'}, '{"q":"a\\"b\\\\c"}'),
    ({"nl": "a\nb\tc"}, '{"nl":"a\\nb\\tc"}'),
    ({"u": "café ✓ \U0001f680"}, '{"u":"café ✓ \U0001f680"}'),
    ({"ctrl": "a\x01b"}, '{"ctrl":"a\\u0001b"}'),
    ({"int": 1.0}, '{"int":1}'),
    ({"frac": 1.5}, '{"frac":1.5}'),
    ({"zero": -0.0}, '{"zero":0}'),
    ({"big": 10000000000}, '{"big":10000000000}'),
    ({"to": ["a@b.c"], "subject": "Hi", "body": "x"}, '{"body":"x","subject":"Hi","to":["a@b.c"]}'),
]
# One pinned hex, so a change to the hash itself (not just the canonical string) is caught too.
PINNED_DIGEST = ("43258cff783fe7036d8a43033f830adfc60ec037382473548ac742b888292777", '{"a":1,"b":2}')


def check(cond: Any, label: str) -> None:
    global passed
    assert cond, label
    passed += 1


def fresh() -> tuple[Database, RunStore, str]:
    """A private database with one conversation, so agent_runs' foreign key resolves."""
    db = Database(tempfile.mkdtemp(prefix="runlog-"))
    store = RunStore(db)
    conv_id = new_id()
    with db.tx() as c:
        c.execute("INSERT INTO conversations(id,title,model,created_at,updated_at) VALUES(?,?,?,?,?)",
                  (conv_id, "t", "m", now(), now()))
    return db, store, conv_id


def events(chunks: list[str]) -> list[tuple[int, str, Any]]:
    """Parse the SSE strings tail() yields back into (id, event, data)."""
    out = []
    for chunk in chunks:
        lines = chunk.strip().split("\n")
        fields = dict(line.split(": ", 1) for line in lines)
        out.append((int(fields["id"]), fields["event"], json.loads(fields["data"])))
    return out


async def collect(store: RunStore, run_id: str, since: int = 0) -> list[str]:
    return [chunk async for chunk in store.tail(run_id, since)]


def test_canon_fixtures() -> None:
    """The canonical string is the contract with lib/planDigest.ts; pin every hazard in it."""
    for args, want in CANON_FIXTURES:
        got = canon(args)
        check(got == want, f"canon({args!r}) == {want!r}, got {got!r}")
        check(args_digest(args) == hashlib.sha256(want.encode("utf-8")).hexdigest(),
              f"args_digest hashes exactly canon() for {args!r}")
    check(canon(None) == "{}", "a missing args object canonicalises as the empty object")


def test_digest_is_order_independent_and_pinned() -> None:
    """Key order is formatting; one changed character is a different call."""
    check(args_digest({"a": 1, "b": 2}) == args_digest({"b": 2, "a": 1}), "key order cannot change the digest")
    check(args_digest({"to": "a@b.c"}) != args_digest({"to": "a@b.d"}), "one changed character is a new digest")
    check(args_digest({"n": 1}) != args_digest({"n": "1"}), "a string is not the number it spells")
    want, src = PINNED_DIGEST
    check(args_digest({"a": 1, "b": 2}) == want, f"the digest of {src} is pinned")


def test_call_key_and_sse() -> None:
    k = call_key("r1", 1001, "gmail_send", args_digest({"to": "a@b.c"}))
    check(k == hashlib.sha256(f"r1|1001|gmail_send|{args_digest({'to': 'a@b.c'})}".encode()).hexdigest(),
          "call_key is sha256 of the pipe-joined tuple")
    check(call_key("r1", 1001, "gmail_send", "d") != call_key("r1", 1002, "gmail_send", "d"),
          "the step is part of the key, so the same call twice is two keys")
    check(sse("delta", {"text": "x"}, 7) == 'id: 7\nevent: delta\ndata: {"text": "x"}\n\n',
          "sse carries the seq as the SSE id so a client can resume from it")
    check(sse("done", {}) == "event: done\ndata: {}\n\n", "without a seq it is runs.sse verbatim")


def test_delta_coalescing() -> None:
    """400 deltas must not be 400 rows, and the tape must still hold every character."""
    db, store, conv = fresh()
    run_id = new_id()
    store.create(run_id, conv)
    text = ""
    for i in range(400):
        piece = f"[{i:04d}]" + "x" * 14
        text += piece
        store.append(run_id, i + 1, "delta", {"id": "m1", "text": piece})
    store.flush(run_id)
    with db.tx() as c:
        rows = c.execute("SELECT COUNT(*) FROM run_events WHERE run_id=?", (run_id,)).fetchone()[0]
    check(rows < 20, f"400 deltas coalesce into fewer than 20 rows, got {rows}")
    check(store.transcript(run_id) == text, "transcript reproduces every character of the reply")
    seen = events(asyncio.run(collect(store, run_id)))
    check("".join(d["text"] for _, e, d in seen if e == "delta") == text, "tail reproduces the full text")
    check(len(seen) == rows, "tail yields one event per taped row")
    check([s for s, _, _ in seen] == sorted(s for s, _, _ in seen), "tail yields ascending seqs")
    check(store.last_seq(run_id) == 400, "the run row tracks the highest seq it has taped")


def test_non_delta_flushes_and_tail_since() -> None:
    """Any other event closes the open delta row, so order on the tape is the order published."""
    _db, store, conv = fresh()
    run_id = new_id()
    store.create(run_id, conv)
    store.append(run_id, 1, "delta", {"id": "m1", "text": "one"})
    store.append(run_id, 2, "tool_call", {"name": "web_search"})
    store.append(run_id, 3, "delta", {"id": "m1", "text": "two"})
    store.append(run_id, 4, "done", {"id": "m1"})
    seen = events(asyncio.run(collect(store, run_id)))
    check([e for _, e, _ in seen] == ["delta", "tool_call", "delta", "done"], "event order survives coalescing")
    check(store.transcript(run_id) == "onetwo", "transcript spans the flushes")
    later = events(asyncio.run(collect(store, run_id, since=2)))
    check([s for s, _, _ in later] == [3, 4], "?since= is served from the tape after the ring is gone")
    store.append(run_id, 5, "delta", {"id": "m1", "text": "a"})
    store.append(run_id, 6, "delta", {"id": "m2", "text": "b"})
    store.flush(run_id)
    tail = events(asyncio.run(collect(store, run_id, since=4)))
    check([d["id"] for _, _, d in tail] == ["m1", "m2"], "a coalesced row never spans two assistant messages")


def test_events_truncated() -> None:
    """Past the cap, deltas stop being taped and the row says so; structure keeps going."""
    db, store, conv = fresh()
    run_id = new_id()
    store.create(run_id, conv)
    cap = runlog.MAX_RUN_EVENTS
    runlog.MAX_RUN_EVENTS = 2
    try:
        for i in range(6):
            store.append(run_id, i + 1, "delta", {"id": "m1", "text": "y" * 4096})
        store.append(run_id, 7, "done", {"id": "m1"})
    finally:
        runlog.MAX_RUN_EVENTS = cap
    row = store.get(run_id) or {}
    check(row["events_truncated"] == 1, "hitting the cap is recorded on the run row")
    check(row["last_seq"] == 7, "last_seq still advances past the untaped deltas")
    with db.tx() as c:
        kinds = [r["type"] for r in c.execute("SELECT type FROM run_events WHERE run_id=? ORDER BY seq",
                                              (run_id,)).fetchall()]
    check(kinds.count("delta") == 2, "only the deltas up to the cap are on the tape")
    check(kinds[-1] == "done", "structural events are still taped after the cap")


def test_call_once_caches_done() -> None:
    """A 'done' row answers a replay from storage; the function is never awaited twice."""
    _db, store, conv = fresh()
    run_id = new_id()
    store.create(run_id, conv)
    calls: list[dict[str, Any]] = []

    async def fn() -> dict[str, Any]:
        calls.append({"n": len(calls)})
        return {"sent": True, "n": len(calls)}

    args = {"to": "a@b.c", "subject": "Hi"}
    first = asyncio.run(store.call_once(run_id, 1001, "gmail_send", args, fn))
    second = asyncio.run(store.call_once(run_id, 1001, "gmail_send", dict(reversed(list(args.items()))), fn))
    check(len(calls) == 1, "the second call_once with the same key does not invoke the function")
    check(second == first == {"sent": True, "n": 1}, "the replay returns the stored result")
    third = asyncio.run(store.call_once(run_id, 1002, "gmail_send", args, fn))
    check(len(calls) == 2 and third["n"] == 2, "a later step is a different key and really runs")
    rows = store.executed(run_id)
    check([r["status"] for r in rows] == ["done", "done"], "both rows finish as done")
    check(rows[0]["attempts"] == 2, "the replay is counted as an attempt without being executed")
    check(rows[0]["args"] == args, "the row keeps the arguments as a dict")


def test_call_once_records_failure_without_retrying() -> None:
    _db, store, conv = fresh()
    run_id = new_id()
    store.create(run_id, conv)
    n = [0]

    async def boom() -> Any:
        n[0] += 1
        raise RuntimeError("no network")

    try:
        asyncio.run(store.call_once(run_id, 1001, "fetch_url", {"url": "x"}, boom))
        check(False, "the caller still sees the exception")
    except RuntimeError:
        check(True, "the caller still sees the exception")
    again = asyncio.run(store.call_once(run_id, 1001, "fetch_url", {"url": "x"}, boom))
    check(n[0] == 1, "the same step does not re-run a call that already failed")
    check("no network" in again["error"], "the stored failure is handed back as a shaped error")


def test_started_row_becomes_unknown() -> None:
    """The one ambiguous state: attempted, outcome unrecorded, never silently repeated."""
    _db, store, conv = fresh()
    run_id = new_id()
    store.create(run_id, conv)
    n = [0]

    async def cancelled() -> Any:
        n[0] += 1
        raise asyncio.CancelledError

    try:
        asyncio.run(store.call_once(run_id, 1001, "gmail_send", {"to": "a@b.c"}, cancelled))
    except asyncio.CancelledError:
        pass
    check(store.executed(run_id)[0]["status"] == "started", "a cut-off call is left at 'started', not 'error'")
    stats = store.recover()
    check(stats["calls"] == 1, "recover() reports the row it relabelled")
    row = store.executed(run_id)[0]
    check(row["status"] == "unknown", "a 'started' row left behind becomes 'unknown'")
    out = asyncio.run(store.call_once(run_id, 1001, "gmail_send", {"to": "a@b.c"}, cancelled))
    check(n[0] == 1, "an unknown call is never executed a second time")
    check(runlog.UNKNOWN_LINE in out["error"] and "gmail_send" in out["error"],
          "it answers with the shaped error, in the words the ledger uses")
    check("try_instead" in out, "the shaped error tells the model what to do instead")


def test_ledger_says_unknown_in_words() -> None:
    db, store, conv = fresh()
    run_id = new_id()
    store.create(run_id, conv)

    async def ok() -> dict[str, Any]:
        return {"ok": True}

    asyncio.run(store.call_once(run_id, 1001, "web_search", {"q": "x"}, ok, desk_id="d1"))
    with db.tx() as c:
        c.execute("INSERT INTO tool_calls(key,run_id,desk_id,step,tool,args,args_digest,status,created_at)"
                  " VALUES('k2',?,'d1',1002,'gmail_send','{}','d','unknown',?)", (run_id, now()))
    lines = [r["line"] for r in store.ledger("d1")]
    check(len(lines) == 2 and lines[0].startswith("1 · web_search · "), "an ordinary row is idx · tool · args · ok")
    check(" · ok · " in lines[0] and lines[0].endswith("ago"), "it carries the status mark and the age")
    check(lines[1] == f"! gmail_send — {runlog.UNKNOWN_LINE}", "an unknown row says so, in those words")
    check(store.ledger("other") == [], "the ledger is scoped to its desk")


def test_run_rows_and_listing() -> None:
    _db, store, conv = fresh()
    a, b = new_id(), new_id()
    store.create(a, conv, kind="desk", desk_id="d1", turn=2, input={"message": "hi"})
    store.create(b, conv)
    row = store.get(a) or {}
    check(row["kind"] == "desk" and row["desk_id"] == "d1" and row["turn"] == 2, "create stores the desk fields")
    check(row["input"] == {"message": "hi"}, "the input comes back as a dict")
    check(row["status"] == "running" and row["ended_at"] is None, "a fresh run is running")
    check((store.latest(conv) or {})["run_id"] == b, "latest is the newest run of the conversation")
    store.update(a, status="done", cost=0.25, rounds=3, budget={"usd": 0.25})
    done = store.get(a) or {}
    check(done["ended_at"] is not None, "a terminal status stamps ended_at")
    check(done["budget"] == {"usd": 0.25} and done["cost"] == 0.25, "the budget snapshot round-trips")
    first_end = done["ended_at"]
    store.update(a, status="done")
    check((store.get(a) or {})["ended_at"] == first_end, "a second terminal update cannot move the end")
    check([r["run_id"] for r in store.list(status=("running",))] == [b], "list filters on status")
    check([r["run_id"] for r in store.list(desk_id="d1")] == [a], "list filters on desk")
    check(len(store.list(conversation_id=conv)) == 2, "list filters on conversation")
    try:
        store.update(a, nonsense=1)
        check(False, "update refuses a column that is not on agent_runs")
    except ValueError:
        check(True, "update refuses a column that is not on agent_runs")


def test_approvals_are_rows() -> None:
    _db, store, conv = fresh()
    run_id = new_id()
    store.create(run_id, conv)
    args = {"to": "a@b.c"}
    row = store.open_approval(call_id="m1:call_0", run_id=run_id, conversation_id=conv, desk_id="d1",
                              message_id="m1", tool="gmail_send", args=args, danger="external", forced=True)
    check(row["status"] == "pending" and row["forced"] == 1, "the card opens pending and remembers it was forced")
    check(row["args_digest"] == args_digest(args), "the row binds the digest the plan would claim against")
    again = store.open_approval(call_id="m1:call_0", run_id=run_id, conversation_id=conv, desk_id="d1",
                                message_id="m1", tool="gmail_send", args={"to": "z@z.z"}, danger="safe",
                                forced=False)
    check(again["args"] == args, "re-opening the same call_id does not rewrite the card under the user")
    store.park("m1:call_0")
    parked = store.approval("m1:call_0") or {}
    check(parked["status"] == "pending", "a parked card stays decidable tomorrow")
    check(parked["decided_by"] == "park", "but the row records that no run is waiting on it")
    check([a["call_id"] for a in store.approvals(desk_id="d1")] == ["m1:call_0"], "it still lists as pending")
    first = store.decide("m1:call_0", "allow", note="go ahead")
    check(first is not None and first["status"] == "approved", "the first decision wins")
    check(first["decided_by"] == "user" and first["note"] == "go ahead", "the decider and note are recorded")
    check(store.decide("m1:call_0", "deny", by="timeout") is None, "a second decision is refused")
    check((store.approval("m1:call_0") or {})["decision"] == "allow", "and cannot change the outcome")
    check(store.approvals(status="pending") == [], "nothing is pending afterwards")
    check(store.approval("nope") is None, "an unknown call_id is None, not an error")


def test_recover_salvages_a_partial_reply() -> None:
    """An interrupted reply becomes the partial text the user watched, not a blank bubble."""
    db, store, conv = fresh()
    run_id, mid = new_id(), new_id()
    store.create(run_id, conv)
    with db.tx() as c:
        c.execute("INSERT INTO messages(id,conversation_id,role,content,created_at) VALUES(?,?,'assistant','',?)",
                  (mid, conv, now()))
    store.update(run_id, message_id=mid)
    store.append(run_id, 1, "delta", {"id": mid, "text": "half a th"})
    store.append(run_id, 2, "delta", {"id": mid, "text": "ought"})
    stats = store.recover()
    check(stats["runs"] == 1 and stats["salvaged"] == 1, "recover reports what it salvaged")
    with db.tx() as c:
        row = c.execute("SELECT content, error FROM messages WHERE id=?", (mid,)).fetchone()
    check(row["content"] == "half a thought", "the unflushed deltas are salvaged into the message row")
    check(row["error"] == "Interrupted", "and the row says why it stops there")
    run = store.get(run_id) or {}
    check(run["status"] == "interrupted" and run["ended_at"] is not None, "the run is interrupted, not done")
    check(store.recover()["salvaged"] == 0, "a second boot has nothing left to salvage")


def test_recover_leaves_a_written_reply_alone() -> None:
    db, store, conv = fresh()
    run_id, mid = new_id(), new_id()
    store.create(run_id, conv)
    with db.tx() as c:
        c.execute("INSERT INTO messages(id,conversation_id,role,content,created_at) VALUES(?,?,'assistant',?,?)",
                  (mid, conv, "the real reply", now()))
    store.update(run_id, message_id=mid)
    store.append(run_id, 1, "delta", {"id": mid, "text": "stale"})
    store.recover()
    with db.tx() as c:
        row = c.execute("SELECT content, error FROM messages WHERE id=?", (mid,)).fetchone()
    check(row["content"] == "the real reply", "a message that persisted its content is never overwritten")
    check(row["error"] is None, "and is not marked interrupted")


def test_prune_drops_old_events_only() -> None:
    db, store, conv = fresh()
    old, recent, live = new_id(), new_id(), new_id()
    for r in (old, recent, live):
        store.create(r, conv)
        store.append(r, 1, "done", {"id": r})
    store.update(old, status="done", ended_at=now() - runlog.EVENTS_RETAIN_S - 60)
    store.update(recent, status="done")
    check(store.prune() == 1, "only the run that ended past the retention window loses its tape")
    with db.tx() as c:
        left = {r["run_id"] for r in c.execute("SELECT DISTINCT run_id FROM run_events").fetchall()}
    check(left == {recent, live}, "the recent and the live run keep their events")
    check(store.get(old) is not None, "the agent_runs row itself survives, so GET /runs still lists it")


def test_events_cascade_with_the_conversation() -> None:
    db, store, conv = fresh()
    run_id = new_id()
    store.create(run_id, conv)
    store.append(run_id, 1, "done", {"id": "m1"})
    with db.tx() as c:
        c.execute("DELETE FROM conversations WHERE id=?", (conv,))
    with db.tx() as c:
        n = c.execute("SELECT COUNT(*) FROM run_events").fetchone()[0]
    check(store.get(run_id) is None and n == 0, "deleting a conversation cascades the run and its tape")


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print(f"ok  {passed} checks passed")


if __name__ == "__main__":
    main()
