"""Pure scheduling logic: busy time, working hours, buffers, DST, slot ranking, change validation.

Run: python backend/tests/test_scheduling.py
"""
from __future__ import annotations

import datetime as dt
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os import scheduling as sch  # noqa: E402

NY = sch.get_tz("America/New_York")
UTC = dt.timezone.utc


def ev(start: str, end: str, **kw: object) -> dict:
    return {"id": kw.pop("id", start), "summary": kw.pop("summary", "x"), "start": start, "end": end, "all_day": len(start) == 10, **kw}


def starts(slots: list[dict]) -> list[str]:
    return [s["start"][:16] for s in slots]


def busy(*events: dict, tz=NY) -> list:
    return sch.busy_from_events(list(events), tz)


class BusyTests(unittest.TestCase):
    def test_overlapping_and_touching_events_fuse(self) -> None:
        b = busy(ev("2026-10-07T10:00:00-04:00", "2026-10-07T11:00:00-04:00"),
                 ev("2026-10-07T10:30:00-04:00", "2026-10-07T11:30:00-04:00"),
                 ev("2026-10-07T11:30:00-04:00", "2026-10-07T12:00:00-04:00"))
        self.assertEqual(len(b), 1)
        self.assertEqual(b[0][1] - b[0][0], dt.timedelta(hours=2))

    def test_declined_free_and_cancelled_do_not_block(self) -> None:
        b = busy(ev("2026-10-07T10:00:00-04:00", "2026-10-07T11:00:00-04:00", my_response="declined"),
                 ev("2026-10-07T12:00:00-04:00", "2026-10-07T13:00:00-04:00", transparency="transparent"),
                 ev("2026-10-07T14:00:00-04:00", "2026-10-07T15:00:00-04:00", status="cancelled"),
                 ev("2026-10-07T16:00:00-04:00", "2026-10-07T17:00:00-04:00",
                    attendee_details=[{"email": "me@x.com", "self": True, "response": "declined"}]))
        self.assertEqual(b, [])

    def test_tentative_and_needs_action_still_block(self) -> None:
        b = busy(ev("2026-10-07T10:00:00-04:00", "2026-10-07T11:00:00-04:00", my_response="tentative"),
                 ev("2026-10-07T12:00:00-04:00", "2026-10-07T13:00:00-04:00", my_response="needsAction"))
        self.assertEqual(len(b), 2)

    def test_all_day_event_blocks_the_whole_local_day(self) -> None:
        b = busy(ev("2026-10-07", "2026-10-08"))
        self.assertEqual(b[0][0], dt.datetime(2026, 10, 7, 4, 0, tzinfo=UTC))  # local midnight, EDT
        self.assertEqual(b[0][1] - b[0][0], dt.timedelta(hours=24))

    def test_one_day_all_day_without_a_usable_end(self) -> None:
        for end in ("2026-10-07", None):
            b = busy(ev("2026-10-07", end))  # type: ignore[arg-type]
            self.assertEqual(b[0][1] - b[0][0], dt.timedelta(hours=24))

    def test_multi_day_all_day_event(self) -> None:
        b = busy(ev("2026-10-07", "2026-10-10"))
        self.assertEqual(b[0][1] - b[0][0], dt.timedelta(hours=72))

    def test_free_all_day_event_does_not_block(self) -> None:
        self.assertEqual(busy(ev("2026-10-07", "2026-10-08", transparency="transparent")), [])

    def test_all_day_can_be_ignored_by_option(self) -> None:
        self.assertEqual(sch.busy_from_events([ev("2026-10-07", "2026-10-08")], NY, all_day_blocks=False), [])

    def test_all_day_event_on_a_dst_change_day_is_23_or_25_hours(self) -> None:
        spring = busy(ev("2026-03-08", "2026-03-09"))
        fall = busy(ev("2026-11-01", "2026-11-02"))
        self.assertEqual(spring[0][1] - spring[0][0], dt.timedelta(hours=23))
        self.assertEqual(fall[0][1] - fall[0][0], dt.timedelta(hours=25))

    def test_ignore_ids_drops_the_event_being_moved(self) -> None:
        e = ev("2026-10-07T10:00:00-04:00", "2026-10-07T11:00:00-04:00", id="mv")
        self.assertEqual(sch.busy_from_events([e], NY, ignore_ids=["mv"]), [])

    def test_naive_times_are_read_in_the_users_zone(self) -> None:
        b = busy(ev("2026-10-07T10:00:00", "2026-10-07T11:00:00"))
        self.assertEqual(b[0][0], dt.datetime(2026, 10, 7, 14, 0, tzinfo=UTC))

    def test_unparseable_events_are_skipped_not_fatal(self) -> None:
        self.assertEqual(busy({"id": "a", "start": "garbage", "end": "x"}, {"id": "b"}), [])

    def test_freebusy_ranges(self) -> None:
        b = sch.busy_from_ranges([{"start": "2026-10-07T14:00:00Z", "end": "2026-10-07T15:00:00Z"}, {"start": "bad"}], NY)
        self.assertEqual(len(b), 1)

    def test_buffer_pads_both_sides_and_fuses(self) -> None:
        b = busy(ev("2026-10-07T10:00:00-04:00", "2026-10-07T11:00:00-04:00"), ev("2026-10-07T11:20:00-04:00", "2026-10-07T12:00:00-04:00"))
        p = sch.pad(b, 15)
        self.assertEqual(len(p), 1)
        self.assertEqual(p[0][0], b[0][0] - dt.timedelta(minutes=15))


