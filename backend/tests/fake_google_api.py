"""A stand-in for the Google discovery clients, good enough to exercise the read-back path.

Only the calls google.py actually makes are implemented, in the same chained shape
(`svc.events().get(...).execute()`), over plain dicts. The knobs are what the verification tests
need: hide an object from read-backs, 404 it a few times (eventual consistency), fail the read
with a server error, or report a different value than was written (a mismatch).

No network, no credentials: FakeGoogle overrides _svc, so nothing below it is ever reached.
"""
from __future__ import annotations

import base64
import copy
import email
from typing import Any

from personal_os.google import Google


class _Resp:
    def __init__(self, status: int):
        self.status = status


class ApiError(Exception):
    """Shaped like googleapiclient.errors.HttpError as far as verify.is_missing cares."""

    def __init__(self, status: int, message: str):
        super().__init__(f"<HttpError {status} when requesting: {message}>")
        self.resp = _Resp(status)


class _Call:
    def __init__(self, thunk: Any):
        self._thunk = thunk

    def execute(self) -> Any:
        return self._thunk()


class FakeServer:
    def __init__(self) -> None:
        self.events: dict[tuple[str, str], dict[str, Any]] = {}
        self.messages: dict[str, dict[str, Any]] = {}
        self.drafts: dict[str, dict[str, Any]] = {}
        self.tasks: dict[tuple[str, str], dict[str, Any]] = {}
        self.n = 0
        self.versions = 0
        # ---- knobs ----
        self.hide: set[str] = set()            # these ids never come back from a read
        self.flaky: dict[str, int] = {}        # id -> this many more reads 404 before it appears
        self.boom: dict[str, int] = {}         # id -> this many more reads raise a 500
        self.corrupt: dict[str, dict[str, Any]] = {}  # id -> fields a read reports differently
        self.send_labels: list[str] = ["SENT"]  # labels a freshly sent message gets
        self.keep_deleted = False              # a "delete" that does not actually delete
        self.tombstone = False                 # a deleted event stays readable as status=cancelled, like Google's
        self.reads: list[str] = []             # every id a read-back asked for, in order

    # ---- shared read gate ----
    def _gate(self, oid: str) -> None:
        self.reads.append(oid)
        if self.boom.get(oid):
            self.boom[oid] -= 1
            raise ApiError(500, "backendError")
        if oid in self.hide:
            raise ApiError(404, "Not Found")
        if self.flaky.get(oid):
            self.flaky[oid] -= 1
            raise ApiError(404, "Not Found")

    def _id(self, prefix: str) -> str:
        self.n += 1
        return f"{prefix}{self.n}"

    def _etag(self) -> str:
        """A new version stamp (an event etag, a task's `updated`); its own counter, so ids stay ev1, ev2, ..."""
        self.versions += 1
        return f"{self.versions:06d}"

    # ---- calendar ----
    def insert_event(self, calendar_id: str, body: dict[str, Any]) -> dict[str, Any]:
        eid = self._id("ev")
        e = {"id": eid, "status": "confirmed", "htmlLink": f"https://cal.test/{eid}", **body, "etag": self._etag()}
        self.events[(calendar_id, eid)] = e
        return dict(e)

    def read_event(self, calendar_id: str, event_id: str) -> dict[str, Any]:
        self._gate(event_id)
        e = self.events.get((calendar_id, event_id))
        if e is None:
            raise ApiError(404, "Not Found")
        return copy.deepcopy({**e, **self.corrupt.get(event_id, {})})  # a fresh JSON object per read, like the API

    def patch_event(self, calendar_id: str, event_id: str, body: dict[str, Any]) -> dict[str, Any]:
        e = self.events.get((calendar_id, event_id))
        if e is None:
            raise ApiError(404, "Not Found")
        e.update({k: v for k, v in body.items()})
        e["etag"] = self._etag()
        return dict(e)

    def delete_event(self, calendar_id: str, event_id: str) -> str:
        if (calendar_id, event_id) not in self.events:
            raise ApiError(404, "Not Found")
        if self.tombstone:
            self.events[(calendar_id, event_id)].update({"status": "cancelled", "etag": self._etag()})
        elif not self.keep_deleted:
            del self.events[(calendar_id, event_id)]
        return ""

    # ---- gmail ----
    def send_message(self, body: dict[str, Any]) -> dict[str, Any]:
        mid = self._id("msg")
        msg = email.message_from_bytes(base64.urlsafe_b64decode(body["raw"]))
        m = {"id": mid, "threadId": body.get("threadId") or mid, "labelIds": list(self.send_labels),
             "payload": {"headers": [{"name": k, "value": v} for k, v in msg.items()]}}
        self.messages[mid] = m
        return {"id": mid, "threadId": m["threadId"]}

    def read_message(self, message_id: str) -> dict[str, Any]:
        self._gate(message_id)
        m = self.messages.get(message_id)
        if m is None:
            raise ApiError(404, "Not Found")
        return {**m, **self.corrupt.get(message_id, {})}

    def modify_message(self, message_id: str, body: dict[str, Any]) -> dict[str, Any]:
        m = self.messages.get(message_id)
        if m is None:
            raise ApiError(404, "Not Found")
        have = [x for x in m["labelIds"] if x not in (body.get("removeLabelIds") or [])]
        m["labelIds"] = have + [x for x in (body.get("addLabelIds") or []) if x not in have]
        return dict(m)

    def create_draft(self, body: dict[str, Any]) -> dict[str, Any]:
        did = self._id("draft")
        self.drafts[did] = {"id": did, "message": body["message"]}
        return {"id": did}

    def read_draft(self, draft_id: str) -> dict[str, Any]:
        self._gate(draft_id)
        d = self.drafts.get(draft_id)
        if d is None:
            raise ApiError(404, "Not Found")
        msg = email.message_from_bytes(base64.urlsafe_b64decode(d["message"]["raw"]))
        headers = [{"name": k, "value": v} for k, v in msg.items()]
        return {"id": draft_id, "message": {"payload": {"headers": headers}}}

    # ---- tasks ----
    def insert_task(self, tasklist: str, body: dict[str, Any]) -> dict[str, Any]:
        tid = self._id("task")
        t = {"id": tid, "status": "needsAction", "updated": "2026-01-01T00:00:00.000Z", **body}
        self.tasks[(tasklist, tid)] = t
        return dict(t)

    def read_task(self, tasklist: str, task_id: str) -> dict[str, Any]:
        self._gate(task_id)
        t = self.tasks.get((tasklist, task_id))
        if t is None:
            raise ApiError(404, "Not Found")
        return {**t, **self.corrupt.get(task_id, {})}

    def patch_task(self, tasklist: str, task_id: str, body: dict[str, Any]) -> dict[str, Any]:
        t = self.tasks.get((tasklist, task_id))
        if t is None:
            raise ApiError(404, "Not Found")
        t.update(body)
        t["updated"] = f"2026-01-02T00:00:00.{self._etag()[-3:]}Z"
        return dict(t)

    def delete_task(self, tasklist: str, task_id: str) -> str:
        if (tasklist, task_id) not in self.tasks:
            raise ApiError(404, "Not Found")
        if not self.keep_deleted:
            del self.tasks[(tasklist, task_id)]
        return ""

    def service(self, name: str) -> Any:
        return {"calendar": _Calendar(self), "gmail": _Gmail(self), "tasks": _Tasks(self)}[name]


