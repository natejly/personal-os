"""The shared work environment: a Python venv with the document and data libraries, and python_install.

A desk agent doing knowledge work needs pandas, openpyxl, python-docx and friends, and the app's own interpreter
should not grow them (nor should a model-driven install touch it). So there is one virtualenv at
`<data_dir>/envs/work`, created on demand, that `run_python` uses when it is ready and that the shell puts first on
PATH (`work_bin()`).

Installs are the one place this module runs third-party code, so they are narrow: package names are validated as
plain PyPI requirements (no URLs, paths, options or whitespace, so `-e`, `--index-url` or `git+https://...` cannot
be smuggled in), at most ten per call, and wheels only (`--only-binary=:all:`) so no package's build script runs.
`python_install` is danger `external`: it asks, and the card shows exactly the packages.

Everything blocking runs through `asyncio.to_thread` at the call site; `WorkEnv` itself is synchronous and takes an
injectable `runner` so tests never touch the network or build a venv.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Callable

BASE_PACKAGES = ("pandas", "openpyxl", "xlsxwriter", "python-docx", "python-pptx", "pypdf", "pdfplumber", "reportlab",
                 "matplotlib", "pillow")
INSTALL_TIMEOUT = 600
MAX_PACKAGES_PER_CALL = 10
MARKER = ".grain-env.json"  # written only after a full install, so a half-built venv never reads as ready

# name, optional [extras], optional single version specifier. Starts with an alphanumeric, so never an option.
_NAME = r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?"
PACKAGE_RE = re.compile(rf"^{_NAME}(?:\[{_NAME}(?:,{_NAME})*\])?(?:(?:==|>=|<=|~=|!=|<|>)[A-Za-z0-9][A-Za-z0-9.*+!_-]*)?$")

_active: "WorkEnv | None" = None  # the app's environment, for the module-level work_bin()


class EnvError(Exception):
    """A clean one-line failure for the tool error envelope."""


def validate_packages(packages: Any) -> list[str]:
    """The cleaned list, or EnvError. Strict on purpose: this string ends up on a pip command line."""
    if not isinstance(packages, list) or not packages:
        raise EnvError("packages must be a non-empty list of package names, e.g. [\"pandas\", \"scipy>=1.11\"]")
    if len(packages) > MAX_PACKAGES_PER_CALL:
        raise EnvError(f"at most {MAX_PACKAGES_PER_CALL} packages per call, got {len(packages)}")
    out: list[str] = []
    for raw in packages:
        if not isinstance(raw, str) or not PACKAGE_RE.match(raw):
            raise EnvError(f"{str(raw)[:60]!r} is not a plain package requirement; use a PyPI name with an optional "
                           "extra and version, like 'scipy' or 'scipy>=1.11'. URLs, paths, options and spaces are refused")
        if raw not in out:
            out.append(raw)
    return out


def _norm(req: str) -> str:
    """The distribution name of a requirement, normalised the way PyPI does (case, `_`/`.`/`-`)."""
    name = re.split(r"[\[<>=!~]", req, maxsplit=1)[0]
    return re.sub(r"[-_.]+", "-", name).lower()


class WorkEnv:
    def __init__(self, data_dir: str | Path, settings_fn: Callable[[], dict[str, Any]],
                 runner: Callable[..., Any] = subprocess.run) -> None:
        global _active
        self.dir = Path(data_dir) / "envs" / "work"
        self.settings_fn = settings_fn
        self.runner = runner
        self.error: str | None = None
        self._lock = threading.Lock()
        _active = self

    # ---- layout ----
    @property
    def _python(self) -> Path:
        return self.dir / "bin" / "python"

    def _installed(self) -> list[str]:
        try:
            data = json.loads((self.dir / MARKER).read_text(encoding="utf-8"))
            return [str(p) for p in data.get("packages", [])]
        except (OSError, ValueError, AttributeError):
            return []

    def _ready(self) -> bool:
        return self._python.exists() and (self.dir / MARKER).is_file()

    def installer(self) -> str:
        return "uv" if shutil.which("uv") else "pip"

    def python_path(self) -> str | None:
        return str(self._python) if self._ready() else None

    def status(self) -> dict[str, Any]:
        return {"ready": self._ready(), "python": self.python_path(), "packages": self._installed(),
                "installer": self.installer(), "error": self.error}

    # ---- commands (separate so tests can read the exact line) ----
    def _venv_cmd(self) -> list[str]:
        if self.installer() == "uv":
            # --seed puts pip in the venv: the shell has this bin first on PATH, and `pip install` there must not fall through to nothing.
            return ["uv", "venv", str(self.dir), "--seed", "--python", sys.executable]
        return [sys.executable, "-m", "venv", str(self.dir)]

    def _install_cmd(self, packages: list[str]) -> list[str]:
        if self.installer() == "uv":
            return ["uv", "pip", "install", "--python", str(self._python), "--only-binary=:all:", *packages]
        return [str(self._python), "-m", "pip", "install", "--disable-pip-version-check", "--only-binary=:all:", *packages]

    def _run(self, cmd: list[str]) -> None:
        try:
            p = self.runner(cmd, capture_output=True, text=True, timeout=INSTALL_TIMEOUT, env={**os.environ, "PIP_NO_INPUT": "1"})
        except subprocess.TimeoutExpired:
            raise EnvError(f"{cmd[0]} timed out after {INSTALL_TIMEOUT}s") from None
        except OSError as e:
            raise EnvError(f"could not run {cmd[0]}: {e.strerror or e}") from None
        if getattr(p, "returncode", 1) != 0:
            tail = ((getattr(p, "stderr", "") or getattr(p, "stdout", "") or "").strip().splitlines() or ["failed"])[-3:]
            raise EnvError(f"{' '.join(cmd[:3])} failed: {' / '.join(tail)}"[:500])

    def _write_marker(self, packages: list[str]) -> None:
        have = self._installed()
        have += [p for p in packages if p not in have]
        (self.dir / MARKER).write_text(json.dumps({"packages": have}), encoding="utf-8")

    def _wanted(self) -> list[str]:
        extra = self.settings_fn().get("workEnvPackages") or []
        try:
            extra = validate_packages(list(extra)) if extra else []
        except EnvError:
            extra = []  # a bad setting must not stop the base set from installing
        return [*BASE_PACKAGES, *[p for p in extra if _norm(p) not in {_norm(b) for b in BASE_PACKAGES}]]

    # ---- operations ----
    def ensure(self) -> dict[str, Any]:
        """Create the venv and install the base set plus `workEnvPackages`. A no-op when all of it is present."""
        with self._lock:
            return self._ensure_locked()

    def _ensure_locked(self) -> dict[str, Any]:
        want = self._wanted()
        done = {_norm(p) for p in self._installed()}
        missing = [p for p in want if _norm(p) not in done]
        if self._ready() and not missing:
            self.error = None
            return self.status()
        try:
            if not self._python.exists():
                self.dir.parent.mkdir(parents=True, exist_ok=True)
                self._run(self._venv_cmd())
            self._run(self._install_cmd(missing or want))
            self._write_marker(missing or want)
            self.error = None
        except EnvError as e:
            self.error = str(e)
        return self.status()

    def install(self, packages: list[str]) -> dict[str, Any]:
        """Add packages to the environment (creating it first when needed)."""
        pkgs = validate_packages(packages)
        with self._lock:
            st = self._ensure_locked()
            if not st["ready"]:
                return st
            try:
                self._run(self._install_cmd(pkgs))
                self._write_marker(pkgs)
                self.error = None
            except EnvError as e:
                self.error = str(e)
            return self.status()


def work_bin() -> str | None:
    """The environment's `bin` directory once it is ready, for PATH; None until then."""
    env = _active
    if env is None or not env._ready():
        return None
    return str(env.dir / "bin")


