"""Persistent, checkpointable sandboxes (microvm.py). An in-memory docker simulator stands in for the CLI."""
from __future__ import annotations

import os
import subprocess
import sys
import time
import types
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from personal_os import llm, microvm  # noqa: E402
from personal_os.microvm import CKPT_REPO, MAX_CKPTS, SandboxError, Sandboxes  # noqa: E402
from personal_os.tools import Toolbox  # noqa: E402


def cp(rc: int = 0, out: str = "", err: str = "") -> "subprocess.CompletedProcess[bytes]":
    return subprocess.CompletedProcess([], rc, out.encode(), err.encode())


def iso(dt: datetime, nanos: bool = True) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S") + (".123456789Z" if nanos else "Z")


class FakeDocker:
    def __init__(self) -> None:
        self.argv: list[list[str]] = []
        self.containers: dict[str, dict[str, Any]] = {}
        self.images: dict[str, str] = {}   # tag -> created string
        self.commit_error = ""
        self.clock = 0

    def __call__(self, argv: list[str], *, input: bytes | None = None, timeout: float = 60) -> "subprocess.CompletedProcess[bytes]":
        self.argv.append(list(argv))
        cmd = argv[1]
        if cmd == "info":
            return cp(0, "24.0")
        if cmd == "inspect":
            name = argv[-1]
            c = self.containers.get(name)
            if not c:
                return cp(1, "", "Error: No such object")
            fmt = argv[3]
            if "FinishedAt" in fmt:
                return cp(0, f"{'true' if c['running'] else 'false'} {c['finished']}")
            if "Running" in fmt:
                return cp(0, "true" if c["running"] else "false")
            if "NetworkMode" in fmt:
                return cp(0, "default" if c["net"] else "none")
        if cmd == "ps":
            running_only = "-a" not in argv
            return cp(0, "\n".join(n for n, c in self.containers.items() if c["running"] or not running_only))
        if cmd == "run":
            name = argv[argv.index("--name") + 1]
            image = argv[-3]
            self.containers[name] = {"running": True, "finished": "0001-01-01T00:00:00Z", "image": image,
                                     "net": "--network" not in argv, "argv": argv}
            return cp(0, "cid")
        if cmd == "start":
            self.containers[argv[2]]["running"] = True
            return cp(0)
        if cmd == "stop":
            self.containers[argv[-1]]["running"] = False
            return cp(0)
        if cmd == "rm":
            self.containers.pop(argv[-1], None)
            return cp(0)
        if cmd == "exec":
            return cp(0, "/usr/bin/bash")
        if cmd == "commit":
            if self.commit_error:
                return cp(1, "", f"Usage: docker commit\nError response from daemon: {self.commit_error}")
            self.clock += 1
            self.images[argv[-1]] = f"2026-10-01 12:00:{self.clock:02d} +0000 UTC"
            return cp(0, "sha256:abc")
        if cmd == "images":
            repo = argv[2]
            rows = [(t.split(":", 1)[1], c) for t, c in self.images.items() if t.split(":", 1)[0] == repo]
            rows.sort(key=lambda r: r[1], reverse=True)
            return cp(0, "\n".join(f"{t}\t{c}" for t, c in rows))
        if cmd == "rmi":
            self.images.pop(argv[-1], None)
            return cp(0)
        raise AssertionError(argv)

    def cmds(self, name: str) -> list[list[str]]:
        return [a for a in self.argv if a[1] == name]


def make(settings: dict[str, Any] | None = None) -> tuple[Sandboxes, FakeDocker]:
    d = FakeDocker()
    s = {"sandboxNetwork": False, **(settings or {})}
    sb = Sandboxes(lambda: s, runner=d)
    sb._settings = s  # type: ignore[attr-defined]
    return sb, d


def test_slug_sanitising() -> None:
    assert microvm.ckpt_slug("Before PIP install!") == "before-pip-install-"
    assert microvm.ckpt_slug("..hidden") == "hidden"
    assert microvm.ckpt_slug("x" * 80) == "x" * 40
    assert microvm.ckpt_slug("").startswith("ckpt-") and microvm.ckpt_slug("///").startswith("--") is False


def test_shutdown_stops_running_and_never_removes() -> None:
    sb, d = make()
    sb.ensure("c1")
    sb.ensure("c2")
    d.containers[sb._name("c2")]["running"] = False
    sb.shutdown()
    assert [a[-1] for a in d.cmds("stop")] == [sb._name("c1")] and d.cmds("stop")[0][:4] == ["docker", "stop", "-t", "3"]
    assert not d.cmds("rm")
    assert sb._name("c1") in d.containers
    sb.ensure("c1")  # the stopped container is restarted, state intact
    assert d.containers[sb._name("c1")]["running"] and len(d.cmds("run")) == 2


