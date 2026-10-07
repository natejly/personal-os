"""E2E backend launcher: the real backend with Google's transport replaced by an in-process stand-in.

Run as `python tests/e2e/backend_fake_google.py --port N --data-dir D` (PYTHONPATH=backend). It marks Google as
connected, seeds a week of calendar events (two calendars), 40 mail threads and 10 tasks, adds /__fake/* debug
routes (this process only, never product code) and then hands over to personal_os.__main__.main().

Debug routes (bearer auth like everything else):
  GET  /__fake/state              -> {events, messages, drafts, tasks, sent, writes}
  POST /__fake/events {n,days}    -> bulk-add n timed events inside this week
  POST /__fake/fail {api, n}      -> the next n calls to that api ("calendar"|"gmail"|"tasks") raise a 500
"""
import base64
import copy
import datetime as dt
import email
import email.utils
import os
import re
import sys
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "backend"))

from fastapi import Request  # noqa: E402
from personal_os import google as gmod  # noqa: E402

NOW = dt.datetime.now().astimezone().replace(microsecond=0)
MONDAY = (NOW - dt.timedelta(days=NOW.weekday())).replace(hour=0, minute=0, second=0)
ME = "me@example.com"


def zulu() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


class ApiError(Exception):
    def __init__(self, status: int, message: str = ""):
        super().__init__(f"<HttpError {status} when requesting: {message}>")
        self.resp = type("R", (), {"status": status})()
        self.status_code = status


class Call:
    def __init__(self, fn):
        self.fn = fn

    def execute(self, *a, **k):
        return self.fn()


def iso(d: dt.datetime) -> str:
    return d.isoformat()


def parse(s: str) -> dt.datetime:
    d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    return d if d.tzinfo else d.astimezone()


def ev_range(e: dict) -> tuple[dt.datetime, dt.datetime]:
    s, en = e["start"], e["end"]
    if "date" in s:
        return (dt.datetime.fromisoformat(s["date"]).astimezone(), dt.datetime.fromisoformat(en["date"]).astimezone())
    return parse(s["dateTime"]), parse(en["dateTime"])


