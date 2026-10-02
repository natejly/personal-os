"""MicroVM sandboxes: container lifecycle, path handling, and the tool surface.

Run: PYTHONPATH=backend backend/.venv/bin/python -m unittest personal_os.tests.test_microvm -v
No docker is needed: every test injects a fake runner and asserts on the argv the
manager would hand the runtime, because that argv IS the security posture
(--network none, --cap-drop ALL, paths as argv words and never interpolated).
"""
from __future__ import annotations

import asyncio
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Callable

from personal_os import microvm
from personal_os.microvm import DEFAULT_IMAGE, MAX_SANDBOXES, WORKSPACE, Sandboxes, guest_path
from personal_os.tools import Toolbox


def cp(argv: list[str], rc: int = 0, out: bytes = b"", err: bytes = b"") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(argv, rc, out, err)


class FakeRun:
    """Dispatches on the runtime subcommand (argv[1]); records every call."""

    def __init__(self, handlers: dict[str, Any] | None = None):
        self.calls: list[dict[str, Any]] = []
        self.handlers: dict[str, Any] = handlers or {}

    def __call__(self, argv: list[str], *, input: bytes | None = None, timeout: float = 60) -> subprocess.CompletedProcess:
        self.calls.append({"argv": argv, "input": input, "timeout": timeout})
        h = self.handlers.get(argv[1] if len(argv) > 1 else "")
        if callable(h):
            return h(argv)
        return h if h is not None else cp(argv, 0, b"", b"")

    def argv_for(self, sub: str) -> list[list[str]]:
        return [c["argv"] for c in self.calls if len(c["argv"]) > 1 and c["argv"][1] == sub]


def running(argv: list[str]) -> subprocess.CompletedProcess:
    if "{{.State.Running}}" in argv:
        return cp(argv, 0, b"true\n")
    if "{{.HostConfig.NetworkMode}}" in argv:
        return cp(argv, 0, b"none\n")
    return cp(argv, 0, b"")


def absent(argv: list[str]) -> subprocess.CompletedProcess:
    return cp(argv, 1, b"", b"Error: No such object\n")


def mgr(run: FakeRun, **cfg: Any) -> Sandboxes:
    return Sandboxes(lambda: cfg, runner=run)


class TestGuestPath(unittest.TestCase):
    def test_relative_paths_live_in_workspace(self) -> None:
        self.assertEqual(guest_path("a/b.txt"), f"{WORKSPACE}/a/b.txt")
        self.assertEqual(guest_path(""), WORKSPACE)
        self.assertEqual(guest_path(None), WORKSPACE)
        self.assertEqual(guest_path("."), WORKSPACE)

    def test_absolute_and_dotdot_normalise(self) -> None:
        self.assertEqual(guest_path("/etc/hosts"), "/etc/hosts")
        self.assertEqual(guest_path("../tmp/x"), "/tmp/x")
        self.assertEqual(guest_path("a/../b.txt"), f"{WORKSPACE}/b.txt")


class TestAvailability(unittest.TestCase):
    def test_missing_binary_is_unavailable_without_calling_the_runner(self) -> None:
        run = FakeRun()
        m = mgr(run, sandboxRuntime="definitely-not-a-binary-xyz")
        self.assertFalse(m.available())
        self.assertEqual(run.calls, [])

    def test_available_pings_once_then_caches(self) -> None:
        run = FakeRun({"info": cp([], 0, b"29.5.2\n")})
        m = mgr(run, sandboxRuntime=sys.executable)  # any real file on PATH-resolvable location
        self.assertTrue(m.available())
        self.assertTrue(m.available())
        self.assertEqual(len(run.argv_for("info")), 1, "second check must come from the TTL cache")


