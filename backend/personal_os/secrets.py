"""Secret storage: the macOS login Keychain, with a 0600 JSON file in the data dir as the fallback.

Every secret is one named string. Reads and writes go through an in-process cache so the settings
layer (read on nearly every request) never shells out more than once per name.

Keychain access uses the `security` CLI in interactive mode (`security -i`) and feeds the command on
stdin, so the secret never appears in argv (visible to `ps`). The value is base64-wrapped and passed
hex-encoded (-X), which keeps newlines and non-ASCII safe through `find-generic-password -w`.

A Keychain failure (locked, no GUI session, denied, no `security` binary) never raises: the secret goes to
the file instead and the failure is logged. Reads check the file first (it only holds what the Keychain refused, so it is newer), then the Keychain.

GRAIN_SECRETS_BACKEND=file forces the file backend (tests, headless installs).
"""
from __future__ import annotations

import base64
import json
import logging
import os
import subprocess
import sys
import threading
from pathlib import Path

from . import logs

log = logging.getLogger("personal_os.secrets")

SERVICE = os.environ.get("GRAIN_KEYCHAIN_SERVICE", "Grain")
FILE_NAME = ".secrets.json"
_NOT_FOUND = 44  # `security` exit status for "item could not be found"


def _teach_logs(value: str | None) -> None:
    """Every secret this store hands out or saves is redacted from the log by its exact value, from now on,
    not only from the next start. A JSON blob (the Google token's secret fields, MCP server keys) holds only
    secrets, so each of its string values is taught too."""
    if not value:
        return
    logs.register_secret(value)
    try:
        blob = json.loads(value)
    except ValueError:
        return
    if isinstance(blob, dict):
        for v in blob.values():
            if isinstance(v, str):
                logs.register_secret(v)


class SecretStore:
    def __init__(self, data_dir: str | Path, backend: str | None = None):
        self.path = Path(data_dir) / FILE_NAME
        want = (backend or os.environ.get("GRAIN_SECRETS_BACKEND") or "").strip().lower()
        self.use_keychain = want != "file" and sys.platform == "darwin"
        self._cache: dict[str, str | None] = {}
        self._lock = threading.RLock()

    # ---- public ----
    def get(self, name: str) -> str | None:
        with self._lock:
            if name in self._cache:
                return self._cache[name]
            # The file wins: it only holds a value the Keychain refused, so it is newer than any Keychain copy.
            value = self._file_read().get(name)
            if value is None and self.use_keychain:
                value = self._kc_get(name)
            self._cache[name] = value
            _teach_logs(value)
            return value

    def set(self, name: str, value: str) -> None:
        if not value:
            return self.delete(name)
        with self._lock:
            if self._cache.get(name) == value:
                return
            if self.use_keychain and self._kc_set(name, value):
                if name in self._file_read():  # drop a copy left from an earlier outage
                    self._file_drop(name)
            else:
                self._file_put(name, value)
            self._cache[name] = value
            _teach_logs(value)

    def delete(self, name: str) -> None:
        with self._lock:
            if self.use_keychain:
                self._kc_delete(name)
            if name in self._file_read():
                self._file_drop(name)
            self._cache[name] = None

    # ---- keychain ----
    def _security(self, command: str) -> subprocess.CompletedProcess[str] | None:
        try:
            return subprocess.run(["security", "-i"], input=command + "\n", capture_output=True, text=True, timeout=15)
        except (OSError, subprocess.SubprocessError) as e:
            log.warning("Keychain unavailable (%s); using the secrets file", e)
            return None

    @staticmethod
    def _quote(s: str) -> str:
        return "'" + s.replace("\\", "\\\\").replace("'", "\\'") + "'"

    def _kc_get(self, name: str) -> str | None:
        r = self._security(f"find-generic-password -s {self._quote(SERVICE)} -a {self._quote(name)} -w")
        if r is None:
            return None
        if r.returncode != 0 or not r.stdout.strip():
            if r.returncode not in (0, _NOT_FOUND):
                log.warning("Keychain read of %s failed (%s)", name, r.returncode)
            return None
        try:
            return base64.b64decode(r.stdout.strip()).decode()
        except ValueError:
            return None

    def _kc_set(self, name: str, value: str) -> bool:
        hexed = base64.b64encode(value.encode()).hex()
        r = self._security(f"add-generic-password -U -s {self._quote(SERVICE)} -a {self._quote(name)} -X {hexed}")
        if r is None:
            return False
        if r.returncode != 0:
            log.warning("Keychain write of %s failed (%s); using the secrets file", name, r.returncode)
            return False
        return True

    def _kc_delete(self, name: str) -> None:
        r = self._security(f"delete-generic-password -s {self._quote(SERVICE)} -a {self._quote(name)}")
        if r is not None and r.returncode not in (0, _NOT_FOUND):
            log.warning("Keychain delete of %s failed (%s)", name, r.returncode)

    # ---- file ----
    def _file_read(self) -> dict[str, str]:
        try:
            data = json.loads(self.path.read_text())
            return {k: v for k, v in data.items() if isinstance(v, str)} if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _file_write(self, data: dict[str, str]) -> None:
        tmp = self.path.with_name(self.path.name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps(data))
        os.replace(tmp, self.path)
        os.chmod(self.path, 0o600)

    def _file_put(self, name: str, value: str) -> None:
        data = self._file_read()
        data[name] = value
        self._file_write(data)

    def _file_drop(self, name: str) -> None:
        data = self._file_read()
        data.pop(name, None)
        self._file_write(data)
