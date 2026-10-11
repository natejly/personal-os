"""Linux. Paths follow the XDG layout; opening goes through xdg-open; free memory comes from /proc/meminfo."""
from __future__ import annotations

import os
from pathlib import Path

from .base import CommonPaths, FileSecrets, NoSandbox, Platform, PosixProc, home, run_opener, which


class LinuxPaths(CommonPaths):
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

    def can(self, feature: str) -> bool:
        if feature in ("open_path", "reveal_path", "open_url"):
            return which("xdg-open")
        return False


def build() -> Platform:
    return Platform("linux", LinuxPaths(), FileSecrets(), NoSandbox(), LinuxDesktop(), LinuxProc())