class TestLifecycle(unittest.TestCase):
    def test_create_is_isolated_by_default(self) -> None:
        run = FakeRun({"inspect": absent, "exec": cp([], 0, b"/bin/bash\n")})
        m = mgr(run)
        name = m.ensure("conv1")
        self.assertTrue(name.startswith("pos-sbx-"))
        (create,) = run.argv_for("run")
        for flag in (["--network", "none"], ["--cap-drop", "ALL"], ["--security-opt", "no-new-privileges"],
                     ["--pids-limit", "256"], ["--label", "personal-os.sandbox=1"], ["-w", WORKSPACE]):
            self.assertIn(flag[0], create)
            self.assertEqual(create[create.index(flag[0]) + 1], flag[1], f"{flag[0]} misconfigured")
        self.assertEqual(create[-3:], [DEFAULT_IMAGE, "sleep", "infinity"])
        self.assertEqual(m._shell[name], "bash")
        self.assertFalse(m.networked("conv1"))

    def test_network_setting_attaches_the_network(self) -> None:
        run = FakeRun({"inspect": absent})
        m = mgr(run, sandboxNetwork=True)
        m.ensure("conv1")
        (create,) = run.argv_for("run")
        self.assertNotIn("--network", create)
        self.assertTrue(m.networked("conv1"))

    def test_stopped_container_is_restarted_and_facts_recovered(self) -> None:
        def inspect(argv: list[str]) -> subprocess.CompletedProcess:
            if "{{.State.Running}}" in argv:
                return cp(argv, 0, b"false\n")
            return cp(argv, 0, b"bridge\n")  # NetworkMode: survived an app restart with network
        run = FakeRun({"inspect": inspect, "exec": cp([], 1, b"")})
        m = mgr(run)
        name = m.ensure("conv1")
        self.assertEqual(len(run.argv_for("start")), 1)
        self.assertEqual(run.argv_for("run"), [], "an existing container is never re-created")
        self.assertTrue(m.networked("conv1"))
        self.assertEqual(m._shell[name], "sh", "no bash in this image")

    def test_reap_removes_the_least_recently_used(self) -> None:
        names = " ".join(f"pos-sbx-old{i}" for i in range(MAX_SANDBOXES)).encode()
        run = FakeRun({"inspect": absent, "ps": cp([], 0, names)})
        m = mgr(run)
        for i in range(MAX_SANDBOXES):
            m._last[f"pos-sbx-old{i}"] = 100.0 + i
        m.ensure("conv-new")
        (removed,) = run.argv_for("rm")
        self.assertEqual(removed[-1], "pos-sbx-old0", "oldest goes first")

    def test_reset_forgets_state(self) -> None:
        run = FakeRun()
        m = mgr(run)
        name = m._name("conv1")
        m._last[name] = m._net[name] = 1
        out = m.reset("conv1")
        self.assertTrue(out["reset"])
        self.assertEqual(run.argv_for("rm")[0][-1], name)
        self.assertNotIn(name, m._last)
        self.assertNotIn(name, m._net)


