"""The interfaces every operating-system implementation fills in.

Nothing in here imports the rest of personal_os, so any module may import `osplat` without a cycle. A call site asks the
platform object (`osplat.current().desktop.open_path(...)`) or checks a capability (`osplat.has("screenshot")`); it never
reads `sys.platform` itself. A new operating system is one new file next to darwin.py that builds a `Platform`.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


class Unsupported(OSError):
    """The platform cannot do this. Callers normally check the capability first; this is the backstop."""


# The shared credential lists. They are the macOS ones and are what every platform uses until a platform needs its own.
KEYCHAIN_DIRS = ("Library/Keychains", "Library/Cookies", "Library/Containers/com.apple.Safari/Data/Library/Cookies")
SYSTEM_KEYCHAINS = ("/Library/Keychains",)
# Where the browsers keep a profile, relative to the home folder.
BROWSER_SUPPORT_DIR = "Library/Application Support"
# Browser profile folders under that parent (macOS names; another platform lists its own, or puts whole profile folders
# in keychain_dirs and returns ()).
BROWSER_DIRS = ("Google/Chrome", "Arc", "BraveSoftware", "Microsoft Edge", "Chromium", "Vivaldi", "Firefox")
APP_BUNDLE = "/Applications/Grain.app"


@runtime_checkable
class Paths(Protocol):
    def data_dir(self) -> Path: ...          # default app data folder (Electron normally sets PERSONAL_OS_DATA_DIR)
    def logs_dir(self) -> Path: ...          # default log folder for a packaged app
    def work_folder(self) -> Path: ...       # where file and shell tools work when a chat has no working folder
    def trash_dir(self) -> Path: ...         # where trash_local moves things
    def app_bundle(self) -> str: ...         # the installed app that no file tool may touch
    def keychain_dirs(self) -> tuple[str, ...]: ...      # relative to home: keychains and cookie jars
    def system_keychains(self) -> tuple[str, ...]: ...   # absolute
    def browser_support_dir(self) -> str: ...            # relative to home: parent of the browser profile folders
    def browser_dirs(self) -> tuple[str, ...]: ...       # relative to browser_support_dir: profile folders judged by file name
    def sensitive_labels(self) -> tuple[str, ...]: ...   # what the System access panel lists as asking first


@runtime_checkable
class Secrets(Protocol):
    def keychain_available(self) -> bool: ...
    def store_name(self) -> str: ...         # "keychain" or "file"
    def keychain_reason(self) -> str | None: ...   # why the OS store is unusable; None when it is usable
    def get(self, service: str, name: str) -> str | None: ...   # None = not there; OSError = the read failed
    def set(self, service: str, name: str, value: str) -> bool: ...   # False = refused, the caller keeps it in the file
    def delete(self, service: str, name: str) -> None: ...


@runtime_checkable
class Sandbox(Protocol):
    name: str
    def shell_available(self) -> bool: ...   # can a confined shell run
    def python_available(self) -> bool: ...  # can run_python be confined
    def wrap(self, argv: list[str], profile: str) -> list[str]: ...   # argv that runs `argv` confined by `profile`
    def failed_to_start(self, exit_code: int | None, output: str) -> bool: ...  # the confinement itself refused to start
    def shell_profile(self, writable: list[str], network: bool = False, proxy_port: int | None = None,
                      allow_hosts: list[str] | None = None, loopback: bool = False) -> str: ...
    def python_profile(self, work: str, py: str, socket_path: str | None = None, workspace: str | None = None) -> str: ...


@runtime_checkable
class Desktop(Protocol):
    def open_path(self, path: str, reveal: bool = False) -> None: ...   # open with the default app, or show in the file manager
    def open_url(self, url: str) -> None: ...
    def file_search_argv(self, query: str, roots: list[str], name_only: bool) -> list[str] | None: ...
    def trash(self, path: str) -> str: ...    # move to the Trash; returns where it ended up. Raises osplat.Unsupported (an OSError) with a user-facing message
    def trash_note(self) -> str: ...          # what to tell the user about getting it back
    def can(self, feature: str) -> bool: ...  # screenshot, file_search, shortcuts, frontmost_app, screen_recording, ...


@runtime_checkable
class Proc(Protocol):
    def memory_available_fraction(self) -> float | None: ...      # share of RAM free or reclaimable; None = unknown
    def group_of(self, pid: int) -> int | None: ...               # the process group a pid belongs to
    def new_group_kwargs(self) -> dict[str, Any]: ...             # Popen / create_subprocess_exec kwargs: own group
    def signal_group(self, pgid: int | None, sig: int) -> None: ...   # signal a whole group; never raises
    def kill_tree(self, pid: int, proc: Any = None) -> None: ...      # force-kill a child and its descendants; never raises


@dataclass(frozen=True)
class Platform:
    name: str                 # "darwin", "linux" or "win32"
    paths: Paths
    secrets: Secrets
    sandbox: Sandbox
    desktop: Desktop
    proc: Proc

    def capabilities(self) -> dict[str, bool]:
        sb, d, s, p = self.sandbox, self.desktop, self.secrets, self.proc
        return {
            "secrets_keychain": s.keychain_available(),
            "sandbox_shell": sb.shell_available(),
            "sandbox_python": sb.python_available(),
            "open_path": d.can("open_path"),
            "reveal_path": d.can("reveal_path"),
            "open_url": d.can("open_url"),
            "file_search": d.can("file_search"),
            "shortcuts": d.can("shortcuts"),
            "screenshot": d.can("screenshot"),
            "screen_recording": d.can("screen_recording"),
            "frontmost_app": d.can("frontmost_app"),
            "full_disk_access": d.can("full_disk_access"),
            "speech_recognition": d.can("speech_recognition"),
            "memory_probe": p.memory_available_fraction() is not None,
            "process_groups": p.new_group_kwargs() != {},
        }


# ---- shared building blocks ----
class PosixProc:
    """Process groups through os.killpg; memory is unknown unless a subclass reads it."""

    def memory_available_fraction(self) -> float | None:
        return None

    def group_of(self, pid: int) -> int | None:
        try:
            r = subprocess.run(["ps", "-o", "pgid=", "-p", str(pid)], capture_output=True, text=True, timeout=5)
            return int(r.stdout.strip()) if r.stdout.strip() else None
        except (OSError, ValueError, subprocess.SubprocessError):
            return None

    def new_group_kwargs(self) -> dict[str, Any]:
        return {"start_new_session": True}   # the child becomes its own group leader

    def signal_group(self, pgid: int | None, sig: int) -> None:
        if not pgid or pgid <= 1:
            return
        try:
            os.killpg(pgid, sig)
        except (ProcessLookupError, PermissionError, OSError):
            pass

    def kill_tree(self, pid: int, proc: Any = None) -> None:
        import signal
        try:
            os.killpg(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            if proc is not None:
                try:
                    proc.kill()
                except OSError:
                    pass


class NoSandbox:
    """A platform with no confinement yet: the shell and run_python report unavailable and are refused or run unconfined
    exactly as they do today."""
    name = "none"

    def shell_available(self) -> bool:
        return False

    def python_available(self) -> bool:
        return False

    def wrap(self, argv: list[str], profile: str) -> list[str]:
        raise Unsupported("no OS sandbox on this platform")

    def failed_to_start(self, exit_code: int | None, output: str) -> bool:
        return False

    def shell_profile(self, *a: Any, **k: Any) -> str:
        raise Unsupported("no OS sandbox on this platform")

    python_profile = shell_profile


class FileSecrets:
    def keychain_available(self) -> bool:
        return False

    def store_name(self) -> str:
        return "file"

    def keychain_reason(self) -> str | None:
        return "this platform has no secret store"

    def get(self, service: str, name: str) -> str | None:
        return None

    def set(self, service: str, name: str, value: str) -> bool:
        return False

    def delete(self, service: str, name: str) -> None:
        return None


def home() -> Path:
    return Path(os.path.expanduser("~"))


class CommonPaths:
    """Home-relative defaults; a platform overrides what differs."""

    def work_folder(self) -> Path:
        return home() / "Grain"

    def trash_dir(self) -> Path:
        return home() / ".Trash"

    def app_bundle(self) -> str:
        return APP_BUNDLE

    def keychain_dirs(self) -> tuple[str, ...]:
        return KEYCHAIN_DIRS

    def system_keychains(self) -> tuple[str, ...]:
        return SYSTEM_KEYCHAINS

    def browser_support_dir(self) -> str:
        return BROWSER_SUPPORT_DIR

    def browser_dirs(self) -> tuple[str, ...]:
        return BROWSER_DIRS

    def sensitive_labels(self) -> tuple[str, ...]:
        return ("~/.ssh", "Keychains", "Browser cookies and saved passwords", "~/.aws, ~/.gnupg, ~/.netrc, gh tokens", ".env files")


def run_opener(argv: list[str]) -> None:
    """Run a desktop opener (open, xdg-open): OSError / CalledProcessError / TimeoutExpired on failure."""
    subprocess.run(argv, check=True, timeout=10, capture_output=True)


def which(name: str) -> bool:
    return shutil.which(name) is not None