class WorkingHoursTests(unittest.TestCase):
    def test_parsing_forms(self) -> None:
        self.assertEqual(sch.parse_working_hours(None), (540, 1080))
        self.assertEqual(sch.parse_working_hours("9-18"), (540, 1080))
        self.assertEqual(sch.parse_working_hours("09:30-17:00"), (570, 1020))
        self.assertEqual(sch.parse_working_hours([8, 16]), (480, 960))
        self.assertEqual(sch.parse_working_hours({"start": "7:15", "end": "12"}), (435, 720))
        self.assertEqual(sch.parse_working_hours("0-24"), (0, 1440))

    def test_bad_hours_raise(self) -> None:
        for bad in ("18-9", "9", "nine-five", [9, 9], "9-25"):
            with self.assertRaises(ValueError, msg=str(bad)):
                sch.parse_working_hours(bad)

    def test_weekends_are_skipped_by_default(self) -> None:
        w0, w1 = sch.parse_dt("2026-10-09T00:00", NY), sch.parse_dt("2026-10-13T00:00", NY)  # Fri..Mon
        days = {a.astimezone(NY).date().isoformat() for a, _ in sch.working_windows(w0, w1, NY, (540, 1080))}
        self.assertEqual(days, {"2026-10-09", "2026-10-12"})
        weekend = {a.astimezone(NY).date().isoformat() for a, _ in sch.working_windows(w0, w1, NY, (540, 1080), weekdays_only=False)}
        self.assertEqual(len(weekend), 4)

    def test_working_day_length_follows_dst(self) -> None:
        # 0:00-24:00 on a DST day, weekends included: 23h in spring, 25h in fall.
        for day, hours in (("2026-03-08", 23), ("2026-11-01", 25), ("2026-10-07", 24)):
            w0 = sch.parse_dt(day + "T00:00", NY)
            (a, b), = sch.working_windows(w0, w0 + dt.timedelta(days=2), NY, (0, 1440), weekdays_only=False)[:1]
            self.assertEqual(b - a, dt.timedelta(hours=hours), day)

    def test_window_clips_to_the_search_range(self) -> None:
        w0, w1 = sch.parse_dt("2026-10-07T11:00", NY), sch.parse_dt("2026-10-07T12:00", NY)
        (a, b), = sch.working_windows(w0, w1, NY, (540, 1080))
        self.assertEqual((a, b), (w0.astimezone(UTC), w1.astimezone(UTC)))


class SubtractTests(unittest.TestCase):
    def test_cuts_holes(self) -> None:
        t = lambda h: dt.datetime(2026, 1, 1, h, tzinfo=UTC)  # noqa: E731
        self.assertEqual(sch.subtract([(t(9), t(18))], [(t(10), t(11)), (t(12), t(13))]),
                         [(t(9), t(10)), (t(11), t(12)), (t(13), t(18))])
        self.assertEqual(sch.subtract([(t(9), t(18))], [(t(8), t(19))]), [])
        self.assertEqual(sch.subtract([(t(9), t(10))], [(t(10), t(11))]), [(t(9), t(10))])  # touching is free