class TestExecAndFiles(unittest.TestCase):
    def setUp(self) -> None:
        self.run = FakeRun({"inspect": running, "exec": self._exec})
        self.exec_handler: Callable[[list[str]], subprocess.CompletedProcess] = lambda argv: cp(argv, 0, b"")
        self.m = mgr(self.run)

    def _exec(self, argv: list[str]) -> subprocess.CompletedProcess:
        if "command -v bash" in argv:
            return cp(argv, 0, b"/bin/bash\n")
        return self.exec_handler(argv)

    def test_exec_runs_under_guest_timeout(self) -> None:
        self.exec_handler = lambda argv: cp(argv, 0, b"hi\n", b"")
        out = self.m.exec("conv1", "echo hi", timeout=9999)
        argv = self.run.argv_for("exec")[-1]
        i = argv.index("timeout")
        self.assertEqual(argv[i:i + 4], ["timeout", "-k", "5", "600"], "timeout clamped and enforced in the guest")
        self.assertEqual(argv[-2:], ["-c", "echo hi"])
        self.assertIn("bash", argv)
        self.assertEqual(out["stdout"], "hi\n")
        self.assertEqual(out["exit_code"], 0)
        self.assertFalse(out["timed_out"])
        self.assertNotIn("network", out)

    def test_exec_reports_guest_timeout(self) -> None:
        self.exec_handler = lambda argv: cp(argv, 124, b"partial", b"")
        out = self.m.exec("conv1", "sleep 999", timeout=5)
        self.assertTrue(out["timed_out"])
        self.assertIn("Timed out after 5s", out["stderr"])

    def test_networked_exec_is_flagged(self) -> None:
        self.m._net[self.m._name("conv1")] = True
        out = self.m.exec("conv1", "curl example.com")
        self.assertTrue(out.get("network"))

    def test_write_passes_content_on_stdin_and_path_as_argv(self) -> None:
        out = self.m.write_file("conv1", "notes/a.md", "hello ✨")
        call = next(c for c in self.run.calls if c["input"] is not None)
        self.assertEqual(call["input"], "hello ✨".encode())
        self.assertEqual(call["argv"][-1], f"{WORKSPACE}/notes/a.md", "path is an argv word, not shell text")
        self.assertIn('cat > "$1"', call["argv"][-3])
        self.assertEqual(out["written"], f"{WORKSPACE}/notes/a.md")

    def test_append_uses_append_redirection(self) -> None:
        self.m.write_file("conv1", "a.md", "x", append=True)
        call = next(c for c in self.run.calls if c["input"] is not None)
        self.assertIn('cat >> "$1"', call["argv"][-3])

    def test_write_needs_a_file_name(self) -> None:
        with self.assertRaises(microvm.SandboxError):
            self.m.write_file("conv1", ".", "x")

    def test_read_text_window(self) -> None:
        body = b"0123456789"

        def handler(argv: list[str]) -> subprocess.CompletedProcess:
            if 'wc -c < "$1"' in argv:
                return cp(argv, 0, b"10\n")
            return cp(argv, 0, body[3:8])
        self.exec_handler = handler
        out = self.m.read_file("conv1", "data.txt", offset=3, length=5)
        self.assertEqual(out["text"], "34567")
        self.assertEqual(out["total_bytes"], 10)
        self.assertTrue(out["truncated"])
        tail = next(a for a in self.run.argv_for("exec") if any("tail -c" in x for x in a))
        self.assertIn("tail -c +4 -- \"$1\" | head -c 5", tail)

    def test_read_image_returns_inline_data(self) -> None:
        png = b"\x89PNG fake"

        def handler(argv: list[str]) -> subprocess.CompletedProcess:
            if 'wc -c < "$1"' in argv:
                return cp(argv, 0, b"9\n")
            return cp(argv, 0, png)
        self.exec_handler = handler
        out = self.m.read_file("conv1", "plot.png")
        self.assertEqual(out["images"][0]["name"], "plot.png")
        self.assertTrue(out["images"][0]["data"].startswith("data:image/png;base64,"))

    def test_list_files_parses_find_output(self) -> None:
        self.exec_handler = lambda argv: cp(argv, 0, f"d\t0\t{WORKSPACE}\nd\t96\t{WORKSPACE}/notes\nf\t42\t{WORKSPACE}/notes/a.md\n".encode())
        out = self.m.list_files("conv1")
        self.assertEqual(out["entries"], [{"type": "dir", "bytes": 96, "path": f"{WORKSPACE}/notes"},
                                          {"type": "file", "bytes": 42, "path": f"{WORKSPACE}/notes/a.md"}])


class _Docs:
    def get(self, id: str) -> dict[str, Any] | None:
        return {"id": id, "name": "report.pdf", "text": "the extracted text"} if id == "doc_1" else None


