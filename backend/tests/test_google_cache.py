"""The TTL read cache in front of the Google APIs (no network; the service is stubbed).

Run: python backend/tests/test_google_cache.py
"""
from __future__ import annotations

import datetime as dt
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os import cache  # noqa: E402
from personal_os import google as google_module  # noqa: E402
from personal_os.google import _CAL_FULL_AFTER, TTL, Google  # noqa: E402
from personal_os.google_store import ReadStore  # noqa: E402


class _Req:
    def __init__(self, result: Any):
        self._result = result

    def execute(self) -> Any:
        return self._result


class _Events:
    """Stands in for calendar.events(); counts how often each verb is reached."""

    def __init__(self, items: list[dict[str, Any]] | None = None):
        self.items = items if items is not None else []
        self.counts: dict[str, int] = {}

    def _bump(self, verb: str) -> None:
        self.counts[verb] = self.counts.get(verb, 0) + 1

    def list(self, **_kw: Any) -> _Req:
        self._bump("list")
        return _Req({"items": self.items})

    def get(self, **_kw: Any) -> _Req:
        self._bump("get")
        return _Req({"id": "e1", "summary": "Standup", "start": {"dateTime": "2026-09-30T10:00:00Z"}, "end": {"dateTime": "2026-09-30T10:15:00Z"}})

    def insert(self, **_kw: Any) -> _Req:
        self._bump("insert")
        return _Req({"id": "new", "summary": "Added", "start": {"dateTime": "2026-09-30T11:00:00Z"}, "end": {"dateTime": "2026-09-30T11:30:00Z"}})

    def delete(self, **_kw: Any) -> _Req:
        self._bump("delete")
        return _Req({})


class _CalendarList:
    def __init__(self, items: list[dict[str, Any]]):
        self.items, self.count = items, 0

    def list(self, **_kw: Any) -> _Req:
        self.count += 1
        return _Req({"items": self.items})


def _google(events: _Events, callist: _CalendarList | None = None) -> Google:
    """A Google whose only outside world is the stubs handed in."""
    g = Google(dict, lambda _s: None)
    cl = callist or _CalendarList([{"id": "primary", "summary": "Me", "primary": True, "accessRole": "owner"}])

    class _Svc:
        def events(self) -> _Events:
            return events

        def calendarList(self) -> _CalendarList:  # noqa: N802 - mirrors the Google API name
            return cl

    g._svc = lambda name, version: _Svc()  # type: ignore[method-assign]
    return g


_EVENT = {"id": "e1", "summary": "Standup", "start": {"dateTime": "2026-09-30T10:00:00Z"}, "end": {"dateTime": "2026-09-30T10:15:00Z"}}