class Store:
    def __init__(self) -> None:
        self.n = 0
        self.writes: list[dict] = []
        self.fail: dict[str, int] = {}
        self.calendars = [
            {"id": "primary", "summary": ME, "primary": True, "accessRole": "owner", "backgroundColor": "#9fe1e7", "selected": True, "timeZone": "UTC"},
            {"id": "team@group.calendar.example", "summary": "Team", "accessRole": "owner", "backgroundColor": "#f6bf26", "selected": True},
        ]
        self.events: dict[str, dict[str, dict]] = {c["id"]: {} for c in self.calendars}
        self.messages: dict[str, dict] = {}
        self.drafts: dict[str, dict] = {}
        self.tasks: dict[str, dict] = {}
        self.sent: list[dict] = []

    def nid(self, p: str) -> str:
        self.n += 1
        return f"{p}{self.n}"

    def gate(self, api: str) -> None:
        if self.fail.get(api):
            self.fail[api] -= 1
            raise ApiError(500, "injected")

    def write(self, kind: str, **kw) -> None:
        self.writes.append({"kind": kind, **kw})

    # ---- seeding ----
    def seed(self) -> None:
        def add(cal, summary, day, hour, mins=60, **kw):
            eid = self.nid("ev")
            s = MONDAY + dt.timedelta(days=day, hours=hour)
            self.events[cal][eid] = {
                "id": eid, "status": "confirmed", "summary": summary, "htmlLink": f"https://cal.test/{eid}", "updated": iso(NOW),
                "start": {"dateTime": iso(s)}, "end": {"dateTime": iso(s + dt.timedelta(minutes=mins))}, **kw}

        for d in range(7):
            add("primary", f"Standup D{d}", d, 9, 30)
            add("primary", f"Lunch D{d}", d, 12, 60, location="Cafe")
        for d in range(5):  # overlapping pair at 14:00
            add("primary", f"Overlap A{d}", d, 14, 90)
            add("primary", f"Overlap B{d}", d, 14.5, 60, colorId="5")
        add("primary", "Review", 2, 16, 60, description="Quarterly review notes", attendees=[
            {"email": ME, "self": True, "responseStatus": "accepted"}, {"email": "bob@example.com", "responseStatus": "needsAction"}],
            reminders={"useDefault": False, "overrides": [{"method": "popup", "minutes": 30}]}, recurrence=["RRULE:FREQ=WEEKLY"], colorId="9")
        for d in (1, 3):
            eid = self.nid("ev")
            day = (MONDAY + dt.timedelta(days=d)).date()
            self.events["primary"][eid] = {"id": eid, "status": "confirmed", "summary": f"Holiday D{d}", "updated": iso(NOW),
                                            "start": {"date": day.isoformat()}, "end": {"date": (day + dt.timedelta(days=1)).isoformat()}}
        for d in range(4):
            add("team@group.calendar.example", f"Team sync D{d}", d, 10, 45)
        names = ["Alice <alice@example.com>", "Bob <bob@example.com>", "Carol <carol@example.com>", "News <news@example.com>"]
        for i in range(40):
            tid, mid = f"t{i}", f"m{i}"
            labels = ["INBOX"] + (["UNREAD"] if i % 3 == 0 else []) + (["STARRED"] if i % 7 == 0 else [])
            when = NOW - dt.timedelta(hours=i * 5 + 1)
            self.messages[mid] = {
                "id": mid, "threadId": tid, "labelIds": labels,
                "snippet": f"Can you send the file for thread {i}?" if i % 4 == 1 else f"Snippet for thread {i}",
                "internalDate": str(int(when.timestamp() * 1000)),
                "headers": [("From", names[i % 4]), ("To", ME), ("Subject", f"Subject {i}" + (" with attachment" if i % 5 == 0 else "")),
                            ("Date", email.utils.format_datetime(when)), ("Message-ID", f"<{mid}@example.com>")],
                "text": f"Hello,\n\nThis is the body of message {i}.\n\nBye",
                "attachments": [{"filename": f"report{i}.pdf", "mimeType": "application/pdf", "size": 2048}] if i % 5 == 0 else [],
            }
        # a thread where I wrote last and asked something, five days ago: "awaiting reply"
        when = NOW - dt.timedelta(days=5)
        self.messages["mine1"] = {
            "id": "mine1", "threadId": "tmine", "labelIds": ["SENT"], "snippet": "Could you confirm the date?",
            "internalDate": str(int(when.timestamp() * 1000)),
            "headers": [("From", ME), ("To", "Dana <dana@example.com>"), ("Subject", "Dinner date"), ("Date", email.utils.format_datetime(when)),
                        ("Message-ID", "<mine1@example.com>")],
            "text": "Could you confirm the date?", "attachments": []}
        for i in range(10):
            tid = self.nid("task")
            self.tasks[tid] = {"id": tid, "title": f"Task {i}", "notes": "", "status": "completed" if i == 9 else "needsAction", "updated": zulu(),
                               **({"due": (NOW + dt.timedelta(days=i)).strftime("%Y-%m-%dT00:00:00.000Z")} if i % 2 == 0 else {})}

    def find(self, eid: str) -> tuple[str, dict]:
        for c, evs in self.events.items():
            if eid in evs:
                return c, evs[eid]
        raise ApiError(404, "Not Found")