class FindSlotsTests(unittest.TestCase):
    def find(self, *events: dict, dur: int = 30, a: str = "2026-10-07T00:00", b: str = "2026-10-08T00:00", **kw):
        return sch.find_slots(dur, a, b, busy(*events), tz=NY, **kw)

    def test_empty_calendar_offers_distinct_ranked_options(self) -> None:
        slots = self.find(max_results=3)
        self.assertEqual([s["rank"] for s in slots], [1, 2, 3])
        spans = [(s["start"], s["end"]) for s in slots]
        for i, x in enumerate(spans):  # no overlaps among the picks
            for y in spans[i + 1:]:
                self.assertTrue(x[1] <= y[0] or y[1] <= x[0])
        for s in slots:
            h = int(s["start"][11:13])
            self.assertTrue(9 <= h < 18)

    def test_a_slot_never_overlaps_an_event(self) -> None:
        slots = self.find(ev("2026-10-07T09:00:00-04:00", "2026-10-07T16:30:00-04:00"), max_results=10)
        self.assertTrue(slots)
        for s in slots:
            self.assertGreaterEqual(s["start"][:16], "2026-10-07T16:30")

    def test_back_to_back_is_allowed_without_buffer(self) -> None:
        slots = self.find(ev("2026-10-07T09:00:00-04:00", "2026-10-07T17:30:00-04:00"), max_results=5)
        self.assertEqual(starts(slots), ["2026-10-07T17:30"])

    def test_buffer_pushes_slots_away_from_events(self) -> None:
        e = ev("2026-10-07T09:00:00-04:00", "2026-10-07T17:30:00-04:00")
        self.assertEqual(self.find(e, buffer_minutes=15), [])  # 17:45 start leaves no 30 min before 18:00
        slots = self.find(ev("2026-10-07T09:00:00-04:00", "2026-10-07T17:00:00-04:00"), buffer_minutes=15)
        self.assertTrue(all(s["start"][:16] >= "2026-10-07T17:15" for s in slots))

    def test_duration_must_fit_the_gap(self) -> None:
        e1 = ev("2026-10-07T09:00:00-04:00", "2026-10-07T10:00:00-04:00")
        e2 = ev("2026-10-07T10:45:00-04:00", "2026-10-07T18:00:00-04:00")
        self.assertEqual(self.find(e1, e2, dur=60), [])
        self.assertEqual(starts(self.find(e1, e2, dur=45)), ["2026-10-07T10:00"])

    def test_declined_event_does_not_block(self) -> None:
        e = ev("2026-10-07T09:00:00-04:00", "2026-10-07T18:00:00-04:00", my_response="declined")
        self.assertTrue(self.find(e))

    def test_all_day_event_blocks_that_day_only(self) -> None:
        slots = self.find(ev("2026-10-07", "2026-10-08"), a="2026-10-07T00:00", b="2026-10-09T00:00")
        self.assertTrue(slots)
        self.assertTrue(all(s["start"].startswith("2026-10-08") for s in slots))

    def test_soonest_day_ranks_first(self) -> None:
        slots = self.find(a="2026-10-07T00:00", b="2026-10-10T00:00", max_results=3)
        self.assertTrue(slots[0]["start"].startswith("2026-10-07"))

    def test_lunch_and_edges_are_avoided_when_there_is_room(self) -> None:
        top = self.find(max_results=1)[0]["start"][11:16]
        self.assertNotIn(top, ("09:00", "12:00", "12:15", "12:30", "17:30"))
        self.assertTrue("09:30" <= top <= "11:30" or "13:00" <= top <= "16:30", top)

    def test_custom_working_hours(self) -> None:
        slots = self.find(working_hours="13:00-14:00", dur=30, max_results=5)
        self.assertEqual(sorted(starts(slots)), ["2026-10-07T13:00", "2026-10-07T13:30"])

    def test_weekend_window_is_empty_unless_allowed(self) -> None:
        a, b = "2026-10-10T00:00", "2026-10-12T00:00"  # Sat-Sun
        self.assertEqual(sch.find_slots(30, a, b, [], tz=NY), [])
        self.assertTrue(sch.find_slots(30, a, b, [], tz=NY, weekdays_only=False))

    def test_now_clips_the_start_of_the_window(self) -> None:
        now = sch.parse_dt("2026-10-07T13:10", NY)
        slots = sch.find_slots(30, "2026-10-07T00:00", "2026-10-08T00:00", [], tz=NY, now=now, max_results=20)
        self.assertTrue(all(s["start"][:16] >= "2026-10-07T13:15" for s in slots))

    def test_date_only_window_end_includes_that_day(self) -> None:
        slots = sch.find_slots(30, "2026-10-07", "2026-10-07", [], tz=NY)
        self.assertTrue(slots)

    def test_inverted_or_empty_window(self) -> None:
        self.assertEqual(sch.find_slots(30, "2026-10-08T00:00", "2026-10-07T00:00", [], tz=NY), [])

    def test_bad_duration(self) -> None:
        with self.assertRaises(ValueError):
            sch.find_slots(0, "2026-10-07", "2026-10-08", [], tz=NY)

    def test_max_results_is_respected(self) -> None:
        self.assertEqual(len(self.find(max_results=2)), 2)
        self.assertEqual(len(self.find(max_results=1)), 1)

    def test_results_carry_local_offset_and_label(self) -> None:
        s = self.find(max_results=1)[0]
        self.assertTrue(s["start"].endswith("-04:00"))
        self.assertIn("Wed Oct 7", s["label"])
        self.assertIn("–", s["label"])

    def test_dst_spring_forward_day(self) -> None:
        # Sunday 2026-03-08 is the change; use weekdays_only=False and a midnight-spanning range.
        slots = sch.find_slots(60, "2026-03-08T00:00", "2026-03-09T00:00", [], tz=NY, working_hours="0-24", weekdays_only=False, max_results=50)
        offs = {s["start"][-6:] for s in slots}
        self.assertEqual(offs, {"-05:00", "-04:00"})
        # No slot starts in the nonexistent 02:00-03:00 hour.
        self.assertFalse([s for s in slots if "T02:" in s["start"]])

    def test_dst_spring_working_hours_stay_nine_to_six_local(self) -> None:
        slots = sch.find_slots(30, "2026-03-09T00:00", "2026-03-10T00:00", [], tz=NY, max_results=100)  # Monday after the change
        self.assertTrue(all(s["start"].endswith("-04:00") for s in slots))
        self.assertEqual(min(s["start"][11:16] for s in slots), "09:00")
        self.assertEqual(max(s["end"][11:16] for s in slots), "18:00")

    def test_dst_fall_back_day_has_a_repeated_hour_not_a_double_booking(self) -> None:
        slots = sch.find_slots(60, "2026-11-01T00:00", "2026-11-02T00:00", [], tz=NY, working_hours="0-24", weekdays_only=False, max_results=100)
        ivs = sorted((s["start"], s["end"]) for s in slots)
        parsed = [(dt.datetime.fromisoformat(a), dt.datetime.fromisoformat(b)) for a, b in ivs]
        for (a1, b1), (a2, b2) in zip(parsed, parsed[1:]):
            self.assertLessEqual(b1, a2)

    def test_event_in_another_zone_blocks_correct_local_time(self) -> None:
        # 15:00Z is 11:00 in New York.
        slots = self.find(ev("2026-10-07T15:00:00Z", "2026-10-07T16:00:00Z"), max_results=50)
        for s in slots:
            self.assertFalse("11:00" <= s["start"][11:16] < "12:00", s)

    def test_other_timezone_for_the_user(self) -> None:
        tokyo = sch.get_tz("Asia/Tokyo")
        slots = sch.find_slots(30, "2026-10-07T00:00", "2026-10-08T00:00", [], tz=tokyo, max_results=1)
        self.assertTrue(slots[0]["start"].endswith("+09:00"))

    def test_unknown_timezone_falls_back_to_utc(self) -> None:
        self.assertIs(sch.get_tz("Not/AZone"), sch.UTC)
        self.assertIs(sch.get_tz(None), sch.UTC)