class TTLCacheTests(unittest.TestCase):
    def test_hit_and_miss_are_distinguished_from_a_cached_none(self) -> None:
        c = cache.TTLCache()
        c.put("ns:k", None, ttl=60)
        self.assertEqual(c.get("ns:k"), (True, None))
        self.assertEqual(c.get("ns:absent"), (False, None))

    def test_entry_expires(self) -> None:
        c = cache.TTLCache()
        c.put("ns:k", 1, ttl=-1)  # a non-positive ttl stores nothing at all
        self.assertEqual(c.get("ns:k"), (False, None))

    def test_invalidate_is_namespaced(self) -> None:
        c = cache.TTLCache()
        c.put("calendar:a", 1, 60)
        c.put("calendar:b", 2, 60)
        c.put("gmail:a", 3, 60)
        self.assertEqual(c.invalidate("calendar"), 2)
        self.assertEqual(c.get("calendar:a")[0], False)
        self.assertEqual(c.get("gmail:a"), (True, 3))
        self.assertEqual(c.invalidate(), 1)

    def test_a_read_overtaken_by_a_write_is_not_stored(self) -> None:
        class _Api:
            def __init__(self) -> None:
                self._cache = cache.TTLCache()
                self.calls = 0

            @cache.cached("calendar", 60)
            def read(self) -> int:
                self.calls += 1
                if self.calls == 1:
                    self._cache.invalidate("calendar")  # a write lands while this read is in flight
                return self.calls

        api = _Api()
        self.assertEqual(api.read(), 1)
        self.assertEqual(api.read(), 2)  # the stale first answer was not kept
        self.assertEqual(api.read(), 2)

    def test_callers_cannot_mutate_a_cached_value(self) -> None:
        c = cache.TTLCache()
        c.put("ns:k", {"items": [1, 2]}, 60)
        first = c.get("ns:k")[1]
        first["items"].append(3)
        self.assertEqual(c.get("ns:k")[1], {"items": [1, 2]})

    def test_eviction_keeps_the_cache_bounded(self) -> None:
        c = cache.TTLCache(max_entries=2)
        c.put("ns:a", 1, 60)
        c.put("ns:b", 2, 60)
        c.put("ns:c", 3, 60)
        self.assertEqual(c.stats()["entries"], 2)
        self.assertEqual(c.get("ns:a")[0], False)  # the oldest went first

    def test_eviction_prefers_the_least_recently_used(self) -> None:
        c = cache.TTLCache(max_entries=2)
        c.put("ns:a", 1, 60)
        c.put("ns:b", 2, 60)
        c.get("ns:a")  # touching a makes b the stale one
        c.put("ns:c", 3, 60)
        self.assertEqual(c.get("ns:a"), (True, 1))
        self.assertEqual(c.get("ns:b")[0], False)

    def test_distinct_arguments_get_distinct_keys(self) -> None:
        self.assertNotEqual(cache.fingerprint((1,), {}), cache.fingerprint((2,), {}))
        self.assertEqual(cache.fingerprint((1,), {"a": 2}), cache.fingerprint((1,), {"a": 2}))
        # Unserialisable arguments must not raise.
        self.assertIsInstance(cache.fingerprint((object(),), {}), str)

    def test_long_keys_are_shortened(self) -> None:
        self.assertLessEqual(len(cache.fingerprint(("x" * 500,), {})), 160)


class IdTokenTests(unittest.TestCase):
    def test_email_comes_from_the_id_token_claims(self) -> None:
        import base64
        import json
        from personal_os.google import _id_token_email
        claims = base64.urlsafe_b64encode(json.dumps({"email": "me@x.com"}).encode()).decode().rstrip("=")
        self.assertEqual(_id_token_email(f"h.{claims}.sig"), "me@x.com")
        self.assertIsNone(_id_token_email(None))


