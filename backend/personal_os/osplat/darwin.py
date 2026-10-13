"""macOS. Keychain for secrets, Seatbelt (sandbox-exec) for confinement, `open`, Spotlight, Shortcuts and screen capture."""
from __future__ import annotations

import base64
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from . import seatbelt
from .base import CommonPaths, Platform, PosixProc, home, run_opener, which

log = logging.getLogger("personal_os.secrets")


class DarwinPaths(CommonPaths):
    def data_dir(self) -> Path:
        return home() / "Library" / "Application Support" / "personal-os" / "data"

    def logs_dir(self) -> Path:
        return home() / "Library" / "Logs" / "Grain"


class DarwinSecrets:
    """The login Keychain through the `security` CLI in interactive mode, fed on stdin so a secret never appears in argv.
    The value is base64-wrapped and passed hex-encoded (-X), which keeps newlines and non-ASCII safe through `-w`."""
    NOT_FOUND = 44   # `security` exit status for "item could not be found"

    def keychain_available(self) -> bool:
        return True   # SecretStore falls back to the file when `security` fails

    def keychain_reason(self) -> str | None:
        return None

    def store_name(self) -> str:
        return "keychain"

    def _security(self, command: str) -> subprocess.CompletedProcess[str] | None:
        try:
            return subprocess.run(["security", "-i"], input=command + "\n", capture_output=True, text=True, timeout=15)
        except (OSError, subprocess.SubprocessError) as e:
            log.warning("Keychain unavailable (%s); using the secrets file", e)
            return None

    @staticmethod
    def _quote(s: str) -> str:
        return "'" + s.replace("\\", "\\\\").replace("'", "\\'") + "'"

    def get(self, service: str, name: str) -> str | None:
        r = self._security(f"find-generic-password -s {self._quote(service)} -a {self._quote(name)} -w")
        if r is None:
            raise OSError("Keychain unavailable")
        if r.returncode != 0 or not r.stdout.strip():
            if r.returncode not in (0, self.NOT_FOUND):
                log.warning("Keychain read of %s failed (%s)", name, r.returncode)
                raise OSError(f"Keychain read failed ({r.returncode})")
            return None
        try:
            return base64.b64decode(r.stdout.strip()).decode()
        except ValueError:
            return None

    def set(self, service: str, name: str, value: str) -> bool:
        hexed = base64.b64encode(value.encode()).hex()
        r = self._security(f"add-generic-password -U -s {self._quote(service)} -a {self._quote(name)} -X {hexed}")
        if r is None:
            return False
        if r.returncode != 0:
            log.warning("Keychain write of %s failed (%s); using the secrets file", name, r.returncode)
            return False
        return True

    def delete(self, service: str, name: str) -> None:
        r = self._security(f"delete-generic-password -s {self._quote(service)} -a {self._quote(name)}")
        if r is not None and r.returncode not in (0, self.NOT_FOUND):
            log.warning("Keychain delete of %s failed (%s)", name, r.returncode)


class DarwinSandbox:
    name = "seatbelt"

    def shell_available(self) -> bool:
        return bool(shutil.which("sandbox-exec"))

    python_available = shell_available

    def wrap(self, argv: list[str], profile: str) -> list[str]:
        return ["sandbox-exec", "-p", profile, *argv]

    def failed_to_start(self, exit_code: int | None, output: str) -> bool:
        # sandbox-exec exits 65 or 71 and prints "sandbox-exec: ..." when the profile does not apply
        return exit_code in (65, 71) and output.lstrip().startswith("sandbox-exec:")

    def shell_profile(self, writable: list[str], network: bool = False, proxy_port: int | None = None,
                      allow_hosts: list[str] | None = None, loopback: bool = False) -> str:
        return seatbelt.shell_profile(writable, network=network, proxy_port=proxy_port, allow_hosts=allow_hosts, loopback=loopback)

    def python_profile(self, work: str, py: str, socket_path: str | None = None, workspace: str | None = None) -> str:
        return seatbelt.python_profile(work, py, socket_path, workspace)


def parse_vm_stat(text: str, total_bytes: int) -> float | None:
    """(free + inactive + speculative pages) / total memory, from `vm_stat` output; None when it cannot be read."""
    m = re.search(r"page size of (\d+) bytes", text or "")
    if not m or total_bytes <= 0:
        return None
    page = int(m.group(1))
    got = {k: int(v) for k, v in re.findall(r"Pages (free|inactive|speculative):\s+(\d+)", text)}
    if len(got) < 3:
        return None
    return min(1.0, sum(got.values()) * page / total_bytes)


class DarwinProc(PosixProc):
    def memory_available_fraction(self) -> float | None:
        try:
            total = os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
            out = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=3).stdout
        except (OSError, ValueError, subprocess.SubprocessError):
            return None
        return parse_vm_stat(out, total)


class DarwinDesktop:
    def open_path(self, path: str, reveal: bool = False) -> None:
        run_opener(["open", "-R", path] if reveal else ["open", path])

    def open_url(self, url: str) -> None:
        run_opener(["open", url])

    def file_search_argv(self, query: str, roots: list[str], name_only: bool) -> list[str] | None:
        argv = ["mdfind"]
        for r in roots:
            argv += ["-onlyin", r]
        return argv + (["-name", query] if name_only else [query])

    def trash(self, path: str) -> str:
        from . import trash
        return trash.darwin_trash(path)

    def trash_note(self) -> str:
        from . import trash
        return trash.MAC_NOTE

    def can(self, feature: str) -> bool:
        if feature == "file_search":
            return which("mdfind")
        if feature == "shortcuts":
            return which("shortcuts")
        # `open` and the Cocoa-backed probes (screen capture, frontmost app, TCC permissions, on-device speech) ship with the OS
        return feature in ("open_path", "reveal_path", "open_url", "screenshot", "screen_recording", "frontmost_app",
                           "full_disk_access", "speech_recognition")


def build() -> Platform:
    return Platform("darwin", DarwinPaths(), DarwinSecrets(), DarwinSandbox(), DarwinDesktop(), DarwinProc())
