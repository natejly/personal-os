"""Linux secret store: a fake keyring backend behind the Secret Service module, file-then-store reads, fallbacks.

Runs on any OS: osplat.use("linux") builds the Linux platform and keyring gets a fake backend.
"""
from __future__ import annotations

import os
import stat
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
keyring = pytest.importorskip("keyring")

from keyring.backend import KeyringBackend  # noqa: E402
from keyring.errors import KeyringLocked  # noqa: E402

from personal_os import osplat  # noqa: E402
from personal_os.osplat import linux_secrets  # noqa: E402
from personal_os.secrets import SecretStore  # noqa: E402


class FakeKeyring(KeyringBackend):
    priority = 5

    def __init__(self) -> None:
        super().__init__()
        self.items: dict[tuple[str, str], str] = {}
        self.locked = False

    def get_password(self, service, username):
        if self.locked:
            raise KeyringLocked("collection is locked")
        return self.items.get((service, username))

    def set_password(self, service, username, password):
        if self.locked:
            raise KeyringLocked("collection is locked")
        self.items[(service, username)] = password

    def delete_password(self, service, username):
        if self.locked:
            raise KeyringLocked("collection is locked")
        from keyring.errors import PasswordDeleteError
        if (service, username) not in self.items:
            raise PasswordDeleteError("not found")
        del self.items[(service, username)]


@pytest.fixture
def kr(monkeypatch):
    monkeypatch.delenv("GRAIN_SECRETS_BACKEND", raising=False)
    saved = keyring.get_keyring()
    fake = FakeKeyring()
    keyring.set_keyring(fake)
    with osplat.use("linux"):
        yield fake
    keyring.set_keyring(saved)


def d() -> Path:
    return Path(tempfile.mkdtemp())


def test_set_get_via_store(kr):
    path = d()
    s = SecretStore(path)
    assert s.use_keychain
    blob = '{"a": "line\nüñí\'\\""}'
    s.set("tok", blob)
    assert kr.items[("Grain", "tok")] == blob
    assert not (path / ".secrets.json").exists()
    assert SecretStore(path).get("tok") == blob   # a fresh process reads it back from the store


def test_file_wins_on_read(kr):
    path = d()
    kr.items[("Grain", "k")] = "store"
    (path / ".secrets.json").write_text('{"k": "file"}')
    assert SecretStore(path).get("k") == "file"
    assert SecretStore(path).get("other") is None
    kr.items[("Grain", "only")] = "x"
    assert SecretStore(path).get("only") == "x"   # existing secrets are not migrated, but the store is still read


def test_set_drops_stale_file_copy(kr):
    path = d()
    (path / ".secrets.json").write_text('{"k": "old", "z": "keep"}')
    s = SecretStore(path)
    s.set("k", "new")
    assert kr.items[("Grain", "k")] == "new"
    assert '"k"' not in (path / ".secrets.json").read_text() and "keep" in (path / ".secrets.json").read_text()


def test_store_failure_falls_back_to_0600_file(kr):
    path = d()
    s = SecretStore(path)
    assert s.use_keychain
    kr.locked = True
    s.set("k", "v")
    f = path / ".secrets.json"
    assert stat.S_IMODE(f.stat().st_mode) == 0o600
    assert SecretStore(path).get("k") == "v"
    ok, reason = s.status()
    assert ok is False and "locked" in (reason or "")


def test_retries_the_probe_after_the_window(kr, monkeypatch):
    kr.locked = True
    s = SecretStore(d())
    assert s.status()[0] is False
    kr.locked = False
    assert s.status()[0] is False   # cached for a while
    monkeypatch.setattr(linux_secrets, "RETRY_AFTER", 0.0)
    assert s.status() == (True, None)


def test_unreachable_reports_reason():
    from keyring.backends import fail
    saved = keyring.get_keyring()
    keyring.set_keyring(fail.Keyring())
    try:
        with osplat.use("linux"):
            s = SecretStore(d(), backend=None)
            ok, reason = s.status()
            assert ok is False and reason
            s.set("k", "v")
            assert SecretStore(s.path.parent).get("k") == "v"
            assert osplat.current().secrets.keychain_available() is False
    finally:
        keyring.set_keyring(saved)


def test_timeout_counts_as_unreachable(kr, monkeypatch):
    import time
    monkeypatch.setattr(linux_secrets, "TIMEOUT", 0.1)
    monkeypatch.setattr(kr, "get_password", lambda *a: time.sleep(1))
    ok, reason = SecretStore(d()).status()
    assert ok is False and "did not answer" in (reason or "")


def test_forced_file_backend(kr, monkeypatch):
    monkeypatch.setenv("GRAIN_SECRETS_BACKEND", "file")
    path = d()
    s = SecretStore(path)
    assert s.status() == (False, "forced file backend")
    s.set("k", "v")
    assert not kr.items and (path / ".secrets.json").exists()
    from fastapi.testclient import TestClient
    from personal_os.app import AUTH_TOKEN, app, db
    c = TestClient(app, headers={"X-Personal-OS-Token": AUTH_TOKEN})
    body = c.get("/capabilities").json()
    assert body["capabilities"]["secrets_keychain"] is False and body["secrets_reason"] == "forced file backend"
    assert c.get("/settings").json()["secretsKeychain"] is False