class CalendarCacheTests(unittest.TestCase):
    def test_second_read_is_served_from_cache(self) -> None:
        events = _Events([_EVENT])
        g = _google(events)
        first = g.calendar_events(days=7)
        second = g.calendar_events(days=7)
        self.assertEqual(first, second)
        self.assertEqual(events.counts["list"], 1)

    def test_zero_max_results_returns_the_whole_window(self) -> None:
        items = [{"id": f"e{i}", "summary": "x", "start": {"dateTime": f"2026-10-0{1 + i % 6}T{i % 20:02d}:00:00Z"},
                  "end": {"dateTime": f"2026-10-0{1 + i % 6}T{i % 20:02d}:30:00Z"}} for i in range(80)]
        g = _google(_Events(items))
        self.assertEqual(len(g.calendar_events(7, "primary", 0, "2026-09-30T12:00:00Z")), 80)
        self.assertEqual(len(g.calendar_events(7, "primary", 60, "2026-09-30T12:00:00Z")), 60)

    def test_different_arguments_are_fetched_separately(self) -> None:
        events = _Events([_EVENT])
        g = _google(events)
        g.calendar_events(days=7)
        g.calendar_events(days=1)
        self.assertEqual(events.counts["list"], 2)

    def test_a_write_makes_the_next_read_fresh(self) -> None:
        events = _Events([_EVENT])
        g = _google(events)
        g.calendar_events(days=7)
        g.calendar_create({"summary": "Added", "start": "2026-09-30T11:00:00Z"})
        g.calendar_events(days=7)
        self.assertEqual(events.counts["list"], 2)

    def test_a_delete_also_invalidates(self) -> None:
        events = _Events([_EVENT])
        g = _google(events)
        g.calendar_events(days=7)
        g.calendar_delete("e1")
        g.calendar_events(days=7)
        self.assertEqual(events.counts["list"], 2)

    def test_gmail_writes_do_not_disturb_calendar_reads(self) -> None:
        events = _Events([_EVENT])
        g = _google(events)
        g.calendar_events(days=7)
        g.invalidate("gmail")
        g.calendar_events(days=7)
        self.assertEqual(events.counts["list"], 1)

    def test_bypass_refetches_and_refills(self) -> None:
        events = _Events([_EVENT])
        g = _google(events)
        g.calendar_events(days=7)
        with cache.bypass():
            g.calendar_events(days=7)
        self.assertEqual(events.counts["list"], 2)
        g.calendar_events(days=7)  # the bypassed call left a warm entry behind
        self.assertEqual(events.counts["list"], 2)

    def test_bypass_is_scoped_to_its_block(self) -> None:
        self.assertFalse(cache.bypassing())
        with cache.bypass():
            self.assertTrue(cache.bypassing())
        self.assertFalse(cache.bypassing())

    def test_the_calendar_list_is_cached_too(self) -> None:
        callist = _CalendarList([{"id": "primary", "summary": "Me", "primary": True, "accessRole": "owner"}])
        g = _google(_Events([_EVENT]), callist)
        g.calendars()
        g.calendars()
        self.assertEqual(callist.count, 1)

    def test_an_all_calendars_read_reuses_the_cached_calendar_list(self) -> None:
        callist = _CalendarList([{"id": "primary", "summary": "Me", "primary": True, "accessRole": "owner"}])
        g = _google(_Events([_EVENT]), callist)
        g.calendars()
        g.calendar_events(days=7, calendar_ids=["all"])
        self.assertEqual(callist.count, 1)

    def test_a_failed_read_is_not_cached(self) -> None:
        class _Boom(_Events):
            def list(self, **_kw: Any) -> _Req:
                self._bump("list")
                raise RuntimeError("Google 503")

        events = _Boom()
        g = _google(events)
        for _ in range(2):
            with self.assertRaises(RuntimeError):
                g.calendar_events(days=7)
        self.assertEqual(events.counts["list"], 2)

    def test_mutating_a_result_does_not_corrupt_the_next_read(self) -> None:
        events = _Events([_EVENT])
        g = _google(events)
        g.calendar_events(days=7).clear()
        self.assertEqual(len(g.calendar_events(days=7)), 1)

    def test_stats_report_the_namespaces_in_play(self) -> None:
        g = _google(_Events([_EVENT]))
        g.calendar_events(days=7)
        g.calendar_events(days=7)
        stats = g.cache_stats()
        self.assertEqual(stats["namespaces"], {"calendar": 1})
        self.assertEqual(stats["hits"], 1)


class ClientReuseTests(unittest.TestCase):
    def test_one_built_client_serves_many_calls(self) -> None:
        """_svc must not re-run discovery per call; that was the per-week cost."""
        builds: list[tuple[str, str]] = []
        creds = object()
        g = Google(dict, lambda _s: None)
        g._creds = lambda: creds  # type: ignore[method-assign]

        import personal_os.google as gmod

        real_import = gmod.__dict__.get("build")
        self.assertIsNone(real_import, "build is imported inside _svc; nothing to shadow at module level")

        def fake_build(name: str, version: str, **_kw: Any) -> str:
            builds.append((name, version))
            return f"{name}/{version}"

        import googleapiclient.discovery as disco

        orig = disco.build
        disco.build = fake_build  # type: ignore[assignment]
        try:
            self.assertEqual(g._svc("calendar", "v3"), "calendar/v3")
            self.assertEqual(g._svc("calendar", "v3"), "calendar/v3")
            g._svc("gmail", "v1")
        finally:
            disco.build = orig  # type: ignore[assignment]
        self.assertEqual(builds, [("calendar", "v3"), ("gmail", "v1")])

    def test_disconnect_forgets_everything(self) -> None:
        events = _Events([_EVENT])
        g = _google(events)
        g.calendar_events(days=7)
        g.disconnect()
        self.assertEqual(g.cache_stats()["entries"], 0)
        g.calendar_events(days=7)
        self.assertEqual(events.counts["list"], 2)


def _soon(hours: int = 1) -> str:
    return (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=hours)).replace(microsecond=0).isoformat()


class _RecordingEvents(_Events):
    """list() keeps the kwargs, and can go quiet after the first page."""

    def __init__(self, items: list[dict[str, Any]]):
        super().__init__(items)
        self.calls: list[dict[str, Any]] = []

    def list(self, **kw: Any) -> _Req:
        self._bump("list")
        self.calls.append(kw)
        if kw.get("updatedMin") and self.counts["list"] > 1:
            return _Req({"items": []})
        return _Req({"items": self.items})


