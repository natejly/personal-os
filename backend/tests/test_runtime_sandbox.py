"""sandbox.backend: a confined request goes through one function that fails closed. A backend that cannot enforce the
request's mode, or only partly, or fails to wrap, means the call is refused and nothing runs, unconfined or elsewhere.

Run: python -m pytest backend/tests/test_runtime_sandbox.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from grain_sdk.testing import FakeSandbox  # noqa: E402
from personal_os import osplat, runtime, sandbox, shell  # noqa: E402
from personal_os.db import Database  # noqa: E402
from personal_os.extensions.host import ExtensionContext, ExtensionHost  # noqa: E402
from personal_os.extensions.manifest import load_manifest  # noqa: E402
from personal_os.osplat import seatbelt  # noqa: E402
from personal_os.osplat.base import SandboxRequest, shortfalls, weaker  # noqa: E402
from personal_os.osplat.darwin import DarwinSandbox  # noqa: E402
from personal_os.runtime.slots import ACCEPT_PARTIAL, Scopes  # noqa: E402

FULL = dict.fromkeys(("fs_write", "fs_read_deny", "network", "process"), "full")
REFUSAL = ("shell needs workspace-write confinement; the selected sandbox backend '{id}' cannot enforce {dim} here ({level}). "
           "It was not run.")


class Rig:
    def __init__(self, tmp_path: Path, hidden: list[str]) -> None:
        self.host = ExtensionHost()
        self.hidden = hidden
        self.events: list[tuple[str, Any]] = []
        for hook in ("sandbox.refused", "runtime.fallback"):
            self.host.bus.subscribe(hook, lambda p, h=hook: self.events.append((h, p)), owner="t", order=0)
        self.selected: dict[str, Any] = {}
        self.host.runtime.scopes = lambda: Scopes(global_=self.selected)
        self.tmp = tmp_path
        self.ctx = ExtensionContext(db=Database(tmp_path / "db"), hidden_views=lambda: hidden, services={}, settings=lambda: {},
                                    set_settings=lambda v: None, sid=lambda p: p, wsid=lambda p: p, publish=lambda _n, _d: None)

    def backend(self, fake: FakeSandbox, name: str = "box", select: bool = True) -> FakeSandbox:
        root = self.tmp / name
        root.mkdir()
        (root / "extension.json").write_text(json.dumps({
            "id": name, "name": name, "version": "1.0.0", "apiVersion": 1, "permissions": ["runtime:sandbox"],
            "contributes": {"runtime": [{"slot": "sandbox.backend", "id": "b", "label": "Box"}]}}), "utf-8")
        (root / "extension.py").write_text("def activate(api):\n    pass\n", "utf-8")
        scope = self.host._add(load_manifest(root / "extension.json"), root=root, ctx=self.ctx)  # a built-in may provide the sandbox
        scope.provide("sandbox.backend", fake, id="b", label="Box")
        if select:
            self.selected["sandbox.backend"] = f"{name}/b"
        return fake


@pytest.fixture
def rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Rig:
    r = Rig(tmp_path, [])
    monkeypatch.setattr(runtime, "active", r.host.runtime)
    return r


@pytest.fixture
def mac(monkeypatch: pytest.MonkeyPatch) -> Any:
    """The macOS platform object with its sandbox reported present, so the default backend can wrap (it only builds an argv)."""
    monkeypatch.setattr(DarwinSandbox, "shell_available", lambda self: True)
    monkeypatch.setattr(DarwinSandbox, "python_available", lambda self: True)
    with osplat.use("darwin"):
        yield


def request(**over: Any) -> SandboxRequest:
    return sandbox.shell_request("/work", ["/work", "/tmp/t"], **over)


def refuse(argv: list[str] | None = None, **over: Any) -> sandbox.SandboxRefused:
    with pytest.raises(sandbox.SandboxRefused) as e:
        sandbox.sandbox_wrap(request(**over), argv or ["true"])
    return e.value


# ---- what a backend enforces ----
def test_a_request_needs_the_dimensions_of_its_mode() -> None:
    assert shortfalls({"workspace-write": FULL}, "workspace-write", "none") == []
    assert shortfalls({"workspace-write": {**FULL, "network": "none"}}, "workspace-write", "none") == [("network", "none")]
    assert shortfalls({"workspace-write": {**FULL, "network": "none"}}, "workspace-write", "open") == []  # open network needs no enforcement
    assert shortfalls({"read-only": {**FULL, "network": "none"}}, "read-only", "none") == []  # read-only has no network to confine
    assert shortfalls({}, "workspace-write", "none") == [("fs_write", "none"), ("fs_read_deny", "none"), ("network", "none")]
    assert shortfalls({}, "full", "none") == []


def test_weaker_names_each_mode_and_dimension_that_enforces_less() -> None:
    assert weaker({"workspace-write": FULL}, {"workspace-write": FULL}) == []
    assert weaker({"workspace-write": {**FULL, "network": "partial"}}, {"workspace-write": FULL}) == ["workspace-write network: partial instead of full"]
    assert weaker({}, {"workspace-write": FULL}) == [f"workspace-write {d}: none instead of full" for d in FULL]
    assert weaker({"workspace-write": FULL}, {}) == []


# ---- fail closed ----
def test_a_backend_without_the_requested_mode_refuses_and_nothing_is_wrapped(rig: Rig) -> None:
    fake = rig.backend(FakeSandbox({"read-only": FULL}))
    err = refuse()
    assert str(err) == REFUSAL.format(id="box/b", dim="fs_write", level="none")
    assert fake.wrapped == []
    [(hook, payload)] = rig.events
    assert hook == "sandbox.refused" and payload == {"tool": "shell", "mode": "workspace-write", "backend": "box/b", "dimension": "fs_write",
                                                    "enforcement": {}, "run_id": None}


def test_a_partial_backend_is_refused_until_the_user_accepts_that_dimension(rig: Rig) -> None:
    fake = rig.backend(FakeSandbox({"workspace-write": {**FULL, "network": "partial"}}))
    assert str(refuse(network="none")) == REFUSAL.format(id="box/b", dim="network", level="partial")
    assert sandbox.sandbox_wrap(request(network="open"), ["true"]) == ["true"]  # an open network needs no enforcement
    rig.selected[ACCEPT_PARTIAL] = {"box/b": ["network"]}
    assert sandbox.sandbox_wrap(request(network="none"), ["true"]) == ["true"] and len(fake.wrapped) == 2
    rig.selected[ACCEPT_PARTIAL] = {"box/b": ["fs_write"]}  # another dimension does not count
    refuse(network="none")
    rig.selected[ACCEPT_PARTIAL] = {"other/b": ["network"]}  # nor does another backend's acceptance
    refuse(network="none")


def test_only_the_global_selection_can_carry_an_acceptance(rig: Rig) -> None:
    rig.backend(FakeSandbox({"workspace-write": {**FULL, "network": "partial"}}))
    rig.host.runtime.scopes = lambda: Scopes(global_=rig.selected, chat={ACCEPT_PARTIAL: {"box/b": ["network"]}},
                                             project={ACCEPT_PARTIAL: {"box/b": ["network"]}},
                                             profiles=(("p", {ACCEPT_PARTIAL: {"box/b": ["network"]}}),))
    refuse(network="none")


def test_a_dimension_that_is_not_enforced_at_all_cannot_be_accepted(rig: Rig) -> None:
    rig.backend(FakeSandbox({"workspace-write": {**FULL, "network": "none"}}))
    rig.selected[ACCEPT_PARTIAL] = {"box/b": ["network"]}
    assert str(refuse(network="none")) == REFUSAL.format(id="box/b", dim="network", level="none")


def test_a_backend_that_fails_to_wrap_is_refused_never_run_unconfined(rig: Rig) -> None:
    rig.backend(FakeSandbox({"workspace-write": FULL}, wrap_fails=True))
    err = str(refuse())
    assert "failed to set it up (OSError: scripted failure)" in err and err.endswith("It was not run.")


def test_a_backend_that_cannot_report_its_support_enforces_nothing(rig: Rig) -> None:
    class Broken(FakeSandbox):
        def support(self) -> dict[str, Any]:
            raise RuntimeError("no idea")
    rig.backend(Broken())
    assert "cannot enforce fs_write here (none)" in str(refuse())


def test_a_full_request_is_never_wrapped() -> None:
    with pytest.raises(ValueError, match="never wrapped"):
        sandbox.sandbox_wrap(SandboxRequest("full", "/", (), "open", None, (), (), "shell"), ["true"])


def test_failed_to_start_asks_the_selected_backend(rig: Rig) -> None:
    assert sandbox.failed_to_start(0, "") is False
    rig.backend(FakeSandbox({"workspace-write": FULL}, start_fails=True))
    assert sandbox.failed_to_start(0, "") is True


# ---- a selected backend that is gone ----
def test_a_missing_backend_falls_back_to_the_default_only_when_it_is_as_strong(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mac: Any) -> None:
    hidden: list[str] = []
    rig = Rig(tmp_path, hidden)
    monkeypatch.setattr(runtime, "active", rig.host.runtime)
    rig.backend(FakeSandbox({"workspace-write": FULL}))
    assert sandbox.sandbox_wrap(request(), ["true"]) == ["true"]
    hidden.append("box")
    wrapped = sandbox.sandbox_wrap(request(), ["true"])  # the default (Seatbelt) is as strong as the backend that was there
    assert wrapped[0] == "sandbox-exec"
    assert rig.events and rig.events[-1][1]["used"] == "core/os-default" and rig.events[-1][1]["refused"] is False
    hidden.clear()
    stronger = Rig(tmp_path / "s", [])
    monkeypatch.setattr(runtime, "active", stronger.host.runtime)
    stronger.backend(FakeSandbox({"workspace-write": FULL, "read-only": FULL}))
    stronger.hidden.append("box")
    err = str(refuse())
    assert "not available and the default is less safe" in err and err.endswith("nothing was run.")


def test_with_no_sandbox_on_the_platform_a_confined_request_is_refused_not_run_open(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime, "active", None)
    with osplat.use("linux"):
        err = str(refuse())
    assert err == REFUSAL.format(id="core/os-default", dim="fs_write", level="none")


# ---- the default adapter ----
def test_the_default_adapter_reports_only_what_it_can_enforce(mac: Any) -> None:
    assert sandbox.OsDefault().support() == {"workspace-write": FULL}
    with osplat.use("linux"):
        assert sandbox.OsDefault().support() == {}


def test_the_default_adapter_builds_the_seatbelt_profile_text_unchanged(tmp_path: Path, mac: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime, "active", None)
    work = ["/work", "/tmp/t"]
    cases = [
        (sandbox.shell_request("/work", work), seatbelt.shell_profile(work, network=False)),
        (sandbox.shell_request("/work", work, network="open"), seatbelt.shell_profile(work, network=True)),
        (sandbox.shell_request("/work", work, network="proxy", proxy_port=8123), seatbelt.shell_profile(work, network=False, proxy_port=8123)),
        (sandbox.shell_request("/work", work, confine=True), seatbelt.shell_profile(work, network=False, confine=True)),
        (sandbox.shell_request("/work", ["/t"], allow_hosts=("*:443",), loopback=True),
         seatbelt.shell_profile(["/t"], network=False, allow_hosts=["*:443"], loopback=True)),
        (sandbox.python_request(str(tmp_path), sys.executable, "/tmp/sock", "/desk"),
         seatbelt.python_profile(str(tmp_path), sys.executable, "/tmp/sock", "/desk")),
        (sandbox.python_request(str(tmp_path), sys.executable, None, None), seatbelt.python_profile(str(tmp_path), sys.executable, None, None)),
    ]
    for req, profile in cases:
        assert sandbox.sandbox_wrap(req, ["/bin/echo", "hi"]) == ["sandbox-exec", "-p", profile, "/bin/echo", "hi"]


def test_requests_carry_the_core_deny_list_and_the_purpose() -> None:
    req = sandbox.shell_request("/work", ["/work"], purpose="terminal")
    assert req.mode == "workspace-write" and req.purpose == "terminal" and req.cwd == "/work"
    assert any(d.endswith("Library/Keychains") for d in req.deny) or any(".ssh" in d for d in req.deny)
    assert sandbox.python_request("/w", "python", None, "/ws").writable == ("/w", "/ws")


# ---- shell: the refusal reaches the tool result and nothing is spawned ----
class Box:
    def __init__(self, tmp: Path) -> None:
        from personal_os.tools import Toolbox
        from personal_os.working import ToolResults
        self.db = Database(tmp / "shell-data")
        with self.db.tx() as c:
            c.execute("INSERT INTO conversations(id, title, model, created_at, updated_at) VALUES('c1','t','m',0,0)")
        self.tb = Toolbox(None, None, None, lambda: {}, results=ToolResults(self.db))  # type: ignore[arg-type]
        self.ctx: dict[str, Any] = {"conversation_id": "c1", "message_id": None, "settings": {}, "tainted": False, "taint_sources": []}

    def run(self, name: str, **args: Any) -> Any:
        return asyncio.run(self.tb.call(name, args, self.ctx))


def test_shell_run_refuses_when_the_backend_cannot_confine_and_spawns_nothing(rig: Rig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rig.backend(FakeSandbox({"read-only": FULL}))
    box = Box(tmp_path)
    spawned: list[Any] = []

    async def start(*a: Any, **k: Any) -> Any:
        spawned.append(a)
        raise AssertionError("a command was started")

    monkeypatch.setattr(box.tb.shell, "start", start)
    monkeypatch.setattr(shell, "sandbox_available", lambda: True)
    result = box.run("shell_run", command="touch x", cwd=str(tmp_path))
    assert "cannot enforce fs_write here (none). It was not run." in result["error"] and spawned == []
    assert not (tmp_path / "x").exists()


def test_confine_refuses_with_a_shell_error_and_gives_back_the_proxy_token(rig: Rig) -> None:
    rig.backend(FakeSandbox({}))
    revoked: list[Any] = []

    async def ensure() -> int:
        return 4000

    jobs = SimpleNamespace(egress=SimpleNamespace(ensure=ensure, new_run=lambda allowed: "tok", env=lambda token: {}, revoke=revoked.append))
    with pytest.raises(shell.ShellError, match="It was not run"):
        asyncio.run(shell.confine(jobs, ["true"], {}, {}, ["/w"], "/w"))
    assert revoked == ["tok"]
    assert asyncio.run(shell.confine(jobs, ["true"], {}, {}, ["/w"], "/w", unsandboxed=True)).argv == ["true"]  # unsandboxed asks for no backend


def test_run_fixed_refuses_and_cleans_up(rig: Rig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rig.backend(FakeSandbox({}))
    monkeypatch.setattr(shell, "sandbox_available", lambda: True)
    made: list[str] = []
    real = tempfile.mkdtemp
    monkeypatch.setattr(tempfile, "mkdtemp", lambda *a, **k: made.append(real(*a, **k)) or made[-1])
    with pytest.raises(shell.ShellError, match="It was not run"):
        asyncio.run(shell.run_fixed(mock.Mock(), ["true"], str(tmp_path), {}, sandboxed=True, timeout=5))
    assert made and not os.path.exists(made[0])


def test_run_python_refuses_before_running_and_removes_its_work_dir(rig: Rig, monkeypatch: pytest.MonkeyPatch, mac: Any) -> None:
    rig.backend(FakeSandbox({}))
    made: list[str] = []
    real = tempfile.mkdtemp
    monkeypatch.setattr(tempfile, "mkdtemp", lambda *a, **k: made.append(real(*a, **k)) or made[-1])
    with pytest.raises(sandbox.SandboxRefused, match="python needs workspace-write confinement"):
        sandbox.run_python("open('x','w')", 5)
    assert made and not os.path.exists(made[0])
