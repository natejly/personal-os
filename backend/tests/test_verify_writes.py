"""Post-write verification: every external write proves itself by reading the remote state back.

The point of these tests is the failure path. A write whose read-back cannot find it, or finds it
changed, must come back `unverified`/`mismatch`, and the tool layer must turn that into an error so
neither the UI nor the model can report success.

Run: python backend/tests/test_verify_writes.py
"""
from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fake_google_api import ApiError, FakeGoogle, FakeServer  # noqa: E402
from personal_os import tools, verify  # noqa: E402
from personal_os.tools import Toolbox  # noqa: E402


class _Naps:
    """Records the backoff instead of waiting it out."""

    def __init__(self) -> None:
        self.slept: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.slept.append(seconds)


class CheckTests(unittest.TestCase):
    """verify.check on its own: the three verdicts, and the bound on the retrying."""

    def test_verified_when_the_read_back_matches(self) -> None:
        v = verify.check("thing", lambda: {"summary": "Dentist"},
                         compare=lambda o: verify.diff({"summary": "Dentist"}, o), compared=["summary"])
        self.assertEqual(v["status"], verify.VERIFIED)
        self.assertEqual(v["attempts"], 1)
        self.assertTrue(verify.ok(v))

    def test_unverified_when_it_is_never_visible(self) -> None:
        naps = _Naps()
        v = verify.check("thing", _missing, delays=(0.5, 1.5), sleep=naps)
        self.assertEqual(v["status"], verify.UNVERIFIED)
        self.assertEqual(v["reason"], "not_visible")
        self.assertFalse(verify.ok(v))
        # Bounded: one attempt per delay plus the first, and no more waiting than the ladder.
        self.assertEqual(v["attempts"], 3)
        self.assertEqual(naps.slept, [0.5, 1.5])

    def test_eventual_consistency_is_not_a_failure(self) -> None:
        """Invisible on the first read, there on the second: verified, and told apart from a miss."""
        naps = _Naps()
        seen: list[int] = []

        def read_back() -> dict[str, Any]:
            seen.append(1)
            if len(seen) < 2:
                raise verify.NotVisible("not yet")
            return {"summary": "Dentist"}

        v = verify.check("thing", read_back, compare=lambda o: verify.diff({"summary": "Dentist"}, o),
                         delays=(0.5, 1.5), sleep=naps)
        self.assertEqual(v["status"], verify.VERIFIED)
        self.assertEqual(v["attempts"], 2)
        self.assertEqual(naps.slept, [0.5])

    def test_mismatch_is_reported_separately_from_not_visible(self) -> None:
        v = verify.check("thing", lambda: {"summary": "Dentist (moved)"},
                         compare=lambda o: verify.diff({"summary": "Dentist"}, o), compared=["summary"],
                         delays=(0.1,), sleep=_Naps())
        self.assertEqual(v["status"], verify.MISMATCH)
        self.assertEqual(v["reason"], "field_mismatch")
        self.assertEqual(v["differences"]["summary"], {"expected": "Dentist", "actual": "Dentist (moved)"})

    def test_a_read_that_keeps_failing_is_unverified_not_mismatched(self) -> None:
        def read_back() -> dict[str, Any]:
            raise ApiError(500, "backendError")

        v = verify.check("thing", read_back, delays=(0.1,), sleep=_Naps())
        self.assertEqual(v["status"], verify.UNVERIFIED)
        self.assertEqual(v["reason"], "read_failed")

    def test_delete_is_proved_by_absence(self) -> None:
        self.assertEqual(verify.check("thing", _missing, absent=True, sleep=_Naps())["status"], verify.VERIFIED)

    def test_a_delete_that_did_not_delete_is_a_mismatch(self) -> None:
        v = verify.check("thing", lambda: {"status": "confirmed"}, absent=True, delays=(0.1,), sleep=_Naps())
        self.assertEqual(v["status"], verify.MISMATCH)
        self.assertEqual(v["reason"], "still_present")

    def test_a_tombstone_counts_as_gone(self) -> None:
        v = verify.check("thing", lambda: {"status": "cancelled"}, absent=True,
                         gone_if=lambda e: e["status"] == "cancelled", sleep=_Naps())
        self.assertEqual(v["status"], verify.VERIFIED)

    def test_lists_compare_regardless_of_order(self) -> None:
        """A guest list in another order is the same guest list, not a mismatch."""
        self.assertEqual(verify.diff({"attendees": ["a@x.test", "b@x.test"]},
                                     {"attendees": ["b@x.test", "a@x.test"]}), {})
        self.assertIn("attendees", verify.diff({"attendees": ["a@x.test"]}, {"attendees": []}))

    def test_times_compare_as_instants_not_strings(self) -> None:
        """Google echoes a written time back in its own offset form; that is not a mismatch."""
        self.assertEqual(verify.diff({"start": "2026-10-07T19:00:00Z"},
                                     {"start": "2026-10-07T15:00:00-04:00"}, time_fields=("start",)), {})