class _Events:
    def __init__(self, s: FakeServer):
        self.s = s

    def insert(self, calendarId: str, body: dict[str, Any], **_: Any) -> _Call:
        return _Call(lambda: self.s.insert_event(calendarId, body))

    def get(self, calendarId: str, eventId: str, **_: Any) -> _Call:
        return _Call(lambda: self.s.read_event(calendarId, eventId))

    def patch(self, calendarId: str, eventId: str, body: dict[str, Any], **_: Any) -> _Call:
        return _Call(lambda: self.s.patch_event(calendarId, eventId, body))

    def delete(self, calendarId: str, eventId: str, **_: Any) -> _Call:
        return _Call(lambda: self.s.delete_event(calendarId, eventId))


class _Calendar:
    def __init__(self, s: FakeServer):
        self.s = s

    def events(self) -> _Events:
        return _Events(self.s)


class _GmailMessages:
    def __init__(self, s: FakeServer):
        self.s = s

    def send(self, userId: str, body: dict[str, Any]) -> _Call:
        return _Call(lambda: self.s.send_message(body))

    def get(self, userId: str, id: str, **_: Any) -> _Call:
        return _Call(lambda: self.s.read_message(id))

    def modify(self, userId: str, id: str, body: dict[str, Any]) -> _Call:
        return _Call(lambda: self.s.modify_message(id, body))


class _GmailDrafts:
    def __init__(self, s: FakeServer):
        self.s = s

    def create(self, userId: str, body: dict[str, Any]) -> _Call:
        return _Call(lambda: self.s.create_draft(body))

    def get(self, userId: str, id: str, **_: Any) -> _Call:
        return _Call(lambda: self.s.read_draft(id))


class _GmailUsers:
    def __init__(self, s: FakeServer):
        self.s = s

    def messages(self) -> _GmailMessages:
        return _GmailMessages(self.s)

    def drafts(self) -> _GmailDrafts:
        return _GmailDrafts(self.s)


class _Gmail:
    def __init__(self, s: FakeServer):
        self.s = s

    def users(self) -> _GmailUsers:
        return _GmailUsers(self.s)


class _TaskOps:
    def __init__(self, s: FakeServer):
        self.s = s

    def insert(self, tasklist: str, body: dict[str, Any]) -> _Call:
        return _Call(lambda: self.s.insert_task(tasklist, body))

    def get(self, tasklist: str, task: str) -> _Call:
        return _Call(lambda: self.s.read_task(tasklist, task))

    def patch(self, tasklist: str, task: str, body: dict[str, Any]) -> _Call:
        return _Call(lambda: self.s.patch_task(tasklist, task, body))

    def delete(self, tasklist: str, task: str) -> _Call:
        return _Call(lambda: self.s.delete_task(tasklist, task))


class _Tasks:
    def __init__(self, s: FakeServer):
        self.s = s

    def tasks(self) -> _TaskOps:
        return _TaskOps(self.s)


class FakeGoogle(Google):
    """The real Google client with its transport swapped out. Every verifier runs for real."""

    def __init__(self, server: FakeServer):
        super().__init__(lambda: {}, lambda patch: None)
        self.server = server

    def _svc(self, name: str, version: str) -> Any:  # type: ignore[override]
        return self.server.service(name)
