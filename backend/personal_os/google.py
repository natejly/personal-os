"""Google Workspace integration: OAuth (desktop loopback flow), Calendar, Gmail, Tasks, Drive, Docs, Sheets.

The user just clicks "Sign in with Google". The OAuth client the app signs in with comes
from GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET in the environment (.env), so it is set up once
per install rather than pasted by every user. Settings → Integrations can still override it
with a user-supplied client (googleClientId / googleClientSecret).

Google treats the client secret of a "Desktop app" OAuth client as non-confidential, so
shipping it with the app is the sanctioned pattern (it is what gcloud, rclone etc. do).
Tokens are stored in the app database (settings.googleToken).

Every write here proves itself: after the API call returns, the object is read back from the
server and the written fields are compared, and the verdict rides on the result under
`verification` (see verify.py). A write whose read-back fails or disagrees comes back with
`verified: False`, which tools.py turns into a tool error and the UI refuses to render as
success. Gmail sends do not happen here at all any more -- they are queued through outbox.py so
they can be undone, and the verification runs when the hold expires and the send goes out.
"""
from __future__ import annotations

import base64
import contextlib
import datetime as dt
import email.mime.text
from email.utils import formataddr, getaddresses, parsedate_to_datetime
import json
import logging
import os
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Callable

from . import verify
from .cache import TTLCache, bypassing, cached, invalidates
from .google_store import ReadStore

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
    # Read-only for browsing/searching, drive.file for anything the app itself creates.
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/drive.file",
    # Docs/Sheets content.
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/spreadsheets",
]

# How long each kind of read may be served from cache. The numbers trade "the UI
# feels instant" against "an edit made in Google's own web UI shows up here", so
# anything the user stares at is seconds, and anything near-static is hours.
# Our own writes invalidate their namespace outright, so these only bound how
# stale a *third-party* change can be.
TTL = {
    "calendar_list": 10 * 60,   # calendars are added/removed rarely
    "calendar_colors": 24 * 3600,  # Google's fixed palette
    "calendar_events": 60,      # repeat views of the same week; after this, only changes are fetched
    "calendar_event": 30,       # one event, opened in the editor
    "gmail_list": 60,           # repeat of the same query; after this, only new or changed messages are fetched
    "gmail_threads": 120,       # recent threads for the reply tracker: 1 + N batched gets
    "gmail_message": 15 * 60,   # a fetched body never changes
    "gmail_labels": 10 * 60,
    "tasks_lists": 10 * 60,
    "tasks": 30,
    "drive_list": 2 * 60,
    "drive_read": 10 * 60,
    "docs_get": 60,
    "sheets_read": 60,
}


class GoogleNotConnected(Exception):
    pass


# A saved calendar window older than this is listed again in full. Younger than this,
# events.list is called with updatedMin, so unchanged events stay in the snapshot.
_CAL_FULL_AFTER = 6 * 3600
_CAL_OVERLAP = dt.timedelta(minutes=2)
_GMAIL_META_CAP = 800
# Saved message bodies: plain mail text on disk, and the whole store is rewritten on each
# save, so keep only the recently opened ones.
_GMAIL_BODY_CAP = 200


