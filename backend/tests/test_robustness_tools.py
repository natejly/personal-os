"""Robustness regressions in the tool layer: bounded sandbox output, empty exception messages, the stale outbox,
reserved desk folders by case, the taint gate, bounded HTTP reads and the local file tools."""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import httpx
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from personal_os import mac, microvm, outbox as outbox_mod, reach, sandbox, tools, verify  # noqa: E402
from personal_os.db import Database  # noqa: E402
from personal_os.outbox import Outbox  # noqa: E402
from personal_os.tools import Toolbox  # noqa: E402
from personal_os.workspace import Workspace, WorkspaceError  # noqa: E402


# ---- 1/2 sandbox output ----
def test_run_python_caps_runaway_output_and_survives_bad_bytes() -> None:
    r = sandbox.run_python('while True: print("x" * 4000)', timeout=20)
    assert r.get("truncated") and len(r["stdout"]) == 20_000 and not r["timed_out"]
    r = sandbox.run_python('import sys; sys.stdout.buffer.write(b"a\\xffb")')
    assert r["stdout"] == "a�b" and r["exit_code"] == 0


def test_run_python_timeout_keeps_partial_output() -> None:
    r = sandbox.run_python('import time\nprint("hi", flush=True)\ntime.sleep(10)', timeout=2)
    assert r["timed_out"] and r["stdout"] == "hi\n"


def test_microvm_run_kills_endless_output() -> None:
    p = microvm._run(["yes"], timeout=30, hard_cap=100_000, keep=1000)
    assert p.truncated and len(p.stdout) <= 1000  # type: ignore[attr-defined]
    with pytest.raises(subprocess.TimeoutExpired):
        microvm._run(["sleep", "5"], timeout=1)


# ---- 3/11 verify ----
class Silent(Exception):
    pass


def test_verify_check_survives_an_empty_exception_message() -> None:
    def boom() -> Any:
        raise Silent()
    out = verify.check("x", boom, delays=(), sleep=lambda s: None)
    assert out["status"] != verify.VERIFIED


def test_is_missing_trusts_a_status_over_the_text() -> None:
    class Resp:
        status = 503

    e = Exception("backend error 410 gone upstream")
    e.resp = Resp()  # type: ignore[attr-defined]
    assert not verify.is_missing(e)
    e.resp.status = 404  # type: ignore[attr-defined]
    assert verify.is_missing(e)
    assert verify.is_missing(Exception("404 not found"))


# ---- 3/4/5 outbox ----
class Clock:
    t = 1_000_000.0

    def __call__(self) -> float:
        return self.t


class FakeG:
    def __init__(self, box: list[Outbox], on_send: Any = None, exc: Exception | None = None):
        self.sent: list[str] = []
        self.box, self.on_send, self.exc = box, on_send, exc

    def gmail_send(self, to: str, subject: str, body: str, reply: Any) -> dict[str, Any]:
        if self.exc:
            raise self.exc
        self.sent.append(subject)
        if self.on_send:
            self.on_send(subject)
        return {"sent": "m1", "thread_id": "t1"}


def _box(g: Any, clock: Clock) -> Outbox:
    return Outbox(Database(tempfile.mkdtemp()), g, lambda: {"gmailSendHold": {"enabled": True, "seconds": 30}},
                  bounds=(0, 120), clock=clock)


def test_outbox_does_not_send_a_mail_held_across_a_sleep() -> None:
    clock, g = Clock(), FakeG([])
    box = _box(g, clock)
    row = box.queue("a@example.com", "old", "b")
    clock.t += 8 * 3600
    assert asyncio.run(box.run_due()) == 0
    assert g.sent == [] and box.get(row["id"])["status"] == "expired"