def register(tb: Any) -> None:
    """Register python_install. The WorkEnv is found lazily (`tb.work_env`, assigned in app.py after construction)."""
    from .tools import ToolSpec, _obj, tool_error

    async def python_install(ctx: dict[str, Any], packages: list[str]) -> Any:
        env: WorkEnv | None = getattr(tb, "work_env", None)
        if env is None:
            return tool_error("The work environment is not available in this run.", alternative="run_python with what is already installed")
        try:
            pkgs = validate_packages(packages)
        except EnvError as e:
            return tool_error(f"python_install: {e}", field="packages", example={"packages": ["scipy", "seaborn>=0.13"]})
        st = await asyncio.to_thread(env.install, pkgs)
        if not st["ready"] or st["error"]:
            return tool_error(f"python_install failed: {st['error'] or 'the environment is not ready'}", field="packages",
                              alternative="check the package name, or do it with the libraries already installed")
        return {"installed": pkgs, **st}

    tb.specs["python_install"] = ToolSpec(
        "python_install",
        "Install Python packages into the shared work environment that run_python uses (wheels only, from the package index). "
        "The environment already carries pandas, openpyxl, xlsxwriter, python-docx, python-pptx, pypdf, pdfplumber, reportlab, "
        "matplotlib and pillow once it is set up; use this for anything else. Asks the user first.",
        _obj({"packages": {"type": "array", "items": {"type": "string"},
                           "description": "PyPI names with an optional version, e.g. ['scipy', 'seaborn>=0.13']; at most 10"}}, ["packages"]),
        python_install, "code", "external",
        examples=[{"packages": ["scipy"]}, {"packages": ["seaborn>=0.13", "statsmodels"]}])