def test_reap_stale_by_finished_at() -> None:
    sb, d = make({"sandboxKeepDays": 14})
    now = datetime.now(timezone.utc)
    for n, fin in {"pos-sbx-old": iso(now - timedelta(days=30)), "pos-sbx-fresh": iso(now - timedelta(days=2)),
                   "pos-sbx-weird": "garbage", "pos-sbx-never": "0001-01-01T00:00:00Z"}.items():
        d.containers[n] = {"running": False, "finished": fin, "image": "x", "net": False}
    d.containers["pos-sbx-runs"] = {"running": True, "finished": iso(now - timedelta(days=99)), "image": "x", "net": False}
    sb.ensure("fresh-convo")
    assert set(d.containers) == {"pos-sbx-fresh", "pos-sbx-weird", "pos-sbx-never", "pos-sbx-runs", sb._name("fresh-convo")}
    scans = len([a for a in d.cmds("inspect") if "FinishedAt" in a[3]])
    sb.ensure("fresh-convo")
    assert len([a for a in d.cmds("inspect") if "FinishedAt" in a[3]]) == scans  # the sweep runs once per app run


def test_finished_at_parser() -> None:
    assert microvm._finished_at("2026-10-01T12:34:56.123456789Z") is not None
    assert microvm._finished_at("2026-10-01T12:34:56Z") is not None
    assert microvm._finished_at("0001-01-01T00:00:00Z") is None
    assert microvm._finished_at("nope") is None


def test_checkpoint_commits_and_prunes_oldest_first() -> None:
    sb, d = make()
    suffix = sb._name("c1")[len("pos-sbx-"):]
    out = sb.checkpoint("c1", "One")
    assert out["checkpoint"] == "one" and out["tag"] == f"{CKPT_REPO}/{suffix}:one" and "filesystem only" in out["note"]
    commit = d.cmds("commit")[0]
    assert commit[:4] == ["docker", "commit", "--pause=true", "-m"] and commit[-2] == sb._name("c1")
    for lab in ("two", "three", "four"):
        out = sb.checkpoint("c1", lab)
    assert MAX_CKPTS == 3 and out["kept"] == ["four", "three", "two"]
    assert [a[-1] for a in d.cmds("rmi")] == [f"{CKPT_REPO}/{suffix}:one"]
    assert [c["label"] for c in sb.checkpoints("c1")["checkpoints"]] == ["four", "three", "two"]
    sb.checkpoint("c1", "two")  # re-using a label replaces it, nothing evicted
    assert len(d.cmds("rmi")) == 1 and len(sb.checkpoints("c1")["checkpoints"]) == 3


def test_checkpoint_failure_surfaces_docker_line() -> None:
    sb, d = make()
    d.commit_error = "no space left on device"
    with pytest.raises(SandboxError, match="no space left on device"):
        sb.checkpoint("c1", "x")


def test_restore_unknown_label_lists_available() -> None:
    sb, d = make()
    sb.checkpoint("c1", "a")
    sb.checkpoint("c1", "b")
    with pytest.raises(SandboxError, match=r"no checkpoint named zzz; have: b, a"):
        sb.restore("c1", "zzz")
    with pytest.raises(SandboxError, match="have: none"):
        sb.restore("never-checkpointed", "a")
    assert not d.cmds("rm")


def test_restore_recreates_from_image_with_current_network_setting() -> None:
    sb, d = make({"sandboxNetwork": True})
    name = sb._name("c1")
    sb.checkpoint("c1", "clean")
    assert d.containers[name]["net"] is True
    sb._settings["sandboxNetwork"] = False  # type: ignore[attr-defined]
    start = len(d.argv)
    out = sb.restore("c1", "clean")
    assert out == {"restored": "clean"}
    after = d.argv[start:]
    kinds = [a[1] for a in after]
    assert kinds.index("rm") < kinds.index("run")
    run = next(a for a in after if a[1] == "run")
    assert run[-3] == f"{CKPT_REPO}/{name[len('pos-sbx-'):]}:clean" and ["--network", "none"] == run[run.index("--network"):run.index("--network") + 2]
    assert "--cap-drop" in run and "no-new-privileges" in run and sb.networked("c1") is False
    # and the other direction: restore never carries "no network" forward either
    sb._settings["sandboxNetwork"] = True  # type: ignore[attr-defined]
    sb.restore("c1", "clean")
    assert "--network" not in [a for a in d.argv if a[1] == "run"][-1] and sb.networked("c1") is True