# ---------------- calendar ----------------
class Events:
    def __init__(self, s: Store):
        self.s = s

    def list(self, calendarId, timeMin=None, timeMax=None, updatedMin=None, showDeleted=False, maxResults=250, pageToken=None, **_):
        def go():
            s = self.s
            s.gate("calendar")
            if calendarId not in s.events:
                raise ApiError(404, "calendar")
            out = []
            for e in s.events[calendarId].values():
                if e.get("status") == "cancelled" and not (showDeleted and updatedMin):
                    continue
                st, en = ev_range(e)
                if (timeMin and en <= parse(timeMin)) or (timeMax and st >= parse(timeMax)):
                    continue
                if updatedMin and parse(e["updated"]) < parse(updatedMin):
                    continue
                out.append(copy.deepcopy(e))
            out.sort(key=lambda e: ev_range(e)[0])
            start = int(pageToken or 0)
            res: dict = {"items": out[start:start + maxResults]}
            if start + maxResults < len(out):
                res["nextPageToken"] = str(start + maxResults)
            return res
        return Call(go)

    def get(self, calendarId, eventId, **_):
        def go():
            self.s.gate("calendar")
            c, e = self.s.find(eventId)
            if c != calendarId:
                raise ApiError(404, "Not Found")
            return copy.deepcopy(e)
        return Call(go)

    def insert(self, calendarId, body, **kw):
        def go():
            self.s.gate("calendar")
            b = copy.deepcopy(body)
            eid = b.pop("id", None) or self.s.nid("ev")
            e = {"status": "confirmed", "htmlLink": f"https://cal.test/{eid}", "updated": iso(dt.datetime.now().astimezone()), "id": eid, **b}
            if kw.get("conferenceDataVersion") and b.get("conferenceData"):
                e["hangoutLink"] = "https://meet.example/abc-defg-hij"
                e.pop("conferenceData", None)
            if e.get("attendees"):
                e["attendees"] = [{"responseStatus": "needsAction", **a} for a in e["attendees"]] + [
                    {"email": ME, "self": True, "organizer": True, "responseStatus": "accepted"}]
            self.s.events[calendarId][eid] = e
            self.s.write("event.insert", calendar=calendarId, id=eid, body=body)
            return copy.deepcopy(e)
        return Call(go)

    def patch(self, calendarId, eventId, body, **kw):
        def go():
            self.s.gate("calendar")
            c, e = self.s.find(eventId)
            for k, v in body.items():
                if isinstance(v, dict) and isinstance(e.get(k), dict):
                    e[k] = {kk: vv for kk, vv in {**e[k], **v}.items() if vv is not None}
                elif v is None:
                    e.pop(k, None)
                else:
                    e[k] = v
            if kw.get("conferenceDataVersion") and body.get("conferenceData"):
                e["hangoutLink"] = "https://meet.example/abc-defg-hij"
                e.pop("conferenceData", None)
            e["updated"] = iso(dt.datetime.now().astimezone())
            self.s.write("event.patch", calendar=c, id=eventId, body=body)
            return copy.deepcopy(e)
        return Call(go)

    def delete(self, calendarId, eventId, **_):
        def go():
            self.s.gate("calendar")
            c, e = self.s.find(eventId)
            e["status"] = "cancelled"
            e["updated"] = iso(dt.datetime.now().astimezone())
            self.s.write("event.delete", calendar=c, id=eventId)
            return ""
        return Call(go)

    def move(self, calendarId, eventId, destination, **_):
        def go():
            c, e = self.s.find(eventId)
            del self.s.events[c][eventId]
            self.s.events[destination][eventId] = e
            self.s.write("event.move", id=eventId, to=destination)
            return copy.deepcopy(e)
        return Call(go)


class CalList:
    def __init__(self, s):
        self.s = s

    def list(self, **_):
        return Call(lambda: {"items": copy.deepcopy(self.s.calendars)})

    def patch(self, calendarId, body):
        def go():
            for c in self.s.calendars:
                if c["id"] == calendarId:
                    c.update(body)
            return {}
        return Call(go)


class Calendars:
    def __init__(self, s):
        self.s = s

    def insert(self, body):
        def go():
            cid = self.s.nid("cal") + "@group.calendar.example"
            self.s.calendars.append({"id": cid, "summary": body["summary"], "accessRole": "owner", "backgroundColor": "#aaaaaa", "selected": False})
            self.s.events[cid] = {}
            return {"id": cid, "summary": body["summary"]}
        return Call(go)


class Colors:
    def get(self):
        ev = {str(i): {"background": f"#{(i * 2654435) % 0xFFFFFF:06x}"} for i in range(1, 12)}
        return Call(lambda: {"event": ev, "calendar": ev})


class FreeBusy:
    def query(self, body):
        return Call(lambda: {"calendars": {i["id"]: {"busy": []} for i in body.get("items", [])}})


class Calendar:
    def __init__(self, s):
        self.s = s

    def events(self): return Events(self.s)
    def calendarList(self): return CalList(self.s)
    def calendars(self): return Calendars(self.s)
    def colors(self): return Colors()
    def freebusy(self): return FreeBusy()


# ---------------- gmail ----------------
def b64(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode()).decode()


