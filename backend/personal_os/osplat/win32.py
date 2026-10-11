"""Windows. Paths follow %APPDATA% / %LOCALAPPDATA%; opening goes through os.startfile; processes are killed with taskkill."""
from __future__ import annotations

import os
import subprocess
from typing import Any
from pathlib import Path

from .base import CommonPaths, FileSecrets, NoSandbox, Platform, home


class WindowsPaths(CommonPaths):
    def data_dir(self) -> Path:
        return Path(os.environ.get("APPDATA") or home() / "AppData" / "Roaming") / "personal-os" / "data"

    def logs_dir(self) -> Path:
        return Path(os.environ.get("LOCALAPPDATA") or home() / "AppData" / "Local") / "Grain" / "Logs"

    def app_bundle(self) -> str:
        return str(Path(os.environ.get("LOCALAPPDATA") or home() / "AppData" / "Local") / "Programs" / "Grain")


class WindowsProc:
    def memory_available_fraction(self) -> float | None:
        return None

    def group_of(self, pid: int) -> int | None:
        return None

    def new_group_kwargs(self) -> dict[str, Any]:
        return {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200)}

    def signal_group(self, pgid: int | None, sig: int) -> None:
        self.kill_tree(pgid or 0)

    def kill_tree(self, pid: int, proc: Any = None) -> None:
        if pid and pid > 4:
            try:
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=10)
                return
            except (OSError, subprocess.SubprocessError):
                pass
        if proc is not None:
            try:
                proc.kill()
            except OSError:
                pass


class WindowsDesktop:
    def open_path(self, path: str, reveal: bool = False) -> None:
        if reveal:
            subprocess.run(["explorer", f"/select,{path}"], timeout=10, capture_output=True)  # explorer exits 1 on success
        else:
            os.startfile(path)  # type: ignore[attr-defined]  # noqa: S606

    def open_url(self, url: str) -> None:
        os.startfile(url)  # type: ignore[attr-defined]  # noqa: S606

    def file_search_argv(self, query: str, roots: list[str], name_only: bool) -> list[str] | None:
        return None

    def can(self, feature: str) -> bool:
        return feature in ("open_path", "reveal_path", "open_url")


def build() -> Platform:
    return Platform("win32", WindowsPaths(), FileSecrets(), NoSandbox(), WindowsDesktop(), WindowsProc())