class _MsgReq:
    def __init__(self, mid: str):
        self.mid = mid


class _GmailBatch:
    def __init__(self, callback: Any, messages: dict[str, dict[str, Any]], throttle: dict[str, int],
                 counts: dict[str, int] | None = None):
        self._callback = callback
        self._counts = counts if counts is not None else {}
        self._messages = messages
        self._throttle = throttle  # id -> how many more times that item answers 429
        self._ids: list[tuple[str, str]] = []

    def add(self, req: _MsgReq, request_id: str | None = None) -> None:
        self._ids.append((request_id or str(len(self._ids)), req.mid))

    def execute(self) -> None:
        self._counts["batches"] = self._counts.get("batches", 0) + 1
        self._counts["sub_requests"] = self._counts.get("sub_requests", 0) + len(self._ids)
        self._counts["largest_batch"] = max(self._counts.get("largest_batch", 0), len(self._ids))
        for rid, mid in self._ids:
            if self._throttle.get(mid, 0) > 0:
                self._throttle[mid] -= 1
                self._callback(rid, None, RuntimeError("429 rateLimitExceeded"))
            else:
                self._callback(rid, self._messages[mid], None)


def _mail(mid: str, subject: str) -> dict[str, Any]:
    return {
        "id": mid, "threadId": "t", "snippet": subject, "labelIds": ["INBOX", "UNREAD"],
        "payload": {"headers": [
            {"name": "From", "value": "Ada <ada@example.com>"},
            {"name": "Subject", "value": subject},
            {"name": "Date", "value": "Thu, 1 Oct 2026 12:00:00 +0000"},
        ]},
    }


class _Gmail:
    def __init__(self) -> None:
        self.ids = ["a", "b"]
        self.rows = {"a": _mail("a", "Hello"), "b": _mail("b", "Invoice"), "c": _mail("c", "New")}
        self.gets: list[str] = []
        self.records: list[dict[str, Any]] = []
        self.history_id = "5"
        self.throttle: dict[str, int] = {}
        self.counts: dict[str, int] = {"messages_list": 0, "history_list": 0, "batches": 0, "sub_requests": 0}
        self.batch_ids: list[list[str]] = []

    def users(self) -> _Gmail:
        return self

    def messages(self) -> _Gmail:
        return self

    def history(self) -> _Gmail:
        return self

    def list(self, **kw: Any) -> _Req:
        if "startHistoryId" in kw:
            self.counts["history_list"] += 1
            return _Req({"historyId": "9", "history": self.records})
        self.counts["messages_list"] += 1
        at = int(kw.get("pageToken") or 0)
        nxt = at + int(kw.get("maxResults") or 100)
        res: dict[str, Any] = {"messages": [{"id": i} for i in self.ids[at:nxt]], "historyId": self.history_id}
        if nxt < len(self.ids):
            res["nextPageToken"] = str(nxt)
        return _Req(res)

    def get(self, **kw: Any) -> _MsgReq:
        self.gets.append(kw["id"])
        return _MsgReq(kw["id"])

    def new_batch_http_request(self, callback: Any) -> _GmailBatch:
        batch = _GmailBatch(callback, self.rows, self.throttle, self.counts)
        self.batch_ids.append(batch._ids)
        return batch


