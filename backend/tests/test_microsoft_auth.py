"""Microsoft Entra public-client auth: PKCE start, code exchange, refresh, dead grants, Graph errors, secret split.

Run: python backend/tests/test_microsoft_auth.py   (no network: httpx is stubbed)
"""
from __future__ import annotations

import base64
import json
import os
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["GRAIN_SECRETS_BACKEND"] = "file"
os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="msauth-"))

import httpx  # noqa: E402

from personal_os.db import Database  # noqa: E402
from personal_os.microsoft import GraphError, Microsoft, MicrosoftNotConnected  # noqa: E402

REDIRECT = "http://127.0.0.1:8798/integrations/microsoft/callback"


class Resp:
    def __init__(self, status: int, body: object = None):
        self.status_code = status
        self._body = body
        self.text = json.dumps(body) if body is not None else ""

    def json(self) -> object:
        return self._body


def id_token(**claims: str) -> str:
    part = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"h.{part}.s"


class MicrosoftAuthTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store: dict = {"microsoftClientId": "cid-123"}
        self.ms = Microsoft(lambda: dict(self.store), lambda patch: self.store.update(patch))
        self.calls: list[tuple[str, str, dict]] = []
        self.queue: list[Resp] = []
        self._orig = (httpx.post, httpx.get, httpx.request)

        def fake(method: str):
            def f(url: str, **kw: object) -> Resp:
                self.calls.append((method, url, kw))
                return self.queue.pop(0)
            return f

        httpx.post, httpx.get = fake("POST"), fake("GET")
        httpx.request = lambda m, url, **kw: fake(m)(url, **kw)  # type: ignore[assignment]

    def tearDown(self) -> None:
        httpx.post, httpx.get, httpx.request = self._orig  # type: ignore[assignment]

    def connect(self) -> None:
        url = self.ms.start_auth(REDIRECT)
        state = parse_qs(urlparse(url).query)["state"][0]
        self.queue += [
            Resp(200, {"access_token": "AT1", "refresh_token": "RT1", "expires_in": 3600, "scope": "User.Read Mail.ReadWrite Mail.Send Calendars.ReadWrite",
                       "id_token": id_token(oid="oid-1", tid="tid-1", name="Ada", preferred_username="ada@x.com")}),
            Resp(200, {"id": "oid-1", "mail": "ada@contoso.com", "userPrincipalName": "ada@x.com"}),
        ]
        self.ms.finish_auth(state, "CODE")

    def test_start_auth_is_pkce_without_secret(self) -> None:
        self.store["microsoftTenant"] = "organizations"
        u = urlparse(self.ms.start_auth(REDIRECT))
        q = parse_qs(u.query)
        self.assertEqual(u.path, "/organizations/oauth2/v2.0/authorize")
        self.assertEqual(q["code_challenge_method"], ["S256"])
        self.assertEqual(q["client_id"], ["cid-123"])
        self.assertEqual(q["prompt"], ["select_account"])
        self.assertNotIn("client_secret", q)
        self.assertIn(q["state"][0], self.store["microsoftAuthPending"])

    def test_finish_auth_exchanges_with_verifier_and_stores_identity(self) -> None:
        self.connect()
        _, url, kw = self.calls[0]
        self.assertTrue(url.endswith("/common/oauth2/v2.0/token"))
        data = kw["data"]
        self.assertNotIn("client_secret", data)
        self.assertEqual(data["code"], "CODE")
        self.assertEqual(len(data["code_verifier"]), 86)
        tok = self.store["microsoftToken"]
        self.assertEqual((tok["oid"], tok["email"], tok["access_token"], tok["refresh_token"]), ("oid-1", "ada@contoso.com", "AT1", "RT1"))
        st = self.ms.status()
        self.assertTrue(st["connected"] and not st["needs_reauth"])
        self.assertEqual(st["missing_scopes"], [])
        self.assertEqual(self.store["microsoftAuthPending"], {})

    def test_finish_auth_rejects_unknown_state_and_reports_graph_error(self) -> None:
        with self.assertRaises(ValueError):
            self.ms.finish_auth("nope", "c")
        state = parse_qs(urlparse(self.ms.start_auth(REDIRECT)).query)["state"][0]
        self.queue.append(Resp(400, {"error": "invalid_grant", "error_description": "AADSTS70008: The code expired.\r\nTrace ID: x"}))
        with self.assertRaisesRegex(ValueError, "^AADSTS70008: The code expired.$"):
            self.ms.finish_auth(state, "c")

    def test_token_refreshes_when_expired(self) -> None:
        self.connect()
        self.calls.clear()
        self.assertEqual(self.ms._token(), "AT1")  # fresh: no network
        self.assertEqual(self.calls, [])
        self.store["microsoftToken"]["expires_at"] = time.time() - 5
        self.queue.append(Resp(200, {"access_token": "AT2", "refresh_token": "RT2", "expires_in": 3600}))
        self.assertEqual(self.ms._token(), "AT2")
        self.assertEqual(self.calls[0][2]["data"]["grant_type"], "refresh_token")
        self.assertEqual(self.calls[0][2]["data"]["refresh_token"], "RT1")
        self.assertEqual((self.store["microsoftToken"]["access_token"], self.store["microsoftToken"]["refresh_token"]), ("AT2", "RT2"))
        self.assertEqual(self.store["microsoftToken"]["oid"], "oid-1")

    def test_invalid_grant_flags_reauth(self) -> None:
        self.connect()
        self.store["microsoftToken"]["expires_at"] = 0
        self.queue.append(Resp(400, {"error": "invalid_grant", "error_description": "AADSTS70043: revoked"}))
        with self.assertRaises(MicrosoftNotConnected):
            self.ms._token()
        self.assertTrue(self.ms.status()["needs_reauth"])

    def test_network_error_is_not_a_reauth(self) -> None:
        self.connect()
        self.store["microsoftToken"]["expires_at"] = 0

        def boom(*a: object, **k: object) -> Resp:
            raise httpx.ConnectError("offline")

        httpx.post = boom  # type: ignore[assignment]
        with self.assertRaises(httpx.ConnectError):
            self.ms._token()
        self.assertFalse(self.ms.status()["needs_reauth"])

    def test_not_connected(self) -> None:
        with self.assertRaises(MicrosoftNotConnected):
            self.ms._req("GET", "/me")

    def test_req_returns_json_none_and_graph_error(self) -> None:
        self.connect()
        self.calls.clear()
        self.queue += [Resp(200, {"value": [1]}), Resp(204), Resp(404, {"error": {"code": "ErrorItemNotFound", "message": "The specified object was not found."}})]
        self.assertEqual(self.ms._req("GET", "/me/messages", params={"$top": 1}), {"value": [1]})
        self.assertEqual(self.calls[0][1], "https://graph.microsoft.com/v1.0/me/messages")
        self.assertEqual(self.calls[0][2]["headers"]["Authorization"], "Bearer AT1")
        self.assertIsNone(self.ms._req("DELETE", "/me/messages/x"))
        with self.assertRaises(GraphError) as cm:
            self.ms._req("GET", "/me/messages/x")
        self.assertEqual((cm.exception.status, cm.exception.message), (404, "The specified object was not found."))

    def test_status_configured_from_env_or_settings(self) -> None:
        self.store.clear()
        old = {k: os.environ.pop(k, None) for k in ("MICROSOFT_CLIENT_ID", "MICROSOFT_TENANT")}
        try:
            self.assertFalse(self.ms.status()["configured"])
            os.environ["MICROSOFT_CLIENT_ID"] = "env-cid"
            st = self.ms.status()
            self.assertEqual((st["configured"], st["source"], st["tenant"]), (True, "env", "common"))
            self.store["microsoftClientId"] = "set-cid"
            self.assertEqual(self.ms.status()["source"], "settings")
        finally:
            for k, v in old.items():
                os.environ.pop(k, None)
                if v is not None:
                    os.environ[k] = v

    def test_disconnect_clears_token(self) -> None:
        self.connect()
        self.ms.disconnect()
        self.assertFalse(self.ms.status()["connected"])


class TokenSplitTests(unittest.TestCase):
    def test_access_and_refresh_tokens_live_in_the_secret_store(self) -> None:
        d = Path(tempfile.mkdtemp())
        db = Database(d)
        tok = {"access_token": "ATSECRET", "refresh_token": "RTSECRET", "oid": "oid-1", "email": "a@b.c"}
        db.set_settings({"microsoftToken": tok})
        raw = json.loads(db.secrets.get("microsoftToken"))
        self.assertEqual(raw, {"access_token": "ATSECRET", "refresh_token": "RTSECRET"})
        row = sqlite3.connect(db.path).execute("SELECT value FROM settings WHERE key='microsoftToken'").fetchone()[0]
        self.assertNotIn("ATSECRET", row)
        self.assertNotIn("RTSECRET", row)
        self.assertIn("oid-1", row)
        self.assertEqual(Database(d).get_settings()["microsoftToken"], tok)
        db.set_settings({"microsoftToken": {}})
        self.assertIsNone(db.secrets.get("microsoftToken"))


if __name__ == "__main__":
    unittest.main()