def _missing() -> dict[str, Any]:
    raise verify.NotVisible("not there")


class GoogleWriteTests(unittest.TestCase):
    """The real google.py write methods, with the Google transport faked out."""

    def setUp(self) -> None:
        self.server = FakeServer()
        self.g = FakeGoogle(self.server)
        self._delays = verify.RETRY_DELAYS
        self._mail_delays = verify.MAIL_RETRY_DELAYS
        self._sleep = verify.SLEEP
        verify.RETRY_DELAYS = (0.0, 0.0)  # the ladder's shape is tested above; here it just must not wait
        verify.MAIL_RETRY_DELAYS = (0.0, 0.0, 0.0)
        verify.SLEEP = lambda s: None

    def tearDown(self) -> None:
        verify.RETRY_DELAYS = self._delays
        verify.MAIL_RETRY_DELAYS = self._mail_delays
        verify.SLEEP = self._sleep

    # ---- calendar ----
    def test_created_event_is_fetched_back_by_id(self) -> None:
        out = self.g.calendar_create({"summary": "Dentist", "start": "2026-10-07T15:00", "end": "2026-10-07T16:00"})
        self.assertTrue(out["verified"])
        self.assertEqual(out["verification"]["status"], verify.VERIFIED)
        self.assertIn("summary", out["verification"]["compared"])
        self.assertIn("start", out["verification"]["compared"])
        self.assertEqual(self.server.reads, [out["id"]])  # it really did re-read it

    def test_created_event_that_cannot_be_read_back_is_unverified(self) -> None:
        self.server.hide.add("ev1")
        out = self.g.calendar_create({"summary": "Dentist", "start": "2026-10-07T15:00"})
        self.assertFalse(out["verified"])
        self.assertEqual(out["verification"]["status"], verify.UNVERIFIED)
        self.assertEqual(out["verification"]["reason"], "not_visible")

    def test_created_event_stored_wrong_is_a_mismatch(self) -> None:
        self.server.corrupt["ev1"] = {"summary": "Something else"}
        out = self.g.calendar_create({"summary": "Dentist", "start": "2026-10-07T15:00"})
        self.assertFalse(out["verified"])
        self.assertEqual(out["verification"]["status"], verify.MISMATCH)
        self.assertIn("summary", out["verification"]["differences"])

    def test_a_slow_calendar_retries_within_the_bound(self) -> None:
        self.server.flaky["ev1"] = 1  # invisible once, then there
        out = self.g.calendar_create({"summary": "Dentist", "start": "2026-10-07T15:00"})
        self.assertTrue(out["verified"])
        self.assertEqual(out["verification"]["attempts"], 2)

    def test_update_verifies_only_the_fields_it_patched(self) -> None:
        made = self.g.calendar_create({"summary": "Dentist", "start": "2026-10-07T15:00"})
        self.server.reads.clear()
        out = self.g.calendar_update(made["id"], {"location": "Clinic"})
        self.assertTrue(out["verified"])
        self.assertIn("location", out["verification"]["compared"])
        self.assertNotIn("start", out["verification"]["compared"])

    def test_a_guest_list_patch_is_checked_against_the_server(self) -> None:
        made = self.g.calendar_create({"summary": "Sprint review", "start": "2026-10-08T10:00"})
        out = self.g.calendar_update(made["id"], {"attendees": [{"email": "mira@example.com"}]})
        self.assertTrue(out["verified"])
        self.assertIn("attendees", out["verification"]["compared"])
        self.server.corrupt[made["id"]] = {"attendees": []}  # the server quietly dropped the guests
        out = self.g.calendar_update(made["id"], {"attendees": [{"email": "mira@example.com"}]})
        self.assertEqual(out["verification"]["status"], verify.MISMATCH)
        self.assertIn("attendees", out["verification"]["differences"])

    def test_delete_is_verified_by_the_event_being_gone(self) -> None:
        made = self.g.calendar_create({"summary": "Dentist", "start": "2026-10-07T15:00"})
        out = self.g.calendar_delete(made["id"])
        self.assertTrue(out["verified"])
        self.assertEqual(out["verification"]["compared"], ["absent"])

    def test_a_delete_the_server_ignored_does_not_report_success(self) -> None:
        made = self.g.calendar_create({"summary": "Dentist", "start": "2026-10-07T15:00"})
        self.server.keep_deleted = True
        out = self.g.calendar_delete(made["id"])
        self.assertFalse(out["verified"])
        self.assertEqual(out["verification"]["reason"], "still_present")

    # ---- gmail ----
    def test_sent_mail_is_confirmed_in_sent(self) -> None:
        out = self.g.gmail_send("mira@example.com", "Hi", "Running late")
        self.assertTrue(out["verified"])
        self.assertIn("SENT label", out["verification"]["compared"])

    def test_mail_missing_from_sent_is_unverified(self) -> None:
        self.server.send_labels = []  # accepted, but never filed as sent
        out = self.g.gmail_send("mira@example.com", "Hi", "Running late")
        self.assertFalse(out["verified"])
        self.assertEqual(out["verification"]["reason"], "not_visible")

    def test_mail_the_mailbox_never_heard_of_is_unverified(self) -> None:
        self.server.hide.add("msg1")
        out = self.g.gmail_send("mira@example.com", "Hi", "Running late")
        self.assertFalse(out["verified"])
        self.assertEqual(out["verification"]["status"], verify.UNVERIFIED)

    def test_mail_stored_against_the_wrong_subject_is_a_mismatch(self) -> None:
        self.server.corrupt["msg1"] = {"payload": {"headers": [{"name": "To", "value": "mira@example.com"},
                                                               {"name": "Subject", "value": "Not what we sent"}]}}
        out = self.g.gmail_send("mira@example.com", "Hi", "Running late")
        self.assertEqual(out["verification"]["status"], verify.MISMATCH)
        self.assertIn("subject", out["verification"]["differences"])

    def test_a_draft_is_read_back(self) -> None:
        self.assertTrue(self.g.gmail_draft("mira@example.com", "Hi", "text")["verified"])

    def test_labels_are_read_back(self) -> None:
        sent = self.g.gmail_send("mira@example.com", "Hi", "text")
        out = self.g.gmail_modify(sent["sent"], star=True)
        self.assertTrue(out["verified"])
        self.assertIn("+STARRED", out["verification"]["compared"])

    # ---- tasks ----
    def test_task_writes_are_read_back(self) -> None:
        made = self.g.tasks_add("File the tax return", due="2026-10-31")
        self.assertTrue(made["verified"])
        done = self.g.tasks_complete(made["id"])
        self.assertTrue(done["verified"])
        gone = self.g.tasks_delete(made["id"])
        self.assertTrue(gone["verified"])

    def test_an_invisible_task_is_unverified(self) -> None:
        self.server.hide.add("task1")
        self.assertFalse(self.g.tasks_add("File the tax return")["verified"])


