"""Google Workspace integration: OAuth (desktop loopback flow), Calendar, Gmail, Tasks, Docs, Sheets.

The user just clicks "Sign in with Google". The OAuth client the app signs in with comes
from GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET in the environment (.env), so it is set up once
per install rather than pasted by every user. Settings → Integrations can still override it
with a user-supplied client (googleClientId / googleClientSecret).

Google treats the client secret of a "Desktop app" OAuth client as non-confidential, so
shipping it with the app is the sanctioned pattern (it is what gcloud, rclone etc. do).
Tokens are stored in the app database (settings.googleToken).
"""
from __future__ import annotations

import base64
import datetime as dt
import email.mime.text
import json
import logging
import os
import re
import secrets
import time
from typing import Any, Callable

# Google lists the scopes it actually granted in the token response, and that set rarely
# matches the request byte for byte (openid aliases, scopes granted to this client earlier).
# oauthlib raises a bare Warning when it differs, which surfaced as "Could not complete
# sign-in"; relax that check and compare the scopes ourselves in status() instead.
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

log = logging.getLogger(__name__)

# A half-finished sign-in stays resumable this long; the pending state is kept in the
# database so restarting the backend mid-flow does not strand the browser callback.
PENDING_KEY = "googleAuthPending"
PENDING_TTL = 15 * 60

SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/tasks",
    # Docs/Sheets content; drive.readonly only to find files by name (no Drive write access).
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.readonly",
]


class GoogleNotConnected(Exception):
    pass