def test_outbox_delivers_one_row_at_a_time_so_undo_still_wins() -> None:
    clock = Clock()
    holder: list[Outbox] = []
    cancelled: dict[str, Any] = {}

    def on_send(subject: str) -> None:
        if subject == "first":  # the user hits Undo on the second while the first is in flight
            cancelled["row"] = holder[0].cancel(second["id"])
    g = FakeG(holder, on_send)
    box = _box(g, clock)
    holder.append(box)
    box.queue("a@example.com", "first", "b")
    second = box.queue("a@example.com", "second", "b")
    clock.t += 31
    asyncio.run(box.run_due())
    assert g.sent == ["first"] and cancelled["row"]["status"] == "cancelled"


def test_outbox_survives_an_empty_exception_message() -> None:
    clock = Clock()
    g = FakeG([], exc=Silent())
    box = _box(g, clock)
    r1 = box.queue("a@example.com", "one", "b")
    r2 = box.queue("a@example.com", "two", "b")
    clock.t += 31
    assert asyncio.run(box.run_due()) == 2
    assert box.get(r1["id"])["status"] == "failed" and box.get(r2["id"])["status"] == "failed"
    assert "Silent" in box.get(r1["id"])["error"]


# ---- 6 workspace ----
def test_reserved_desk_folders_are_reserved_in_any_case() -> None:
    ws = Workspace(Path(tempfile.mkdtemp()))
    ws.write("d1", "work/a.md", "hello")
    for rel in (".BASELINE/work/a.md", ".Baseline/x.md", ".TRASH/x.md"):
        with pytest.raises(WorkspaceError):
            ws.write("d1", rel, "evil")
    with pytest.raises(WorkspaceError):
        ws.trash("d1", ".BASELINE")
    with pytest.raises(WorkspaceError):
        ws.trash("d1", ".Trash/x")


# ---- 7 taint gate ----
class FakeSandboxes:
    def __init__(self, net: bool = False, setting: Any = False):
        self.net, self.setting = net, setting

    def networked(self, cid: str) -> bool:
        return self.net

    def reaches_out(self, cid: str) -> bool:
        return self.net

    def settings(self) -> dict[str, Any]:
        return {"sandboxNetwork": self.setting}

    def available(self) -> bool:
        return True


def _box_with(sb: Any) -> Toolbox:
    box = Toolbox(None, None, None, lambda: {}, sandboxes=sb)  # type: ignore[arg-type]
    box.specs["schedule_task"] = tools.ToolSpec("schedule_task", "", {}, None, "schedule", "schedules")  # type: ignore[arg-type]
    box.specs["sandbox_exec"] = tools.ToolSpec("sandbox_exec", "", {}, None, "sandbox", "executes")  # type: ignore[arg-type]
    return box


def test_taint_gate_covers_schedules_and_networked_sandboxes() -> None:
    tainted: dict[str, Any] = {"tainted": True, "conversation_id": "c"}
    clean: dict[str, Any] = {"conversation_id": "c"}
    box = _box_with(FakeSandboxes(net=True))
    assert box.gate("schedule_task", "on", tainted) == "ask"
    assert box.gate("schedule_task", "on", clean) == "on"
    assert box.gate("sandbox_exec", "on", tainted) == "ask"
    assert box.gate("sandbox_exec", "on", clean) == "on"
    assert _box_with(FakeSandboxes(setting=True)).gate("sandbox_exec", "on", tainted) == "ask"
    assert _box_with(FakeSandboxes()).gate("sandbox_exec", "on", tainted) == "on"  # no network: nothing to leak through
    # the allowlisting proxy still lets a command reach a host, so a tainted reply asks there too; "off" is no network
    assert _box_with(FakeSandboxes(setting="proxy")).gate("sandbox_exec", "on", tainted) == "ask"
    assert _box_with(FakeSandboxes(setting="off")).gate("sandbox_exec", "on", tainted) == "on"


