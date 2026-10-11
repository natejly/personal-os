"""Where the platform has no OS sandbox, run_python and shell_run say so and never run without a card."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="unsandboxed-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os import osplat, shell  # noqa: E402
from personal_os.tools import Toolbox  # noqa: E402


def _box(settings: dict | None = None) -> Toolbox:
    return Toolbox(None, None, None, lambda: settings or {})  # type: ignore[arg-type]


def test_linux_run_python_tops_out_at_ask() -> None:
    with osplat.use("linux"):
        tb = _box({"tools": {"run_python": "on"}})
        spec = tb.specs["run_python"]
        assert tb.ask_locked(spec)
        assert tb.default_mode(spec) == "ask"
        assert tb.effective({"run_python": "on"}, {"run_python": "on"}, {"run_python": "on"})["run_python"] == "ask"
        assert tb.cap_modes({"run_python": "on", "fs_glob": "on"}) == {"run_python": "ask", "fs_glob": "on"}
        assert tb.effective({"run_python": "off"}, None, None)["run_python"] == "off"
        assert {t["name"]: t for t in tb.list()}["run_python"]["ask_locked"] is True


def test_linux_descriptions_are_honest() -> None:
    with osplat.use("linux"):
        tb = _box()
        d = tb.specs["run_python"].description
        assert "unsandboxed" in d and "isolated sandbox" not in d and "No network" not in d
        assert "approves each run" in d
        sh = tb.specs["shell_run"].description
        assert "unsandboxed" in sh and "macOS" not in sh and "sandboxed by the OS" not in sh
        assert "macOS sandbox-exec" not in shell.no_sandbox_reason()


def test_darwin_unchanged() -> None:
    with osplat.use("darwin"):
        tb = _box({})
        spec = tb.specs["run_python"]
        assert not tb.ask_locked(spec)
        assert tb.effective({"run_python": "on"}, None, None)["run_python"] == "on"
        d = spec.description
        assert d.startswith("Run a Python 3 script in an isolated sandbox and return stdout/stderr. No network, no subprocesses, "
                            "and writes only inside the temp working directory (CPU/memory/time limits apply). In a cowork desk")
        assert tb.specs["shell_run"].description.startswith("Run a shell command (zsh) on this Mac. It is sandboxed by the OS")
        assert shell.no_sandbox_reason() == "it needs macOS sandbox-exec"