class IncrementalTests(unittest.TestCase):
    def test_a_second_calendar_read_asks_only_for_changes_and_keeps_the_rest(self) -> None:
        start = _soon(1)
        end = _soon(2)
        event = {"id": "e1", "summary": "Standup", "start": {"dateTime": start}, "end": {"dateTime": end}}
        events = _RecordingEvents([event])
        g = _google(events)
        first = g.calendar_events(days=7)
        self.assertEqual([e["id"] for e in first], ["e1"])
        self.assertNotIn("updatedMin", events.calls[0])
        g.invalidate("calendar")
        second = g.calendar_events(days=7)
        self.assertEqual([e["id"] for e in second], ["e1"])
        self.assertIn("updatedMin", events.calls[-1])
        # The quiet second page must not have been a per-event refetch.
        self.assertEqual(events.counts.get("get", 0), 0)

    def test_a_refused_edit_keeps_the_event_on_the_grid(self) -> None:
        event = {"id": "e1", "summary": "Standup", "start": {"dateTime": _soon(1)}, "end": {"dateTime": _soon(2)}}
        events = _RecordingEvents([event])

        def refused(**_kw: Any) -> _Req:
            raise RuntimeError("403 forbidden: reader only")

        events.patch = refused  # type: ignore[attr-defined]
        g = _google(events)
        g.calendar_events(days=7)
        with self.assertRaises(RuntimeError):
            g.calendar_update("e1", {"summary": "Moved"})
        g.invalidate("calendar")  # the next read after the minute-scale cache
        self.assertEqual([e["id"] for e in g.calendar_events(days=7)], ["e1"])
        self.assertIn("updatedMin", events.calls[-1])  # the incremental read alone still has it

    def _two(self) -> tuple[dict[str, Any], dict[str, Any]]:
        return (
            {"id": "e1", "summary": "Standup", "start": {"dateTime": _soon(1)}, "end": {"dateTime": _soon(2)}},
            {"id": "e2", "summary": "Review", "start": {"dateTime": _soon(3)}, "end": {"dateTime": _soon(4)}},
        )

    def _age_full_list(self, g: Google, seconds: float) -> None:
        snap = dict(g._reads.get("calendar", "primary"))
        snap["full_at"] = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=seconds)).isoformat()
        g._reads.put("calendar", "primary", snap, flush=False)

    def test_an_old_full_list_is_repeated_even_after_a_recent_incremental_read(self) -> None:
        e1, e2 = self._two()
        events = _RecordingEvents([e1, e2])
        g = _google(events)
        g.calendar_events(days=7)
        g.invalidate("calendar")
        g.calendar_events(days=7)  # incremental: stamps synced_at just now
        self.assertIn("updatedMin", events.calls[-1])
        self._age_full_list(g, _CAL_FULL_AFTER + 60)
        events.items = [e1]  # e2 was removed in a way an incremental list cannot report
        g.invalidate("calendar")
        got = g.calendar_events(days=7)
        self.assertNotIn("updatedMin", events.calls[-1])
        self.assertEqual([e["id"] for e in got], ["e1"])

    def test_a_young_full_list_keeps_reading_incrementally(self) -> None:
        e1, e2 = self._two()
        events = _RecordingEvents([e1, e2])
        g = _google(events)
        g.calendar_events(days=7)
        self._age_full_list(g, _CAL_FULL_AFTER - 60)
        g.invalidate("calendar")
        g.calendar_events(days=7)
        self.assertIn("updatedMin", events.calls[-1])

    def test_a_snapshot_without_a_full_list_stamp_is_listed_in_full(self) -> None:
        e1, _e2 = self._two()
        events = _RecordingEvents([e1])
        g = _google(events)
        g.calendar_events(days=7)
        snap = dict(g._reads.get("calendar", "primary"))
        snap.pop("full_at")
        g._reads.put("calendar", "primary", snap, flush=False)
        g.invalidate("calendar")
        g.calendar_events(days=7)
        self.assertNotIn("updatedMin", events.calls[-1])

    def test_the_incremental_list_asks_for_deleted_entries_too(self) -> None:
        e1, _e2 = self._two()
        events = _RecordingEvents([e1])
        g = _google(events)
        g.calendar_events(days=7)
        g.invalidate("calendar")
        g.calendar_events(days=7)
        self.assertIn("updatedMin", events.calls[-1])
        self.assertIs(events.calls[-1].get("showDeleted"), True)
        self.assertNotIn("orderBy", events.calls[-1])

    def test_an_event_reported_cancelled_is_dropped(self) -> None:
        e1, e2 = self._two()

        class _Cancelling(_RecordingEvents):
            def list(self, **kw: Any) -> _Req:
                self._bump("list")
                self.calls.append(kw)
                return _Req({"items": [{"id": "e2", "status": "cancelled"}] if kw.get("updatedMin") else self.items})

        events = _Cancelling([e1, e2])
        g = _google(events)
        self.assertEqual([e["id"] for e in g.calendar_events(days=7)], ["e1", "e2"])
        g.invalidate("calendar")
        got = g.calendar_events(days=7)
        self.assertIn("updatedMin", events.calls[-1])
        self.assertEqual([e["id"] for e in got], ["e1"])
        self.assertEqual(sorted(g._reads.get("calendar", "primary")["events"]), ["e1"])

    def test_a_refresh_the_user_asked_for_lists_in_full(self) -> None:
        e1, e2 = self._two()
        events = _RecordingEvents([e1, e2])
        g = _google(events)
        g.calendar_events(days=7)
        events.items = [e1]  # Google no longer returns e2
        g.invalidate("calendar")
        with cache.bypass():
            got = g.calendar_events(days=7)
        self.assertNotIn("updatedMin", events.calls[-1])
        self.assertEqual([e["id"] for e in got], ["e1"])
        self.assertEqual(sorted(g._reads.get("calendar", "primary")["events"]), ["e1"])

    def test_saved_events_survive_a_new_process(self) -> None:
        start = _soon(1)
        end = _soon(2)
        event = {"id": "e1", "summary": "Standup", "start": {"dateTime": start}, "end": {"dateTime": end}}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "google-reads.json"
            events = _RecordingEvents([event])
            g = _google(events)
            g._reads = ReadStore(path)
            self.assertEqual([e["id"] for e in g.calendar_events(days=7)], ["e1"])

            quiet = _RecordingEvents([])

            class _Svc:
                def events(self) -> _RecordingEvents:
                    return quiet

                def calendarList(self) -> _CalendarList:
                    return _CalendarList([])

            again = Google(dict, lambda _s: None)
            again._reads = ReadStore(path)
            again._svc = lambda _name, _version: _Svc()  # type: ignore[method-assign]
            got = again.calendar_events(days=7)
            self.assertEqual([e["id"] for e in got], ["e1"])
            self.assertIn("updatedMin", quiet.calls[-1])

    def test_disconnect_forgets_the_saved_snapshot(self) -> None:
        start = _soon(1)
        end = _soon(2)
        event = {"id": "e1", "summary": "Standup", "start": {"dateTime": start}, "end": {"dateTime": end}}
        events = _RecordingEvents([event])
        g = _google(events)
        g.calendar_events(days=7)
        g.disconnect()
        g.calendar_events(days=7)
        self.assertNotIn("updatedMin", events.calls[-1])

    def test_gmail_refetches_only_new_or_changed_messages(self) -> None:
        svc = _Gmail()
        g = Google(dict, lambda _s: None)
        g._svc = lambda name, version: svc  # type: ignore[method-assign]
        first = g.gmail_search("in:inbox", 10)
        self.assertEqual([m["id"] for m in first], ["a", "b"])
        self.assertEqual(svc.gets, ["a", "b"])
        g.invalidate("gmail")
        second = g.gmail_search("in:inbox", 10)
        self.assertEqual([m["id"] for m in second], ["a", "b"])
        self.assertEqual(svc.gets, ["a", "b"])  # nothing new, so no metadata get
        svc.ids = ["a", "c"]
        svc.records = [{"id": "8", "messagesAdded": [{"message": {"id": "c"}}], "messagesDeleted": [{"message": {"id": "b"}}]}]
        g.invalidate("gmail")
        third = g.gmail_search("in:inbox", 10)
        self.assertEqual([m["id"] for m in third], ["a", "c"])
        self.assertEqual(svc.gets, ["a", "b", "c"])

    def test_a_throttled_batch_item_is_asked_for_again(self) -> None:
        svc = _Gmail()
        svc.throttle = {"b": 1}
        g = Google(dict, lambda _s: None)
        g._svc = lambda name, version: svc  # type: ignore[method-assign]
        self.assertEqual([m["id"] for m in g.gmail_search("in:inbox", 10)], ["a", "b"])
        self.assertEqual(svc.gets, ["a", "b", "b"])

    def test_a_list_missing_messages_is_not_cached(self) -> None:
        svc = _Gmail()
        svc.throttle = {"b": 2}
        g = Google(dict, lambda _s: None)
        g._svc = lambda name, version: svc  # type: ignore[method-assign]
        self.assertEqual([m["id"] for m in g.gmail_search("in:inbox", 10)], ["a"])
        # No invalidate: an incomplete answer must not be served for the rest of the TTL.
        self.assertEqual([m["id"] for m in g.gmail_search("in:inbox", 10)], ["a", "b"])
        self.assertEqual([m["id"] for m in g.gmail_search("in:inbox", 10)], ["a", "b"])
        self.assertEqual(svc.gets, ["a", "b", "b", "b"])

    def test_gmail_search_follows_page_tokens(self) -> None:
        class _Paged(_Gmail):
            def list(self, **kw: Any) -> _Req:
                if "startHistoryId" in kw:
                    return super().list(**kw)
                ids = ["a", "b", "c"]
                at = int(kw.get("pageToken") or 0)
                nxt = at + min(2, kw["maxResults"])
                res = {"messages": [{"id": i} for i in ids[at:nxt]], "historyId": "5"}
                return _Req({**res, "nextPageToken": str(nxt)} if nxt < len(ids) else res)

        g = Google(dict, lambda _s: None)
        g._svc = lambda name, version: _Paged()  # type: ignore[method-assign]
        self.assertEqual([m["id"] for m in g.gmail_search("in:inbox", 60)], ["a", "b", "c"])

    def test_a_saved_message_body_is_not_fetched_again(self) -> None:
        svc = _Gmail()
        body = {"id": "a", "threadId": "t", "payload": {"mimeType": "text/plain", "headers": [{"name": "Subject", "value": "Hello"}], "body": {"data": "aGk="}}, "snippet": "hi"}

        class _Full(_Gmail):
            def __init__(self) -> None:
                super().__init__()
                self.full_gets = 0

            def get(self, **kw: Any) -> _Req:
                self.full_gets += 1
                return _Req(body)

        full = _Full()
        g = Google(dict, lambda _s: None)
        g._svc = lambda name, version: full  # type: ignore[method-assign]
        self.assertEqual(g.gmail_get("a")["subject"], "Hello")
        g.invalidate("gmail")
        self.assertEqual(g.gmail_get("a")["subject"], "Hello")
        self.assertEqual(full.full_gets, 1)


