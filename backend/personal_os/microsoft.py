"""Microsoft 365 / Outlook integration: Entra public-client OAuth (PKCE, loopback redirect) and one Graph HTTP helper.

A public client has no client secret, so there is nothing to ship or hide except the app's client id, which
comes from MICROSOFT_CLIENT_ID (and MICROSOFT_TENANT, default `common`) in the environment, or from the
Settings overrides microsoftClientId / microsoftTenant. Only the access and refresh tokens are secret; they
live in the SecretStore (see db.TOKEN_SECRET_FIELDS) and the rest of settings.microsoftToken stays in SQLite.

Identity is the Graph `oid` (stable per account), kept in the token next to the display email.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Callable

import httpx

from .cache import TTLCache
from .google import GoogleNotConnected, _local_tz
from .microsoft_calendar import CalendarMixin
from .microsoft_mail import MailMixin

log = logging.getLogger(__name__)

PENDING_KEY = "microsoftAuthPending"
PENDING_TTL = 15 * 60
TOKEN_KEY = "microsoftToken"
GRAPH = "https://graph.microsoft.com/v1.0"

SCOPES = [
    "openid", "profile", "offline_access",
    "User.Read", "Mail.ReadWrite", "Mail.Send", "Calendars.ReadWrite",
]
# Graph does not echo these back in the token response's `scope`.
_IMPLICIT = {"openid", "profile", "offline_access", "email"}
# Refresh errors that mean the sign-in itself is dead (as opposed to the network or a 5xx).
_DEAD_GRANT = {"invalid_grant", "interaction_required", "consent_required", "login_required"}


class MicrosoftNotConnected(GoogleNotConnected):
    pass


class GraphError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(f"Microsoft Graph {status}: {message}")
        self.status = status
        self.message = message


def _claims(id_token: Any) -> dict[str, Any]:
    """The claims of an OpenID id_token, or {}. Unverified: it came straight from the token endpoint over TLS."""
    try:
        part = str(id_token).split(".")[1]
        c = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
        return c if isinstance(c, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _one_sentence(text: str) -> str:
    """Entra's error_description carries trace and correlation ids on later lines; keep the first line."""
    return (text or "").strip().splitlines()[0].strip() if (text or "").strip() else ""


def _err(r: Any) -> tuple[str, str]:
    """(error code, one-line description) of a failed token-endpoint response."""
    try:
        body = r.json()
    except Exception:  # noqa: BLE001
        body = {}
    body = body if isinstance(body, dict) else {}
    return str(body.get("error") or ""), _one_sentence(str(body.get("error_description") or "")) or f"HTTP {r.status_code}"