def match(m: dict, q: str) -> bool:
    labels = set(m["labelIds"])
    hdr = {k.lower(): v for k, v in m["headers"]}
    for tok in re.findall(r'-?(?:[\w-]+:)?(?:"[^"]*"|\S+)', q or ""):
        neg = tok.startswith("-")
        t = tok.lstrip("-")
        if t == "is:unread": ok = "UNREAD" in labels
        elif t == "is:starred": ok = "STARRED" in labels
        elif t == "in:inbox": ok = "INBOX" in labels
        elif t == "in:sent": ok = "SENT" in labels
        elif t in ("in:spam", "in:trash"): ok = False
        elif t.startswith(("category:", "newer_than:")): continue
        elif t == "has:attachment": ok = bool(m["attachments"])
        elif t.startswith("label:"): ok = t[6:] in labels
        elif t.startswith("from:"): ok = t[5:].lower() in hdr.get("from", "").lower()
        elif t.startswith("to:"): ok = t[3:].lower() in hdr.get("to", "").lower()
        elif t.startswith("subject:"): ok = t[8:].strip('"').lower() in hdr.get("subject", "").lower()
        else:
            ok = t.strip('"').lower() in (hdr.get("subject", "") + hdr.get("from", "") + m["snippet"] + m["text"]).lower()
        if ok == neg:
            return False
    return True


def render(m: dict, fmt: str, meta_headers=None) -> dict:
    out = {"id": m["id"], "threadId": m["threadId"], "labelIds": list(m["labelIds"]), "snippet": m["snippet"], "internalDate": m["internalDate"]}
    if fmt == "minimal":
        return out
    hs = [{"name": k, "value": v} for k, v in m["headers"] if fmt == "full" or not meta_headers or k.lower() in [h.lower() for h in meta_headers]]
    if fmt == "full":
        parts = [{"mimeType": "text/plain", "body": {"data": b64(m["text"]), "size": len(m["text"])}}] + [
            {"mimeType": a["mimeType"], "filename": a["filename"], "body": {"attachmentId": "att-" + a["filename"], "size": a["size"]}} for a in m["attachments"]]
        out["payload"] = {"mimeType": "multipart/mixed", "headers": hs, "parts": parts}
    else:
        out["payload"] = {"headers": hs}
    return out


def parse_raw(raw: str) -> tuple[Any, str, list[str]]:
    """The message, its text/plain body and the file names of its attachments (the raw may be multipart)."""
    msg = email.message_from_bytes(base64.urlsafe_b64decode(raw))
    text, names = None, []
    for part in msg.walk():
        if part.is_multipart():
            continue
        if part.get_filename():
            names.append(part.get_filename())
        elif text is None and part.get_content_type() == "text/plain":
            text = part.get_payload(decode=True)
    return msg, text.decode("utf-8", "replace") if text else "", names


class Attachments:
    def __init__(self, s): self.s = s

    def get(self, userId, messageId, id, **_):
        def go():
            self.s.gate("gmail")
            if not any("att-" + a["filename"] == id for a in self.s.messages.get(messageId, {}).get("attachments", [])):
                raise ApiError(404, "Not Found")
            data = b"%PDF-1.4\n" + b"x" * 2000
            return {"data": base64.urlsafe_b64encode(data).decode(), "size": len(data)}
        return Call(go)


class Msgs:
    def __init__(self, s): self.s = s

    def attachments(self): return Attachments(self.s)

    def list(self, userId, q="", maxResults=100, pageToken=None, **_):
        def go():
            self.s.gate("gmail")
            ms = sorted((m for m in self.s.messages.values() if match(m, q)), key=lambda m: -int(m["internalDate"]))
            st = int(pageToken or 0)
            res: dict = {"messages": [{"id": m["id"], "threadId": m["threadId"]} for m in ms[st:st + maxResults]]}
            if st + maxResults < len(ms):
                res["nextPageToken"] = str(st + maxResults)
            return res
        return Call(go)

    def get(self, userId, id, format="full", metadataHeaders=None, **_):
        def go():
            self.s.gate("gmail")
            m = self.s.messages.get(id)
            if not m:
                raise ApiError(404, "Not Found")
            return render(m, format, metadataHeaders)
        return Call(go)

    def modify(self, userId, id, body):
        def go():
            self.s.gate("gmail")
            m = self.s.messages.get(id)
            if not m:
                raise ApiError(404, "Not Found")
            rem = body.get("removeLabelIds") or []
            m["labelIds"] = [x for x in m["labelIds"] if x not in rem] + [x for x in (body.get("addLabelIds") or []) if x not in m["labelIds"]]
            self.s.write("mail.modify", id=id, add=body.get("addLabelIds"), remove=rem)
            return render(m, "minimal")
        return Call(go)

    def send(self, userId, body):
        def go():
            self.s.gate("gmail")
            msg, text, names = parse_raw(body["raw"])
            mid = self.s.nid("sent")
            m = {"id": mid, "threadId": body.get("threadId") or mid, "labelIds": ["SENT"], "snippet": "",
                 "internalDate": str(int(dt.datetime.now().timestamp() * 1000)), "headers": list(msg.items()),
                 "text": text, "attachments": []}
            self.s.messages[mid] = m
            self.s.sent.append({"id": mid, "to": msg["To"], "subject": msg["Subject"], "body": m["text"], "threadId": m["threadId"]})
            self.s.write("mail.send", id=mid, to=msg["To"], subject=msg["Subject"], attachments=names)
            return {"id": mid, "threadId": m["threadId"]}
        return Call(go)