class ValidateChangesTests(unittest.TestCase):
    def test_accepts_the_three_ops_and_defaults_the_calendar(self) -> None:
        out = sch.validate_changes([
            {"op": "create", "summary": "A", "start": "2026-10-07T10:00", "end": "2026-10-07T11:00", "attendees": ["a@x.com"]},
            {"op": "update", "event_id": "e1", "start": "2026-10-07T12:00"},
            {"op": "delete", "event_id": "e2"}])
        self.assertEqual([c["calendar_id"] for c in out], ["primary"] * 3)

    def test_rejects_the_obvious(self) -> None:
        bad = [None, [], "x", [1], [{"op": "move"}], [{"op": "create", "start": "2026-10-07T10:00"}],
               [{"op": "create", "summary": "A"}], [{"op": "update", "summary": "A"}], [{"op": "delete"}],
               [{"op": "create", "summary": "A", "start": "nope"}],
               [{"op": "create", "summary": "A", "start": "2026-10-07T11:00", "end": "2026-10-07T10:00"}],
               [{"op": "create", "summary": "A", "start": "2026-10-07", "end": "2026-10-08T10:00"}],
               [{"op": "create", "summary": "A", "start": "2026-10-07T10:00", "attendees": ["not-an-email"]}],
               [{"op": "create", "summary": "A", "start": "2026-10-07T10:00", "attendees": "a@x.com"}],
               [{"op": "create", "summary": "A", "start": "2026-10-07T10:00", "conference": "yes"}],
               [{"op": "create", "summary": "A", "start": "2026-10-07T10:00", "send_updates": "everyone"}],
               [{"op": "create", "summary": "A", "start": "2026-10-07T10:00", "recurrence": "RRULE:FREQ=DAILY"}],
               [{"op": "create", "summary": "A", "start": "2026-10-07T10:00"}] * (sch.MAX_CHANGES + 1)]
        for b in bad:
            with self.assertRaises(ValueError, msg=repr(b)[:80]):
                sch.validate_changes(b)

    def test_unknown_keys_are_dropped(self) -> None:
        out = sch.validate_changes([{"op": "delete", "event_id": "e", "evil": "x", "id": "other"}])
        self.assertEqual(set(out[0]), {"op", "event_id", "calendar_id"})

    def test_delete_does_not_need_times(self) -> None:
        sch.validate_changes([{"op": "delete", "event_id": "e", "start": "garbage"}])

    def test_all_day_create_is_valid(self) -> None:
        sch.validate_changes([{"op": "create", "summary": "Trip", "start": "2026-10-07", "end": "2026-10-09"}])

    def test_change_fields_translates_for_google(self) -> None:
        f = sch.change_fields({"op": "create", "summary": "A", "start": "2026-10-07T10:00", "attendees": ["a@x.com"], "conference": True, "event_id": "zz"})
        self.assertEqual(f, {"summary": "A", "start": "2026-10-07T10:00", "attendees": [{"email": "a@x.com"}], "create_meet": True})