class ToolLayerTests(unittest.TestCase):
    """What the model is handed. An unproven write must not read as a success."""

    def setUp(self) -> None:
        self.server = FakeServer()
        self.g = FakeGoogle(self.server)
        self._delays, self._sleep = verify.RETRY_DELAYS, verify.SLEEP
        verify.RETRY_DELAYS, verify.SLEEP = (0.0,), lambda s: None
        self.box = Toolbox(None, None, None, lambda: {}, google=self.g)  # type: ignore[arg-type]

    def tearDown(self) -> None:
        verify.RETRY_DELAYS, verify.SLEEP = self._delays, self._sleep

    def _call(self, name: str, **args: Any) -> Any:
        return asyncio.run(self.box.call(name, args, {"project_id": None, "conversation_id": "c1"}))

    def test_a_verified_write_passes_through_clean(self) -> None:
        out = self._call("calendar_create", summary="Dentist", start="2026-10-07T15:00")
        self.assertIsNone(out.get("error"))
        self.assertTrue(out["verified"])

    def test_an_unverified_write_is_returned_as_an_error(self) -> None:
        self.server.hide.add("ev1")
        out = self._call("calendar_create", summary="Dentist", start="2026-10-07T15:00")
        self.assertIn("UNVERIFIED", out["error"])
        self.assertIn("do not", out["error"].lower())
        self.assertFalse(out["verified"])
        # The ids survive, so the model can say what to go and check.
        self.assertEqual(out["id"], "ev1")

    def test_a_mismatched_write_is_returned_as_an_error(self) -> None:
        self.server.corrupt["ev1"] = {"summary": "Something else"}
        out = self._call("calendar_create", summary="Dentist", start="2026-10-07T15:00")
        self.assertIn("UNVERIFIED", out["error"])
        self.assertIn("disagrees", out["error"])

    def test_the_error_tells_the_model_not_to_retry_the_write(self) -> None:
        self.server.hide.add("ev1")
        out = self._call("calendar_create", summary="Dentist", start="2026-10-07T15:00")
        self.assertIn("may well have landed", out["try_instead"])

    def test_the_verdict_is_on_the_result_the_journal_row_keeps(self) -> None:
        """tool_events store summarize_result(result), so the verdict is what the row records."""
        self.server.hide.add("ev1")
        out = self._call("calendar_create", summary="Dentist", start="2026-10-07T15:00")
        preview = tools.summarize_result(out)
        self.assertIn("unverified", preview)
        self.assertIn("not_visible", preview)


if __name__ == "__main__":
    unittest.main(verbosity=2)
