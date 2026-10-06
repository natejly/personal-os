"""Where a stdio connector's command lives.

The packaged app is launched by the Dock, whose PATH is the bare system one: `npx`, `uvx` and `docker`
(Homebrew, nvm, Docker Desktop, ~/.local/bin) are all invisible to it, and a spawn would fail with a bare
ENOENT. This module rebuilds the PATH the user's own terminal has - the login shell's, then the usual
install dirs - once, and resolves a command against it so a missing runtime becomes a readable status
("npx not found. Install Node.js ...") instead of a stack trace.

The server's own env always wins: a PATH set on the server is used as given and never extended.
"""
from __future__ import annotations

import functools
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from . import shell

PROBE_TIMEOUT = 5.0  # a login shell that takes longer (a hung rc file) is abandoned; the common dirs still apply
MARKER = "__GRAIN_PATH__"  # brackets the value in stdout so rc-file chatter around it cannot corrupt the PATH
DOCKER_DESKTOP_BIN = "/Applications/Docker.app/Contents/Resources/bin"

# What to tell the user, by the command that was not found.
RUNTIME_HINTS = {
    "npx": "Install Node.js (nodejs.org)", "node": "Install Node.js (nodejs.org)", "npm": "Install Node.js (nodejs.org)",
    "uvx": "Install uv (docs.astral.sh/uv)", "uv": "Install uv (docs.astral.sh/uv)",
    "docker": "Install Docker Desktop (docker.com)",
    "bunx": "Install Bun (bun.sh)", "bun": "Install Bun (bun.sh)",
    "deno": "Install Deno (deno.com)",
}
FALLBACK_HINT = "Install it, or give the full path to the command"
# The runtimes the catalog's `runtime` field names, as the catalog screen shows them.
CATALOG_RUNTIMES = {"node": "npx", "python": "uvx", "docker": "docker"}


def _login_shell_path() -> str:
    """$PATH as the user's login shell sees it ('' when it cannot be read in time)."""
    sh = os.environ.get("SHELL") or "/bin/zsh"
    try:
        # -i as well as -l: nvm, volta and friends are usually set up in the interactive rc file.
        r = subprocess.run([sh, "-ilc", f'printf "%s%s%s" {MARKER} "$PATH" {MARKER}'], capture_output=True, text=True,
                           timeout=PROBE_TIMEOUT, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return ""
    parts = r.stdout.split(MARKER)
    return parts[1] if len(parts) >= 3 else ""


def _node_bins(home: Path) -> list[str]:
    """nvm keeps one bin dir per installed Node, newest last; prefer the newest."""
    root = home / ".nvm" / "versions" / "node"
    try:
        found = sorted((p for p in root.iterdir() if p.is_dir()), key=lambda p: [int(x) if x.isdigit() else 0 for x in p.name.lstrip("v").split(".")])
    except OSError:
        return []
    return [str(p / "bin") for p in reversed(found)]


def common_dirs() -> list[str]:
    home = Path.home()
    return ["/opt/homebrew/bin", "/usr/local/bin", str(home / ".local/bin"), str(home / ".volta/bin"), *_node_bins(home),
            str(home / ".bun/bin"), str(home / ".cargo/bin"), str(home / ".deno/bin"), DOCKER_DESKTOP_BIN]


def build_path(login: str, extra: list[str], base: str = shell.SAFE_PATH) -> str:
    """login-shell dirs first (the user's own order), then the common dirs, then the base; existing dirs only, deduped."""
    seen: list[str] = []
    for d in [*login.split(os.pathsep), *extra, *base.split(os.pathsep)]:
        if d and d not in seen and os.path.isdir(d):
            seen.append(d)
    return os.pathsep.join(seen)


@functools.lru_cache(maxsize=1)
def user_path() -> str:
    """Resolved once per process: the login-shell probe spawns a shell, so it is not repeated per connection."""
    return build_path(_login_shell_path(), common_dirs())


def resolve(command: str, env: dict[str, str] | None = None) -> str | None:
    """Absolute path of `command` on env's PATH (the user's PATH when env has none), or None.
    A command with a slash is taken as given and only checked for being executable."""
    path = (env or {}).get("PATH") or user_path()
    return shutil.which(command.strip(), path=path)


def hint_for(command: str) -> str:
    return RUNTIME_HINTS.get(os.path.basename(command.strip()), FALLBACK_HINT)


def missing_message(command: str) -> str:
    return f"{command.strip()} not found. {hint_for(command)}, then restart this connector."


def runtimes() -> dict[str, dict[str, Any]]:
    """GET /mcp/catalog's `runtimes`: is each launcher the catalog relies on installed?"""
    out: dict[str, dict[str, Any]] = {}
    for name, cmd in CATALOG_RUNTIMES.items():
        path = resolve(cmd)
        out[name] = {"command": cmd, "found": path is not None, "path": path, "hint": hint_for(cmd)}
    return out
