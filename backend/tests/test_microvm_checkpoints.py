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
        self.networks: set[str] = set()
        self.image_env: dict[str, dict[str, str]] = {}  # tag -> env a commit kept from the container
        self.px_log: tuple[int, str] | None = None  # (rc, stdout) of reading the proxy sidecar's report

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
                a = c.get("argv", [])
                return cp(0, a[a.index("--network") + 1] if "--network" in a else "default")
        if cmd == "ps":
            running_only = "-a" not in argv
            label = argv[argv.index("--filter") + 1][len("label="):]
            names = [n for n, c in self.containers.items() if (c["running"] or not running_only)
                     and label in c.get("argv", [f"{microvm.LABEL}=1"])]
            if "\t" in argv[-1]:  # list(): name, state, created, conversation label
                rows = []
                for n in names:
                    a = self.containers[n].get("argv") or []
                    conv = next((x.split("=", 1)[1] for x in a if x.startswith(microvm.CONV_LABEL + "=")), "")
                    rows.append(f"{n}\t{'running' if self.containers[n]['running'] else 'exited'}\t2026-10-01 12:00:00\t{conv}")
                return cp(0, "\n".join(rows))
            return cp(0, "\n".join(names))
        if cmd == "network":
            if argv[2] == "create":
                self.networks.add(argv[-1])
            elif argv[2] == "rm":
                self.networks.discard(argv[-1])
            return cp(0)
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
            if argv[2].startswith(microvm.PX_PREFIX):  # the sidecar's report
                return cp(1, "", "cat: /tmp/egress.json: No such file or directory") if self.px_log is None else cp(*self.px_log)
            return cp(0, "/usr/bin/bash")
        if cmd == "commit":
            if self.commit_error:
                return cp(1, "", f"Usage: docker commit\nError response from daemon: {self.commit_error}")
            self.clock += 1
            self.images[argv[-1]] = f"2026-10-01 12:00:{self.clock:02d} +0000 UTC"
            self.image_env[argv[-1]] = _env(self.containers[argv[-2]]["argv"])  # commit keeps the container's Config.Env
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


def test_a_token_in_a_checkpoint_label_is_stripped() -> None:
    import asyncio

    pat = "github_pat_11AAAAAAA0AAAAAAAAAAAAAAAAAAAA"
    slug = microvm.ckpt_slug(pat)
    sb, d = make()
    tb = Toolbox(None, None, None, lambda: {}, sandboxes=sb)  # type: ignore[arg-type]
    ctx = {"conversation_id": "c9"}
    saved = asyncio.run(tb.specs["sandbox_checkpoint"].fn(ctx, label=pat))
    assert slug not in str(saved)
    assert saved["checkpoint"] == "[github-pat]"
    assert "[github-pat]" in saved["tag"] and "[github-pat]" in saved["kept"]
    assert any(slug in tag for tag in d.images)
    restored = asyncio.run(tb.specs["sandbox_restore"].fn(ctx, label=pat))
    assert restored == {"restored": "[github-pat]"}
    missing = "github_pat_11BBBBBBBB0BBBBBBBBBBBBBBBBB"
    err = asyncio.run(tb.specs["sandbox_restore"].fn(ctx, label=missing))
    assert missing.lower() not in str(err).lower() and slug not in str(err)
    assert "[github-pat]" in err["error"]


def test_a_token_in_a_sandbox_path_is_stripped() -> None:
    import asyncio

    pat = "github_pat_11AAAAAAA0AAAAAAAAAAAAAAAAAAAA"
    seen: list[str] = []

    class Box:
        def holds_import(self, _cid: str) -> bool:
            return False

        def write_file(self, _cid: str, path: str, content: str, append: bool = False) -> dict[str, Any]:
            seen.append(path)
            if path == "missing":
                raise SandboxError(f"could not write /workspace/{pat}: denied")
            return {"written": f"/workspace/{path}", "bytes": len(content.encode()), "appended": append}

        def read_file(self, _cid: str, path: str, offset: int = 0, length: int = 6000) -> dict[str, Any]:
            return {"path": f"/workspace/{path}", "text": f"hello {pat}", "offset": 0, "total_bytes": 4,
                    "images": [{"name": pat, "mime": "image/png", "bytes": 4, "data": f"data:image/png;base64,{pat}"}]}

        def list_files(self, _cid: str, path: str | None = None) -> dict[str, Any]:
            return {"path": f"/workspace/{pat}", "entries": [{"type": "file", "bytes": 1, "path": f"/workspace/{pat}"}]}

    tb = Toolbox(None, None, None, lambda: {}, sandboxes=Box())  # type: ignore[arg-type]
    ctx = {"conversation_id": "c9"}
    wrote = asyncio.run(tb.specs["sandbox_write_file"].fn(ctx, path=pat, content="hello"))
    assert seen == [pat]
    assert pat not in str(wrote) and wrote["written"] == "/workspace/[github-pat]"
    read = asyncio.run(tb.specs["sandbox_read_file"].fn(ctx, path=pat))
    assert pat not in read["path"] and pat not in read["text"] and read["images"][0]["name"] == "[github-pat]"
    assert pat in read["images"][0]["data"]
    listed = asyncio.run(tb.specs["sandbox_list_files"].fn(ctx))
    assert pat not in str(listed) and listed["entries"][0]["path"] == "/workspace/[github-pat]"
    err = asyncio.run(tb.specs["sandbox_write_file"].fn(ctx, path="missing", content="x"))
    assert pat not in str(err) and "[github-pat]" in err["error"]