class ConflictTests(unittest.TestCase):
    def test_flags_overlap_with_existing_event(self) -> None:
        events = [ev("2026-10-07T10:00:00-04:00", "2026-10-07T11:00:00-04:00", id="a", summary="Standup")]
        out = sch.conflicts([{"op": "create", "summary": "N", "start": "2026-10-07T10:30:00-04:00", "end": "2026-10-07T11:30:00-04:00"}], events, NY)
        self.assertEqual(out, [{"index": 0, "with": "Standup"}])

    def test_touching_is_not_a_conflict(self) -> None:
        events = [ev("2026-10-07T10:00:00-04:00", "2026-10-07T11:00:00-04:00", id="a")]
        self.assertEqual(sch.conflicts([{"op": "create", "start": "2026-10-07T11:00:00-04:00", "end": "2026-10-07T12:00:00-04:00"}], events, NY), [])

    def test_moving_an_event_onto_its_own_old_slot_is_fine(self) -> None:
        events = [ev("2026-10-07T10:00:00-04:00", "2026-10-07T11:00:00-04:00", id="a")]
        self.assertEqual(sch.conflicts([{"op": "update", "event_id": "a", "start": "2026-10-07T10:30:00-04:00", "end": "2026-10-07T11:30:00-04:00"}], events, NY), [])

    def test_two_changes_colliding_with_each_other(self) -> None:
        cs = [{"op": "create", "start": "2026-10-07T10:00:00-04:00", "end": "2026-10-07T11:00:00-04:00"},
              {"op": "create", "start": "2026-10-07T10:30:00-04:00", "end": "2026-10-07T11:30:00-04:00"}]
        self.assertEqual(sch.conflicts(cs, [], NY), [{"index": 1, "with": "change 1"}])

    def test_deletes_and_declined_events_never_conflict(self) -> None:
        events = [ev("2026-10-07T10:00:00-04:00", "2026-10-07T11:00:00-04:00", id="a", my_response="declined")]
        cs = [{"op": "create", "start": "2026-10-07T10:00:00-04:00", "end": "2026-10-07T11:00:00-04:00"}, {"op": "delete", "event_id": "z"}]
        self.assertEqual(sch.conflicts(cs, events, NY), [])


if __name__ == "__main__":
    unittest.main()
