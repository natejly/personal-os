"""The TTL read cache in front of the Google APIs (no network; the service is stubbed).

Run: python backend/tests/test_google_cache.py
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os import cache  # noqa: E402
from personal_os.google import Google  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