class TestToolSurface(unittest.TestCase):
    def _toolbox(self, run: FakeRun, **cfg: Any) -> Toolbox:
        return Toolbox(None, None, _Docs(), lambda: cfg, sandboxes=Sandboxes(lambda: cfg, runner=run))  # type: ignore[arg-type]

    def test_tools_registered_as_executes_tier(self) -> None:
        tb = self._toolbox(FakeRun())
        for name in ("sandbox_exec", "sandbox_write_file", "sandbox_read_file", "sandbox_list_files",
                     "sandbox_put_document", "sandbox_reset"):
            self.assertIn(name, tb.specs)
            self.assertEqual(tb.specs[name].danger, "executes")
            self.assertEqual(tb.specs[name].group, "sandbox")
            self.assertFalse(tb.specs[name].taints, "taint is decided per call, by whether the sandbox has network")

    def test_unavailable_runtime_hides_the_tools_from_schemas(self) -> None:
        tb = self._toolbox(FakeRun(), sandboxRuntime="definitely-not-a-binary-xyz")
        modes = {n: "on" for n in tb.specs}
        self.assertFalse([s for s in tb.schemas(modes) if s["function"]["name"].startswith("sandbox_")])
        self.assertIn("run_python", [s["function"]["name"] for s in tb.schemas(modes)])

    def test_networked_exec_taints_the_run(self) -> None:
        run = FakeRun({"inspect": absent})
        tb = self._toolbox(run, sandboxNetwork=True)
        ctx: dict[str, Any] = {"conversation_id": "c1", "tainted": False}
        out = asyncio.run(tb.call("sandbox_exec", {"command": "curl example.com"}, ctx))
        self.assertNotIn("error", out if isinstance(out, dict) else {})
        self.assertTrue(ctx["tainted"])
        self.assertIn("sandbox_exec", ctx["taint_sources"])

    def test_isolated_exec_does_not_taint(self) -> None:
        run = FakeRun({"inspect": absent})
        tb = self._toolbox(run)
        ctx: dict[str, Any] = {"conversation_id": "c1", "tainted": False}
        asyncio.run(tb.call("sandbox_exec", {"command": "echo hi"}, ctx))
        self.assertFalse(ctx["tainted"])

    def test_put_document_writes_the_extracted_text(self) -> None:
        run = FakeRun({"inspect": running})
        tb = self._toolbox(run)
        ctx = {"conversation_id": "c1"}
        out = asyncio.run(tb.call("sandbox_put_document", {"document_id": "doc_1"}, ctx))
        self.assertEqual(out["document"], "report.pdf")
        self.assertEqual(out["written"], f"{WORKSPACE}/report.pdf.txt", "non-text names get a .txt suffix")
        call = next(c for c in run.calls if c["input"] is not None)
        self.assertEqual(call["input"], b"the extracted text")

    def test_an_imported_document_taints_later_commands_until_reset(self) -> None:
        run = FakeRun({"inspect": running})
        tb = self._toolbox(run)
        ctx: dict[str, Any] = {"conversation_id": "c1", "tainted": False, "taint_sources": []}
        asyncio.run(tb.call("sandbox_put_document", {"document_id": "doc_1"}, ctx))
        self.assertTrue(ctx["tainted"])
        self.assertIn("sandbox_put_document", ctx["taint_sources"])
        ctx["tainted"] = False
        asyncio.run(tb.call("sandbox_exec", {"command": "cat report.pdf.txt"}, ctx))
        self.assertTrue(ctx["tainted"], "printing the imported file taints even with network off")
        asyncio.run(tb.call("sandbox_reset", {}, {"conversation_id": "c1"}))
        ctx["tainted"] = False
        asyncio.run(tb.call("sandbox_exec", {"command": "echo hi"}, ctx))
        self.assertFalse(ctx["tainted"])

    def test_import_mark_survives_a_new_manager(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            run = FakeRun()
            first = Sandboxes(lambda: {}, runner=run, import_dir=Path(d))
            first.note_import("c1")
            second = Sandboxes(lambda: {}, runner=run, import_dir=Path(d))
            self.assertTrue(second.holds_import("c1"))
            second.reset("c1")
            self.assertFalse(second.holds_import("c1"))

    def test_put_document_unknown_id_is_a_tool_error(self) -> None:
        tb = self._toolbox(FakeRun({"inspect": running}))
        out = asyncio.run(tb.call("sandbox_put_document", {"document_id": "nope"}, {"conversation_id": "c1"}))
        self.assertIn("error", out)


if __name__ == "__main__":
    unittest.main()
