"""Linux secret store: the freedesktop Secret Service (GNOME Keyring, KWallet) over D-Bus, through the `keyring` package.

The Secret Service can be missing (headless, no session bus), locked with nobody to unlock it, or slow because it is
waiting on an unlock dialog. Every call therefore runs on a short-lived thread with a timeout, and the answer to "is it
usable" is cached: a good answer for good, a bad one for RETRY_AFTER seconds, so a service that comes up later is picked
up without a restart. While it is unusable the caller (secrets.SecretStore) keeps secrets in the 0600 file.
Secret values are never logged.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable

log = logging.getLogger("personal_os.secrets")

TIMEOUT = 8.0         # seconds one Secret Service call may take before it counts as unreachable
RETRY_AFTER = 60.0    # seconds before a failed probe is tried again
PROBE_NAME = "__grain_probe__"


class LinuxSecrets:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._ok: bool | None = None
        self._reason: str | None = None
        self._at = 0.0

    # ---- state ----
    def _call(self, fn: Callable[[], Any]) -> Any:
        """Run fn on a daemon thread; raise TimeoutError if it does not finish in time."""
        box: dict[str, Any] = {}

        def run() -> None:
            try:
                box["v"] = fn()
            except BaseException as e:  # noqa: BLE001 - handed back to the caller
                box["e"] = e

        t = threading.Thread(target=run, daemon=True, name="secret-service")
        t.start()
        t.join(TIMEOUT)
        if t.is_alive():
            raise TimeoutError("the secret service did not answer (is the keyring waiting to be unlocked?)")
        if "e" in box:
            raise box["e"]
        return box.get("v")

    @staticmethod
    def _backend() -> Any:
        import keyring
        from keyring.backends import fail
        kr = keyring.get_keyring()
        if isinstance(kr, fail.Keyring) or getattr(kr, "priority", 1) <= 0:
            raise RuntimeError("no secret service is running on this session")
        return kr

    @staticmethod
    def _describe(e: BaseException) -> str:
        text = str(e).strip().splitlines()[0] if str(e).strip() else ""
        return (text or type(e).__name__)[:200]

    def _probe(self) -> tuple[bool, str | None]:
        try:
            kr = self._call(self._backend)
            self._call(lambda: kr.get_password("Grain", PROBE_NAME))   # forces a connection and exposes a locked keyring
            return True, None
        except Exception as e:  # noqa: BLE001
            return False, self._describe(e)

    def _state(self) -> tuple[bool, str | None]:
        with self._lock:
            now = time.monotonic()
            if self._ok is None or (not self._ok and now - self._at >= RETRY_AFTER):
                self._ok, self._reason = self._probe()
                self._at = now
                if not self._ok:
                    log.warning("Secret Service unavailable (%s); using the secrets file", self._reason)
            return bool(self._ok), self._reason

    def _fail(self, reason: str) -> None:
        with self._lock:
            self._ok, self._reason, self._at = False, reason, time.monotonic()

    # ---- Secrets protocol ----
    def keychain_available(self) -> bool:
        return self._state()[0]

    def keychain_reason(self) -> str | None:
        ok, reason = self._state()
        return None if ok else (reason or "no secret service")

    def store_name(self) -> str:
        return "keychain" if self.keychain_available() else "file"

    def get(self, service: str, name: str) -> str | None:
        try:
            return self._call(lambda: self._backend().get_password(service, name))
        except Exception as e:  # noqa: BLE001
            self._fail(self._describe(e))
            log.warning("Secret Service read of %s failed (%s)", name, self._describe(e))
            return None

    def set(self, service: str, name: str, value: str) -> bool:
        try:
            self._call(lambda: self._backend().set_password(service, name, value))
            return True
        except Exception as e:  # noqa: BLE001
            self._fail(self._describe(e))
            log.warning("Secret Service write of %s failed (%s); using the secrets file", name, self._describe(e))
            return False

    def delete(self, service: str, name: str) -> None:
        try:
            self._call(lambda: self._backend().delete_password(service, name))
        except Exception as e:  # noqa: BLE001
            import keyring.errors
            if not isinstance(e, keyring.errors.PasswordDeleteError):
                self._fail(self._describe(e))
                log.warning("Secret Service delete of %s failed (%s)", name, self._describe(e))
