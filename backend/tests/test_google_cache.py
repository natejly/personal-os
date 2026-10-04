"""The TTL read cache in front of the Google APIs (no network; the service is stubbed).

Run: python backend/tests/test_google_cache.py
"""
from __future__ import annotations

import datetime as dt
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os import cache  # noqa: E402
from personal_os.google import Google  # noqa: E402
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


class CalendarCacheTests(unittest.TestCase):
    def test_second_read_is_served_from_cache(self) -> None:
        events = _Events([_EVENT])
        g = _google(events)
        first = g.calendar_events(days=7)
        second = g.calendar_events(days=7)
        self.assertEqual(first, second)
        self.assertEqual(events.counts["list"], 1)

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
    def __init__(self, callback: Any, messages: dict[str, dict[str, Any]], throttle: dict[str, int]):
        self._callback = callback
        self._messages = messages
        self._throttle = throttle  # id -> how many more times that item answers 429
        self._ids: list[tuple[str, str]] = []

    def add(self, req: _MsgReq, request_id: str | None = None) -> None:
        self._ids.append((request_id or str(len(self._ids)), req.mid))

    def execute(self) -> None:
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

    def users(self) -> _Gmail:
        return self

    def messages(self) -> _Gmail:
        return self

    def history(self) -> _Gmail:
        return self

    def list(self, **kw: Any) -> _Req:
        if "startHistoryId" in kw:
            return _Req({"historyId": "9", "history": self.records})
        return _Req({"messages": [{"id": i} for i in self.ids], "historyId": self.history_id})

    def get(self, **kw: Any) -> _MsgReq:
        self.gets.append(kw["id"])
        return _MsgReq(kw["id"])

    def new_batch_http_request(self, callback: Any) -> _GmailBatch:
        return _GmailBatch(callback, self.rows, self.throttle)


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


if __name__ == "__main__":
    unittest.main()