def _lots(svc: _Gmail, n: int) -> None:
    """Make the fake hold n messages, newest first."""
    svc.ids = [f"m{i:03d}" for i in range(n)]
    svc.rows = {mid: _mail(mid, f"Subject {mid}") for mid in svc.ids}


def _look(g: Google, query: str, pages: int, size: int = 25) -> list[int]:
    """The assistant tool's calls: one past each page, as tools.gmail_search asks."""
    return [len(g.gmail_search(query, off + size + 1)) for off in range(0, pages * size, size)]


class GmailSearchCostTests(unittest.TestCase):
    def _g(self, svc: _Gmail) -> Google:
        g = Google(dict, lambda _s: None)
        g._svc = lambda name, version: svc  # type: ignore[method-assign]
        return g

    def test_two_pages_of_one_query_list_and_check_history_once(self) -> None:
        svc = _Gmail()
        _lots(svc, 60)
        g = self._g(svc)
        g.gmail_search("from:ada", 1)  # a saved cursor, as any account that has searched before has
        g.invalidate("gmail")  # the TTL has run out: the next search must ask Gmail
        svc.counts.update(messages_list=0, history_list=0, batches=0, sub_requests=0)
        svc.batch_ids.clear()
        self.assertEqual(len(g.gmail_search("in:inbox", 26)), 26)
        self.assertEqual(len(g.gmail_search("in:inbox", 51)), 51)
        self.assertEqual(svc.counts["messages_list"], 1)
        self.assertEqual(svc.counts["history_list"], 1)
        first, second = [[mid for _rid, mid in ids] for ids in svc.batch_ids]
        self.assertEqual(len(first), 25)  # m000 was saved by the first search
        self.assertEqual(len(second), 25)  # only the ids the first page did not save
        self.assertFalse(set(first) & set(second))

    def test_first_ever_look_costs_one_list_and_no_history(self) -> None:
        svc = _Gmail()
        _lots(svc, 60)
        g = self._g(svc)
        self.assertEqual(_look(g, "in:inbox", 2), [26, 51])
        self.assertEqual((svc.counts["messages_list"], svc.counts["history_list"]), (1, 0))
        self.assertEqual((svc.counts["batches"], svc.counts["sub_requests"]), (2, 51))

    def test_a_different_query_still_lists(self) -> None:
        svc = _Gmail()
        _lots(svc, 10)
        g = self._g(svc)
        g.gmail_search("in:inbox", 5)
        g.gmail_search("from:ada", 5)
        self.assertEqual(svc.counts["messages_list"], 2)
        g.gmail_search("in:inbox", 8)
        g.gmail_search("from:ada", 8)
        self.assertEqual(svc.counts["messages_list"], 2)

    def test_a_listing_older_than_the_ttl_is_listed_again(self) -> None:
        svc = _Gmail()
        _lots(svc, 10)
        g = self._g(svc)
        t = [1000.0]
        with mock.patch.object(google_module.time, "time", lambda: t[0]):
            g.gmail_search("in:inbox", 3)
            t[0] += TTL["gmail_list"] - 1
            g.gmail_search("in:inbox", 5)
            self.assertEqual((svc.counts["messages_list"], svc.counts["history_list"]), (1, 0))
            t[0] += 2
            g.gmail_search("in:inbox", 6)
            self.assertEqual((svc.counts["messages_list"], svc.counts["history_list"]), (2, 1))

    def test_a_new_message_shows_in_a_search_after_the_ttl(self) -> None:
        svc = _Gmail()
        _lots(svc, 3)
        g = self._g(svc)
        t = [1000.0]
        with mock.patch.object(google_module.time, "time", lambda: t[0]):
            g.gmail_search("in:inbox", 10)
            svc.ids = ["new"] + svc.ids
            svc.rows["new"] = _mail("new", "Fresh")
            svc.records = [{"id": "8", "messagesAdded": [{"message": {"id": "new"}}]}]
            t[0] += TTL["gmail_list"] + 1
            self.assertEqual([m["id"] for m in g.gmail_search("in:inbox", 11)][0], "new")

    def test_a_write_makes_the_next_search_ask_gmail_again(self) -> None:
        svc = _Gmail()
        _lots(svc, 3)
        g = self._g(svc)
        g.gmail_search("in:inbox", 10)
        g._gmail_unsure()  # what a send or a label change does
        g.gmail_search("in:inbox", 9)
        self.assertEqual((svc.counts["messages_list"], svc.counts["history_list"]), (2, 1))

    def test_more_than_a_hundred_ids_are_fetched_in_batches_of_at_most_a_hundred(self) -> None:
        svc = _Gmail()
        _lots(svc, 250)
        g = self._g(svc)
        self.assertEqual(len(g.gmail_search("in:inbox", 250)), 250)
        self.assertEqual([len(b) for b in svc.batch_ids], [100, 100, 50])
        self.assertEqual(svc.counts["sub_requests"], 250)

    def test_a_throttled_item_in_a_later_chunk_is_retried_and_nothing_is_cached_if_it_stays_missing(self) -> None:
        svc = _Gmail()
        _lots(svc, 120)
        svc.throttle = {"m110": 2}
        g = self._g(svc)
        self.assertEqual(len(g.gmail_search("in:inbox", 120)), 119)
        self.assertEqual(len(g.gmail_search("in:inbox", 120)), 120)  # not served from the TTL cache

    def test_an_explicit_refresh_still_asks_gmail_for_history_and_the_listing(self) -> None:
        svc = _Gmail()
        _lots(svc, 5)
        g = self._g(svc)
        g.gmail_search("in:inbox", 5)
        g.invalidate("gmail")
        g.gmail_search("in:inbox", 5)
        before = dict(svc.counts)
        g.gmail_search("in:inbox", 5)  # the TTL cache answers
        self.assertEqual(svc.counts, before)
        with cache.bypass():
            g.gmail_search("in:inbox", 5)
        self.assertEqual(svc.counts["history_list"], before["history_list"] + 1)
        self.assertEqual(svc.counts["messages_list"], before["messages_list"] + 1)

    def test_only_a_few_dozen_queries_keep_a_listing(self) -> None:
        svc = _Gmail()
        _lots(svc, 3)
        g = self._g(svc)
        for i in range(google_module._GMAIL_LISTS_KEPT + 5):
            g.gmail_search(f"label:l{i}", 3)
        lists = g._reads.get("gmail", "meta")["lists"]
        self.assertEqual(len(lists), google_module._GMAIL_LISTS_KEPT)
        self.assertNotIn("label:l0", lists)


if __name__ == "__main__":
    unittest.main()
