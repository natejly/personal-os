"""Linux. Paths follow the XDG layout; opening goes through xdg-open; free memory comes from /proc/meminfo."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .base import CommonPaths, NoSandbox, Platform, PosixProc, home, run_opener, which


# Credential stores and browser profiles on Linux, relative to home. Whole folders: opening any file in one needs approval.
LINUX_CRED_DIRS = (
    ".local/share/keyrings", ".local/share/gnome-keyring", ".config/keyring", ".gnupg", ".password-store", ".pki",
    ".mozilla", "snap/firefox", ".var/app/org.mozilla.firefox", "snap/chromium", ".var/app/com.google.Chrome",
    ".var/app/org.chromium.Chromium", ".var/app/com.brave.Browser", ".var/app/com.microsoft.Edge",
    ".config/google-chrome", ".config/google-chrome-beta", ".config/chromium", ".config/BraveSoftware",
    ".config/microsoft-edge", ".config/vivaldi", ".config/opera", ".config/chrome-for-testing",
    ".config/Grain", ".config/grain", ".local/share/Grain", ".local/share/personal-os",
)


class LinuxPaths(CommonPaths):
    def keychain_dirs(self) -> tuple[str, ...]:
        return LINUX_CRED_DIRS

    def system_keychains(self) -> tuple[str, ...]:
        return ()

    def browser_support_dir(self) -> str:
        return ".config"

    def browser_dirs(self) -> tuple[str, ...]:
        return ()   # the profile folders are in keychain_dirs whole

    def sensitive_labels(self) -> tuple[str, ...]:
        return ("~/.ssh", "Keyrings and ~/.password-store", "Browser cookies and saved passwords",
                "~/.aws, ~/.gnupg, ~/.netrc, gh tokens", ".env files")

    def data_dir(self) -> Path:
        return Path(os.environ.get("XDG_DATA_HOME") or home() / ".local" / "share") / "personal-os" / "data"

    def logs_dir(self) -> Path:
        return Path(os.environ.get("XDG_STATE_HOME") or home() / ".local" / "state") / "Grain" / "logs"


def parse_meminfo(text: str) -> float | None:
    """MemAvailable / MemTotal from /proc/meminfo text; None when either is missing."""
    got: dict[str, int] = {}
    for line in (text or "").splitlines():
        key, _, rest = line.partition(":")
        if key in ("MemTotal", "MemAvailable"):
            try:
                got[key] = int(rest.split()[0])
            except (IndexError, ValueError):
                return None
    if got.get("MemTotal", 0) <= 0 or "MemAvailable" not in got:
        return None
    return min(1.0, got["MemAvailable"] / got["MemTotal"])


class LinuxProc(PosixProc):
    def memory_available_fraction(self) -> float | None:
        try:
            return parse_meminfo(Path("/proc/meminfo").read_text())
        except OSError:
            return None


class LinuxDesktop:
    def open_path(self, path: str, reveal: bool = False) -> None:
        # xdg-open has no "select this file": reveal opens the containing folder.
        target = str(Path(path).parent) if reveal else path
        run_opener(["xdg-open", target])

    def open_url(self, url: str) -> None:
        run_opener(["xdg-open", url])

    def file_search_argv(self, query: str, roots: list[str], name_only: bool) -> list[str] | None:
        return None

    def trash(self, path: str) -> str:
        from . import trash
        return trash.linux_trash(path)

    def trash_note(self) -> str:
        from . import trash
        return trash.LINUX_NOTE

    def can(self, feature: str) -> bool:
        if feature in ("open_path", "reveal_path", "open_url"):
            return which("xdg-open")
        return False


def _linux_secrets() -> Any:
    from .linux_secrets import LinuxSecrets
    return LinuxSecrets()


def build() -> Platform:
    return Platform("linux", LinuxPaths(), _linux_secrets(), NoSandbox(), LinuxDesktop(), LinuxProc())
