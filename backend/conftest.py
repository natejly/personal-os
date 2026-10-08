"""Run every backend test file in a process of its own.

The suite is written for that, whether or not it says so. Each file points PERSONAL_OS_DATA_DIR at a scratch dir
with setdefault, imports the app singleton, and many then rebind shared module globals -- llm.stream_chat,
llm.complete, toolbox.call -- to scripted stubs, often at import time and rarely restored. In one pytest process every
module is imported during collection before any test runs, so whichever stub was installed last is the one every
module's tests talk to (an artifact test failing with "pop from empty list" was another file's scripted stub
running dry), and every file writes into whichever database the first import opened ("exactly 3 chunks in the
index"). Which tests failed depended on the order files happened to sort in.

So `pytest` here collects one item per test file and runs that file in a fresh interpreter with a fresh data dir:

- pytest-style files run under an inner pytest (which sees PERSONAL_OS_TEST_CHILD and collects normally);
- script-style files -- checks at import time, a bare module-level setup() (which pytest 8+ no longer calls), or a
  module-level pytest.skip pointing at the file's own __main__ harness -- run as `python file`, exactly as their
  docstrings say. Under a shared pytest process those were either aborting collection or silently running nothing.

The terminal summary adds up the inner counts so the totals stay comparable with a shared-process run.
"""
from __future__ import annotations

import ast
import asyncio
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest

# Tests never touch the login Keychain: secrets go to a file in each test's temp data dir (children inherit it).
os.environ["GRAIN_SECRETS_BACKEND"] = "file"
# Nor the user's OpenCode data: the usage import reads a missing file unless a test points it at its own fake.
os.environ["PERSONAL_OS_OPENCODE_DB"] = os.path.join(tempfile.mkdtemp(prefix="no-opencode-"), "opencode.db")

BACKEND = Path(__file__).resolve().parent
REPO = BACKEND.parent
CHILD = os.environ.get("PERSONAL_OS_TEST_CHILD") == "1"
FILE_TIMEOUT = 900
_TOTALS: dict[str, int] = {}


# ---- inside a child (and for anything else sharing a process): every module and test finds an event loop ----
def _ensure_loop() -> None:
    """asyncio.run() clears the main thread's loop on return; a later get_event_loop() would raise."""
    try:
        if not asyncio.get_event_loop().is_closed():
            return
    except RuntimeError:
        pass
    asyncio.set_event_loop(asyncio.new_event_loop())


def pytest_collectstart(collector: Any) -> None:
    _ensure_loop()


def pytest_runtest_setup(item: Any) -> None:
    _ensure_loop()


# ---- the parent: one isolated run per file ----
def _defs(path: Path) -> tuple[bool, bool]:
    """(has pytest tests, has a bare module-level setup())"""
    try:
        tree = ast.parse(path.read_text())
    except (OSError, SyntaxError):
        return True, False  # let the inner pytest report it
    tests = any((isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith("test"))
                or (isinstance(n, ast.ClassDef) and n.name.startswith("Test")) for n in tree.body)
    setup = any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "setup" for n in tree.body)
    return tests, setup


def _opts_out(path: Path) -> bool:
    """Skips itself under pytest and names its own harness instead (test_cowork: its run tasks need one portal for
    the whole module, which pytest's per-request client would close). Its tests only ever run that way."""
    try:
        src = path.read_text()
    except OSError:
        return False
    return "allow_module_level=True" in src and '__name__ == "__main__"' in src


def _as_script(path: Path) -> bool:
    tests, setup = _defs(path)
    return not tests or setup or _opts_out(path)


class FileFailed(Exception):
    def __init__(self, proc: subprocess.CompletedProcess[str]):
        super().__init__(f"exited {proc.returncode}")
        self.proc = proc


_COUNT = re.compile(r"(\d+) (passed|failed|skipped|errors?|xfailed|xpassed|subtests passed|subtests failed)")


class FileItem(pytest.Item):
    def runtest(self) -> None:
        script = _as_script(self.path)
        env = dict(os.environ)
        # Fresh store per file: the files only setdefault this, so an inherited value would hand them all one database.
        env["PERSONAL_OS_DATA_DIR"] = tempfile.mkdtemp(prefix=f"pos-{self.path.stem}-")
        env["PYTHONPATH"] = os.pathsep.join(p for p in (str(BACKEND), env.get("PYTHONPATH", "")) if p)
        env["PERSONAL_OS_TEST_CHILD"] = "1"
        if script:
            cmd, cwd = [sys.executable, str(self.path)], REPO
        else:
            cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                   "-W", "ignore::DeprecationWarning", "-W", "ignore::FutureWarning", str(self.path)]
            cwd = BACKEND
        try:
            proc = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=FILE_TIMEOUT)
        except subprocess.TimeoutExpired as e:
            raise AssertionError(f"{self.path.name} did not finish within {FILE_TIMEOUT}s") from e
        if script:
            _TOTALS["script files"] = _TOTALS.get("script files", 0) + 1
        else:
            tail = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
            for n, kind in _COUNT.findall(tail):
                _TOTALS[kind] = _TOTALS.get(kind, 0) + int(n)
        if proc.returncode != 0:
            raise FileFailed(proc)

    def repr_failure(self, excinfo: Any, style: Any = None) -> str:  # type: ignore[override]
        if isinstance(excinfo.value, FileFailed):
            p = excinfo.value.proc
            return (f"{self.path.name} exited {p.returncode}\n--- stdout (tail) ---\n{p.stdout[-5000:]}\n"
                    f"--- stderr (tail) ---\n{p.stderr[-4000:]}")
        return str(super().repr_failure(excinfo))

    def reportinfo(self) -> tuple[Path, int, str]:
        return self.path, 0, f"{'script' if _as_script(self.path) else 'pytest'} {self.path.name}"


class FileRun(pytest.File):
    def collect(self) -> Any:
        yield FileItem.from_parent(self, name=self.path.stem)


def pytest_pycollect_makemodule(module_path: Path, parent: Any) -> Any:
    if CHILD or not module_path.name.startswith("test_"):
        return None
    return FileRun.from_parent(parent, path=module_path)


def pytest_terminal_summary(terminalreporter: Any) -> None:
    if CHILD or not _TOTALS:
        return
    order = ["passed", "failed", "error", "errors", "skipped", "xfailed", "xpassed", "subtests passed",
             "subtests failed", "script files"]
    parts = [f"{_TOTALS[k]} {k}" for k in order if _TOTALS.get(k)]
    terminalreporter.write_sep("=", "inner totals: " + ", ".join(parts))
