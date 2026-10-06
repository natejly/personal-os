"""Branch in a new chat: POST /conversations/{id}/fork copies the live prefix up to a message into a new chat.

Run: PYTHONPATH=backend python -m pytest backend/tests/test_fork.py
Built on the test_runs harness (TestClient + a scripted llm.stream_chat).
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_runs as T  # noqa: E402  (sets the data dir and scripts llm.stream_chat)

from personal_os import app as app_mod  # noqa: E402
from personal_os import llm  # noqa: E402

client, j, check, drain = T.client, T.j, T.check, T.drain

try:
    import pytest
except ImportError:
    pytest = None
else:
    @pytest.fixture(scope="module", autouse=True)
    def _portal():  # type: ignore[no-untyped-def]
        with client:
            client.put("/settings", json={"autoLearn": False, "baseUrl": ""})
            yield

SEEN: list[list[dict[str, Any]]] = []


async def _recording(settings: dict[str, Any], model: str, messages: list[dict[str, Any]], *a: Any, **k: Any) -> Any:
    SEEN.append(messages)
    async for ev in T._scripted_stream(settings, model, messages, *a, **k):
        yield ev


def three_turns() -> str:
    llm.stream_chat = T._scripted_stream
    T.SCRIPT["chunks"] = ["ok"]
    T.SCRIPT["delay"] = 0.0
    cid = T.new_conv()
    for t in ("one", "two", "three"):
        j("POST", f"/conversations/{cid}/chat", {"content": t})
        drain(cid)
    return cid


def msgs(cid: str) -> list[dict[str, Any]]:
    return j("GET", f"/conversations/{cid}")["messages"]


def fork(cid: str, mid: str) -> Any:
    return client.post(f"/conversations/{cid}/fork", json={"message_id": mid})


def test_fork_copies_the_active_prefix_only() -> None:
    cid = three_turns()
    ms = msgs(cid)
    # Regenerate the last answer: the old one is superseded, the new variant active.
    var = app_mod.convos.begin_variant(ms[5]["id"])
    app_mod.convos.finish_message(var["id"], "ok v2", None, None)
    before = msgs(cid)
    r = fork(cid, var["id"])
    check(r.status_code == 200, f"fork answered {r.status_code} {r.text}")
    f = r.json()
    check(f["id"] != cid and f["title"].endswith("(branch)"), f"a new chat with a branch title, got {f['title']}")
    got = [(m["role"], m["content"]) for m in f["messages"]]
    check(got == [("user", "one"), ("assistant", "ok"), ("user", "two"), ("assistant", "ok"), ("user", "three"), ("assistant", "ok v2")], f"active rows in order, got {got}")
    mid = fork(cid, ms[3]["id"]).json()
    check([m["content"] for m in mid["messages"]] == ["one", "ok", "two", "ok"], "a mid-transcript fork stops at that message")
    check(not {m["id"] for m in f["messages"]} & {m["id"] for m in before}, "copies have fresh ids")
    check(all(not m.get("variant_of") and not m.get("variants") for m in f["messages"]), "no variant links come along")
    check(f["settings"]["forkedFrom"] == cid and f["settings"]["titleSource"] == "auto", "forkedFrom and an auto title")
    check(msgs(cid) == before, "the source chat is unchanged")


def test_fork_errors() -> None:
    cid = three_turns()
    ms = msgs(cid)
    check(fork(cid, "nope").status_code == 404, "an unknown message is 404")
    check(fork("nope", ms[0]["id"]).status_code == 404, "an unknown chat is 404")
    other = three_turns()
    check(fork(other, ms[0]["id"]).status_code == 404, "a message from another chat is 404")
    app_mod.convos.supersede_from(cid, ms[2]["id"])
    check(fork(cid, ms[3]["id"]).status_code == 409, "a superseded message is 409")
    app_mod.convos.update(other, {"settings": {"deskId": "d1"}})
    check(fork(other, msgs(other)[0]["id"]).status_code == 409, "a desk transcript cannot be branched")
    job = three_turns()
    app_mod.convos.update(job, {"settings": {"job_id": "j1"}})
    check(fork(job, msgs(job)[0]["id"]).status_code == 409, "a job transcript cannot be branched")


def test_taint_carries_and_grants_do_not() -> None:
    cid = three_turns()
    app_mod.convos.update(cid, {"settings": {"tainted": True, "taint_sources": ["fetch_url"], "skipPermissions": True,
                                             "planMode": "always", "effort": "high",
                                             "tools": {"gmail_send": "on", "web_search": "off"}}})
    f = fork(cid, msgs(cid)[1]["id"]).json()
    s = f["settings"]
    check(s["tainted"] is True and s["taint_sources"] == ["fetch_url"], f"taint carries over, got {s}")
    check("skipPermissions" not in s, "skipPermissions is reset to the default")
    check(s["planMode"] == "always" and s["effort"] == "high", "chat settings carry over")
    check(s["tools"] == {"web_search": "off"}, f"an always-this-chat grant does not apply in the fork, got {s['tools']}")
    with app_mod.db.tx() as c:
        n = c.execute("SELECT COUNT(*) AS n FROM approvals WHERE conversation_id=?", (f["id"],)).fetchone()["n"]
    check(n == 0, "no approvals are copied")


def test_sandbox_import_taints_the_fork() -> None:
    cid = three_turns()
    real = app_mod.sandboxes.holds_import
    app_mod.sandboxes.holds_import = lambda conv: conv == cid  # type: ignore[method-assign]
    try:
        f = fork(cid, msgs(cid)[1]["id"]).json()
    finally:
        app_mod.sandboxes.holds_import = real  # type: ignore[method-assign]
    check(f["settings"].get("tainted") is True and "fork:sandbox_import" in f["settings"]["taint_sources"],
          f"a held sandbox import taints the fork, got {f['settings']}")


def test_new_turn_in_the_fork_sees_the_copied_history() -> None:
    cid = three_turns()
    f = fork(cid, msgs(cid)[1]["id"]).json()
    SEEN.clear()
    llm.stream_chat = _recording
    try:
        j("POST", f"/conversations/{f['id']}/chat", {"content": "next"})
        drain(f["id"])
    finally:
        llm.stream_chat = T._scripted_stream
    sent = [m["content"] for m in SEEN[-1] if m["role"] in ("user", "assistant")]
    check(sent == ["one", "ok", "next"], f"the model saw the branch's history, got {sent}")
    check(len(msgs(cid)) == 6, "the source did not grow")
