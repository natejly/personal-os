"""OAuth for remote (streamable HTTP) MCP servers, e.g. COROS's hosted one.

The MCP spec has a server answer 401 and point at its authorization server; the SDK's
OAuthClientProvider does the rest (metadata discovery, dynamic client registration, PKCE, token
exchange and refresh). This module supplies the three things the SDK leaves to the host:

- storage: tokens and the registered client live in their own table, never in `mcp_servers.secrets`
  (those are injected into a stdio child's environment) and never in any API response;
- the browser leg: `begin()` runs one connection with an *interactive* provider. Its redirect handler
  hands the authorization URL back to the caller (the renderer opens it in the system browser) and
  its callback handler waits for GET /mcp/oauth/callback to deliver the code for that `state`;
- the background leg: supervisors connect with a *non-interactive* provider. It refreshes tokens on
  its own, and when it would need the browser it raises McpNeedsAuth, which stops the reconnect loop
  with "sign-in required" instead of retrying forever.

Sign-in waits as long as the user takes (up to SIGN_IN_TIMEOUT), outside the supervisor's connect
timeout, which is why it is a separate one-shot connection and not part of the supervisor's cycle.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable
from urllib.parse import parse_qs, urlparse

from mcp.client.auth import OAuthClientProvider, TokenStorage
from mcp.shared.auth import AuthorizationCodeResult, OAuthClientInformationFull, OAuthClientMetadata, OAuthToken

from .db import Database, now

SCHEMA = """
CREATE TABLE IF NOT EXISTS mcp_oauth (
  server_id TEXT PRIMARY KEY REFERENCES mcp_servers(id) ON DELETE CASCADE,
  tokens TEXT,            -- OAuthToken JSON
  client TEXT,            -- OAuthClientInformationFull JSON (dynamic registration result)
  updated_at REAL NOT NULL
);
"""

CALLBACK_PATH = "/mcp/oauth/callback"
SIGN_IN_TIMEOUT = 600.0    # how long a browser sign-in may take
URL_WAIT = 20.0            # how long begin() waits for the server to ask for the browser at all


class McpNeedsAuth(RuntimeError):
    """The server wants a browser sign-in and this connection cannot do one."""


class OAuthStore:
    def __init__(self, db: Database):
        self.db = db
        with db.tx() as c:
            c.executescript(SCHEMA)

    def _row(self, server_id: str) -> dict[str, Any]:
        with self.db.tx() as c:
            r = c.execute("SELECT tokens, client, updated_at FROM mcp_oauth WHERE server_id=?", (server_id,)).fetchone()
        return dict(r) if r else {}

    def _put(self, server_id: str, col: str, value: str | None) -> None:
        with self.db.tx() as c:
            c.execute("INSERT INTO mcp_oauth(server_id, updated_at) VALUES(?, ?) ON CONFLICT(server_id) DO NOTHING", (server_id, now()))
            c.execute(f"UPDATE mcp_oauth SET {col}=?, updated_at=? WHERE server_id=?", (value, now(), server_id))

    def tokens(self, server_id: str) -> OAuthToken | None:
        raw = self._row(server_id).get("tokens")
        return OAuthToken.model_validate_json(raw) if raw else None

    def token_expiry(self, server_id: str) -> float | None:
        """When the stored access token expires: written at `updated_at`, good for `expires_in` from then."""
        row = self._row(server_id)
        tok = OAuthToken.model_validate_json(row["tokens"]) if row.get("tokens") else None
        if tok is None or tok.expires_in is None:
            return None
        return float(row["updated_at"]) + float(tok.expires_in)

    def client(self, server_id: str) -> OAuthClientInformationFull | None:
        raw = self._row(server_id).get("client")
        return OAuthClientInformationFull.model_validate_json(raw) if raw else None

    def set_tokens(self, server_id: str, tokens: OAuthToken | None) -> None:
        self._put(server_id, "tokens", tokens.model_dump_json() if tokens else None)

    def set_client(self, server_id: str, client: OAuthClientInformationFull | None) -> None:
        self._put(server_id, "client", client.model_dump_json() if client else None)

    def signed_in(self, server_id: str) -> bool:
        return self.tokens(server_id) is not None

    def forget(self, server_id: str) -> None:
        with self.db.tx() as c:
            c.execute("DELETE FROM mcp_oauth WHERE server_id=?", (server_id,))


class _Storage(TokenStorage):
    def __init__(self, store: OAuthStore, server_id: str):
        self.store, self.server_id = store, server_id

    async def get_tokens(self) -> OAuthToken | None:
        return self.store.tokens(self.server_id)

    async def set_tokens(self, tokens: OAuthToken) -> None:
        self.store.set_tokens(self.server_id, tokens)

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        return self.store.client(self.server_id)

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        self.store.set_client(self.server_id, client_info)


class _Provider(OAuthClientProvider):
    """The SDK loads stored tokens without their expiry, so after a restart an expired access token passes as valid,
    draws a 401 and starts a browser sign-in. Restoring the expiry lets it use the refresh grant instead."""

    def __init__(self, *a: Any, store: OAuthStore, server_id: str, **kw: Any):
        super().__init__(*a, **kw)
        self._store, self._server_id = store, server_id

    async def _initialize(self) -> None:
        await super()._initialize()
        self.context.token_expiry_time = self._store.token_expiry(self._server_id)


@dataclass
class SignIn:
    server_id: str
    state: str = ""
    auth_url: str = ""
    status: str = "starting"          # starting | waiting | done | error
    error: str = ""
    started_at: float = field(default_factory=time.time)
    url_ready: asyncio.Event = field(default_factory=asyncio.Event)
    code: asyncio.Future[AuthorizationCodeResult] | None = None
    task: asyncio.Task[Any] | None = None


class OAuthFlows:
    """Pending browser sign-ins, keyed by server id and by OAuth `state`."""

    def __init__(self, store: OAuthStore):
        self.store = store
        self._by_server: dict[str, SignIn] = {}
        self._by_state: dict[str, SignIn] = {}

    def provider(self, server_id: str, url: str, redirect_uri: str | None = None,
                 sign_in: SignIn | None = None) -> OAuthClientProvider:
        """An auth provider for one server. Without `sign_in` it never opens a browser."""
        async def redirect(auth_url: str) -> None:
            if sign_in is None:
                raise McpNeedsAuth("sign-in required")
            sign_in.state = (parse_qs(urlparse(auth_url).query).get("state") or [""])[0]
            sign_in.auth_url, sign_in.status = auth_url, "waiting"
            sign_in.code = asyncio.get_running_loop().create_future()
            self._by_state[sign_in.state] = sign_in
            sign_in.url_ready.set()

        async def callback() -> AuthorizationCodeResult:
            if sign_in is None or sign_in.code is None:
                raise McpNeedsAuth("sign-in required")
            return await asyncio.wait_for(sign_in.code, SIGN_IN_TIMEOUT)

        # The registered redirect is the loopback callback on this backend. A stored registration made
        # for another port cannot complete a new sign-in, so it is dropped and the client re-registers.
        uri = redirect_uri or f"http://127.0.0.1{CALLBACK_PATH}"
        if sign_in is not None:
            client = self.store.client(server_id)
            if client and uri not in [str(u) for u in (client.redirect_uris or [])]:
                self.store.set_client(server_id, None)
        meta = OAuthClientMetadata(client_name="Grain", redirect_uris=[uri],  # type: ignore[list-item]
                                   grant_types=["authorization_code", "refresh_token"], response_types=["code"],
                                   token_endpoint_auth_method="none")
        return _Provider(url, meta, _Storage(self.store, server_id), redirect_handler=redirect,
                         callback_handler=callback, store=self.store, server_id=server_id)

    async def begin(self, server_id: str, run: Callable[[SignIn], Awaitable[None]]) -> SignIn:
        """Start (or join) a sign-in. `run` makes one interactive connection with `self.provider(..., sign_in=s)`.
        Returns once the browser URL is known, the connection already succeeded, or it failed."""
        cur = self._by_server.get(server_id)
        if cur and cur.status in ("starting", "waiting") and time.time() - cur.started_at < SIGN_IN_TIMEOUT:
            return cur
        s = SignIn(server_id)
        self._by_server[server_id] = s

        async def go() -> None:
            try:
                await run(s)
                s.status = "done"
            except BaseException as exc:  # noqa: BLE001 - reported through status, polled by the UI
                if isinstance(exc, asyncio.CancelledError):
                    s.status, s.error = "error", "cancelled"
                    raise
                s.status, s.error = "error", _first_line(exc)
            finally:
                s.url_ready.set()
                self._by_state.pop(s.state, None)

        s.task = asyncio.create_task(go(), name=f"mcp-oauth:{server_id}")
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(s.url_ready.wait(), URL_WAIT)
        return s

    def status(self, server_id: str) -> dict[str, Any]:
        s = self._by_server.get(server_id)
        out: dict[str, Any] = {"signed_in": self.store.signed_in(server_id)}
        if s:
            out.update(status=s.status, error=s.error, auth_url=s.auth_url if s.status == "waiting" else "")
        else:
            out.update(status="idle", error="", auth_url="")
        return out

    def complete(self, state: str, code: str | None, error: str | None) -> bool:
        """Deliver the browser's answer. False when the state matches no pending sign-in."""
        s = self._by_state.get(state)
        if not s or not s.code or s.code.done():
            return False
        if error or not code:
            s.code.set_exception(McpNeedsAuth(f"sign-in was not completed: {error or 'no code returned'}"))
        else:
            s.code.set_result(AuthorizationCodeResult(code=code, state=state))
        return True

    async def stop(self) -> None:
        for s in list(self._by_server.values()):
            if s.task and not s.task.done():
                s.task.cancel()
                with contextlib.suppress(BaseException):
                    await s.task


def _first_line(exc: BaseException) -> str:
    inner = getattr(exc, "exceptions", None)
    if inner:
        return _first_line(inner[0])
    msg = str(exc).strip().splitlines()
    return f"{type(exc).__name__}: {msg[0][:300]}" if msg else type(exc).__name__


def headers_of(raw: Any) -> dict[str, str]:
    if isinstance(raw, str):
        with contextlib.suppress(ValueError):
            raw = json.loads(raw)
    return {str(k): str(v) for k, v in (raw or {}).items()} if isinstance(raw, dict) else {}