class Microsoft(MailMixin, CalendarMixin):
    """Auth + the one Graph HTTP helper; mail and calendar live in the mixins."""
    def __init__(self, get_settings: Callable[[], dict[str, Any]], set_settings: Callable[[dict[str, Any]], None], cache_dir: str | Path | None = None):
        self.get_settings = get_settings
        self.set_settings = set_settings
        self._cache = TTLCache()
        self._lock = threading.Lock()
        self._me: dict[str, Any] | None = None

    # ---------- cache ----------
    def invalidate(self, *namespaces: str) -> int:
        return self._cache.invalidate(*namespaces)

    def cache_stats(self) -> dict[str, Any]:
        return self._cache.stats()

    def forget(self, *namespaces: str) -> int:
        return self._cache.invalidate(*namespaces)

    def _reset_clients(self) -> None:
        """Drop cached reads and the cached profile - used when the account changes."""
        self._me = None
        self._cache.clear()

    # ---------- status / auth ----------
    def _client(self) -> tuple[str | None, str, str | None]:
        """(client_id, tenant, source). Settings win over the environment; tenant defaults to `common`."""
        s = self.get_settings()
        cid, tenant = (s.get("microsoftClientId") or "").strip(), (s.get("microsoftTenant") or "").strip()
        if cid:
            return cid, tenant or os.environ.get("MICROSOFT_TENANT", "").strip() or "common", "settings"
        cid = os.environ.get("MICROSOFT_CLIENT_ID", "").strip()
        if cid:
            return cid, tenant or os.environ.get("MICROSOFT_TENANT", "").strip() or "common", "env"
        return None, tenant or os.environ.get("MICROSOFT_TENANT", "").strip() or "common", None

    def _endpoint(self, tenant: str, leaf: str) -> str:
        return f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/{leaf}"

    def status(self) -> dict[str, Any]:
        tok = self.get_settings().get(TOKEN_KEY) or {}
        cid, tenant, source = self._client()
        connected = bool(tok.get("refresh_token"))
        granted = [g.rsplit("/", 1)[-1] for g in tok.get("scopes") or []]
        have = {g.lower() for g in granted}
        missing = [s for s in SCOPES if s not in _IMPLICIT and s.lower() not in have] if connected and granted else []
        stale_client = bool(connected and cid and tok.get("client_id") and tok["client_id"] != cid)
        return {
            "configured": bool(cid),
            "source": source,
            "connected": connected,
            "email": tok.get("email"),
            "connected_at": tok.get("connected_at"),
            "scopes": granted,
            "missing_scopes": missing,
            "needs_reauth": bool(connected and (tok.get("needs_reauth") or missing or stale_client)),
            "reauth_reason": (
                "Sign-in expired or was revoked in your Microsoft account." if tok.get("needs_reauth")
                else "The app registration changed, so the saved sign-in no longer works." if stale_client
                else f"Missing permissions: {', '.join(missing)}." if missing
                else None
            ),
            "oid": tok.get("oid"),
            "tenant": tok.get("tenant") or tenant,
            "name": tok.get("name"),
        }

    def _pending_states(self) -> dict[str, Any]:
        pending = self.get_settings().get(PENDING_KEY) or {}
        now = time.time()
        return {k: v for k, v in pending.items() if isinstance(v, dict) and now - float(v.get("ts", 0)) < PENDING_TTL}

    def start_auth(self, redirect_uri: str) -> str:
        cid, tenant, _ = self._client()
        if not cid:
            raise ValueError(
                "No Microsoft app registered. Put MICROSOFT_CLIENT_ID in .env (see .env.example and docs/microsoft.md) "
                "or enter a client id in Settings → Integrations."
            )
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        state = secrets.token_urlsafe(24)
        pending = self._pending_states()
        pending[state] = {"verifier": verifier, "redirect": redirect_uri, "ts": time.time()}
        self.set_settings({PENDING_KEY: pending})
        q = httpx.QueryParams({
            "client_id": cid, "response_type": "code", "redirect_uri": redirect_uri, "response_mode": "query",
            "scope": " ".join(SCOPES), "state": state, "code_challenge": challenge, "code_challenge_method": "S256",
            "prompt": "select_account",
        })
        return f"{self._endpoint(tenant, 'authorize')}?{q}"

    def finish_auth(self, state: str, code: str) -> dict[str, Any]:
        pending = self._pending_states()
        entry = pending.pop(state, None)
        self.set_settings({PENDING_KEY: pending})
        if not entry:
            raise ValueError("This sign-in link has expired. Click “Sign in with Microsoft” again.")
        cid, tenant, _ = self._client()
        if not cid:
            raise ValueError("No Microsoft app registered.")
        r = httpx.post(self._endpoint(tenant, "token"), data={
            "client_id": cid, "grant_type": "authorization_code", "code": code, "redirect_uri": entry["redirect"],
            "code_verifier": entry.get("verifier"), "scope": " ".join(SCOPES),
        }, timeout=20)
        if r.status_code != 200:
            _, desc = _err(r)
            log.warning("Microsoft token exchange failed: %s", desc)
            raise ValueError(desc)
        body = r.json()
        if not body.get("refresh_token"):
            log.warning("Microsoft returned no refresh token")
        claims = _claims(body.get("id_token"))
        profile: dict[str, Any] = {}
        try:
            pr = httpx.get(f"{GRAPH}/me", headers={"Authorization": f"Bearer {body['access_token']}"}, timeout=20)
            if pr.status_code == 200:
                profile = pr.json()
        except Exception:  # noqa: BLE001 - the id_token claims are enough to identify the account
            pass
        oid = claims.get("oid") or profile.get("id")
        if not oid:
            raise ValueError("Microsoft did not say which account signed in.")
        token = {
            "access_token": body["access_token"],
            "refresh_token": body.get("refresh_token"),
            "expires_at": time.time() + float(body.get("expires_in") or 3600),
            "scopes": str(body.get("scope") or "").split(),
            "client_id": cid,
            "tenant": tenant,
            "oid": oid,
            "tid": claims.get("tid"),
            "email": profile.get("mail") or profile.get("userPrincipalName") or claims.get("preferred_username"),
            "name": claims.get("name") or profile.get("displayName"),
            "connected_at": time.time(),
        }
        self.set_settings({TOKEN_KEY: token})
        self._reset_clients()  # a new sign-in may be a different account
        return self.status()

    def disconnect(self) -> None:
        # A public client has no revoke endpoint; dropping the tokens is the whole of signing out.
        self.set_settings({TOKEN_KEY: {}, PENDING_KEY: {}})
        self._reset_clients()

    def _save_token(self, tok: dict[str, Any], patch: dict[str, Any]) -> None:
        """Write refresh results back, unless the sign-in changed while the refresh ran."""
        cur = self.get_settings().get(TOKEN_KEY) or {}
        if cur.get("refresh_token") != tok.get("refresh_token"):
            return
        self.set_settings({TOKEN_KEY: {**cur, **patch}})

    def _token(self) -> str:
        tok = self.get_settings().get(TOKEN_KEY) or {}
        if not tok.get("refresh_token"):
            raise MicrosoftNotConnected("Microsoft account not connected")
        if tok.get("access_token") and float(tok.get("expires_at") or 0) - 60 > time.time():
            return tok["access_token"]
        with self._lock:  # one refresh at a time: a rotated refresh token must not be spent twice
            tok = self.get_settings().get(TOKEN_KEY) or {}
            if not tok.get("refresh_token"):
                raise MicrosoftNotConnected("Microsoft account not connected")
            if tok.get("access_token") and float(tok.get("expires_at") or 0) - 60 > time.time():
                return tok["access_token"]
            cid, tenant, _ = self._client()
            # Network errors (offline, DNS, timeout) propagate untouched: the sign-in is fine and the next
            # call retries. Flagging them told the user to reconnect after every wake from sleep.
            r = httpx.post(self._endpoint(tok.get("tenant") or tenant, "token"), data={
                "client_id": tok.get("client_id") or cid, "grant_type": "refresh_token",
                "refresh_token": tok["refresh_token"], "scope": " ".join(SCOPES),
            }, timeout=20)
            if r.status_code != 200:
                code, desc = _err(r)
                log.warning("Microsoft token refresh failed: %s", desc)
                if r.status_code == 400 and code in _DEAD_GRANT:
                    self._save_token(tok, {"needs_reauth": True})
                    raise MicrosoftNotConnected(
                        "Microsoft sign-in expired or was revoked. Open Settings → Integrations and sign in again."
                    )
                raise GraphError(r.status_code, desc)
            body = r.json()
            patch = {
                "access_token": body["access_token"],
                "expires_at": time.time() + float(body.get("expires_in") or 3600),
                "needs_reauth": False,
            }
            if body.get("refresh_token"):  # Entra rotates it; keep the newest
                patch["refresh_token"] = body["refresh_token"]
            cur = self.get_settings().get(TOKEN_KEY) or {}
            if cur.get("refresh_token") == tok["refresh_token"]:
                self.set_settings({TOKEN_KEY: {**cur, **patch}})
            return body["access_token"]

    # ---------- Graph ----------
    def _req(self, method: str, path: str, *, params: dict | None = None, json: dict | None = None, headers: dict | None = None) -> dict | None:
        url = path if path.startswith("https://") else GRAPH + path
        r = httpx.request(method, url, params=params, json=json, headers={"Authorization": f"Bearer {self._token()}", **(headers or {})}, timeout=30)
        if r.status_code >= 300:
            try:
                err = r.json().get("error")
                msg = err.get("message") if isinstance(err, dict) else None
            except Exception:  # noqa: BLE001
                msg = None
            raise GraphError(r.status_code, msg or (r.text or "")[:300] or f"HTTP {r.status_code}")
        if r.status_code in (202, 204) or not r.text:
            return None
        return r.json()

    @property
    def me(self) -> dict[str, Any]:
        if self._me is None:
            self._me = self._req("GET", "/me", params={"$select": "id,mail,userPrincipalName,displayName"}) or {}
        return self._me

    def _tz(self) -> str:
        return _local_tz()
