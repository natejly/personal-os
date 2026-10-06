"""Built-in slash commands (/skill, /schedule, /loop) and skill import by URL.

Run: backend/.venv/bin/python backend/tests/test_slash_builtins.py
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os import commands, skillmd  # noqa: E402
from personal_os.db import Database  # noqa: E402
from personal_os.learn import Skills  # noqa: E402


class SlashBuiltins(unittest.TestCase):
    def setUp(self) -> None:
        d = tempfile.TemporaryDirectory(prefix="slash-")
        self.addCleanup(d.cleanup)
        self.db = Database(Path(d.name))
        self.skills = Skills(self.db)
        self.cmds = commands.Commands(self.db)
        s = self.skills.propose("Weekly review", "when the user asks for a weekly review", "1. Pull done todos.\n2. Summarise.", source="user")
        self.skills.update(s["id"], {"status": "approved"})
        self.skills.propose("Mail triage", "sort the inbox", "1. Read mail.", source="user")

    def test_skill_rides_under_the_turn_by_slug(self) -> None:
        out = commands.expand("/skill weekly-review for last week", self.cmds, self.skills)
        self.assertTrue(out.startswith("/skill weekly-review for last week"))
        self.assertIn("“Weekly review”", out)
        self.assertIn("1. Pull done todos.", out)

    def test_candidate_and_unknown_skills_show_no_procedure(self) -> None:
        out = commands.expand("/skill mail-triage now", self.cmds, self.skills)
        self.assertIn("waiting for approval", out)
        self.assertNotIn("1. Read mail.", out)
        out = commands.expand("/skill nope", self.cmds, self.skills)
        self.assertIn("No approved skill", out)
        self.assertIn("weekly-review", out)
        self.assertIn("without a name", commands.expand("/skill", self.cmds, self.skills))

    def test_schedule_and_loop_point_at_schedule_task(self) -> None:
        self.assertIn("schedule_task", commands.expand("/schedule tomorrow 9am, check mail", self.cmds))
        loop = commands.expand("/loop every 5 minutes check the deploy", self.cmds)
        self.assertIn("cron", loop)
        self.assertTrue(loop.startswith("/loop every 5 minutes"))

    def test_saved_commands_and_plain_text_are_unchanged_by_the_builtins(self) -> None:
        self.cmds.save("---\nname: standup\ndescription: d\n---\nSummarise $ARGUMENTS\n")
        self.assertIn("Summarise yesterday", commands.expand("/standup yesterday", self.cmds, self.skills))
        self.assertEqual(commands.expand("/unknown x", self.cmds, self.skills), "/unknown x")
        self.assertEqual(commands.expand("hello /skill", self.cmds, self.skills), "hello /skill")
        # A saved command named like a built-in is shadowed, as the composer's menu shadows it.
        self.cmds.save("---\nname: loop\ndescription: d\n---\nMINE\n")
        self.assertNotIn("MINE", commands.expand("/loop x", self.cmds, self.skills))
        hist = commands.expand_history([{"role": "user", "content": "/skill weekly-review"}, {"role": "assistant", "content": "ok"}], self.cmds, self.skills)
        self.assertIn("1. Pull done todos.", hist[0]["content"])
        self.assertEqual(hist[1]["content"], "ok")


class RawUrl(unittest.TestCase):
    def test_pages_folders_and_files(self) -> None:
        raw = "https://raw.githubusercontent.com/o/r/main/skills/x/SKILL.md"
        self.assertEqual(skillmd.raw_url("https://github.com/o/r/tree/main/skills/x"), raw)
        self.assertEqual(skillmd.raw_url("https://github.com/o/r/tree/main/skills/x/"), raw)
        self.assertEqual(skillmd.raw_url("https://github.com/o/r/blob/main/skills/x/SKILL.md"), raw)
        self.assertEqual(skillmd.raw_url(raw), raw)
        self.assertEqual(skillmd.raw_url("https://example.com/skills/x"), "https://example.com/skills/x/SKILL.md")
        self.assertEqual(skillmd.raw_url("https://example.com/a/skill.md"), "https://example.com/a/skill.md")


class FoldedFrontmatter(unittest.TestCase):
    def test_block_scalars_and_wrapped_lines(self) -> None:
        folded = "---\nname: nudge\ndescription: >\n  After you answer,\n  ask a question.\n\n  Once per chat.\nlicense: MIT\n---\nbody\n"
        p = skillmd.parse(folded)
        self.assertEqual(p["errors"], [])
        self.assertEqual(p["frontmatter"]["description"], "After you answer, ask a question. Once per chat.")
        self.assertEqual(p["frontmatter"]["license"], "MIT")
        literal = "---\nname: lit\ndescription: |-\n  line one\n  line two\n---\nbody\n"
        self.assertEqual(skillmd.parse(literal)["frontmatter"]["description"], "line one\nline two")
        wrapped = "---\nname: wrap\ndescription: starts here\n  and goes on\nmetadata:\n  author: me\n---\nbody\n"
        p = skillmd.parse(wrapped)
        self.assertEqual(p["errors"], [])
        self.assertEqual(p["frontmatter"]["description"], "starts here and goes on")
        self.assertEqual(p["frontmatter"]["metadata"], {"author": "me"})


if __name__ == "__main__":
    unittest.main()