class Threads:
    def __init__(self, s): self.s = s

    def list(self, userId, q="", maxResults=100, **_):
        def go():
            seen: set[str] = set()
            out = []
            for m in sorted((m for m in self.s.messages.values() if match(m, q)), key=lambda m: -int(m["internalDate"])):
                if m["threadId"] not in seen:
                    seen.add(m["threadId"])
                    out.append({"id": m["threadId"]})
            return {"threads": out[:maxResults]}
        return Call(go)

    def get(self, userId, id, format="metadata", metadataHeaders=None, **_):
        def go():
            ms = [render(m, format, metadataHeaders) for m in self.s.messages.values() if m["threadId"] == id]
            if not ms:
                raise ApiError(404, "Not Found")
            return {"id": id, "messages": ms}
        return Call(go)


class Drafts:
    def __init__(self, s): self.s = s

    def create(self, userId, body):
        def go():
            did = self.s.nid("draft")
            self.s.drafts[did] = body["message"]
            msg, _text, names = parse_raw(body["message"]["raw"])
            self.s.write("mail.draft", id=did, to=msg["To"], subject=msg["Subject"], attachments=names)
            return {"id": did}
        return Call(go)

    def get(self, userId, id, **_):
        def go():
            d = self.s.drafts.get(id)
            if not d:
                raise ApiError(404, "Not Found")
            msg = email.message_from_bytes(base64.urlsafe_b64decode(d["raw"]))
            return {"id": id, "message": {"payload": {"headers": [{"name": k, "value": v} for k, v in msg.items()]}}}
        return Call(go)


class Labels:
    def list(self, **_):
        sysl = [{"id": i, "name": i, "type": "system"} for i in ("INBOX", "STARRED", "UNREAD", "SENT", "DRAFT")]
        return Call(lambda: {"labels": sysl + [{"id": "Label_1", "name": "Receipts", "type": "user"}, {"id": "Label_2", "name": "Travel", "type": "user"}]})


class History:
    def list(self, **_):
        return Call(lambda: {"history": []})


class Batch:
    def __init__(self, callback):
        self.cb, self.reqs = callback, []

    def add(self, req, request_id=None):
        self.reqs.append((request_id, req))

    def execute(self):
        for rid, r in self.reqs:
            try:
                self.cb(rid, r.execute(), None)
            except Exception as e:  # noqa: BLE001
                self.cb(rid, None, e)


class Gmail:
    def __init__(self, s): self.s = s
    def users(self): return self
    def messages(self): return Msgs(self.s)
    def threads(self): return Threads(self.s)
    def drafts(self): return Drafts(self.s)
    def labels(self): return Labels()
    def history(self): return History()
    def new_batch_http_request(self, callback=None): return Batch(callback)


