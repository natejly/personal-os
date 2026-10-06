"""Programmatic tool calling (toolbridge.py): a sandboxed run_python script calling app tools over a Unix socket.
The script runs under the real Seatbelt profile; the tools are small stand-ins registered on a real Toolbox, so the
gate, taint and call paths are the production ones. Skipped with a message where sandbox-exec is absent."""
from __future__ import annotations

import asyncio
import os
import shutil
import socket
import sys
import time
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from personal_os import sandbox, toolbridge  # noqa: E402
from personal_os.db import Database  # noqa: E402
from personal_os.tools import ToolSpec, Toolbox, _obj  # noqa: E402
from personal_os.working import ToolResults  # noqa: E402

HAVE_SEATBELT = sys.platform == "darwin" and bool(shutil.which("sandbox-exec"))
needs_seatbelt = pytest.mark.skipif(not HAVE_SEATBELT, reason="sandbox-exec is not available: bridge assertions skipped")


class Rig:
    def __init__(self, tmp: Path, modes: dict[str, str] | None = None):
        self.root = tmp / "proj"
        self.root.mkdir()
        (self.root / "a.py").write_text("x = 1  # TODO one\n")
        (self.root / "b.py").write_text("y = 2\n# TODO two\n# TODO three\n")
        (self.root / "c.txt").write_text("nothing\n")
        self.db = Database(tmp / "data")
        with self.db.tx() as c:
            c.execute("INSERT INTO conversations(id, title, model, created_at, updated_at) VALUES('c1','t','m',0,0)")
        self.tb = Toolbox(None, None, None, lambda: {}, results=ToolResults(self.db))  # type: ignore[arg-type]
        self.seen: list[tuple[str, dict[str, Any]]] = []
        self.ctx: dict[str, Any] = {"conversation_id": "c1", "message_id": None, "tainted": False, "taint_sources": [],
                                    "modes": dict(modes or {})}
        self._stubs()

    def _stubs(self) -> None:
        R = self.tb.specs.__setitem__

        async def fs_glob(ctx: dict[str, Any], pattern: str, root: str) -> Any:
            self.seen.append(("fs_glob", {"pattern": pattern}))
            return {"files": sorted(str(p) for p in Path(root).glob(pattern))}

        async def fs_grep(ctx: dict[str, Any], pattern: str, root: str) -> Any:
            self.seen.append(("fs_grep", {"pattern": pattern}))
            hits = [{"file": str(p), "line": i + 1, "text": ln} for p in sorted(Path(root).rglob("*"))
                    if p.is_file() for i, ln in enumerate(p.read_text().splitlines()) if pattern in ln]
            return {"matches": hits}

        async def fs_edit(ctx: dict[str, Any], path: str, old: str, new: str) -> Any:
            self.seen.append(("fs_edit", {"path": path}))
            p = Path(path)
            p.write_text(p.read_text().replace(old, new))
            return {"edited": path}

        async def web_search(ctx: dict[str, Any], query: str) -> Any:
            return {"results": [{"title": "t"}]}

        async def shell_stub(ctx: dict[str, Any], command: str) -> Any:
            self.seen.append(("shell_run", {"command": command}))
            return {"ran": True}

        for name, fn, dang in (("fs_glob", fs_glob, "safe"), ("fs_grep", fs_grep, "safe"), ("fs_edit", fs_edit, "writes"),
                               ("web_search", web_search, "network")):
            R(name, ToolSpec(name, name, _obj({}, []), fn, "files", dang, taints=(name == "web_search")))
        R("shell_run", ToolSpec("shell_run", "x", _obj({}, []), shell_stub, "shell", "executes"))

    async def run(self, code: str, tools: list[str], timeout: int = 20) -> Any:
        return await self.tb.call("run_python", {"code": code, "tools": tools, "timeout": timeout}, self.ctx)


@pytest.fixture
def rig(tmp_path: Path) -> Rig:
    return Rig(tmp_path)


GREP_SCRIPT = """
import grain_tools, collections
files = grain_tools.call("fs_glob", pattern="*.py", root={root!r})["files"]
by = collections.defaultdict(list)
for m in grain_tools.call("fs_grep", pattern="TODO", root={root!r})["matches"]:
    by[m["file"].split("/")[-1]].append(m["line"])
for f in sorted(by):
    print(f, by[f])
print("globbed", len(files))
"""