def test_reset_removes_checkpoint_images() -> None:
    sb, d = make()
    sb.checkpoint("c1", "a")
    sb.checkpoint("c1", "b")
    sb.checkpoint("c2", "keepme")
    sb.reset("c1")
    assert sb.checkpoints("c1")["checkpoints"] == [] and sb._name("c1") not in d.containers
    assert [c["label"] for c in sb.checkpoints("c2")["checkpoints"]] == ["keepme"]


def test_tools_registered_and_wired() -> None:
    sb, d = make()
    tb = Toolbox(None, None, None, lambda: {}, sandboxes=sb)  # type: ignore[arg-type]
    import asyncio
    ctx = {"conversation_id": "c9"}
    r = asyncio.run(tb.specs["sandbox_checkpoint"].fn(ctx, label="clean"))
    assert r["checkpoint"] == "clean"
    assert asyncio.run(tb.specs["sandbox_restore"].fn(ctx, label="clean")) == {"restored": "clean"}
    assert llm.DEFAULT_SETTINGS["sandboxKeepDays"] == 14


def test_exec_result_carries_the_keys_the_tool_card_reads() -> None:
    sb, _ = make()
    out = sb.exec("c1", "true")
    assert {"stdout", "stderr", "exit_code", "timed_out"} <= set(out) and "network" not in out
    sb, _ = make({"sandboxNetwork": True})
    assert sb.exec("c1", "true")["network"] is True


def test_export_file_binary_round_trip_and_limits(tmp_path: Any) -> None:
    import asyncio

    from personal_os.workspace import Workspace
    blob = bytes(range(256)) * 3
    files = {"/workspace/out/a.bin": blob, "/workspace/big.bin": b"x" * (microvm.EXPORT_MAX_BYTES + 1)}
    sb, d = make()
    base = d.__call__

    def runner(argv: list[str], *, input: bytes | None = None, timeout: float = 60) -> "subprocess.CompletedProcess[bytes]":
        if argv[1] == "exec" and argv[-1].startswith("/workspace"):
            data = files.get(argv[-1])
            if data is None:
                return cp(1, "", "no such file")
            if argv[-2] == "cat":
                return subprocess.CompletedProcess([], 0, data, b"")
            return cp(0, str(len(data)))
        return base(argv, input=input, timeout=timeout)
    sb._run = runner  # type: ignore[assignment]
    assert sb.export_file("c1", "out/a.bin") == ("/workspace/out/a.bin", blob)
    for bad in ("/etc/passwd", "../etc/passwd", "/workspace", "big.bin", "missing.bin"):
        with pytest.raises(SandboxError):
            sb.export_file("c1", bad)
    ws = Workspace(tmp_path)
    tb = Toolbox(None, None, None, lambda: {}, sandboxes=sb, workspace=ws)  # type: ignore[arg-type]
    ctx: dict[str, Any] = {"conversation_id": "c1", "desk_id": "d1"}
    r = asyncio.run(tb.specs["sandbox_export_file"].fn(ctx, path="out/a.bin"))
    assert r["saved"] == "outputs/a.bin" and ws.read_bytes("d1", "outputs/a.bin") == blob
    assert asyncio.run(tb.specs["sandbox_export_file"].fn(ctx, path="out/a.bin"))["saved"] != "outputs/a.bin"  # never overwrites
    assert "error" in asyncio.run(tb.specs["sandbox_export_file"].fn({"conversation_id": "c1"}, path="out/a.bin"))


def test_export_file_read_passes_the_export_cap_to_the_real_runner(monkeypatch: Any) -> None:
    """A 9 MB file must not hit the 8 MB default read cap of the real runner."""
    size = 9_000_000
    seen: list[int] = []

    def fake_capped(argv: list[str], *, input: bytes | None = None, timeout: float = 60, hard_cap: int = 0, keep: Any = None) -> Any:
        seen.append(hard_cap)
        out = str(size).encode() if "wc -c" in " ".join(argv) else b"x" * size
        return types.SimpleNamespace(returncode=0, stdout=out[:hard_cap], stderr=b"", timed_out=False, truncated=len(out) > hard_cap)
    sb, d = make()
    monkeypatch.setattr(microvm, "capped_run", fake_capped)
    monkeypatch.setattr(sb, "_run", microvm._run)
    monkeypatch.setattr(sb, "ensure", lambda cid: "c")
    assert len(sb.export_file("c1", "big.bin")[1]) == size
    assert seen[-1] > size
