"""Teach a task: frame dedupe, the step draft's normalisation, the save path, and the Screen Recording gate."""
from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image  # noqa: E402

from personal_os import macos, skillbuild, teach  # noqa: E402
from personal_os.db import Database  # noqa: E402
from personal_os.learn import Skills  # noqa: E402


def _img(path: Path, dark_right: bool = False) -> Path:
    im = Image.new("RGB", (320, 200), (255, 255, 255))
    if dark_right:
        im.paste((0, 0, 0), (160, 0, 320, 200))
    im.save(path, "JPEG")
    return path


class FramesTest(unittest.TestCase):
    def test_dedupe_drops_consecutive_copies_only(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            a, b = _img(Path(d) / "a.jpg"), _img(Path(d) / "b.jpg")
            c, e = _img(Path(d) / "c.jpg", dark_right=True), _img(Path(d) / "e.jpg")
            self.assertEqual(teach.dedupe([a, b, c, e]), [a, c, e])

    def test_pick_spreads_and_keeps_ends(self) -> None:
        self.assertEqual(teach.pick(5), [0, 1, 2, 3, 4])
        p = teach.pick(100)
        self.assertEqual(len(p), teach.MODEL_FRAMES)
        self.assertEqual((p[0], p[-1]), (0, 99))

    def test_timeline_groups_focus(self) -> None:
        ev = [{"t": 10, "app": "Mail", "title": "Inbox", "frame": 1}, {"t": 11, "app": "Mail", "title": "Inbox", "frame": 2},
              {"t": 15, "app": "Sheets", "title": "Budget", "frame": 3}]
        self.assertEqual(teach.timeline_text(ev), "+0s Mail - Inbox (frames 1-2)\n+5s Sheets - Budget (frame 3)")
        self.assertEqual(teach.timeline_text([{"t": 0, "app": "", "title": "", "frame": 1}]), "")


class DraftTest(unittest.TestCase):
    def test_normalize(self) -> None:
        d = teach.normalize({"title": "  File  a receipt ", "inputs": [{"name": "vendor", "example": "Acme"}, {"name": ""}, "x"],
                             "steps": [{"action": "Open Mail", "frame": 2}, {"action": "  "}, "junk",
                                       {"action": "Save the PDF", "app": "Finder", "frame": True}]})
        self.assertEqual(d["title"], "File a receipt")
        self.assertEqual(d["inputs"], [{"name": "vendor", "example": "Acme"}])
        self.assertEqual([(s["n"], s["action"], s["frame"]) for s in d["steps"]], [(1, "Open Mail", 2), (2, "Save the PDF", None)])
        self.assertEqual(len(teach.normalize({"steps": [{"action": f"s{i}"} for i in range(40)]})["steps"]), teach.MAX_STEPS)
        self.assertEqual(teach.normalize(None)["steps"], [])

    def test_to_skill_is_lintable_and_leaves_examples_out(self) -> None:
        name, desc, proc = teach.to_skill({
            "title": "File a receipt", "goal": "when a receipt arrives by mail",
            "inputs": [{"name": "vendor", "example": "billing@acme.example"}],
            "steps": [{"app": "Mail", "action": "Open the receipt mail", "detail": "search for the vendor"},
                      {"app": "Finder", "action": "Save the attached PDF to Receipts"}]})
        self.assertIn("1. Open the receipt mail (in Mail): search for the vendor", proc)
        self.assertIn("2. Save the attached PDF to Receipts (in Finder)", proc)
        self.assertNotIn("acme", proc)
        findings = skillbuild.lint_skill(name, desc, proc, known_tools=set())
        self.assertEqual(skillbuild.blocking(findings), [])
        self.assertFalse([f for f in findings if f["code"] in ("unnumbered", "one_step", "concrete")])


class StoreTest(unittest.TestCase):
    def setUp(self) -> None:
        d = tempfile.TemporaryDirectory(prefix="teach-")
        self.addCleanup(d.cleanup)
        self.db = Database(Path(d.name))
        self.t = teach.Teach(self.db)
        self.saved = (macos.IS_MAC, macos.screen_recording_status, macos.frontmost_app,
                      macos.focused_window_title, macos.secure_input_active, teach.INTERVAL)
        self.addCleanup(self._restore)
        macos.IS_MAC = True
        macos.frontmost_app = lambda: ("Mail", "", 1)
        macos.focused_window_title = lambda pid: "Inbox"
        macos.secure_input_active = lambda: False
        teach.INTERVAL = 0.01

    def _restore(self) -> None:
        (macos.IS_MAC, macos.screen_recording_status, macos.frontmost_app,
         macos.focused_window_title, macos.secure_input_active, teach.INTERVAL) = self.saved

    def test_permission_gate_records_nothing(self) -> None:
        macos.screen_recording_status = lambda: macos.DENIED
        self.assertEqual(self.t.start(), {"needs_permission": True, "state": macos.DENIED})
        self.assertEqual(self.t.list(), [])
        self.assertIsNone(self.t._active)

    def test_record_dedupes_and_delete_removes_frames(self) -> None:
        macos.screen_recording_status = lambda: macos.GRANTED
        self.t._grab = lambda dest: bool(_img(dest))  # the same screen every tick
        row = self.t.start()
        time.sleep(0.2)
        done = self.t.stop()
        self.assertEqual(done["status"], "ready")
        self.assertEqual(done["frame_count"], 1, "identical screens with the same focus keep one frame")
        self.assertTrue(self.t.frame_path(row["id"], 1))
        self.assertIn("Mail - Inbox", teach.timeline_text(self.t.events(row["id"])))
        self.t.delete(row["id"])
        self.assertFalse(Path(row["dir"]).exists())
        self.assertIsNone(self.t.get(row["id"]))

    def test_steps_save_as_candidate(self) -> None:
        rid = self.t._create("import")["id"]
        row = self.t.set_steps(rid, {"title": "Weekly report", "steps": [{"action": "Open the sheet"}, {"action": "Export it"}]})
        self.assertEqual(row["steps"]["steps"][1]["n"], 2)
        name, desc, proc = teach.to_skill(row["steps"])
        s = Skills(self.db).propose(name, desc, proc, source="teach")
        self.assertEqual((s["status"], s["source"]), ("candidate", "teach"))
        self.assertEqual(self.t.attach(rid, skill_id=s["id"], status="saved")["skill_id"], s["id"])


if __name__ == "__main__":
    unittest.main()