# ---------------- tasks / drive ----------------
class TaskOps:
    def __init__(self, s): self.s = s

    def list(self, tasklist, showCompleted=False, showHidden=False, maxResults=100, pageToken=None, updatedMin=None, showDeleted=False, **_):
        def go():
            self.s.gate("tasks")
            ts = [t for t in self.s.tasks.values() if (showCompleted or t["status"] != "completed") and (showDeleted or not t.get("deleted"))
                  and (not updatedMin or parse(t["updated"]) >= parse(updatedMin))]
            st = int(pageToken or 0)
            res: dict = {"items": copy.deepcopy(ts[st:st + maxResults])}
            if st + maxResults < len(ts):
                res["nextPageToken"] = str(st + maxResults)
            return res
        return Call(go)

    def get(self, tasklist, task):
        def go():
            t = self.s.tasks.get(task)
            if not t or t.get("deleted"):
                raise ApiError(404, "Not Found")
            return copy.deepcopy(t)
        return Call(go)

    def insert(self, tasklist, body):
        def go():
            self.s.gate("tasks")
            tid = self.s.nid("task")
            t = {"id": tid, "status": "needsAction", "updated": zulu(), **body}
            self.s.tasks[tid] = t
            self.s.write("task.insert", id=tid, title=body.get("title"))
            return copy.deepcopy(t)
        return Call(go)

    def patch(self, tasklist, task, body):
        def go():
            t = self.s.tasks.get(task)
            if not t:
                raise ApiError(404, "Not Found")
            for k, v in body.items():
                if v is None:
                    t.pop(k, None)
                else:
                    t[k] = v
            t["updated"] = zulu()
            self.s.write("task.patch", id=task, body=body)
            return copy.deepcopy(t)
        return Call(go)

    def delete(self, tasklist, task):
        def go():
            t = self.s.tasks.get(task)
            if not t:
                raise ApiError(404, "Not Found")
            t["deleted"] = True
            t["updated"] = zulu()
            self.s.write("task.delete", id=task)
            return ""
        return Call(go)


class TaskLists:
    def list(self, **_):
        return Call(lambda: {"items": [{"id": "@default", "title": "My Tasks"}]})


class Tasks:
    def __init__(self, s): self.s = s
    def tasks(self): return TaskOps(self.s)
    def tasklists(self): return TaskLists()


class Files:
    def list(self, **kw):
        return Call(lambda: {"files": [
            {"id": f"f{i}", "name": f"Drive doc {i}", "mimeType": "application/vnd.google-apps.document", "modifiedTime": iso(NOW - dt.timedelta(hours=i)),
             "webViewLink": f"https://docs.test/f{i}", "owners": [{"displayName": "Me", "me": True}]} for i in range(10)]})


class Drive:
    def files(self): return Files()


def install(store: Store) -> None:
    def _svc(self, name, version):
        return Drive() if name == "drive" else {"calendar": Calendar, "gmail": Gmail, "tasks": Tasks}[name](store)

    gmod.Google._svc = _svc  # type: ignore[method-assign]
    gmod.Google._creds = lambda self: None  # type: ignore[method-assign]


def main() -> None:
    store = Store()
    store.seed()
    install(store)
    for part in filter(None, os.environ.get("GRAIN_FAKE_FAIL", "").split(",")):
        api, _, n = part.partition(":")
        store.fail[api] = int(n or 1)
    from personal_os import app as appmod

    if os.environ.get("GRAIN_FAKE_GOOGLE_CONNECTED", "1") == "1":
        appmod.db.set_settings({"googleToken": {
            "refresh_token": "fake", "token": "fake", "email": ME, "client_id": "fake", "client_secret": "fake",
            "token_uri": "x", "scopes": gmod.SCOPES, "connected_at": iso(NOW)}})

    @appmod.app.get("/__fake/state")
    def state() -> Any:
        return {"events": {c: list(v.values()) for c, v in store.events.items()}, "messages": store.messages, "drafts": store.drafts,
                "tasks": list(store.tasks.values()), "sent": store.sent, "writes": store.writes}

    @appmod.app.post("/__fake/events")
    async def bulk(request: Request) -> Any:
        b = await request.json()
        for i in range(int(b.get("n", 200))):
            eid = store.nid("bulk")
            s = MONDAY + dt.timedelta(days=i % int(b.get("days", 7)), hours=7 + (i * 7) % 12, minutes=(i * 15) % 60)
            store.events["primary"][eid] = {"id": eid, "status": "confirmed", "summary": f"Bulk {i}", "updated": iso(NOW),
                                            "start": {"dateTime": iso(s)}, "end": {"dateTime": iso(s + dt.timedelta(minutes=45))}}
        return {"ok": True}

    @appmod.app.post("/__fake/fail")
    async def fail(request: Request) -> Any:
        b = await request.json()
        store.fail[b["api"]] = int(b.get("n", 1))
        return {"ok": True}

    from personal_os.__main__ import main as real_main
    real_main()


if __name__ == "__main__":
    main()