def test_a_token_in_a_sandbox_document_id_is_stripped() -> None:
    import asyncio

    pat = "github_pat_11AAAAAAA0AAAAAAAAAAAAAAAAAAAA"
    seen: list[str] = []

    class Docs:
        def get(self, document_id: str) -> dict[str, Any] | None:
            seen.append(document_id)
            if document_id == "doc1":
                return {"id": "doc1", "name": "note.md", "text": f"body {pat}"}
            return None

    docs = Docs()
    tb = Toolbox(None, None, docs, lambda: {}, sandboxes=object())  # type: ignore[arg-type]
    err = asyncio.run(tb.specs["sandbox_put_document"].fn({"conversation_id": "c9"}, document_id=pat))
    assert seen == [pat]
    assert pat not in str(err) and "[github-pat]" in err["error"]
    assert docs.get("doc1")["text"] == f"body {pat}"


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
    # A plain chat has no desk: the file lands in the chat's outbox, listed for the card, and still never overwrites.
    chat = asyncio.run(tb.specs["sandbox_export_file"].fn({"conversation_id": "c1"}, path="out/a.bin"))
    assert chat["saved"] == "outputs/a.bin" and chat["outputs"] == [{"name": "a.bin", "size": len(blob), "path": "outputs/a.bin"}]
    assert (tmp_path / "chats" / "c1" / "outputs" / "a.bin").read_bytes() == blob
    again = asyncio.run(tb.specs["sandbox_export_file"].fn({"conversation_id": "c1"}, path="out/a.bin"))
    assert again["saved"] == "outputs/a 2.bin" and (tmp_path / "chats" / "c1" / "outputs" / "a.bin").read_bytes() == blob
    assert "error" in asyncio.run(tb.specs["sandbox_export_file"].fn({"conversation_id": "c1"}, path="out/a.bin", dest="../escape.bin"))
    assert not (tmp_path / "chats" / "escape.bin").exists()
    # The no-desk export does not reach a desk's files.
    assert not (tmp_path / "cowork" / "c1").exists()


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


def test_run_args_carry_the_conversation_label_and_no_network_by_default() -> None:
    sb, d = make()
    sb.ensure("conv-a")
    run = d.cmds("run")[0]
    assert f"{microvm.CONV_LABEL}=conv-a" in run and run[run.index("--network") + 1] == "none"
    sb.checkpoint("conv-a", "x")
    sb.restore("conv-a", "x")
    assert f"{microvm.CONV_LABEL}=conv-a" in d.cmds("run")[-1], "a restored container keeps its conversation"


def test_list_maps_containers_back_to_conversations(tmp_path: Any) -> None:
    sb, d = make()
    sb._import_dir = tmp_path
    sb.ensure("conv-a")
    sb.checkpoint("conv-a", "clean")
    sb.note_import("conv-a")
    d.containers["pos-sbx-000000000000"] = {"running": False, "finished": "x", "image": "x", "net": False}  # pre-label
    items = {i["name"]: i for i in sb.list()}
    a = items[sb._name("conv-a")]
    assert a["conversation_id"] == "conv-a" and a["status"] == "running" and a["checkpoints"] == ["clean"]
    assert a["holds_import"] is True and a["networked"] is False and a["last_used"]
    old = items["pos-sbx-000000000000"]
    assert old["conversation_id"] is None and old["status"] == "exited" and old["checkpoints"] == []
    sb.reset_name("pos-sbx-000000000000")
    assert "pos-sbx-000000000000" not in d.containers


def test_status_says_why_the_runtime_is_unavailable(monkeypatch: Any) -> None:
    sb, d = make({"sandboxRuntime": "definitely-not-a-binary-xyz"})
    assert sb.status() == {"available": False, "runtime": "definitely-not-a-binary-xyz",
                           "reason": "definitely-not-a-binary-xyz is not on PATH"}
    sb, d = make()
    monkeypatch.setattr(microvm.shutil, "which", lambda b: "/usr/local/bin/" + b)
    sb._run = lambda argv, **kw: cp(1, "", "Cannot connect to the Docker daemon. Is the docker daemon running?")  # type: ignore[assignment]
    st = sb.status()
    assert st["available"] is False and st["reason"].startswith("`docker info` failed: Cannot connect")
    assert llm.DEFAULT_SETTINGS["sandboxImage"] == microvm.DEFAULT_IMAGE and llm.DEFAULT_SETTINGS["sandboxNetwork"] == "off"