class Google:
    def __init__(self, get_settings: Callable[[], dict[str, Any]], set_settings: Callable[[dict[str, Any]], None], cache_dir: str | Path | None = None):
        self.get_settings = get_settings
        self.set_settings = set_settings
        self._pending: dict[str, Any] = {}  # state -> flow
        self._cache = TTLCache()
        # Survives the minute-scale TTL and a process restart. See google_store.py.
        self._reads = ReadStore(Path(cache_dir) / "google-reads.json" if cache_dir else None)
        # Built API clients and the Credentials they wrap, reused across calls; see _svc.
        self._svc_lock = threading.Lock()
        self._svcs: dict[tuple[str, str, int], tuple[Any, Any]] = {}  # (api, version, thread) -> (creds, client)
        self._creds_obj: Any = None
        self._creds_key: tuple[str, str] | None = None

    def invalidate(self, *namespaces: str) -> int:
        """Forget cached reads. No arguments forgets everything Google-related."""
        return self._cache.invalidate(*namespaces)

    def cache_stats(self) -> dict[str, Any]:
        return self._cache.stats()

    def _reset_clients(self) -> None:
        """Drop cached credentials, API clients and reads - used when the account changes."""
        with self._svc_lock:
            self._svcs.clear()
            self._creds_obj = None
            self._creds_key = None
        self._cache.clear()
        self._reads.clear()

    def forget(self, *namespaces: str) -> int:
        """Drop the short TTL cache and the saved snapshots. A write does not call this:
        the next read asks Google for what changed and merges it into the snapshot.
        """
        n = self._cache.invalidate(*namespaces)
        self._reads.clear(*namespaces)
        return n

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
        self._reset_clients()  # a new sign-in may be a different account
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
        self._reset_clients()

    def _creds(self):  # type: ignore[no-untyped-def]
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials

        tok = self.get_settings().get("googleToken") or {}
        if not tok.get("refresh_token"):
            raise GoogleNotConnected("Google account not connected")
        # One Credentials object per (account, client), reused across calls: building a
        # fresh one every call threw away the live access token, so a long-lived backend
        # re-checked expiry - and sometimes re-refreshed - on every single API call.
        key = (tok["refresh_token"], tok.get("client_id") or "")
        with self._svc_lock:
            if self._creds_key == key and self._creds_obj is not None and self._creds_obj.valid:
                return self._creds_obj
            # A different account (or OAuth client) must not read the old one's cache.
            changed_account = self._creds_key is not None and self._creds_key != key
        creds = Credentials(
            token=tok.get("token"), refresh_token=tok["refresh_token"], token_uri=tok["token_uri"],
            client_id=tok["client_id"], client_secret=tok["client_secret"], scopes=tok.get("scopes") or SCOPES,
        )
        if tok.get("expiry"):
            try:
                creds.expiry = _parse_iso(tok["expiry"]).replace(tzinfo=None)
            except ValueError:
                pass
        if not creds.valid:
            try:
                creds.refresh(Request())
            except Exception as e:  # noqa: BLE001
                log.warning("Google token refresh failed: %s", e)
                if not _refresh_rejected(e):
                    # Offline, DNS, a 5xx: the sign-in is fine and the next call will retry.
                    # Flagging these told the user to reconnect after every wake from sleep.
                    raise
                # invalid_grant: the refresh token was revoked, expired (testing-mode consent
                # screens expire them in 7 days) or belongs to a different OAuth client.
                self._save_token(tok, {"needs_reauth": True})
                raise GoogleNotConnected(
                    "Google sign-in expired or was revoked. Open Settings → Integrations and sign in again."
                ) from e
            self._save_token(tok, {"token": creds.token, "expiry": creds.expiry.isoformat() if creds.expiry else None, "needs_reauth": False})
        with self._svc_lock:
            self._creds_obj = creds
            self._creds_key = key
        if changed_account:
            self._cache.clear()
            self._reads.clear()
        return creds

    def _save_token(self, tok: dict[str, Any], patch: dict[str, Any]) -> None:
        """Write refresh results back, unless the sign-in changed while the refresh ran.

        `tok` was read before a network call. Writing it back blind undid a disconnect
        (or a switch of account) that happened in the meantime.
        """
        cur = self.get_settings().get("googleToken") or {}
        if cur.get("refresh_token") != tok.get("refresh_token"):
            return
        self.set_settings({"googleToken": {**cur, **patch}})

    def _svc(self, name: str, version: str):  # type: ignore[no-untyped-def]
        """A built API client, reused per (api, version).

        `build()` parses a discovery document and synthesises the whole resource tree,
        which costs tens of milliseconds - noticeable when drawing one calendar week
        does it fifteen times over. Each client is remembered alongside the exact
        Credentials object it captured, so once `_creds` mints a new one (a token
        refresh, or a different account) the stale client is rebuilt rather than reused.

        One client per thread: a client wraps one httplib2 connection, which is not
        thread-safe. Shared across the worker threads that serve concurrent requests
        (the Mail view loads its search and its labels at once), two calls interleave
        on one socket and one of them hangs until the read times out a minute later.
        """
        from googleapiclient.discovery import build

        creds = self._creds()
        key = (name, version, threading.get_ident())
        with self._svc_lock:
            entry = self._svcs.get(key)
            if entry is not None and entry[0] is creds:
                return entry[1]
        svc = build(name, version, credentials=creds, cache_discovery=False)
        with self._svc_lock:
            self._svcs[key] = (creds, svc)
        return svc

    # ---------- Calendar ----------
    @cached("calendar", TTL["calendar_list"])
    def calendars(self) -> list[dict[str, Any]]:
        """The user's calendar list: primary first, then the ones they can write to."""
        res = self._svc("calendar", "v3").calendarList().list(maxResults=100).execute()
        out = []
        for c in res.get("items", []):
            if c.get("deleted"):
                continue
            out.append({
                "id": c["id"],
                "summary": c.get("summaryOverride") or c.get("summary") or c["id"],
                "primary": bool(c.get("primary")),
                "access_role": c.get("accessRole"),
                "color": c.get("backgroundColor"),
                "time_zone": c.get("timeZone"),
                "hidden": bool(c.get("hidden")),
                "selected": c.get("selected", False),
            })
        return sorted(out, key=lambda c: (not c["primary"], c["access_role"] not in ("owner", "writer"), c["summary"].lower()))

    def calendar_ensure(self, summary: str) -> dict[str, Any]:
        """Find, or create, a secondary calendar of this name that we can write to.

        Used for the todo mirror: its own calendar keeps generated events out of the
        primary one and lets the user hide them all with one checkbox in Google Calendar.
        """
        want = summary.strip().lower()
        for c in self.calendars():
            if c["summary"].strip().lower() == want and c["access_role"] in ("owner", "writer"):
                return {"id": c["id"], "summary": c["summary"], "created": False}
        svc = self._svc("calendar", "v3")
        cal = svc.calendars().insert(body={"summary": summary, "timeZone": _local_tz()}).execute()
        # A brand new calendar is not necessarily shown in the UI; make sure it is.
        with contextlib.suppress(Exception):
            svc.calendarList().patch(calendarId=cal["id"], body={"selected": True}).execute()
        return {"id": cal["id"], "summary": cal.get("summary") or summary, "created": True}

    @cached("calendar", TTL["calendar_colors"])
    def calendar_colors(self) -> dict[str, Any]:
        """Google's fixed palettes, id -> hex; events reference these by colorId."""
        res = self._svc("calendar", "v3").colors().get().execute()
        return {
            "event": {k: v.get("background") for k, v in (res.get("event") or {}).items()},
            "calendar": {k: v.get("background") for k, v in (res.get("calendar") or {}).items()},
        }

    @cached("calendar", TTL["calendar_events"])
    def calendar_events(self, days: int = 2, calendar_id: str = "primary", max_results: int = 30, start: str | None = None, calendar_ids: list[str] | None = None) -> list[dict[str, Any]]:
        now = _parse_iso(start) if start else dt.datetime.now(dt.timezone.utc)
        if now.tzinfo is None:
            now = now.astimezone()  # a naive start is the user's wall clock, same as calendar_create reads it
        end = now + dt.timedelta(days=max(1, min(int(days), 60)))
        ids = calendar_ids or [calendar_id]
        if ids == ["all"]:
            ids = [c["id"] for c in self.calendars() if not c["hidden"]][:15]
        svc = self._svc("calendar", "v3")
        out: list[dict[str, Any]] = []
        try:
            for cid in ids:
                try:
                    out.extend(self._sync_calendar(svc, cid, now, end, max_results))
                except Exception as e:  # noqa: BLE001  # one broken subscription should not empty the whole grid
                    if len(ids) == 1:
                        raise
                    log.warning("calendar %s skipped: %s", cid, e)
        finally:
            self._reads.flush()
        out.sort(key=lambda e: e["start"] or "")
        return out[: max_results if len(ids) == 1 else max_results * 2]

    @cached("calendar", TTL["calendar_events"])
    def calendar_free_busy(self, time_min: str, time_max: str, calendars: list[str] | None = None,
                           attendees: list[str] | None = None) -> dict[str, Any]:
        """Busy ranges from the Google freebusy API.

        `calendars` defaults to every calendar the user has not hidden; `attendees` are other
        people's emails (Google answers for those it can see, and reports the rest as errors,
        which come back under `unreachable` rather than as silence).
        """
        a, b = _parse_iso(time_min), _parse_iso(time_max)
        if a.tzinfo is None:
            a = a.astimezone()
        if b.tzinfo is None:
            b = b.astimezone()
        ids = [c for c in (calendars or []) if c]
        if not ids or ids == ["all"]:
            ids = [c["id"] for c in self.calendars() if not c["hidden"]][:20] or ["primary"]
        people = [p for p in (attendees or []) if p and p not in ids]
        items = [{"id": i} for i in [*ids, *people]]
        res = self._svc("calendar", "v3").freebusy().query(
            body={"timeMin": a.isoformat(), "timeMax": b.isoformat(), "timeZone": _local_tz(), "items": items}).execute()
        out: dict[str, Any] = {}
        unreachable: list[str] = []
        for cid, row in (res.get("calendars") or {}).items():
            errs = row.get("errors") or []
            if errs:
                unreachable.append(cid)
            out[cid] = {"busy": [{"start": x["start"], "end": x["end"]} for x in row.get("busy", [])],
                        **({"errors": [e.get("reason") for e in errs]} if errs else {}), "attendee": cid in people}
        return {"time_min": a.isoformat(), "time_max": b.isoformat(), "calendars": out, "unreachable": unreachable}
    def _sync_calendar(self, svc: Any, cid: str, start: dt.datetime, end: dt.datetime, max_results: int) -> list[dict[str, Any]]:
        """Events in [start, end) for one calendar.

        The first time a window is seen it is listed in full and saved. Later reads of a
        window we already cover ask only for rows changed since that save (updatedMin),
        and merge them in. A snapshot older than `_CAL_FULL_AFTER` is listed again, so an
        event moved out of the window cannot linger forever.
        """
        snap = self._reads.get("calendar", cid) or {}
        stored: dict[str, dict[str, Any]] = dict(snap.get("events") or {})
        covered_start, covered_end = _parse_opt(snap.get("start")), _parse_opt(snap.get("end"))
        synced_at = _parse_opt(snap.get("synced_at"))
        slack = dt.timedelta(seconds=2)
        fresh = synced_at is not None and (dt.datetime.now(dt.timezone.utc) - synced_at).total_seconds() < _CAL_FULL_AFTER
        # A window that starts inside what we saved and does not jump more than a day past it
        # (the agenda's "now" sliding forward) is an incremental read. A week we have never
        # opened still lists in full.
        near = bool(
            fresh and covered_start and covered_end and synced_at
            and start >= covered_start - slack and start <= covered_end
            and end <= covered_end + dt.timedelta(days=1)
        )
        listed: list[dict[str, Any]] | None = None
        if near and covered_start and covered_end and synced_at:
            changed = _list_events(svc, cid, covered_start, max(covered_end, end), synced_at - _CAL_OVERLAP, max(max_results, 250), paginate=True)
            _merge_events(stored, changed, cid)
            if end > covered_end + dt.timedelta(hours=1):
                # The far edge has slid by more than a clock tick. Unmodified events that
                # just entered the window are not in an updatedMin result, so list that sliver.
                sliver = _list_events(svc, cid, covered_end, end, None, max(max_results, 250), paginate=True)
                _merge_events(stored, sliver, cid)
            window_start, window_end = covered_start, max(covered_end, end)
        else:
            w0 = min(start, covered_start) if covered_start else start
            w1 = max(end, covered_end) if covered_end else end
            if w1 - w0 > dt.timedelta(days=120):
                w0, w1 = start, end
            stored = {eid: ev for eid, ev in stored.items() if not _overlaps(ev, w0, w1)}
            # Listed in full, not just the first `max_results`: the snapshot is recorded as
            # covering this window, and later reads only ask for what changed since. A list
            # cut at the display limit left every later event missing until the snapshot aged out.
            items = _list_events(svc, cid, w0, w1, None, max(max_results, 250), paginate=True)
            listed = [_event_out(e, cid) for e in items if e.get("status") != "cancelled"]
            for ev in listed:
                if ev.get("id"):
                    stored[str(ev["id"])] = ev
            window_start, window_end = w0, w1
        if len(stored) > 1500:
            stored = {eid: ev for eid, ev in stored.items() if _overlaps(ev, start, end)}
        self._reads.put("calendar", cid, {
            "start": window_start.isoformat(), "end": window_end.isoformat(),
            "synced_at": dt.datetime.now(dt.timezone.utc).isoformat(), "events": stored,
        }, flush=False)
        # A full list of exactly this window is returned as Google sent it. A wider saved
        # window, or an incremental merge, is sliced locally so unchanged rows are not
        # downloaded again to answer a smaller question.
        if listed is not None and window_start == start and window_end == end:
            return listed
        return [ev for ev in stored.values() if _overlaps(ev, start, end)]

    def calendar_saved(self, days: int) -> list[dict[str, Any]] | None:
        """Events in the next `days` from the saved snapshots only; never calls Google. None when nothing was ever saved."""
        snaps = [v for v in self._reads.values("calendar") if isinstance(v, dict) and isinstance(v.get("events"), dict)]
        if not snaps:
            return None
        a = dt.datetime.now(dt.timezone.utc)
        b = a + dt.timedelta(days=days)
        return [ev for v in snaps for ev in v["events"].values() if _overlaps(ev, a, b)]

    def _drop_saved_event(self, calendar_id: str, event_id: str) -> None:
        """Take an event we are about to change out of the saved window.

        The next read asks Google only for rows changed since the snapshot, inside the
        window it covers. An event moved out of that window (or to another calendar) is
        not in that answer, so its old copy stayed on the grid for hours. Dropped here, it
        comes back from that same read if it still belongs. A series id drops its instances.
        """
        snap = self._reads.get("calendar", calendar_id)
        if not isinstance(snap, dict) or not isinstance(snap.get("events"), dict):
            return
        events = {eid: ev for eid, ev in snap["events"].items()
                  if eid != event_id and not eid.startswith(f"{event_id}_")}
        if len(events) != len(snap["events"]):
            self._reads.put("calendar", calendar_id, {**snap, "events": events})

    @cached("calendar", TTL["calendar_event"])
    def calendar_get(self, event_id: str, calendar_id: str = "primary") -> dict[str, Any]:
        e = self._svc("calendar", "v3").events().get(calendarId=calendar_id, eventId=event_id).execute()
        return _event_out(e, calendar_id, full=True)

    @invalidates("calendar")
    def calendar_create(self, event: dict[str, Any], calendar_id: str = "primary", send_updates: str = "none") -> dict[str, Any]:
        body = self._event_body(event)
        kwargs: dict[str, Any] = {"calendarId": calendar_id, "body": body, "sendUpdates": _send_updates(send_updates)}
        if event.get("create_meet"):
            body["conferenceData"] = {"createRequest": {"requestId": secrets.token_hex(16), "conferenceSolutionKey": {"type": "hangoutsMeet"}}}
            kwargs["conferenceDataVersion"] = 1
        e = self._svc("calendar", "v3").events().insert(**kwargs).execute()
        out = _event_out(e, calendar_id, full=True)
        return verify.attach(out, self._verify_event(calendar_id, out["id"], out, body))

    @invalidates("calendar")
    def calendar_update(self, event_id: str, event: dict[str, Any], calendar_id: str = "primary", send_updates: str = "none") -> dict[str, Any]:
        """Patch an event; only the keys present in `event` change.

        To edit one instance of a recurring event pass the instance id (from the list);
        to edit the whole series pass its recurring_event_id.
        """
        svc = self._svc("calendar", "v3").events()
        updates = _send_updates(send_updates)
        self._drop_saved_event(calendar_id, event_id)
        dest = event.get("move_to_calendar_id")
        if dest and dest != calendar_id:
            svc.move(calendarId=calendar_id, eventId=event_id, destination=dest, sendUpdates=updates).execute()
            calendar_id = dest
        if event.get("start") and not event.get("end"):
            # Only the start moved. Without an end the body below falls back to one hour
            # (or one day), which silently shortened a longer event.
            end = _end_keeping_length(svc, calendar_id, event_id, str(event["start"]))
            if end:
                event = {**event, "end": end}
        body = self._event_body(event, patch=True)
        kwargs: dict[str, Any] = {"calendarId": calendar_id, "eventId": event_id, "body": body, "sendUpdates": updates}
        if event.get("create_meet"):
            body["conferenceData"] = {"createRequest": {"requestId": secrets.token_hex(16), "conferenceSolutionKey": {"type": "hangoutsMeet"}}}
            kwargs["conferenceDataVersion"] = 1
        elif event.get("clear_meet"):
            body["conferenceData"] = None
            kwargs["conferenceDataVersion"] = 1
        if not body:
            # Nothing was written (a bare move, or an empty patch), so there is nothing to prove.
            return _event_out(svc.get(calendarId=calendar_id, eventId=event_id).execute(), calendar_id, full=True)
        out = _event_out(svc.patch(**kwargs).execute(), calendar_id, full=True)
        return verify.attach(out, self._verify_event(calendar_id, event_id, out, body))

    @invalidates("calendar")
    def calendar_delete(self, event_id: str, calendar_id: str = "primary", send_updates: str = "none") -> dict[str, Any]:
        self._svc("calendar", "v3").events().delete(calendarId=calendar_id, eventId=event_id, sendUpdates=_send_updates(send_updates)).execute()
        self._drop_saved_event(calendar_id, event_id)
        return verify.attach({"deleted": event_id, "calendar_id": calendar_id},
                             self._verify_event_gone(calendar_id, event_id))

    @invalidates("calendar")
    def calendar_respond(self, event_id: str, response: str, calendar_id: str = "primary", send_updates: str = "none") -> dict[str, Any]:
        """RSVP to an invitation: accepted, declined, tentative or needsAction."""
        if response not in ("accepted", "declined", "tentative", "needsAction"):
            raise ValueError("response must be accepted, declined, tentative or needsAction")
        svc = self._svc("calendar", "v3").events()
        e = svc.get(calendarId=calendar_id, eventId=event_id).execute()
        attendees = e.get("attendees") or []
        me = next((a for a in attendees if a.get("self")), None)
        if not me:
            raise ValueError("You are not an attendee of this event, so there is nothing to respond to.")
        me["responseStatus"] = response
        e = svc.patch(calendarId=calendar_id, eventId=event_id, body={"attendees": attendees}, sendUpdates=_send_updates(send_updates)).execute()
        out = _event_out(e, calendar_id, full=True)
        return verify.attach(out, self._verify_rsvp(calendar_id, event_id, response))

    # ---------- Calendar read-backs ----------
    # Each one re-fetches from the server rather than trusting the write's own echo, and names in
    # `compared` exactly which fields it proved.
    def _event_reader(self, calendar_id: str, event_id: str) -> Callable[[], dict[str, Any]]:
        def read_back() -> dict[str, Any]:
            try:
                return self._svc("calendar", "v3").events().get(calendarId=calendar_id, eventId=event_id).execute()
            except Exception as e:  # noqa: BLE001
                if verify.is_missing(e):
                    raise verify.NotVisible(f"event {event_id} is not on {calendar_id}") from e
                raise
        return read_back

    def _verify_event(self, calendar_id: str, event_id: str, wrote: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
        """Fetch the event back by id and compare the fields this call actually wrote."""
        keys = [flat for api, flat in _EVENT_VERIFY_FIELDS.items() if api in body] or ["summary", "start"]
        want = {**{k: wrote.get(k) for k in keys}, "status": wrote.get("status") or "confirmed"}

        def compare(e: dict[str, Any]) -> dict[str, Any]:
            got = _event_out(e, calendar_id, full=True)
            return verify.diff(want, got, time_fields=("start", "end"))

        return verify.check(f"calendar event {event_id} on {calendar_id}", self._event_reader(calendar_id, event_id),
                            compare=compare, compared=sorted(want))

    def _verify_event_gone(self, calendar_id: str, event_id: str) -> dict[str, Any]:
        """A deleted event must be 404, or kept as a cancelled tombstone."""
        return verify.check(f"calendar event {event_id} on {calendar_id}", self._event_reader(calendar_id, event_id),
                            absent=True, gone_if=lambda e: e.get("status") == "cancelled", compared=["absent"])

    def _verify_rsvp(self, calendar_id: str, event_id: str, response: str) -> dict[str, Any]:
        def compare(e: dict[str, Any]) -> dict[str, Any]:
            me = next((a for a in e.get("attendees") or [] if a.get("self")), {})
            return verify.diff({"my_response": response}, {"my_response": me.get("responseStatus")})

        return verify.check(f"RSVP on event {event_id}", self._event_reader(calendar_id, event_id),
                            compare=compare, compared=["my_response"])

    def _event_body(self, f: dict[str, Any], patch: bool = False) -> dict[str, Any]:
        """Translate our flat event fields into a Calendar API body (insert or patch)."""
        body: dict[str, Any] = {}
        for src, dst in (("summary", "summary"), ("description", "description"), ("location", "location"),
                         ("visibility", "visibility"), ("transparency", "transparency"),
                         ("guests_can_invite_others", "guestsCanInviteOthers"), ("guests_can_modify", "guestsCanModify"),
                         ("guests_can_see_other_guests", "guestsCanSeeOtherGuests")):
            if f.get(src) is not None:
                body[dst] = f[src]
        if f.get("color_id") is not None:
            body["colorId"] = f["color_id"] or None  # '' clears back to the calendar's color
        if f.get("recurrence") is not None:
            # [] clears the recurrence (a series becomes a single event).
            body["recurrence"] = [r for r in f["recurrence"] if r and r.strip()]
        if f.get("start"):
            start, end = str(f["start"]), f.get("end")
            all_day = len(start) == 10
            if not end:
                # All-day end is exclusive, so a one-day event needs start+1.
                end = (dt.date.fromisoformat(start) + dt.timedelta(days=1)).isoformat() if all_day else (_parse_iso(start) + dt.timedelta(hours=1)).isoformat()
            elif all_day and end == start:
                end = (dt.date.fromisoformat(start) + dt.timedelta(days=1)).isoformat()
            if all_day:
                body["start"], body["end"] = {"date": start}, {"date": end}
                if patch:  # a patch keeps the old dateTime unless it is cleared explicitly
                    body["start"].update({"dateTime": None, "timeZone": None})
                    body["end"].update({"dateTime": None, "timeZone": None})
            else:
                body["start"], body["end"] = {"dateTime": start}, {"dateTime": str(end)}
                naive = "T" in start and not re.search(r"[+-]\d\d:\d\d$|Z$", start)
                tz = f.get("time_zone") or (_local_tz() if naive else None)
                if tz:
                    body["start"]["timeZone"] = body["end"]["timeZone"] = tz
                if patch:
                    body["start"]["date"] = body["end"]["date"] = None
        if f.get("attendees") is not None:
            body["attendees"] = [
                {"email": a["email"].strip(),
                 **({"optional": True} if a.get("optional") else {}),
                 # A replaced attendee list resets RSVPs unless each responseStatus is resent.
                 **({"responseStatus": a["response"]} if a.get("response") else {})}
                for a in ({"email": a} if isinstance(a, str) else a for a in f["attendees"])
                if (a.get("email") or "").strip()
            ]
        if f.get("reminders") is not None:
            r = f["reminders"]
            use_default = bool(r.get("use_default", r.get("useDefault", False)))
            body["reminders"] = {"useDefault": use_default}
            if not use_default:
                body["reminders"]["overrides"] = [
                    {"method": o.get("method") if o.get("method") in ("popup", "email") else "popup", "minutes": max(0, min(int(o.get("minutes", 10)), 40320))}
                    for o in (r.get("overrides") or [])
                ][:5]
        return body

    # ---------- Gmail ----------
    @cached("gmail", TTL["gmail_list"])
    def gmail_search(self, query: str = "is:unread in:inbox newer_than:14d", max_results: int = 15) -> list[dict[str, Any]]:
        """Headers for a query. Message ids come from messages.list every time; the
        metadata get runs only for ids we have never stored, or that history says changed.
        """
        svc = self._svc("gmail", "v1")
        res = svc.users().messages().list(userId="me", q=query, maxResults=max(1, min(int(max_results), 50))).execute()
        ids = [m["id"] for m in res.get("messages") or []]
        if not ids:
            return []
        snap = self._reads.get("gmail", "meta") or {}
        by_id: dict[str, dict[str, Any]] = dict(snap.get("by_id") or {})
        cursor = snap.get("history_id") if isinstance(snap.get("history_id"), str) else None
        listed_history = res.get("historyId") if isinstance(res.get("historyId"), str) else None
        changed: set[str] = set()
        history_id = cursor
        if cursor:
            try:
                changed, deleted, latest = _gmail_history(svc, cursor)
            except Exception as e:  # noqa: BLE001
                if verify.is_missing(e):
                    # The cursor is older than Gmail keeps. The saved headers are no longer a base.
                    by_id.clear()
                    changed = set(ids)
                    history_id = listed_history
                else:
                    log.warning("gmail history skipped: %s", e)
                    # Keep the cursor so the next read retries this gap. A refresh still
                    # re-reads the page on screen; an ordinary load does not re-get every row.
                    changed = set(ids) if bypassing() else set()
            else:
                for mid in deleted:
                    by_id.pop(mid, None)
                # A change outside this page must not stay cached under a cursor that has moved past it.
                for mid in changed:
                    if mid not in ids:
                        by_id.pop(mid, None)
                history_id = latest or cursor
        else:
            changed = set(ids)
            history_id = listed_history
        need = [i for i in ids if i not in by_id or i in changed]
        if need:
            by_id.update(self._gmail_batch_meta(svc, need))
        protect = set(ids)
        extras = [k for k in by_id if k not in protect]
        while len(by_id) > _GMAIL_META_CAP and extras:
            by_id.pop(extras.pop(0))
        self._reads.put("gmail", "meta", {"history_id": history_id, "by_id": by_id})
        return [by_id[i] for i in ids if i in by_id]

    def _gmail_batch_meta(self, svc: Any, ids: list[str]) -> dict[str, dict[str, Any]]:
        by_id: dict[str, dict[str, Any]] = {}

        def _cb(_request_id: str, response: Any, exception: Exception | None) -> None:
            if exception or not isinstance(response, dict):
                if exception:
                    log.warning("gmail batch get failed: %s", exception)
                return
            mid = response.get("id")
            if mid:
                by_id[mid] = _gmail_meta(response)

        batch = svc.new_batch_http_request(callback=_cb)
        for mid in ids:
            batch.add(svc.users().messages().get(userId="me", id=mid, format="metadata", metadataHeaders=["From", "Subject", "Date"]))
        batch.execute()
        return by_id

    def _note_gmail_labels(self, message_id: str, add: list[str], rem: list[str]) -> None:
        """Keep the saved header in step with a label change we just made, so the next
        list does not paint the old unread/star state while history catches up."""
        snap = self._reads.get("gmail", "meta")
        if not isinstance(snap, dict):
            return
        by_id = dict(snap.get("by_id") or {})
        row = by_id.get(message_id)
        if not isinstance(row, dict):
            return
        labels = [l for l in (row.get("labels") or []) if l not in rem]
        for label in add:
            if label not in labels:
                labels.append(label)
        by_id[message_id] = {**row, "labels": labels, "unread": "UNREAD" in labels}
        self._reads.put("gmail", "meta", {**snap, "by_id": by_id})

    def _me(self) -> str | None:
        """The connected address, from the token settings already held (no API call)."""
        return ((self.get_settings().get("googleToken") or {}).get("email") or None)

    @cached("gmail", TTL["gmail_threads"])
    def gmail_threads_recent(self, query: str = "newer_than:14d -category:promotions -category:social -in:spam", max_threads: int = 40) -> list[dict[str, Any]]:
        """Recent threads as metadata only (headers plus snippet, never bodies), messages oldest to newest."""
        svc = self._svc("gmail", "v1")
        res = svc.users().threads().list(userId="me", q=query, maxResults=max(1, min(int(max_threads), 100))).execute()
        ids = [t["id"] for t in res.get("threads") or []]
        if not ids:
            return []
        by_id: dict[str, dict[str, Any]] = {}

        def _cb(_request_id: str, response: Any, exception: Exception | None) -> None:
            if exception or not isinstance(response, dict):
                if exception:
                    log.warning("gmail thread batch get failed: %s", exception)
                return
            if response.get("id"):
                by_id[response["id"]] = _gmail_thread_meta(response)

        batch = svc.new_batch_http_request(callback=_cb)
        for tid in ids:
            batch.add(svc.users().threads().get(userId="me", id=tid, format="metadata", metadataHeaders=_THREAD_HEADERS))
        batch.execute()
        return [by_id[i] for i in ids if i in by_id]

    @cached("gmail", TTL["gmail_message"])
    def gmail_get(self, message_id: str, max_chars: int = 8000) -> dict[str, Any]:
        # A message body does not change once it is stored. Serve the saved copy so opening
        # a message again is not another full get. A bypass (explicit refresh) still fetches.
        key = f"{message_id}:{max_chars}"
        if not bypassing():
            hit = self._reads.get("gmail-body", key)
            if isinstance(hit, dict) and hit.get("id") == message_id:
                return hit
        svc = self._svc("gmail", "v1")
        msg = svc.users().messages().get(userId="me", id=message_id, format="full").execute()
        h = {x["name"].lower(): x["value"] for x in msg.get("payload", {}).get("headers", [])}
        body = _extract_body(msg.get("payload", {}))
        out = {"id": message_id, "thread_id": msg.get("threadId"), "from": h.get("from"), "to": h.get("to"), "subject": h.get("subject"), "date": _rfc2822_iso(h.get("date")), "body": body[:max_chars]}
        self._reads.put("gmail-body", key, out, cap=_GMAIL_BODY_CAP)
        return out

    def _reply_headers(self, reply_to_message_id: str) -> tuple[str | None, dict[str, str]]:
        """Thread id plus In-Reply-To/References headers so mail clients thread the reply."""
        svc = self._svc("gmail", "v1")
        orig = svc.users().messages().get(userId="me", id=reply_to_message_id, format="metadata", metadataHeaders=["Message-ID", "References"]).execute()
        h = {x["name"].lower(): x["value"] for x in orig.get("payload", {}).get("headers", [])}
        mid = h.get("message-id")
        headers = {"in_reply_to": mid, "references": f"{h.get('references', '')} {mid}".strip()} if mid else {}
        return orig.get("threadId"), headers

    @invalidates("gmail")
    def gmail_draft(self, to: str, subject: str, body: str, reply_to_message_id: str | None = None) -> dict[str, Any]:
        svc = self._svc("gmail", "v1")
        message: dict[str, Any] = {}
        headers: dict[str, str] = {}
        if reply_to_message_id:
            tid, headers = self._reply_headers(reply_to_message_id)
            if tid:
                message["threadId"] = tid
        message["raw"] = _raw_message(to, subject, body, **headers)
        d = svc.users().drafts().create(userId="me", body={"message": message}).execute()
        out = {"draft_id": d.get("id"), "to": to, "subject": subject, "note": "Draft saved in Gmail; not sent."}
        return verify.attach(out, self._verify_draft(d.get("id") or "", subject))

    @invalidates("gmail")
    def gmail_send(self, to: str, subject: str, body: str, reply_to_message_id: str | None = None) -> dict[str, Any]:
        """Send now. Callers go through outbox.py instead, which holds the send so it can be undone."""
        svc = self._svc("gmail", "v1")
        message: dict[str, Any] = {}
        headers: dict[str, str] = {}
        if reply_to_message_id:
            tid, headers = self._reply_headers(reply_to_message_id)
            if tid:
                message["threadId"] = tid
        message["raw"] = _raw_message(to, subject, body, **headers)
        m = svc.users().messages().send(userId="me", body=message).execute()
        out = {"sent": m.get("id"), "to": to, "subject": subject, "thread_id": m.get("threadId")}
        return verify.attach(out, self._verify_sent(m.get("id") or "", to, subject, m.get("threadId")))

    def _verify_sent(self, message_id: str, to: str, subject: str, thread_id: str | None) -> dict[str, Any]:
        """Prove the mail is really in SENT: fetch it by id, insist on the SENT label, and check
        the thread, subject and recipients of the message the server actually stored."""
        if not message_id:
            return {"status": verify.UNVERIFIED, "reason": "not_visible", "what": "sent message",
                    "detail": "Gmail returned no message id", "attempts": 0, "compared": []}
        want_to = _addresses(to)

        def read_back() -> dict[str, Any]:
            try:
                m = self._svc("gmail", "v1").users().messages().get(
                    userId="me", id=message_id, format="metadata", metadataHeaders=["To", "Subject"]).execute()
            except Exception as e:  # noqa: BLE001
                if verify.is_missing(e):
                    raise verify.NotVisible(f"message {message_id} is not in the mailbox") from e
                raise
            if "SENT" not in (m.get("labelIds") or []):
                # It exists but Gmail has not filed it as sent yet: not visible, not a mismatch.
                raise verify.NotVisible(f"message {message_id} is not labelled SENT yet")
            return m

        def compare(m: dict[str, Any]) -> dict[str, Any]:
            h = {x["name"].lower(): x["value"] for x in m.get("payload", {}).get("headers", [])}
            got_to = _addresses(h.get("to", ""))
            d = verify.diff({"thread_id": thread_id, "subject": subject},
                            {"thread_id": m.get("threadId"), "subject": h.get("subject")})
            missing = [a for a in want_to if a not in got_to]
            if missing:
                d["to"] = {"expected": ", ".join(want_to), "actual": h.get("to")}
            return d

        return verify.check(f"sent message {message_id} in Gmail SENT", read_back, compare=compare,
                            compared=["SENT label", "thread_id", "subject", "to"], delays=verify.MAIL_RETRY_DELAYS)

    def _verify_draft(self, draft_id: str, subject: str) -> dict[str, Any]:
        def read_back() -> dict[str, Any]:
            try:
                return self._svc("gmail", "v1").users().drafts().get(userId="me", id=draft_id, format="metadata").execute()
            except Exception as e:  # noqa: BLE001
                if verify.is_missing(e):
                    raise verify.NotVisible(f"draft {draft_id} is not in Gmail") from e
                raise

        def compare(d: dict[str, Any]) -> dict[str, Any]:
            h = {x["name"].lower(): x["value"] for x in (d.get("message") or {}).get("payload", {}).get("headers", [])}
            return verify.diff({"subject": subject}, {"subject": h.get("subject")})

        return verify.check(f"draft {draft_id} in Gmail", read_back, compare=compare, compared=["exists", "subject"])

    @invalidates("gmail")
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
        self._note_gmail_labels(message_id, add, rem)
        return verify.attach({"ok": True, "added": add, "removed": rem}, self._verify_labels(message_id, add, rem))

    def _verify_labels(self, message_id: str, add: list[str], rem: list[str]) -> dict[str, Any]:
        """Re-read the message's labels: every added one present, every removed one gone."""
        def read_back() -> dict[str, Any]:
            try:
                return self._svc("gmail", "v1").users().messages().get(
                    userId="me", id=message_id, format="minimal").execute()
            except Exception as e:  # noqa: BLE001
                if verify.is_missing(e):
                    raise verify.NotVisible(f"message {message_id} is not in the mailbox") from e
                raise

        def compare(m: dict[str, Any]) -> dict[str, Any]:
            have = set(m.get("labelIds") or [])
            out: dict[str, Any] = {}
            for label in add:
                if label not in have:
                    out[f"+{label}"] = {"expected": "present", "actual": "missing"}
            for label in rem:
                if label in have:
                    out[f"-{label}"] = {"expected": "removed", "actual": "still set"}
            return out

        labels = [f"+{x}" for x in add] + [f"-{x}" for x in rem]
        return verify.check(f"labels on message {message_id}", read_back, compare=compare,
                            compared=labels or ["labels"])

    @cached("gmail", TTL["gmail_labels"])
    def gmail_labels(self) -> list[dict[str, Any]]:
        res = self._svc("gmail", "v1").users().labels().list(userId="me").execute()
        labels = [{"id": l["id"], "name": l.get("name", l["id"]), "type": l.get("type", "user")} for l in res.get("labels", [])]
        return sorted(labels, key=lambda x: (x["type"] != "system", x["name"].lower()))

    # ---------- Tasks ----------
    @cached("tasks", TTL["tasks_lists"])
    def tasks_lists(self) -> list[dict[str, Any]]:
        res = self._svc("tasks", "v1").tasklists().list(maxResults=50).execute()
        return [{"id": t["id"], "title": t["title"]} for t in res.get("items", [])]

    @cached("tasks", TTL["tasks"])
    def tasks_list(self, tasklist: str = "@default", show_completed: bool = False, max_results: int = 50) -> list[dict[str, Any]]:
        res = self._svc("tasks", "v1").tasks().list(tasklist=tasklist, showCompleted=show_completed, showHidden=show_completed, maxResults=max_results).execute()
        return [{"id": t["id"], "title": t.get("title"), "notes": t.get("notes"), "due": t.get("due"), "status": t.get("status")} for t in res.get("items", [])]

    @invalidates("tasks")
    def tasks_add(self, title: str, notes: str = "", due: str | None = None, tasklist: str = "@default") -> dict[str, Any]:
        body: dict[str, Any] = {"title": title, "notes": notes}
        if due:
            body["due"] = due if "T" in due else f"{due}T00:00:00.000Z"
        t = self._svc("tasks", "v1").tasks().insert(tasklist=tasklist, body=body).execute()
        out = {"id": t["id"], "title": t.get("title"), "due": t.get("due")}
        return verify.attach(out, self._verify_task(tasklist, t["id"], _task_row(t), ("title", "due", "status")))

    @invalidates("tasks")
    def tasks_complete(self, task_id: str, tasklist: str = "@default") -> dict[str, Any]:
        t = self._svc("tasks", "v1").tasks().patch(tasklist=tasklist, task=task_id, body={"status": "completed"}).execute()
        return verify.attach({"id": t["id"], "status": t.get("status")},
                             self._verify_task(tasklist, task_id, {"status": "completed"}, ("status",)))

    def tasks_all(self, tasklist: str = "@default") -> list[dict[str, Any]]:
        """Every task in a list, completed and hidden included, with `updated` timestamps (for sync).

        Deliberately uncached: this is the two-way sync's view of remote state, and it
        resolves conflicts by comparing `updated` timestamps. A stale read here could
        push over a newer remote edit, which no latency win is worth.
        """
        svc = self._svc("tasks", "v1").tasks()
        out: list[dict[str, Any]] = []
        token = None
        while True:
            res = svc.list(tasklist=tasklist, showCompleted=True, showHidden=True, maxResults=100, pageToken=token).execute()
            out += [_task_row(t) for t in res.get("items", [])]
            token = res.get("nextPageToken")
            if not token:
                return out

    def tasks_get(self, task_id: str, tasklist: str = "@default") -> dict[str, Any]:
        return _task_row(self._svc("tasks", "v1").tasks().get(tasklist=tasklist, task=task_id).execute())

    @invalidates("tasks")
    def tasks_insert(self, body: dict[str, Any], tasklist: str = "@default") -> dict[str, Any]:
        t = self._svc("tasks", "v1").tasks().insert(tasklist=tasklist, body=_task_body(body)).execute()
        row = _task_row(t)
        return verify.attach(row, self._verify_task(tasklist, t["id"], row, ("title", "notes", "due", "status")))

    @invalidates("tasks")
    def tasks_update(self, task_id: str, patch: dict[str, Any], tasklist: str = "@default") -> dict[str, Any]:
        t = self._svc("tasks", "v1").tasks().patch(tasklist=tasklist, task=task_id, body=_task_body(patch)).execute()
        row = _task_row(t)
        fields = tuple(k for k in ("title", "notes", "due", "status") if k in patch) or ("title",)
        return verify.attach(row, self._verify_task(tasklist, task_id, row, fields))

    @invalidates("tasks")
    def tasks_delete(self, task_id: str, tasklist: str = "@default") -> dict[str, Any]:
        self._svc("tasks", "v1").tasks().delete(tasklist=tasklist, task=task_id).execute()
        return verify.attach({"deleted": task_id, "tasklist": tasklist},
                             verify.check(f"task {task_id} in {tasklist}", self._task_reader(tasklist, task_id),
                                          absent=True, gone_if=lambda t: bool(t.get("deleted")), compared=["absent"]))

    def _task_reader(self, tasklist: str, task_id: str) -> Callable[[], dict[str, Any]]:
        def read_back() -> dict[str, Any]:
            try:
                return self.tasks_get(task_id, tasklist)
            except Exception as e:  # noqa: BLE001
                if verify.is_missing(e):
                    raise verify.NotVisible(f"task {task_id} is not in {tasklist}") from e
                raise
        return read_back

    def _verify_task(self, tasklist: str, task_id: str, wrote: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
        want = {k: wrote.get(k) for k in fields}

        def compare(t: dict[str, Any]) -> dict[str, Any]:
            return verify.diff(want, t, time_fields=("due",))

        return verify.check(f"task {task_id} in {tasklist}", self._task_reader(tasklist, task_id),
                            compare=compare, compared=sorted(want))

    # ---------- Drive ----------
    @cached("drive", TTL["drive_list"])
    def drive_files(self, query: str = "", max_results: int = 20) -> list[dict[str, Any]]:
        """Search Drive by name/content; with no query, list recently modified files."""
        params: dict[str, Any] = {
            "q": _drive_query(query),
            "pageSize": max(1, min(int(max_results), 50)),
            "fields": "files(id,name,mimeType,modifiedTime,webViewLink,size,owners(displayName,me))",
        }
        if not (query or "").strip():
            # Drive rejects orderBy on fullText queries; relevance order is fine there.
            params["orderBy"] = "modifiedTime desc"
        res = self._svc("drive", "v3").files().list(**params).execute()
        out = []
        for f in res.get("files", []):
            owners = f.get("owners") or []
            out.append({
                "id": f.get("id"), "name": f.get("name"), "mime_type": f.get("mimeType"),
                "modified": f.get("modifiedTime"), "link": f.get("webViewLink"),
                "size": int(f["size"]) if f.get("size") else None,
                "owner": "me" if any(o.get("me") for o in owners) else (owners[0].get("displayName") if owners else None),
            })
        return out

    @cached("drive", TTL["drive_read"])
    def drive_read(self, file_id: str, max_chars: int = 8000) -> dict[str, Any]:
        svc = self._svc("drive", "v3")
        meta = svc.files().get(fileId=file_id, fields="id,name,mimeType,webViewLink,size").execute()
        mime = meta.get("mimeType", "")
        export = _DRIVE_EXPORTS.get(mime)
        if export:
            data = svc.files().export(fileId=file_id, mimeType=export).execute()
        elif mime.startswith("text/") or mime in ("application/json", "application/xml", "application/rtf"):
            data = svc.files().get_media(fileId=file_id).execute()
        else:
            return {"id": file_id, "name": meta.get("name"), "mime_type": mime, "link": meta.get("webViewLink"),
                    "content": None, "note": "Binary file; no text to extract. Open it in Drive via the link."}
        text = data.decode("utf-8", "replace") if isinstance(data, (bytes, bytearray)) else str(data)
        return {"id": file_id, "name": meta.get("name"), "mime_type": mime, "link": meta.get("webViewLink"),
                "content": text[:max_chars], "truncated": len(text) > max_chars}

    # ---------- Docs / Sheets ----------
    _MIME = {"doc": "application/vnd.google-apps.document", "sheet": "application/vnd.google-apps.spreadsheet"}

    @cached("drive", TTL["drive_list"])
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

    @cached("docs", TTL["docs_get"])
    def docs_get(self, document_id: str, max_chars: int = 20000) -> dict[str, Any]:
        doc = self._svc("docs", "v1").documents().get(documentId=document_id).execute()
        text = _doc_text(doc)
        return {"id": document_id, "title": doc.get("title"), "text": text[:max_chars],
                "truncated": len(text) > max_chars,
                "link": f"https://docs.google.com/document/d/{document_id}/edit"}

    @invalidates("docs", "drive")
    def docs_create(self, title: str, content: str = "") -> dict[str, Any]:
        svc = self._svc("docs", "v1")
        doc = svc.documents().create(body={"title": title}).execute()
        did = doc["documentId"]
        if content:
            svc.documents().batchUpdate(documentId=did, body={"requests": [
                {"insertText": {"location": {"index": 1}, "text": content}}]}).execute()
        out = {"id": did, "title": title, "link": f"https://docs.google.com/document/d/{did}/edit"}
        return verify.attach(out, self._verify_doc(did, title, content))

    @invalidates("docs", "drive")
    def docs_append(self, document_id: str, content: str) -> dict[str, Any]:
        svc = self._svc("docs", "v1")
        doc = svc.documents().get(documentId=document_id, fields="title,body(content(endIndex))").execute()
        # The document body always ends with a newline the API will not let us write past,
        # hence endIndex - 1.
        end = (doc.get("body", {}).get("content") or [{}])[-1].get("endIndex", 2)
        svc.documents().batchUpdate(documentId=document_id, body={"requests": [
            {"insertText": {"location": {"index": max(1, end - 1)}, "text": "\n" + content}}]}).execute()
        out = {"id": document_id, "title": doc.get("title"), "appended_chars": len(content)}
        return verify.attach(out, self._verify_doc(document_id, None, content))

    def _verify_doc(self, document_id: str, title: str | None, content: str) -> dict[str, Any]:
        """Re-read the doc: the title when we set one, and that the text we wrote is in the body.

        Content is matched on whitespace-collapsed text (Docs splits a paragraph into runs), and
        only on its tail, which is enough to tell "the write landed" from "the write vanished".
        """
        needle = _collapse(content)[-200:]
        want = {k: v for k, v in (("title", title), ("content", needle or None)) if v}

        def read_back() -> dict[str, Any]:
            try:
                return self._svc("docs", "v1").documents().get(documentId=document_id).execute()
            except Exception as e:  # noqa: BLE001
                if verify.is_missing(e):
                    raise verify.NotVisible(f"document {document_id} is not in Drive") from e
                raise

        def compare(doc: dict[str, Any]) -> dict[str, Any]:
            out: dict[str, Any] = {}
            if title and (doc.get("title") or "").strip() != title.strip():
                out["title"] = {"expected": title, "actual": doc.get("title")}
            if needle and needle not in _collapse(_doc_text(doc)):
                out["content"] = {"expected": f"…{needle[-60:]}", "actual": "not found in the document"}
            return out

        return verify.check(f"document {document_id}", read_back, compare=compare, compared=sorted(want) or ["exists"])

    @cached("sheets", TTL["sheets_read"])
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

    @invalidates("sheets", "drive")
    def sheets_write(self, spreadsheet_id: str, cell_range: str, values: list[list[Any]], append: bool = False) -> dict[str, Any]:
        vals = self._svc("sheets", "v4").spreadsheets().values()
        if append:
            r = vals.append(spreadsheetId=spreadsheet_id, range=cell_range, valueInputOption="USER_ENTERED",
                            insertDataOption="INSERT_ROWS", body={"values": values}).execute()
            u = r.get("updates", {})
            out = {"id": spreadsheet_id, "range": u.get("updatedRange"), "cells": u.get("updatedCells")}
        else:
            r = vals.update(spreadsheetId=spreadsheet_id, range=cell_range, valueInputOption="USER_ENTERED",
                            body={"values": values}).execute()
            out = {"id": spreadsheet_id, "range": r.get("updatedRange"), "cells": r.get("updatedCells")}
        return verify.attach(out, self._verify_cells(spreadsheet_id, out["range"] or cell_range, values))

    def _verify_cells(self, spreadsheet_id: str, written_range: str, values: list[list[Any]]) -> dict[str, Any]:
        """Re-read the range Sheets says it wrote and compare its shape.

        Shape, not text: USER_ENTERED coerces input (a date becomes a serial, "1,400" a number), so
        comparing the strings we sent against what comes back would cry wolf. Row count and filled-cell
        count prove the write landed where it was supposed to, which is the claim being checked.
        """
        want = {"rows": len(values), "filled_cells": sum(1 for row in values for v in row if str(v) != "")}

        def read_back() -> dict[str, Any]:
            try:
                res = self._svc("sheets", "v4").spreadsheets().values().get(
                    spreadsheetId=spreadsheet_id, range=written_range).execute()
            except Exception as e:  # noqa: BLE001
                if verify.is_missing(e):
                    raise verify.NotVisible(f"range {written_range} is not readable") from e
                raise
            if not res.get("values"):
                raise verify.NotVisible(f"range {written_range} came back empty")
            return res

        def compare(res: dict[str, Any]) -> dict[str, Any]:
            got = res.get("values") or []
            return verify.diff(want, {"rows": len(got),
                                      "filled_cells": sum(1 for row in got for v in row if str(v) != "")})

        return verify.check(f"range {written_range} in spreadsheet {spreadsheet_id}", read_back,
                            compare=compare, compared=["rows", "filled_cells"])

    @invalidates("sheets", "drive")
    def sheets_create(self, title: str, values: list[list[Any]] | None = None) -> dict[str, Any]:
        svc = self._svc("sheets", "v4").spreadsheets()
        ss = svc.create(body={"properties": {"title": title}}, fields="spreadsheetId,spreadsheetUrl").execute()
        sid = ss["spreadsheetId"]
        if values:
            svc.values().update(spreadsheetId=sid, range="A1", valueInputOption="USER_ENTERED",
                                body={"values": values}).execute()
        out = {"id": sid, "title": title, "link": ss.get("spreadsheetUrl")}

        def read_back() -> dict[str, Any]:
            try:
                return self._svc("sheets", "v4").spreadsheets().get(
                    spreadsheetId=sid, fields="properties(title)").execute()
            except Exception as e:  # noqa: BLE001
                if verify.is_missing(e):
                    raise verify.NotVisible(f"spreadsheet {sid} is not in Drive") from e
                raise

        v = verify.check(f"spreadsheet {sid}", read_back, compared=["title"],
                         compare=lambda ss_: verify.diff({"title": title}, {"title": (ss_.get("properties") or {}).get("title")}))
        return verify.attach(out, v)


# Google-native formats can't be downloaded raw; export to the closest text form.
_DRIVE_EXPORTS = {
    "application/vnd.google-apps.document": "text/plain",
    "application/vnd.google-apps.spreadsheet": "text/csv",
    "application/vnd.google-apps.presentation": "text/plain",
}


def _drive_query(query: str) -> str:
    """Build a Drive v3 `q` expression; user text is embedded in single quotes, so escape it."""
    base = "trashed = false"
    q = (query or "").strip()
    if not q:
        return base
    esc = q.replace("\\", "\\\\").replace("'", "\\'")
    return f"{base} and (name contains '{esc}' or fullText contains '{esc}')"


def _task_row(t: dict[str, Any]) -> dict[str, Any]:
    return {"id": t["id"], "title": t.get("title", ""), "notes": t.get("notes", ""), "due": t.get("due"),
            "status": t.get("status"), "updated": t.get("updated"), "deleted": bool(t.get("deleted"))}


def _task_body(fields: dict[str, Any]) -> dict[str, Any]:
    body = dict(fields)
    due = body.get("due")
    if isinstance(due, str) and due and "T" not in due:
        body["due"] = f"{due}T00:00:00.000Z"
    if body.get("status") == "needsAction":
        # Reopening a task: the API keeps `completed` unless it is explicitly nulled.
        body["completed"] = None
    return body


def _send_updates(v: str | None) -> str:
    """Whether Google emails guests about the change; anything unrecognized means don't."""
    return v if v in ("all", "externalOnly") else "none"


# Calendar API body key -> the flat key _event_out puts it under. Only the keys a call actually
# wrote are compared on the read-back, so a patch is judged on the patch, not on the whole event.
# Both sides of the comparison come out of _event_out, so the guest list Google adds the organizer
# to is compared against the same server's view of it, not against what we asked for.
_EVENT_VERIFY_FIELDS = {"summary": "summary", "location": "location", "description": "description",
                        "start": "start", "end": "end", "attendees": "attendees",
                        "recurrence": "recurrence"}


def _collapse(text: str) -> str:
    """Whitespace-insensitive form, for matching text we wrote against text a service stored."""
    return re.sub(r"\s+", " ", text or "").strip()


def _addresses(to: str) -> list[str]:
    """The bare email addresses in a To header, lowercased."""
    return [a.lower() for a in re.findall(r"[\w.!#$%&'*+/=?^`{|}~-]+@[\w-]+(?:\.[\w-]+)+", to or "")]


def _parse_opt(value: Any) -> dt.datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        t = _parse_iso(value)
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    return t


def _overlaps(ev: dict[str, Any], start: dt.datetime, end: dt.datetime) -> bool:
    """True when an event's span meets [start, end). Unparseable times are kept."""
    sraw, eraw = ev.get("start") or "", ev.get("end") or ""
    if not isinstance(sraw, str) or not sraw:
        return True
    try:
        if len(sraw) == 10:
            es = dt.datetime.fromisoformat(sraw).replace(tzinfo=dt.timezone.utc)
            ee = dt.datetime.fromisoformat(eraw).replace(tzinfo=dt.timezone.utc) if isinstance(eraw, str) and len(eraw) == 10 else es + dt.timedelta(days=1)
        else:
            es = _parse_iso(sraw)
            ee = _parse_iso(eraw) if isinstance(eraw, str) and eraw else es
            if es.tzinfo is None:
                es = es.replace(tzinfo=dt.timezone.utc)
            if ee.tzinfo is None:
                ee = ee.replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return True
    return es < end and ee > start


def _end_keeping_length(svc: Any, calendar_id: str, event_id: str, start: str) -> str | None:
    """The end that keeps an event as long as it is now, given a new start. None to use the default."""
    try:
        cur = svc.get(calendarId=calendar_id, eventId=event_id).execute()
        was_start, was_end = cur.get("start") or {}, cur.get("end") or {}
        if len(start) == 10:
            if not (was_start.get("date") and was_end.get("date")):
                return None  # timed -> all-day: there is no length to carry over
            days = dt.date.fromisoformat(was_end["date"]) - dt.date.fromisoformat(was_start["date"])
            return (dt.date.fromisoformat(start) + days).isoformat()
        if not (was_start.get("dateTime") and was_end.get("dateTime")):
            return None
        length = _parse_iso(was_end["dateTime"]) - _parse_iso(was_start["dateTime"])
        if length <= dt.timedelta(0):
            return None
        return (_parse_iso(start) + length).isoformat()
    except Exception:  # noqa: BLE001 - the default length is a fair fallback
        return None


def _refresh_rejected(e: Exception) -> bool:
    """True when Google refused the refresh token itself, rather than the call failing in transit."""
    from google.auth.exceptions import RefreshError

    return isinstance(e, RefreshError) and not getattr(e, "retryable", False)


def _list_events(svc: Any, cid: str, time_min: dt.datetime, time_max: dt.datetime, updated_min: dt.datetime | None, max_results: int, paginate: bool) -> list[dict[str, Any]]:
    kwargs: dict[str, Any] = {
        "calendarId": cid, "timeMin": time_min.isoformat(), "timeMax": time_max.isoformat(),
        "singleEvents": True, "maxResults": max_results,
    }
    if updated_min is None:
        kwargs["orderBy"] = "startTime"
    else:
        # orderBy is rejected together with updatedMin. showDeleted is what brings
        # removals back, so a cancelled event can be dropped from the snapshot.
        kwargs["updatedMin"] = updated_min.isoformat()
        kwargs["showDeleted"] = True
    items: list[dict[str, Any]] = []
    token: str | None = None
    for _ in range(8 if paginate else 1):
        if token:
            kwargs["pageToken"] = token
        res = svc.events().list(**kwargs).execute()
        items.extend(res.get("items") or [])
        token = res.get("nextPageToken")
        if not token or not paginate:
            break
    return items


def _merge_events(stored: dict[str, dict[str, Any]], items: list[dict[str, Any]], cid: str) -> None:
    for e in items:
        eid = e.get("id")
        if not eid:
            continue
        if e.get("status") == "cancelled":
            stored.pop(eid, None)
        else:
            stored[eid] = _event_out(e, cid)


def _gmail_history(svc: Any, start_history_id: str) -> tuple[set[str], set[str], str | None]:
    """Ids added or relabelled, ids deleted, and the latest history id Gmail reported."""
    changed: set[str] = set()
    deleted: set[str] = set()
    latest: str | None = None
    token: str | None = None
    for _ in range(5):
        kwargs: dict[str, Any] = {
            "userId": "me", "startHistoryId": start_history_id,
            "historyTypes": ["messageAdded", "messageDeleted", "labelAdded", "labelRemoved"],
        }
        if token:
            kwargs["pageToken"] = token
        res = svc.users().history().list(**kwargs).execute()
        if isinstance(res.get("historyId"), str):
            latest = res["historyId"]
        for record in res.get("history") or []:
            if isinstance(record.get("id"), str):
                latest = record["id"]
            for item in record.get("messagesDeleted") or []:
                mid = (item.get("message") or {}).get("id")
                if mid:
                    deleted.add(mid)
            for key in ("messagesAdded", "labelsAdded", "labelsRemoved"):
                for item in record.get(key) or []:
                    mid = (item.get("message") or {}).get("id")
                    if mid:
                        changed.add(mid)
        token = res.get("nextPageToken")
        if not token:
            break
    changed -= deleted
    return changed, deleted, latest


def _event_out(e: dict[str, Any], calendar_id: str | None = None, full: bool = False) -> dict[str, Any]:
    """Flatten a Calendar API event. `full` adds the editor-grade fields and the whole description."""
    st, en = e.get("start", {}), e.get("end", {})
    desc = e.get("description") or ""
    out = {
        "id": e.get("id"), "calendar_id": calendar_id,
        "summary": e.get("summary", "(no title)"),
        "start": st.get("dateTime") or st.get("date"), "end": en.get("dateTime") or en.get("date"),
        "all_day": "date" in st,
        "location": e.get("location"), "link": e.get("htmlLink"),
        "attendees": [a.get("email") for a in e.get("attendees", [])][:10],
        "description": desc if full else desc[:400],
        "meet": e.get("hangoutLink") or "",
        "color_id": e.get("colorId"),
        "recurring_event_id": e.get("recurringEventId"),
        "transparency": e.get("transparency") or "opaque",
        "status": e.get("status"),
        # The user's own RSVP (accepted/declined/tentative/needsAction); None when not an invitee.
        "self_response": next((x.get("responseStatus") for x in e.get("attendees", []) if x.get("self")), None),
        # Same value under the name the calendar cards read.
        "my_response": next((a.get("responseStatus") for a in e.get("attendees", []) if a.get("self")), None),
    }
    if full:
        out.update({
            "time_zone": st.get("timeZone"),
            "recurrence": e.get("recurrence"),
            "visibility": e.get("visibility") or "default",
            "reminders": e.get("reminders"),
            "organizer": (e.get("organizer") or {}).get("email"),
            "attendee_details": [
                {"email": a.get("email"), "optional": bool(a.get("optional")), "response": a.get("responseStatus"),
                 "organizer": bool(a.get("organizer")), "self": bool(a.get("self"))}
                for a in e.get("attendees", [])
            ][:60],
            "guests_can_invite_others": e.get("guestsCanInviteOthers", True),
            "guests_can_modify": e.get("guestsCanModify", False),
            "guests_can_see_other_guests": e.get("guestsCanSeeOtherGuests", True),
        })
    return out


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


def _parse_iso(value: str) -> dt.datetime:
    """Parse RFC3339 / JS Date.toISOString() values.

    Python 3.10's fromisoformat rejects a trailing Z (the form the calendar UI
    sends as `start`), which then surfaced as "Google API error: Invalid isoformat string".
    """
    s = value.strip()
    if s.endswith(("Z", "z")):
        s = s[:-1] + "+00:00"
    return dt.datetime.fromisoformat(s)


def _rfc2822_iso(value: str | None) -> str | None:
    """Gmail Date headers are RFC 2822; the UI's Date() parser is happier with ISO."""
    if not value:
        return None
    try:
        d = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError, IndexError):
        return value
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return d.isoformat()


def _gmail_meta(msg: dict[str, Any]) -> dict[str, Any]:
    h = {x["name"].lower(): x["value"] for x in msg.get("payload", {}).get("headers", [])}
    return {
        "id": msg.get("id"), "thread_id": msg.get("threadId"),
        "from": h.get("from"), "subject": h.get("subject"), "date": _rfc2822_iso(h.get("date")),
        "snippet": msg.get("snippet"), "unread": "UNREAD" in (msg.get("labelIds") or []),
        "labels": msg.get("labelIds") or [],
    }


_THREAD_HEADERS = ["From", "To", "Cc", "Subject", "Date", "List-Unsubscribe", "Auto-Submitted", "Precedence"]


def _gmail_thread_meta(thread: dict[str, Any]) -> dict[str, Any]:
    msgs: list[dict[str, Any]] = []
    for m in thread.get("messages") or []:
        h = {x["name"].lower(): x["value"] for x in m.get("payload", {}).get("headers", [])}
        auto = (bool(h.get("list-unsubscribe")) or (h.get("precedence") or "").strip().lower() in ("bulk", "list", "junk")
                or (h.get("auto-submitted") or "no").strip().lower() != "no")
        date = _rfc2822_iso(h.get("date"))
        if not date and m.get("internalDate"):
            date = dt.datetime.fromtimestamp(int(m["internalDate"]) / 1000, dt.timezone.utc).isoformat()
        msgs.append({"id": m.get("id"), "from": h.get("from"), "to": h.get("to"), "cc": h.get("cc"), "date": date,
                     "labels": m.get("labelIds") or [], "snippet": m.get("snippet") or "", "auto": auto, "_subject": h.get("subject")})
    msgs.sort(key=lambda m: m["date"] or "")
    subject = next((m["_subject"] for m in msgs if m["_subject"]), "") or ""
    for m in msgs:
        m.pop("_subject", None)
    return {"thread_id": thread.get("id"), "subject": subject, "messages": msgs}


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


def _address_header(value: str) -> str:
    """A recipient list with only the display names encoded.

    Assigning "Zoë <a@b.com>" to a header encodes the whole value, address included, as one
    opaque word, and no mail server can read a recipient out of that.
    """
    if value.isascii():
        return value
    return ", ".join(formataddr((name, addr)) for name, addr in getaddresses([value]) if addr)


def _raw_message(to: str, subject: str, body: str, in_reply_to: str | None = None, references: str | None = None) -> str:
    msg = email.mime.text.MIMEText(body)
    msg["to"], msg["subject"] = _address_header(to), subject
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    if references:
        msg["References"] = references
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