@needs_seatbelt
def test_script_globs_and_greps_through_the_bridge(rig: Rig) -> None:
    out = asyncio.run(rig.run(GREP_SCRIPT.format(root=str(rig.root)), ["fs_glob", "fs_grep"]))
    assert out["exit_code"] == 0, out
    assert out["stdout"].splitlines() == ["a.py [1]", "b.py [2, 3]", "globbed 2"]
    assert out["bridged_calls"] == 2 and [n for n, _ in rig.seen] == ["fs_glob", "fs_grep"]
    assert not os.path.exists(rig.root / "grain_tools.py")  # the client lives in the run's temp dir only


@needs_seatbelt
def test_a_tool_not_offered_or_never_bridgeable_is_refused(rig: Rig) -> None:
    code = """
import grain_tools
for name in ("shell_run", "fs_edit", "agent_spawn", "nope"):
    try:
        grain_tools.call(name, command="ls")
        print(name, "RAN")
    except grain_tools.ToolError as e:
        print(name, "refused")
print(grain_tools.call("fs_glob", pattern="*.txt", root=%r)["files"][0].split("/")[-1])
""" % str(rig.root)
    out = asyncio.run(rig.run(code, ["fs_glob"]))  # fs_edit exists and is allowed, but this script did not ask for it
    assert out["stdout"].splitlines() == ["shell_run refused", "fs_edit refused", "agent_spawn refused", "nope refused", "c.txt"]
    assert out["bridged_calls"] == 1
    assert [n for n, _ in rig.seen] == ["fs_glob"]


def test_never_bridged_tools_are_dropped_from_the_request_with_a_reason(rig: Rig) -> None:
    names, why = toolbridge.offered(rig.tb, rig.ctx, ["shell_run", "agent_spawn", "workflow_run", "gmail_send", "fs_grep"],
                                    rig.ctx["modes"])
    assert names == ["fs_grep"] and set(why) == {"shell_run", "agent_spawn", "workflow_run", "gmail_send"}
    out = asyncio.run(rig.run("print(1)", ["shell_run"]))
    assert "None of the requested tools" in out["error"] and not rig.seen


def test_an_off_tool_is_refused(tmp_path: Path) -> None:
    r = Rig(tmp_path, modes={"fs_grep": "off", "fs_glob": "on"})
    names, why = toolbridge.offered(r.tb, r.ctx, ["fs_grep", "fs_glob"], r.ctx["modes"])
    assert names == ["fs_glob"] and "turned off" in why["fs_grep"]
    # and even a Bridge built by hand re-checks the mode on every call
    b = toolbridge.Bridge(r.tb, r.ctx, ["fs_grep"], r.ctx["modes"])
    res = asyncio.run(b.handle("fs_grep", {"pattern": "x", "root": str(r.root)}))
    assert res["ok"] is False and "turned off" in res["error"] and not r.seen


@needs_seatbelt
def test_the_call_cap_is_fifty(rig: Rig) -> None:
    code = """
import grain_tools
ok = 0
for i in range(55):
    try:
        grain_tools.call("fs_glob", pattern="*.py", root=%r)
        ok += 1
    except grain_tools.ToolError as e:
        print("stopped:", e)
        break
print("ok", ok)
""" % str(rig.root)
    out = asyncio.run(rig.run(code, ["fs_glob"], timeout=60))
    assert "ok 50" in out["stdout"] and "call cap" in out["stdout"] and out["bridged_calls"] == 50
    assert len(rig.seen) == 50


@needs_seatbelt
def test_an_ask_tool_parks_the_script_until_approved_and_its_clock_stops(tmp_path: Path) -> None:
    r = Rig(tmp_path, modes={"fs_edit": "ask"})
    asked: list[tuple[str, bool]] = []
    gate = asyncio.Event()

    async def approve(name: str, args: dict[str, Any], forced: bool) -> bool:
        asked.append((name, forced))
        await gate.wait()
        return True
    r.ctx["bridge_approve"] = approve
    code = f"""
import grain_tools
print(grain_tools.call("fs_edit", path={str(r.root / 'c.txt')!r}, old="nothing", new="something"))
"""

    async def go() -> Any:
        task = asyncio.create_task(r.run(code, ["fs_edit"], timeout=3))
        for _ in range(100):
            if asked:
                break
            await asyncio.sleep(0.05)
        assert asked == [("fs_edit", False)] and not r.seen and not task.done()
        await asyncio.sleep(4.5)  # longer than the script's 3 s timeout: parked time does not count
        assert not task.done()
        gate.set()
        return await task
    out = asyncio.run(go())
    assert out["timed_out"] is False and out["exit_code"] == 0, out
    assert (r.root / "c.txt").read_text() == "something\n" and [n for n, _ in r.seen] == ["fs_edit"]