# ---- sandboxNetwork: off | proxy | open ----
def _env(argv: list[str]) -> dict[str, str]:
    return dict(argv[i + 1].split("=", 1) for i, a in enumerate(argv) if a == "-e")


def test_network_setting_is_a_tri_state_and_a_stored_true_means_open() -> None:
    assert [microvm.net_mode(v) for v in (True, False, None, "off", "proxy", "open", "bogus", 1)] == \
        ["open", "off", "off", "off", "proxy", "open", "off", "off"]
    assert llm.DEFAULT_SETTINGS["sandboxNetwork"] == "off"
    for setting, flag in ((False, ["--network", "none"]), ("off", ["--network", "none"]), (True, None), ("open", None)):
        sb, d = make({"sandboxNetwork": setting})
        sb.ensure("c1")
        (run,) = d.cmds("run")
        got = run[run.index("--network"):run.index("--network") + 2] if "--network" in run else None
        assert got == flag, setting
        assert not d.networks and sb.networked("c1") is (flag is None)


def test_proxy_mode_puts_the_sandbox_on_an_internal_network_whose_only_way_out_is_the_sidecar() -> None:
    sb, d = make({"sandboxNetwork": "proxy", "shellAllowedDomains": ["example.com", "10.0.0.1"]})
    name = sb.ensure("c1")
    sfx = name[len("pos-sbx-"):]
    net, px = microvm.NET_PREFIX + sfx, microvm.PX_PREFIX + sfx
    create = next(a for a in d.cmds("network") if a[2] == "create")
    assert "--internal" in create and create[-1] == net and net in d.networks
    connect = next(a for a in d.cmds("network") if a[2] == "connect")
    assert connect[-2:] == [net, px] and connect[connect.index("--alias") + 1] == microvm.PROXY_ALIAS
    side, box = [a for a in d.cmds("run") if a[a.index("--name") + 1] == px][0], d.containers[name]["argv"]
    assert "--network" not in side and side[-4:-1] == [microvm.DEFAULT_IMAGE, "python3", "-c"]  # bridge only, runs egress.py
    assert "def sidecar()" in side[-1] and "--cap-drop" in side and "no-new-privileges" in side
    assert f"{microvm.PROXY_LABEL}=1" in side and f"{microvm.LABEL}=1" not in side  # never counted, reaped or stopped as a sandbox
    se, be = _env(side), _env(box)
    allow = se["GRAIN_PROXY_ALLOW"].split(",")
    assert "pypi.org" in allow and "example.com" in allow and "10.0.0.1" not in allow
    assert box[box.index("--network") + 1] == net and "none" not in box
    tok = se["GRAIN_PROXY_TOKEN"]
    assert len(tok) > 16 and be["HTTPS_PROXY"] == be["http_proxy"] == f"http://grain:{tok}@{microvm.PROXY_ALIAS}:{microvm.PROXY_PORT}"
    assert be["NO_PROXY"] == "" and sb.reaches_out("c1") and sb.networked("c1") is False
    assert [n for n in sb._live("docker")] == [name]


def test_proxy_mode_taints_only_after_a_host_outside_the_registries_and_reports_per_call() -> None:
    import json
    sb, d = make({"sandboxNetwork": "proxy"})
    out = sb.exec("c1", "true")
    assert out["egress"] == {"mode": "allowlist", "contacted": [], "blocked": []} and "network" not in out
    d.px_log = (0, json.dumps({"contacted": ["pypi.org", "files.pythonhosted.org"], "blocked": ["evil.io"]}))
    out = sb.exec("c1", "pip download requests")
    assert out["egress"]["contacted"] == ["pypi.org", "files.pythonhosted.org"] and out["egress"]["blocked"] == ["evil.io"]
    assert "network" not in out and "evil.io" in out["note"] and sb.networked("c1") is False
    out = sb.exec("c1", "true")
    assert out["egress"] == {"mode": "allowlist", "contacted": [], "blocked": []} and "note" not in out  # only what is new
    d.px_log = (0, json.dumps({"contacted": ["pypi.org", "files.pythonhosted.org", "example.com"], "blocked": ["evil.io"]}))
    out = sb.exec("c1", "curl https://example.com")
    assert out["egress"]["contacted"] == ["example.com"] and out["network"] is True and sb.networked("c1") is True
    assert sb.list_files("c1").get("network") is True
    d.px_log = (0, json.dumps({"contacted": [], "blocked": []}))  # sticky: a reset report cannot launder what was fetched
    assert sb.networked("c1") is True and sb.exec("c1", "true")["network"] is True