def test_proposal_only_runs_refuse_a_networked_sandbox() -> None:
    box = _box_with(FakeSandboxes(net=True))
    out = asyncio.run(box.call("sandbox_exec", {"command": "curl x"}, {"proposal_only": True, "conversation_id": "c"}))
    assert "error" in out and "network" in out["error"]


# ---- 8/9/12 HTTP ----
def _patch_http(monkeypatch: pytest.MonkeyPatch, handler: Any, ips: list[str] | None = None) -> None:
    async def fake_resolve(host: str) -> list[str]:
        return ips or ["93.184.216.34"]
    monkeypatch.setattr(tools, "_resolve", fake_resolve)
    real = httpx.AsyncClient

    def client(*a: Any, **k: Any) -> httpx.AsyncClient:
        k.pop("transport", None)
        return real(*a, transport=httpx.MockTransport(handler), **k)
    monkeypatch.setattr(tools.httpx, "AsyncClient", client)


def _fetch(**kw: Any) -> Any:
    tb = Toolbox(None, None, None, lambda: {"readerFallback": False})  # type: ignore[arg-type]
    return asyncio.run(tb.specs["fetch_url"].fn({}, **kw))


def test_fetch_url_reads_a_capped_body_and_flags_truncation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(reach, "MAX_BODY", 100_000)
    monkeypatch.setattr(reach, "read_capped", _small_cap(reach.read_capped))

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/plain"}, content=b"word " * 400_000)
    _patch_http(monkeypatch, handler)
    out = _fetch(url="https://example.org/big", max_chars=2000)
    assert out["truncated"] is True and len(out["text"]) == 2000


def _small_cap(real: Any) -> Any:
    async def cap(r: httpx.Response, cap: int = 100_000) -> httpx.Response:
        return await real(r, cap)
    return cap


def test_fetch_url_truncated_flag_uses_the_real_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/plain"}, content=b"a" * 60_000)
    _patch_http(monkeypatch, handler)
    out = _fetch(url="https://example.org/t", max_chars=100_000)  # capped to 40000 internally
    assert len(out["text"]) == 40_000 and out["truncated"] is True


def test_open_pinned_falls_back_to_the_next_validated_ip(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req.url.host)
        if req.url.host == "2001:db8::1":
            raise httpx.ConnectError("unreachable")
        return httpx.Response(200, text="ok")
    _patch_http(monkeypatch, handler, ips=["2001:db8::1", "93.184.216.34"])
    out = _fetch(url="https://example.org/")
    assert out["status"] == 200 and seen == ["2001:db8::1", "93.184.216.34"]


# ---- 10 local files ----
@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    h = (tmp_path / "home").resolve()
    for d in ("Desktop", "Documents", "Downloads"):
        (h / d).mkdir(parents=True)
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setenv("PERSONAL_OS_DATA_DIR", str(tmp_path / "data"))
    return h


def test_move_into_a_folder_that_does_not_exist_yet(home: Path) -> None:
    src = home / "Downloads" / "r.pdf"
    src.write_text("x")
    out = mac.move_local("~/Downloads/r.pdf", "~/Documents/Receipts/")
    assert (home / "Documents" / "Receipts").is_dir() and (home / "Documents" / "Receipts" / "r.pdf").exists()
    assert out["path"].endswith("Receipts/r.pdf")


def test_write_local_rejects_non_string_and_create_never_replaces(home: Path) -> None:
    f = home / "Documents" / "n.txt"
    f.write_text("keep")
    with pytest.raises(ValueError):
        mac.write_local("~/Documents/n.txt", None, mode="overwrite")  # type: ignore[arg-type]
    assert f.read_text() == "keep"
    with pytest.raises(mac.LocalPathError):
        mac.write_local("~/Documents/n.txt", "new", mode="create")
    assert f.read_text() == "keep"
    # a file that appears after the exists() check is still not replaced (O_EXCL)
    with pytest.raises(mac.LocalPathError):
        mac._write_nofollow(f, "race", "create")
    assert f.read_text() == "keep"
