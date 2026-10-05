"""Microsoft over HTTP: status, PKCE sign-in, callback, disconnect, and the one-active-provider switch for mail/calendar.

Run: PERSONAL_OS_DATA_DIR=/tmp/x python backend/tests/test_microsoft_routes.py   (no network: httpx and the providers are stubbed)
"""
from __future__ import annotations

import base64
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse

os.environ["GRAIN_SECRETS_BACKEND"] = "file"
os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="msroutes-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from personal_os import app as appmod  # noqa: E402
from personal_os.microsoft import MicrosoftNotConnected  # noqa: E402

AT, RT = "at-secret-123", "rt-secret-456"
SCOPE = "User.Read Mail.ReadWrite Mail.Send Calendars.ReadWrite"


class Resp:
    def __init__(self, status: int, body: object = None):
        self.status_code, self._body = status, body
        self.text = json.dumps(body) if body is not None else ""

    def json(self) -> object:
        return self._body


def id_token(**claims: str) -> str:
    return "h." + base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=") + ".s"


class MicrosoftRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        os.environ.pop("MICROSOFT_CLIENT_ID", None)
        os.environ.pop("MICROSOFT_TENANT", None)
        self.client = TestClient(appmod.app)
        self.h = {"X-Personal-OS-Token": appmod.AUTH_TOKEN}
        self._reset()
        self._httpx = (httpx.post, httpx.get)
        self.posts: list[tuple[str, dict]] = []

        def post(url: str, **kw: object) -> Resp:
            self.posts.append((url, kw))
            return Resp(200, {"access_token": AT, "refresh_token": RT, "expires_in": 3600, "scope": SCOPE,
                              "id_token": id_token(oid="oid-1", preferred_username="mira@contoso.com", name="Mira")})

        httpx.post = post  # type: ignore[assignment]
        httpx.get = lambda url, **kw: Resp(200, {"id": "oid-1", "mail": "mira@contoso.com"})  # type: ignore[assignment]
        self._stubs: list[tuple[object, str, object]] = []

    def tearDown(self) -> None:
        httpx.post, httpx.get = self._httpx
        for obj, name, old in self._stubs:
            if old is None:
                delattr(obj, name)
            else:
                setattr(obj, name, old)
        self._reset()
        for row in appmod.outbox.list():  # leave the shared database as it was found
            appmod.outbox.cancel(row["id"])

    def _reset(self) -> None:
        appmod.db.set_settings({"microsoftClientId": "", "microsoftTenant": "", "microsoftToken": {},
                                "microsoftAuthPending": {}, "pimProvider": "google"})
        appmod.microsoft._reset_clients()

    def stub(self, obj: object, name: str, fn: object) -> None:
        self._stubs.append((obj, name, obj.__dict__.get(name)))
        setattr(obj, name, fn)

    def configure(self) -> None:
        r = self.client.put("/settings", headers=self.h,
                            json={"microsoftClientId": "abc", "microsoftTenant": "contoso.onmicrosoft.com"})
        self.assertEqual(r.status_code, 200)

    def sign_in(self) -> None:
        self.configure()
        url = self.client.post("/integrations/microsoft/auth/start", headers=self.h).json()["url"]
        state = parse_qs(urlparse(url).query)["state"][0]
        self.assertEqual(self.client.get("/integrations/microsoft/callback", params={"state": state, "code": "xyz"}).status_code, 200)

    # 1
    def test_status_reflects_the_client_id_setting(self) -> None:
        st = self.client.get("/integrations/microsoft/status", headers=self.h)
        self.assertEqual(st.status_code, 200)
        self.assertFalse(st.json()["configured"])
        self.configure()
        st = self.client.get("/integrations/microsoft/status", headers=self.h).json()
        self.assertTrue(st["configured"])
        self.assertEqual((st["source"], st["tenant"], st["connected"]), ("settings", "contoso.onmicrosoft.com", False))

    # 2
    def test_auth_start_needs_a_client_id_then_returns_a_pkce_url(self) -> None:
        self.assertEqual(self.client.post("/integrations/microsoft/auth/start", headers=self.h).status_code, 400)
        self.configure()
        r = self.client.post("/integrations/microsoft/auth/start", headers=self.h)
        self.assertEqual(r.status_code, 200)
        u = urlparse(r.json()["url"])
        self.assertEqual(f"{u.netloc}{u.path}", "login.microsoftonline.com/contoso.onmicrosoft.com/oauth2/v2.0/authorize")
        q = parse_qs(u.query)
        self.assertEqual(q["code_challenge_method"], ["S256"])
        self.assertTrue(q["redirect_uri"][0].endswith("/integrations/microsoft/callback"))
        self.assertIn(q["state"][0], appmod.settings()["microsoftAuthPending"])
        self.assertNotIn("microsoftAuthPending", self.client.get("/settings", headers=self.h).json())

    # 3
    def test_callback_connects_and_keeps_tokens_out_of_settings(self) -> None:
        self.configure()
        url = self.client.post("/integrations/microsoft/auth/start", headers=self.h).json()["url"]
        state = parse_qs(urlparse(url).query)["state"][0]
        r = self.client.get("/integrations/microsoft/callback", params={"state": state, "code": "xyz"})  # a public path: no token
        self.assertEqual(r.status_code, 200)
        self.assertIn("mira@contoso.com", r.text)
        self.assertEqual(self.posts[0][1]["data"]["code"], "xyz")
        st = self.client.get("/integrations/microsoft/status", headers=self.h).json()
        self.assertTrue(st["connected"])
        self.assertEqual((st["oid"], st["email"]), ("oid-1", "mira@contoso.com"))
        self.assertNotIn(state, appmod.settings()["microsoftAuthPending"])  # single use

        shown = self.client.get("/settings", headers=self.h)
        self.assertNotIn(AT, shown.text)
        self.assertNotIn(RT, shown.text)
        self.assertNotIn("microsoftToken", shown.json())
        row = sqlite3.connect(appmod.db.path).execute("SELECT value FROM settings WHERE key='microsoftToken'").fetchone()[0]
        self.assertNotIn(AT, row)
        self.assertNotIn(RT, row)
        self.assertEqual(appmod.settings()["microsoftToken"]["refresh_token"], RT)  # still reachable by the backend

    def test_callback_error_and_stale_state_stay_disconnected(self) -> None:
        self.configure()
        r = self.client.get("/integrations/microsoft/callback", params={"error": "access_denied", "error_description": "User said no"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("sign-in failed", r.text)
        self.assertIn("User said no", r.text)
        r = self.client.get("/integrations/microsoft/callback", params={"state": "nope", "code": "xyz"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("expired", r.text)
        self.assertEqual(self.posts, [])  # never reached the token endpoint
        self.assertFalse(self.client.get("/integrations/microsoft/status", headers=self.h).json()["connected"])

    # 4
    def test_mail_and_calendar_routes_follow_pim_provider(self) -> None:
        self.stub(appmod.google, "gmail_search", lambda *a, **k: [{"id": "g"}])
        self.stub(appmod.microsoft, "gmail_search", lambda *a, **k: [{"id": "m"}])
        self.stub(appmod.google, "calendar_events", lambda *a, **k: [{"id": "ge"}])
        self.stub(appmod.microsoft, "calendar_events", lambda *a, **k: [{"id": "me"}])

        def both() -> tuple[object, object]:
            return (self.client.get("/integrations/google/gmail", headers=self.h, params={"q": "is:unread"}).json(),
                    self.client.get("/integrations/google/calendar", headers=self.h, params={"days": 1}).json())

        self.assertEqual(both(), ([{"id": "g"}], [{"id": "ge"}]))
        self.assertEqual(self.client.put("/settings", headers=self.h, json={"pimProvider": "microsoft"}).status_code, 200)
        self.assertEqual(both(), ([{"id": "m"}], [{"id": "me"}]))
        self.assertEqual(self.client.put("/settings", headers=self.h, json={"pimProvider": "bogus"}).status_code, 400)
        self.assertEqual(appmod.settings()["pimProvider"], "microsoft")  # the bad value was not stored

    def test_microsoft_errors_map_to_http(self) -> None:
        self.client.put("/settings", headers=self.h, json={"pimProvider": "microsoft"})

        def boom(*a: object, **k: object) -> list:
            raise RuntimeError("boom")

        self.stub(appmod.microsoft, "gmail_search", boom)
        r = self.client.get("/integrations/google/gmail", headers=self.h)
        self.assertEqual(r.status_code, 502)
        self.assertIn("Microsoft API error", r.json()["detail"])

    def test_send_queues_through_the_outbox_for_either_provider(self) -> None:
        body = {"to": "mira@example.com", "subject": "Hi", "body": "later"}
        shapes = []
        for provider in ("google", "microsoft"):
            self.client.put("/settings", headers=self.h, json={"pimProvider": provider})
            r = self.client.post("/integrations/google/gmail/send", headers=self.h, json=body)
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.json()["status"], "holding")
            shapes.append(set(r.json()))
        self.assertEqual(shapes[0], shapes[1])

    # 5
    def test_disconnect_then_mail_is_a_409(self) -> None:
        self.sign_in()
        self.client.put("/settings", headers=self.h, json={"pimProvider": "microsoft"})
        r = self.client.post("/integrations/microsoft/disconnect", headers=self.h)
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["connected"])
        self.assertEqual(appmod.settings()["microsoftToken"], {})
        r = self.client.get("/integrations/google/gmail", headers=self.h)
        self.assertEqual(r.status_code, 409)
        self.assertIn("not connected", r.json()["detail"])
        self.assertTrue(issubclass(MicrosoftNotConnected, appmod.GoogleNotConnected))

    # 6
    def test_dashboard_uses_the_microsoft_account_for_mail_and_calendar(self) -> None:
        self.client.put("/settings", headers=self.h, json={"pimProvider": "microsoft"})
        connected = {**appmod.microsoft.status(), "configured": True, "connected": True, "email": "mira@contoso.com"}
        self.stub(appmod.microsoft, "status", lambda: connected)
        self.stub(appmod.microsoft, "gmail_search", lambda *a, **k: [])
        self.stub(appmod.microsoft, "calendar_events", lambda *a, **k: [])
        self.stub(appmod.microsoft, "enabled_calendar_ids", lambda: [])
        r = self.client.get("/dashboard", headers=self.h)
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertEqual(d["pim_provider"], "microsoft")
        self.assertEqual((d["gmail"], d["calendar"]), ([], []))
        self.assertNotIn("gmail", d["errors"])
        self.assertNotIn("calendar", d["errors"])
        self.assertFalse(d["google"]["connected"])
        self.assertIsNone(d["tasks"])
        self.assertTrue(d["microsoft"]["connected"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
