"""Windows. Paths follow %APPDATA% / %LOCALAPPDATA%; opening goes through os.startfile; processes are killed with taskkill."""
from __future__ import annotations

import os
import subprocess
from typing import Any
from pathlib import Path

from .base import CommonPaths, FileSecrets, NoSandbox, Platform, home


# Credential stores and browser profiles on Windows, relative to home (AppData is where %APPDATA% / %LOCALAPPDATA% live).
WIN_CRED_DIRS = (
    "AppData/Roaming/Microsoft/Credentials", "AppData/Roaming/Microsoft/Protect", "AppData/Roaming/Microsoft/Vault",
    "AppData/Local/Microsoft/Credentials", "AppData/Local/Microsoft/Vault",
    "AppData/Local/Google/Chrome/User Data", "AppData/Local/Microsoft/Edge/User Data",
    "AppData/Local/BraveSoftware", "AppData/Local/Vivaldi", "AppData/Roaming/Opera Software",
    "AppData/Roaming/Mozilla/Firefox/Profiles", "AppData/Local/Mozilla/Firefox/Profiles",
    "AppData/Roaming/Grain", "AppData/Roaming/personal-os", "AppData/Local/Grain", ".gnupg",
)


class WindowsPaths(CommonPaths):
    def keychain_dirs(self) -> tuple[str, ...]:
        return WIN_CRED_DIRS

    def system_keychains(self) -> tuple[str, ...]:
        return ()

    def browser_dirs(self) -> tuple[str, ...]:
        return ()

    def sensitive_labels(self) -> tuple[str, ...]:
        return ("~/.ssh", "Windows Credential Manager and DPAPI keys", "Browser cookies and saved passwords",
                "~/.aws, ~/.gnupg, ~/.netrc, gh tokens", ".env files")

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

    def trash(self, path: str) -> str:
        from . import trash
        return trash.win32_trash(path)

    def trash_note(self) -> str:
        from . import trash
        return trash.WINDOWS_NOTE

    def can(self, feature: str) -> bool:
        return feature in ("open_path", "reveal_path", "open_url")


def build() -> Platform:
    return Platform("win32", WindowsPaths(), FileSecrets(), NoSandbox(), WindowsDesktop(), WindowsProc())