@needs_seatbelt
def test_a_declined_ask_never_runs_and_no_card_means_refused(tmp_path: Path) -> None:
    r = Rig(tmp_path, modes={"fs_edit": "ask"})
    code = f"""
import grain_tools
try:
    grain_tools.call("fs_edit", path={str(r.root / 'c.txt')!r}, old="nothing", new="x")
except grain_tools.ToolError as e:
    print("refused:", e)
"""

    async def no(name: str, args: dict[str, Any], forced: bool) -> bool:
        return False
    r.ctx["bridge_approve"] = no
    out = asyncio.run(r.run(code, ["fs_edit"]))
    assert "declined" in out["stdout"] and not r.seen
    del r.ctx["bridge_approve"]  # nobody to ask (a background run): refused, not run
    out = asyncio.run(r.run(code, ["fs_edit"]))
    assert "nobody to ask" in out["stdout"] and not r.seen
    r.ctx["bridge_approve"] = no
    r.ctx["proposal_only"] = True
    assert "nobody to ask" in asyncio.run(r.run(code, ["fs_edit"]))["stdout"]
    assert (r.root / "c.txt").read_text() == "nothing\n"


def test_taint_upgrades_on_to_ask_for_bridged_calls_too(tmp_path: Path) -> None:
    """The gate is Toolbox.gate: an alwaysAsk tool in a tainted run asks even when its mode is on."""
    r = Rig(tmp_path)
    r.tb.specs["fs_edit"].danger = "external"  # stand-in: any external-tier tool the gate upgrades
    r.tb.settings = lambda: {"alwaysAsk": ["fs_edit"]}
    r.ctx["tainted"] = True
    asked: list[str] = []

    async def approve(name: str, args: dict[str, Any], forced: bool) -> bool:
        asked.append(f"{name}:{forced}")
        return False
    b = toolbridge.Bridge(r.tb, r.ctx, ["fs_edit"], {"fs_edit": "on"}, approve)
    res = asyncio.run(b.handle("fs_edit", {"path": str(r.root / "a.py"), "old": "1", "new": "9"}))
    assert asked == ["fs_edit:True"] and res["ok"] is False


@needs_seatbelt
def test_bridged_results_taint_the_run(rig: Rig) -> None:
    out = asyncio.run(rig.run('import grain_tools\nprint(grain_tools.call("web_search", query="q")["results"][0]["title"])', ["web_search"]))
    assert out["stdout"].strip() == "t"
    assert rig.ctx["tainted"] is True


def test_stdout_is_kept_as_forty_percent_head_and_sixty_tail() -> None:
    b = toolbridge.Bridge(None, {}, [])  # type: ignore[arg-type]
    short = "x" * 50_000
    assert b.shape_stdout(short) == short and b.full_stdout is None
    text = "H" * 30_000 + "M" * 100_000 + "T" * 30_000
    shaped = b.shape_stdout(text)
    assert b.full_stdout == text
    head, rest = shaped.split("\n[... ", 1)
    marker, tail = rest.split(" ...]\n", 1)
    assert len(head) == 20_000 and len(tail) == 30_000 and marker == "110000 characters omitted"
    assert set(head) == {"H"} and set(tail) == {"T"}


@needs_seatbelt
def test_big_stdout_is_shaped_and_spilled_behind_a_handle(rig: Rig) -> None:
    out = asyncio.run(rig.run("print('A' * 20000)\nprint('B' * 100000)\nprint('C' * 20000)", ["fs_glob"]))
    assert len(out["stdout"]) <= toolbridge.STDOUT_KEEP + 100 and out["stdout"].startswith("A" * 1000)
    assert out["stdout"].rstrip().endswith("C" * 1000) and "characters omitted" in out["stdout"]
    full = rig.tb.results.read("c1", out["result_id"], offset=0, limit=200_000)
    assert full["total_chars"] >= 140_000 and "read_tool_result" in out["note"]


@needs_seatbelt
def test_stderr_is_kept_to_ten_kb(rig: Rig) -> None:
    out = asyncio.run(rig.run("import sys\nsys.stderr.write('E' * 30000)", ["fs_glob"]))
    assert len(out["stderr"]) == toolbridge.STDERR_KEEP