def test_proxy_mode_treats_an_unreadable_report_as_tainted() -> None:
    sb, d = make({"sandboxNetwork": "proxy"})
    sb.ensure("c1")
    d.px_log = (1, "")  # the sidecar is gone or wedged: nothing says what the sandbox reached
    assert sb.networked("c1") is True and sb.exec("c1", "true")["network"] is True
    d.px_log = None
    assert sb.networked("c1") is False  # not remembered: only a real report of an outside host is sticky


def test_tool_result_taints_through_the_proxy_only_on_an_outside_host() -> None:
    import asyncio
    import json
    sb, d = make({"sandboxNetwork": "proxy"})
    tb = Toolbox(None, None, None, lambda: {}, sandboxes=sb)  # type: ignore[arg-type]
    ctx: dict[str, Any] = {"conversation_id": "c1"}
    d.px_log = (0, json.dumps({"contacted": ["pypi.org"], "blocked": []}))
    asyncio.run(tb.specs["sandbox_exec"].fn(ctx, command="pip install x"))
    assert not ctx.get("tainted")
    assert tb.gate("sandbox_exec", "on", {**ctx, "tainted": True}) == "ask"  # an allowed host can still carry data out
    d.px_log = (0, json.dumps({"contacted": ["pypi.org", "example.com"], "blocked": []}))
    asyncio.run(tb.specs["sandbox_exec"].fn(ctx, command="curl example.com"))
    assert ctx["tainted"] is True and ctx["taint_sources"] == ["sandbox_exec"]


def test_proxy_sidecar_follows_the_sandbox_through_stop_restart_restore_and_reset() -> None:
    sb, d = make({"sandboxNetwork": "proxy"})
    name = sb.ensure("c1")
    sfx = name[len("pos-sbx-"):]
    px, net = microvm.PX_PREFIX + sfx, microvm.NET_PREFIX + sfx
    sb.shutdown()
    assert [a[-1] for a in d.cmds("stop")] == [name, px] and not d.containers[px]["running"]
    sb._net.clear()  # an app restart: the mode is recovered from the container itself
    sb.ensure("c1")
    assert d.containers[px]["running"] and sb._net[name] == "proxy"
    sb.checkpoint("c1", "clean")
    sb._settings["sandboxNetwork"] = "off"  # type: ignore[attr-defined]
    sb.restore("c1", "clean")  # the mode is re-applied from current settings: the sidecar and its network go
    assert px not in d.containers and net not in d.networks and sb.reaches_out("c1") is False
    sb._settings["sandboxNetwork"] = "proxy"  # type: ignore[attr-defined]
    sb.restore("c1", "clean")
    assert px in d.containers and net in d.networks and sb._net[name] == "proxy"
    sb.reset("c1")
    assert name not in d.containers and px not in d.containers and net not in d.networks


def test_an_orphaned_sidecar_is_swept_once_per_app_run() -> None:
    sb, d = make({"sandboxNetwork": "proxy"})
    name = sb.ensure("c1")
    px = microvm.PX_PREFIX + name[len("pos-sbx-"):]
    d.containers.pop(name)  # removed while this app run never knew its mode
    sb2 = Sandboxes(lambda: {"sandboxNetwork": "off"}, runner=d)
    sb2.ensure("c2")
    assert px not in d.containers and not d.networks


def test_restore_out_of_proxy_mode_blanks_the_proxy_env_the_checkpoint_carries() -> None:
    sb, d = make({"sandboxNetwork": "proxy"})
    name = sb.ensure("c1")
    sb.checkpoint("c1", "clean")
    (tag,) = d.image_env
    assert d.image_env[tag]["HTTPS_PROXY"].startswith("http://grain:")
    sb._settings["sandboxNetwork"] = "open"  # type: ignore[attr-defined]
    sb.restore("c1", "clean")
    run = d.containers[name]["argv"]
    env = {**d.image_env[tag], **_env(run)}  # what the restored container sees: image env, then its -e overrides
    assert all(env.get(k, "") == "" for k in microvm.PROXY_VARS) and "--network" not in run


def test_removing_a_sandbox_whose_mode_is_not_recovered_yet_still_removes_its_sidecar() -> None:
    sb, d = make({"sandboxNetwork": "proxy"})
    name = sb.ensure("c1")
    px = microvm.PX_PREFIX + name[len("pos-sbx-"):]
    sb2 = Sandboxes(lambda: {"sandboxNetwork": "proxy"}, runner=d)  # a later app run that has not touched c1 yet
    sb2.reset("c1")
    assert name not in d.containers and px not in d.containers and not d.networks