class Google:
    def __init__(self, get_settings: Callable[[], dict[str, Any]], set_settings: Callable[[dict[str, Any]], None]):
        self.get_settings = get_settings
        self.set_settings = set_settings
        self._pending: dict[str, Any] = {}  # state -> flow
        self._event_palette: dict[str, Any] | None = None  # colors().get() event palette, fetched once per process

    # ---------- status / auth ----------
    def _client(self) -> tuple[str | None, str | None, str | None]:
        """Resolve the OAuth client: (client_id, client_secret, source).

        A client pasted in Settings wins over the one in the environment, so a user can
        swap in their own without touching .env. Source is "settings", "env" or None.
        """
        s = self.get_settings()
        if s.get("googleClientId") and s.get("googleClientSecret"):
            return s["googleClientId"], s["googleClientSecret"], "settings"
        cid, sec = os.environ.get("GOOGLE_CLIENT_ID", "").strip(), os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()
        if cid and sec:
            return cid, sec, "env"
        return None, None, None

    def status(self) -> dict[str, Any]:
        tok = self.get_settings().get("googleToken") or {}
        cid, _, source = self._client()
        connected = bool(tok.get("refresh_token"))
        granted = tok.get("scopes") or []
        # Google lets people untick permissions on the consent screen, so a connected account
        # can still be missing scopes the app needs; surface that instead of failing later.
        missing = [s for s in SCOPES if s not in granted] if connected and granted else []
        # A token minted by a different OAuth client (someone swapped the client in Settings
        # or .env) can never be refreshed.
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
                "Sign-in expired or was revoked in your Google account." if tok.get("needs_reauth")
                else "The OAuth client changed, so the saved sign-in no longer works." if stale_client
                else f"Missing permissions: {', '.join(m.rsplit('/', 1)[-1] for m in missing)}." if missing
                else None
            ),
        }

    def _flow(self, redirect_uri: str, code_verifier: str | None = None):  # type: ignore[no-untyped-def]
        from google_auth_oauthlib.flow import Flow

        cid, sec, _ = self._client()
        if not (cid and sec):
            raise ValueError(
                "No Google OAuth client configured. Put GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in .env "
                "(see .env.example) or paste a client in Settings → Integrations."
            )
        client_config = {
            "installed": {
                "client_id": cid,
                "client_secret": sec,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [redirect_uri],
            }
        }
        return Flow.from_client_config(client_config, scopes=SCOPES, redirect_uri=redirect_uri, code_verifier=code_verifier)

    def _pending_states(self) -> dict[str, Any]:
        pending = self.get_settings().get(PENDING_KEY) or {}
        now = time.time()
        return {k: v for k, v in pending.items() if isinstance(v, dict) and now - float(v.get("ts", 0)) < PENDING_TTL}

    def start_auth(self, redirect_uri: str) -> str:
        flow = self._flow(redirect_uri)
        state = secrets.token_urlsafe(24)
        # No include_granted_scopes: incremental auth makes Google return the union of every
        # scope this client was ever granted, which we would then have to reconcile.
        url, _ = flow.authorization_url(access_type="offline", prompt="consent", state=state)
        pending = self._pending_states()
        pending[state] = {"verifier": flow.code_verifier, "redirect": redirect_uri, "ts": time.time()}
        self.set_settings({PENDING_KEY: pending})
        return url

    def finish_auth(self, state: str, code: str) -> dict[str, Any]:
        pending = self._pending_states()
        entry = pending.pop(state, None)
        self.set_settings({PENDING_KEY: pending})
        if not entry:
            raise ValueError("This sign-in link has expired. Click “Sign in with Google” again.")
        flow = self._flow(entry["redirect"], code_verifier=entry.get("verifier"))
        try:
            flow.fetch_token(code=code)
        except Exception as e:  # noqa: BLE001
            log.warning("Google token exchange failed: %s", e)
            raise ValueError(_token_error_hint(e)) from e
        creds = flow.credentials
        if not creds.refresh_token:
            # Without a refresh token the connection dies in an hour. Google only withholds it
            # when consent is skipped, which prompt="consent" should prevent.
            log.warning("Google returned no refresh token")
        email_addr = None
        try:
            import httpx

            r = httpx.get("https://openidconnect.googleapis.com/v1/userinfo", headers={"Authorization": f"Bearer {creds.token}"}, timeout=10)
            if r.status_code == 200:
                email_addr = r.json().get("email")
        except Exception:  # noqa: BLE001
            pass
        token = {
            "token": creds.token,
            "refresh_token": creds.refresh_token,
            "token_uri": creds.token_uri,
            "client_id": creds.client_id,
            "client_secret": creds.client_secret,
            "scopes": list(creds.scopes or SCOPES),
            "expiry": creds.expiry.isoformat() if creds.expiry else None,
            "email": email_addr,
            # lets the UI tell a fresh sign-in from the one it already had
            "connected_at": time.time(),
        }
        self.set_settings({"googleToken": token})
        return self.status()

    def disconnect(self) -> None:
        tok = (self.get_settings().get("googleToken") or {})
        if tok.get("token"):
            try:
                import httpx

                httpx.post("https://oauth2.googleapis.com/revoke", params={"token": tok.get("refresh_token") or tok["token"]}, timeout=10)
            except Exception:  # noqa: BLE001
                pass
        self.set_settings({"googleToken": {}, PENDING_KEY: {}})

    def _creds(self):  # type: ignore[no-untyped-def]
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials

        tok = self.get_settings().get("googleToken") or {}
        if not tok.get("refresh_token"):
            raise GoogleNotConnected("Google account not connected")
        creds = Credentials(
            token=tok.get("token"), refresh_token=tok["refresh_token"], token_uri=tok["token_uri"],
            client_id=tok["client_id"], client_secret=tok["client_secret"], scopes=tok.get("scopes") or SCOPES,
        )
        if tok.get("expiry"):
            try:
                creds.expiry = dt.datetime.fromisoformat(tok["expiry"]).replace(tzinfo=None)
            except ValueError:
                pass
        if not creds.valid:
            try:
                creds.refresh(Request())
            except Exception as e:  # noqa: BLE001
                # invalid_grant: the refresh token was revoked, expired (testing-mode consent
                # screens expire them in 7 days) or belongs to a different OAuth client.
                log.warning("Google token refresh failed: %s", e)
                self.set_settings({"googleToken": {**tok, "needs_reauth": True}})
                raise GoogleNotConnected(
                    "Google sign-in expired or was revoked. Open Settings → Integrations and sign in again."
                ) from e
            self.set_settings({"googleToken": {**tok, "token": creds.token, "expiry": creds.expiry.isoformat() if creds.expiry else None, "needs_reauth": False}})
        return creds

    def _svc(self, name: str, version: str):  # type: ignore[no-untyped-def]
        from googleapiclient.discovery import build

        return build(name, version, credentials=self._creds(), cache_discovery=False)

    # ---------- Calendar ----------
    def calendar_list(self) -> list[dict[str, Any]]:
        res = self._svc("calendar", "v3").calendarList().list(maxResults=100).execute()
        return [{
            "id": c.get("id"), "name": c.get("summary"),
            "primary": bool(c.get("primary")),
            "color": c.get("backgroundColor"),
            # The API omits `selected` for calendars unchecked in Google's UI (documented default: false).
            "selected": bool(c.get("selected")),
            "access_role": c.get("accessRole"),
        } for c in res.get("items", [])]

    def _event_colors(self) -> dict[str, Any]:
        if self._event_palette is None:
            self._event_palette = self._svc("calendar", "v3").colors().get().execute().get("event", {})
        return self._event_palette

    def calendar_events(self, days: int = 2, calendar_id: str = "primary", max_results: int = 30, start: str | None = None) -> list[dict[str, Any]]:
        # JS toISOString sends a 'Z' suffix, which fromisoformat only accepts from Python 3.11.
        now = dt.datetime.fromisoformat(start.replace("Z", "+00:00")) if start else dt.datetime.now(dt.timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=dt.timezone.utc)
        end = now + dt.timedelta(days=max(1, min(int(days), 60)))
        svc = self._svc("calendar", "v3")
        try:
            palette = self._event_colors()
        except Exception as e:  # noqa: BLE001
            log.warning("Google color palette fetch failed: %s", e)
            palette = {}
        # The calendar list supplies names and fallback colors; for 'all' it is also the fan-out set.
        cals: list[dict[str, Any]] = []
        try:
            cals = self.calendar_list()
        except Exception as e:  # noqa: BLE001
            log.warning("Google calendar list fetch failed: %s", e)
        if calendar_id == "all":
            targets = [c for c in cals if c.get("selected")] or [{"id": "primary"}]
        else:
            info = next((c for c in cals if c.get("id") == calendar_id or (calendar_id == "primary" and c.get("primary"))), {})
            targets = [{**info, "id": calendar_id}]
        out: list[dict[str, Any]] = []
        for cal in targets:
            try:
                res = svc.events().list(
                    calendarId=cal["id"], timeMin=now.isoformat(), timeMax=end.isoformat(), singleEvents=True, orderBy="startTime", maxResults=max_results
                ).execute()
            except Exception as e:  # noqa: BLE001
                # One broken shared calendar must not kill the merge.
                log.warning("Google calendar %s fetch failed: %s", cal["id"], e)
                continue
            for ev in res.get("items", []):
                st, en = ev.get("start", {}), ev.get("end", {})
                color = (palette.get(ev.get("colorId") or "") or {}).get("background") or cal.get("color")
                out.append({
                    "id": ev.get("id"), "summary": ev.get("summary", "(no title)"),
                    "start": st.get("dateTime") or st.get("date"), "end": en.get("dateTime") or en.get("date"),
                    "all_day": "date" in st, "location": ev.get("location"), "link": ev.get("htmlLink"),
                    "attendees": [a.get("email") for a in ev.get("attendees", [])][:10],
                    "description": (ev.get("description") or "")[:400],
                    "meet": (ev.get("hangoutLink") or ""),
                    "calendar_id": cal["id"], "calendar": cal.get("name"), "color": color,
                })
        out.sort(key=lambda r: _start_ts(r.get("start")))
        # Each calendar's fetch is already capped at max_results; slicing the merge too
        # would silently drop the latest events in the range, so only a lone fetch slices.
        return out[:max_results] if len(targets) == 1 else out

    def calendar_create(self, summary: str, start: str, end: str | None = None, description: str = "", location: str = "", attendees: list[str] | None = None, calendar_id: str = "primary") -> dict[str, Any]:
        all_day = len(start) == 10
        if not end:
            end = start if all_day else (dt.datetime.fromisoformat(start) + dt.timedelta(hours=1)).isoformat()
        body: dict[str, Any] = {"summary": summary, "description": description, "location": location}
        if all_day:
            body["start"], body["end"] = {"date": start}, {"date": end}
        else:
            body["start"], body["end"] = {"dateTime": start}, {"dateTime": end}
            tz = dt.datetime.now().astimezone().tzname()
            if "T" in start and not re.search(r"[+-]\d\d:\d\d$|Z$", start):
                body["start"]["timeZone"] = body["end"]["timeZone"] = _local_tz()
        if attendees:
            body["attendees"] = [{"email": a} for a in attendees]
        e = self._svc("calendar", "v3").events().insert(calendarId=calendar_id, body=body).execute()
        return {"id": e.get("id"), "link": e.get("htmlLink"), "summary": e.get("summary")}

    # ---------- Gmail ----------
    def gmail_search(self, query: str = "is:unread", max_results: int = 15) -> list[dict[str, Any]]:
        svc = self._svc("gmail", "v1")
        res = svc.users().messages().list(userId="me", q=query, maxResults=max(1, min(int(max_results), 50))).execute()
        out = []
        for m in res.get("messages", []):
            msg = svc.users().messages().get(userId="me", id=m["id"], format="metadata", metadataHeaders=["From", "Subject", "Date"]).execute()
            h = {x["name"].lower(): x["value"] for x in msg.get("payload", {}).get("headers", [])}
            out.append({"id": m["id"], "thread_id": msg.get("threadId"), "from": h.get("from"), "subject": h.get("subject"), "date": h.get("date"),
                        "snippet": msg.get("snippet"), "unread": "UNREAD" in msg.get("labelIds", []), "labels": msg.get("labelIds", [])})
        return out

    def gmail_get(self, message_id: str, max_chars: int = 8000) -> dict[str, Any]:
        svc = self._svc("gmail", "v1")
        msg = svc.users().messages().get(userId="me", id=message_id, format="full").execute()
        h = {x["name"].lower(): x["value"] for x in msg.get("payload", {}).get("headers", [])}
        body = _extract_body(msg.get("payload", {}))
        return {"id": message_id, "thread_id": msg.get("threadId"), "from": h.get("from"), "to": h.get("to"), "subject": h.get("subject"), "date": h.get("date"), "body": body[:max_chars]}

    def gmail_draft(self, to: str, subject: str, body: str, reply_to_message_id: str | None = None) -> dict[str, Any]:
        svc = self._svc("gmail", "v1")
        raw = _raw_message(to, subject, body)
        payload: dict[str, Any] = {"message": {"raw": raw}}
        if reply_to_message_id:
            payload["message"]["threadId"] = svc.users().messages().get(userId="me", id=reply_to_message_id, format="minimal").execute().get("threadId")
        d = svc.users().drafts().create(userId="me", body=payload).execute()
        return {"draft_id": d.get("id"), "to": to, "subject": subject, "note": "Draft saved in Gmail; not sent."}

    def gmail_send(self, to: str, subject: str, body: str) -> dict[str, Any]:
        svc = self._svc("gmail", "v1")
        m = svc.users().messages().send(userId="me", body={"raw": _raw_message(to, subject, body)}).execute()
        return {"sent": m.get("id"), "to": to, "subject": subject}

    def gmail_modify(self, message_id: str, mark_read: bool | None = None, archive: bool = False, star: bool | None = None) -> dict[str, Any]:
        add, rem = [], []
        if mark_read is True:
            rem.append("UNREAD")
        if mark_read is False:
            add.append("UNREAD")
        if archive:
            rem.append("INBOX")
        if star is True:
            add.append("STARRED")
        if star is False:
            rem.append("STARRED")
        self._svc("gmail", "v1").users().messages().modify(userId="me", id=message_id, body={"addLabelIds": add, "removeLabelIds": rem}).execute()
        return {"ok": True, "added": add, "removed": rem}

    # ---------- Tasks ----------
    def tasks_lists(self) -> list[dict[str, Any]]:
        res = self._svc("tasks", "v1").tasklists().list(maxResults=50).execute()
        return [{"id": t["id"], "title": t["title"]} for t in res.get("items", [])]

    def tasks_list(self, tasklist: str = "@default", show_completed: bool = False, max_results: int = 50) -> list[dict[str, Any]]:
        res = self._svc("tasks", "v1").tasks().list(tasklist=tasklist, showCompleted=show_completed, showHidden=show_completed, maxResults=max_results).execute()
        return [{"id": t["id"], "title": t.get("title"), "notes": t.get("notes"), "due": t.get("due"), "status": t.get("status")} for t in res.get("items", [])]

    def tasks_add(self, title: str, notes: str = "", due: str | None = None, tasklist: str = "@default") -> dict[str, Any]:
        body: dict[str, Any] = {"title": title, "notes": notes}
        if due:
            body["due"] = due if "T" in due else f"{due}T00:00:00.000Z"
        t = self._svc("tasks", "v1").tasks().insert(tasklist=tasklist, body=body).execute()
        return {"id": t["id"], "title": t.get("title"), "due": t.get("due")}

    def tasks_complete(self, task_id: str, tasklist: str = "@default") -> dict[str, Any]:
        t = self._svc("tasks", "v1").tasks().patch(tasklist=tasklist, task=task_id, body={"status": "completed"}).execute()
        return {"id": t["id"], "status": t.get("status")}

    # ---------- Docs / Sheets ----------
    _MIME = {"doc": "application/vnd.google-apps.document", "sheet": "application/vnd.google-apps.spreadsheet"}

    def drive_find(self, query: str = "", kind: str | None = None, max_results: int = 20) -> list[dict[str, Any]]:
        q = ["trashed = false"]
        if query:
            q.append("name contains '%s'" % query.replace("\\", "\\\\").replace("'", "\\'"))
        mimes = [self._MIME[kind]] if kind in self._MIME else list(self._MIME.values())
        q.append("(" + " or ".join(f"mimeType = '{m}'" for m in mimes) + ")")
        res = self._svc("drive", "v3").files().list(
            q=" and ".join(q), pageSize=max(1, min(int(max_results), 50)), orderBy="modifiedTime desc",
            fields="files(id,name,mimeType,modifiedTime,webViewLink)",
        ).execute()
        return [{"id": f["id"], "name": f.get("name"),
                 "kind": "sheet" if f.get("mimeType") == self._MIME["sheet"] else "doc",
                 "modified": f.get("modifiedTime"), "link": f.get("webViewLink")} for f in res.get("files", [])]

    def docs_get(self, document_id: str, max_chars: int = 20000) -> dict[str, Any]:
        doc = self._svc("docs", "v1").documents().get(documentId=document_id).execute()
        text = _doc_text(doc)
        return {"id": document_id, "title": doc.get("title"), "text": text[:max_chars],
                "truncated": len(text) > max_chars,
                "link": f"https://docs.google.com/document/d/{document_id}/edit"}

    def docs_create(self, title: str, content: str = "") -> dict[str, Any]:
        svc = self._svc("docs", "v1")
        doc = svc.documents().create(body={"title": title}).execute()
        did = doc["documentId"]
        if content:
            svc.documents().batchUpdate(documentId=did, body={"requests": [
                {"insertText": {"location": {"index": 1}, "text": content}}]}).execute()
        return {"id": did, "title": title, "link": f"https://docs.google.com/document/d/{did}/edit"}

    def docs_append(self, document_id: str, content: str) -> dict[str, Any]:
        svc = self._svc("docs", "v1")
        doc = svc.documents().get(documentId=document_id, fields="title,body(content(endIndex))").execute()
        # The document body always ends with a newline the API will not let us write past,
        # hence endIndex - 1.
        end = (doc.get("body", {}).get("content") or [{}])[-1].get("endIndex", 2)
        svc.documents().batchUpdate(documentId=document_id, body={"requests": [
            {"insertText": {"location": {"index": max(1, end - 1)}, "text": "\n" + content}}]}).execute()
        return {"id": document_id, "title": doc.get("title"), "appended_chars": len(content)}

    def docs_replace(self, document_id: str, content: str, title: str | None = None) -> dict[str, Any]:
        """Overwrite the document's whole body (doc backups re-push the same file, never append)."""
        svc = self._svc("docs", "v1")
        doc = svc.documents().get(documentId=document_id, fields="title,body(content(endIndex))").execute()
        end = (doc.get("body", {}).get("content") or [{}])[-1].get("endIndex", 2)
        requests: list[dict[str, Any]] = []
        if end > 2:  # deleteContentRange refuses an empty range, and an empty doc has only its final newline
            requests.append({"deleteContentRange": {"range": {"startIndex": 1, "endIndex": end - 1}}})
        if content:
            requests.append({"insertText": {"location": {"index": 1}, "text": content}})
        if requests:
            svc.documents().batchUpdate(documentId=document_id, body={"requests": requests}).execute()
        return {"id": document_id, "title": title or doc.get("title"),
                "link": f"https://docs.google.com/document/d/{document_id}/edit"}

    def sheets_read(self, spreadsheet_id: str, cell_range: str | None = None, max_rows: int = 200) -> dict[str, Any]:
        svc = self._svc("sheets", "v4").spreadsheets()
        meta = svc.get(spreadsheetId=spreadsheet_id, fields="properties(title),sheets(properties(title))").execute()
        tabs = [s["properties"]["title"] for s in meta.get("sheets", [])]
        rng = cell_range or (f"'{tabs[0]}'" if tabs else "A1:Z200")
        res = svc.values().get(spreadsheetId=spreadsheet_id, range=rng).execute()
        values = res.get("values", [])
        return {"id": spreadsheet_id, "title": meta.get("properties", {}).get("title"), "tabs": tabs,
                "range": res.get("range"), "values": values[:max_rows], "truncated": len(values) > max_rows,
                "link": f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit"}

    def sheets_write(self, spreadsheet_id: str, cell_range: str, values: list[list[Any]], append: bool = False) -> dict[str, Any]:
        vals = self._svc("sheets", "v4").spreadsheets().values()
        if append:
            r = vals.append(spreadsheetId=spreadsheet_id, range=cell_range, valueInputOption="USER_ENTERED",
                            insertDataOption="INSERT_ROWS", body={"values": values}).execute()
            u = r.get("updates", {})
            return {"id": spreadsheet_id, "range": u.get("updatedRange"), "cells": u.get("updatedCells")}
        r = vals.update(spreadsheetId=spreadsheet_id, range=cell_range, valueInputOption="USER_ENTERED",
                        body={"values": values}).execute()
        return {"id": spreadsheet_id, "range": r.get("updatedRange"), "cells": r.get("updatedCells")}

    def sheets_create(self, title: str, values: list[list[Any]] | None = None) -> dict[str, Any]:
        svc = self._svc("sheets", "v4").spreadsheets()
        ss = svc.create(body={"properties": {"title": title}}, fields="spreadsheetId,spreadsheetUrl").execute()
        sid = ss["spreadsheetId"]
        if values:
            svc.values().update(spreadsheetId=sid, range="A1", valueInputOption="USER_ENTERED",
                                body={"values": values}).execute()
        return {"id": sid, "title": title, "link": ss.get("spreadsheetUrl")}


def _token_error_hint(e: Exception) -> str:
    """Google\'s token endpoint errors are terse; say what actually needs fixing."""
    msg = str(e)
    if "redirect_uri_mismatch" in msg:
        return (
            "Google rejected the redirect URI. The OAuth client must be of type “Desktop app” "
            "(a Web application client would need every loopback port registered up front)."
        )
    if "invalid_client" in msg:
        return "Google rejected the client ID/secret. Check the values in .env or Settings → Integrations."
    if "invalid_grant" in msg:
        return "The sign-in code expired before it was used. Try signing in again."
    if "access_denied" in msg:
        return "Sign-in was cancelled, or this Google account is not a test user on the OAuth consent screen."
    return f"Google rejected the sign-in: {msg}"


def _start_ts(value: str | None) -> float:
    """Epoch sort key: raw ISO strings carry per-calendar UTC offsets, so string order is not time order."""
    try:
        d = dt.datetime.fromisoformat((value or "").replace("Z", "+00:00"))
    except ValueError:
        return float("inf")
    if d.tzinfo is None:  # all-day dates parse as naive midnight
        d = d.replace(tzinfo=dt.timezone.utc)
    return d.timestamp()


def _local_tz() -> str:
    try:
        import zoneinfo  # noqa: F401
        from pathlib import Path

        lt = Path("/etc/localtime")
        if lt.is_symlink():
            m = re.search(r"zoneinfo/(.+)$", str(lt.resolve()))
            if m:
                return m.group(1)
    except Exception:  # noqa: BLE001
        pass
    return "UTC"


def _raw_message(to: str, subject: str, body: str) -> str:
    msg = email.mime.text.MIMEText(body)
    msg["to"], msg["subject"] = to, subject
    return base64.urlsafe_b64encode(msg.as_bytes()).decode()


def _doc_text(doc: dict[str, Any]) -> str:
    """Flatten a Docs API document to plain text (paragraphs, tables, TOC)."""
    out: list[str] = []

    def walk(elements: list[dict[str, Any]] | None) -> None:
        for el in elements or []:
            if "paragraph" in el:
                for pe in el["paragraph"].get("elements", []):
                    t = pe.get("textRun", {}).get("content")
                    if t:
                        out.append(t)
            elif "table" in el:
                for row in el["table"].get("tableRows", []):
                    cells = []
                    for cell in row.get("tableCells", []):
                        mark = len(out)
                        walk(cell.get("content"))
                        cells.append("".join(out[mark:]).strip())
                        del out[mark:]
                    out.append(" | ".join(cells) + "\n")
            elif "tableOfContents" in el:
                walk(el["tableOfContents"].get("content"))

    walk(doc.get("body", {}).get("content"))
    return "".join(out)


def _extract_body(payload: dict[str, Any]) -> str:
    """Prefer text/plain; fall back to stripped HTML."""
    plain, html_ = [], []

    def walk(p: dict[str, Any]) -> None:
        mime = p.get("mimeType", "")
        data = p.get("body", {}).get("data")
        if data:
            text = base64.urlsafe_b64decode(data + "===").decode("utf-8", "replace")
            (plain if mime == "text/plain" else html_ if mime == "text/html" else []).append(text)
        for part in p.get("parts", []) or []:
            walk(part)

    walk(payload)
    if plain:
        return "\n".join(plain).strip()
    if html_:
        t = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", "\n".join(html_), flags=re.S | re.I)
        t = re.sub(r"<br\s*/?>|</p>|</div>", "\n", t, flags=re.I)
        t = re.sub(r"<[^>]+>", " ", t)
        import html as _html

        return re.sub(r"\n\s*\n+", "\n\n", re.sub(r"[ \t]+", " ", _html.unescape(t))).strip()
    return ""


def json_safe(v: Any) -> Any:
    return json.loads(json.dumps(v, default=str))