@needs_seatbelt
def test_the_profile_still_denies_the_network_and_only_the_socket_is_reachable(rig: Rig, tmp_path: Path) -> None:
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    import tempfile
    other = Path(tempfile.mkdtemp(dir="/tmp")) / "o.sock"  # sun_path is ~104 bytes: pytest's tmp_path is too long
    usrv = socket.socket(socket.AF_UNIX)
    usrv.bind(str(other))
    usrv.listen(1)
    code = f"""
import socket
for fam, addr in ((socket.AF_INET, ("127.0.0.1", {srv.getsockname()[1]})), (socket.AF_UNIX, {str(other)!r})):
    s = socket.socket(fam)
    try:
        s.connect(addr)
        print("connected", fam)
    except OSError as e:
        print("blocked", fam.name)
"""
    try:
        out = asyncio.run(rig.run(code, ["fs_glob"]))
    finally:
        srv.close()
        usrv.close()
    assert out["stdout"].splitlines() == ["blocked AF_INET", "blocked AF_UNIX"], out


@needs_seatbelt
def test_timeout_without_approvals_still_kills_the_script(rig: Rig) -> None:
    t0 = time.time()
    out = asyncio.run(rig.run("import time\ntime.sleep(60)", ["fs_glob"], timeout=1))
    assert out["timed_out"] is True and time.time() - t0 < 8


@needs_seatbelt
def test_run_python_without_tools_is_unchanged(rig: Rig) -> None:
    out = asyncio.run(rig.tb.call("run_python", {"code": "print(6*7)"}, rig.ctx))
    assert out["stdout"].strip() == "42" and "bridged_calls" not in out
    out = asyncio.run(rig.tb.call("run_python", {"code": "import grain_tools"}, rig.ctx))
    assert out["exit_code"] != 0  # the client is only on the path of a bridged run


def test_schema_advertises_tools_and_the_socket_dir_is_cleaned_up(rig: Rig) -> None:
    props = rig.tb.specs["run_python"].parameters["properties"]
    assert props["tools"]["type"] == "array"

    async def go() -> str:
        b = await toolbridge.Bridge(rig.tb, rig.ctx, ["fs_glob"]).start()
        path = b.socket_path
        assert os.path.exists(path) and len(path) < 100
        await b.stop()
        return path
    assert not os.path.exists(asyncio.run(go()))


def test_profile_with_a_socket_allows_exactly_that_path(tmp_path: Path) -> None:
    p = sandbox._mac_profile(str(tmp_path), sys.executable, str(tmp_path / "b.sock"))
    assert "(deny network*)" in p and "unix-socket" in p and "b.sock" in p
    assert "unix-socket" not in sandbox._mac_profile(str(tmp_path), sys.executable)


def test_permission_rules_apply_to_bridged_calls(tmp_path: Path) -> None:
    """A deny rule refuses a bridged call, and an ask rule cards an `on` tool, as they would a direct call."""
    r = Rig(tmp_path)
    r.ctx["settings"] = {"permissionRules": {"deny": [f"Edit({r.root}/a.py)"], "ask": [f"Edit({r.root}/b.py)"]}}
    asked: list[str] = []

    async def approve(name: str, args: dict[str, Any], forced: bool) -> bool:
        asked.append(args["path"])
        return False
    b = toolbridge.Bridge(r.tb, r.ctx, ["fs_edit"], {"fs_edit": "on"}, approve)
    res = asyncio.run(b.handle("fs_edit", {"path": str(r.root / "a.py"), "old": "1", "new": "9"}))
    assert res["ok"] is False and "refused" in res["error"] and not asked
    res = asyncio.run(b.handle("fs_edit", {"path": str(r.root / "b.py"), "old": "2", "new": "9"}))
    assert res["ok"] is False and asked == [str(r.root / "b.py")] and not r.seen


@needs_seatbelt
def test_big_stdout_of_a_tainted_run_is_stored_untrusted(rig: Rig) -> None:
    rig.ctx["tainted"] = True
    out = asyncio.run(rig.run("print('A' * 60000)", ["fs_glob"]))
    with rig.db.tx() as c:
        shape = c.execute("SELECT shape FROM tool_results WHERE id=?", (out["result_id"],)).fetchone()[0]
    assert '"untrusted": true' in shape
