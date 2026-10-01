"""Writing style: the prose filter, sample bookkeeping, and what ends up in a prompt.

Run: PYTHONPATH=backend backend/.venv/bin/python -m unittest personal_os.tests.test_style -v

Two properties carry the feature. The filter has to keep "fix the bug in app.py" out of the samples,
or the profile learns to write like a ticket; and a failed or hand-edited profile must never be
silently replaced, or the user loses writing they curated.
"""
from __future__ import annotations

import asyncio
import tempfile
import unittest
from typing import Any

from personal_os import style as mod
from personal_os.context import build_context
from personal_os.db import Database
from personal_os.repos import Documents, Graph, Memories, Projects
from personal_os.style import WritingStyle, clean_profile, context_block, learn_style_from_exchange, looks_like_prose

PROSE = (
    "I spent the morning rewriting the onboarding email. The old one opened with three sentences of "
    "preamble before it got anywhere near the point, which is exactly the sort of thing I keep telling "
    "everyone else not to do. The new one asks for the thing in the first line and explains why "
    "afterwards. It is shorter and it reads like a person wrote it."
)
SECOND_PROSE = (
    "We should probably talk about the pricing page before Thursday. My worry is not the numbers, it is "
    "that the page makes you read two paragraphs before it tells you what anything costs. People do not "
    "read two paragraphs. They scan for a number, fail to find one, and close the tab."
)


def run(coro: Any) -> Any:
    return asyncio.run(coro)


class FilterTests(unittest.TestCase):
    def test_accepts_real_prose(self) -> None:
        self.assertTrue(looks_like_prose(PROSE))
        self.assertTrue(looks_like_prose(SECOND_PROSE))

    def test_rejects_short_instructions(self) -> None:
        for text in ("add a feature to learn writing style", "fix it", "what did I do yesterday?", ""):
            self.assertFalse(looks_like_prose(text), text)

    def test_rejects_long_single_sentence(self) -> None:
        self.assertFalse(looks_like_prose("please " + "rewrite this bit of the page and then " * 12))

    def test_rejects_code_and_markup(self) -> None:
        self.assertFalse(looks_like_prose("Here is the fix.\n\n```python\nprint(1)\n```\nIt works now, I think."))
        self.assertFalse(looks_like_prose(PROSE + "<div class='x'>" + PROSE))

    def test_rejects_logs_and_paths(self) -> None:
        log = "\n".join(f"2026-09-30T10:0{i}:00 ERROR personal_os.app request failed id=abc{i} status=500" for i in range(9))
        self.assertFalse(looks_like_prose(log))
        self.assertFalse(looks_like_prose(
            "Check these. I think the problem is in one of them. Probably the second one, honestly. "
            "/Users/someone/Desktop/Project/backend/personal_os/application_module.py "
            "/Users/someone/Desktop/Project/backend/personal_os/another_long_module_name.py "
            "/Users/someone/Desktop/Project/backend/personal_os/third_long_module_name.py"))

    def test_rejects_quoted_mail(self) -> None:
        quoted = "Thoughts on this? It seems off to me.\n" + "\n".join(f"> line {i} of what they wrote to me" for i in range(12))
        self.assertFalse(looks_like_prose(quoted))


class ProfileShapeTests(unittest.TestCase):
    def test_clean_profile_bounds_and_dedupes(self) -> None:
        out = clean_profile({
            "summary": "x" * 5000,
            "guidelines": ["be direct", "be direct", *[f"rule {i}" for i in range(40)]],
            "traits": {f"t{i}": f"v{i}" for i in range(40)},
            "phrases": ["quick one", 42],
            "avoid": ["no exclamation marks"],
        })
        self.assertEqual(len(out["summary"]), 1200)
        self.assertEqual(len(out["guidelines"]), mod.MAX_GUIDELINES)
        self.assertEqual(out["guidelines"][0], "be direct")
        self.assertEqual(out["guidelines"].count("be direct"), 1)
        self.assertEqual(len(out["traits"]), mod.MAX_TRAITS)
        self.assertEqual(out["phrases"], ["quick one", "42"])

    def test_clean_profile_survives_junk(self) -> None:
        out = clean_profile({"guidelines": "not a list", "traits": ["not a dict"]})
        self.assertEqual(out, {"summary": "", "guidelines": [], "traits": {}, "phrases": [], "avoid": []})

    def test_context_block_includes_voice_and_the_drafting_rule(self) -> None:
        block = context_block({
            "enabled": 1, "summary": "Writes short, direct sentences.", "guidelines": ["open with the ask"],
            "traits": {"formality": "low"}, "phrases": ["quick one"], "avoid": ["corporate jargon"],
        })
        self.assertIn("Writes short, direct sentences.", block)
        self.assertIn("- open with the ask", block)
        self.assertIn("formality: low", block)
        self.assertIn("quick one", block)
        # The whole point: the voice is for drafts, not for talking to the user.
        self.assertIn("Do not imitate it when you are speaking to the user", block)

    def test_context_block_empty_when_off_or_bare(self) -> None:
        self.assertEqual(context_block(None), "")
        self.assertEqual(context_block({"enabled": 0, "summary": "Writes well.", "guidelines": []}), "")
        self.assertEqual(context_block({"enabled": 1, "summary": "", "guidelines": [], "traits": {}}), "")


class StoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(self.tmp.name)
        self.style = WritingStyle(self.db)
        self.projects = Projects(self.db)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_sample_filter_applies_unless_waived(self) -> None:
        self.assertIsNone(self.style.add_sample(None, "fix the tests"))
        s = self.style.add_sample(None, "fix the tests", check=False)
        self.assertIsNotNone(s)
        self.assertEqual(self.style.stats(None), {"samples": 1, "chars": len("fix the tests"), "pending": 1})

    def test_same_ref_refreshes_one_sample(self) -> None:
        self.style.add_sample(None, PROSE, source="doc", ref="doc:1")
        self.style.add_sample(None, SECOND_PROSE, source="doc", ref="doc:1")
        rows = self.style.samples(None)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["text"], SECOND_PROSE)

    def test_window_keeps_the_newest_samples(self) -> None:
        for i in range(mod.MAX_SAMPLES_PER_SCOPE + 5):
            self.assertIsNotNone(self.style.add_sample(None, f"{PROSE} Sample number {i}."))
        rows = self.style.samples(None, limit=200)
        self.assertEqual(len(rows), mod.MAX_SAMPLES_PER_SCOPE)
        self.assertIn(f"Sample number {mod.MAX_SAMPLES_PER_SCOPE + 4}.", rows[0]["text"])

    def test_scopes_are_separate_and_project_wins(self) -> None:
        p = self.projects.create("Work")
        personal = self.style.save_profile(None, {"summary": "Personal voice.", "guidelines": ["be warm"]})
        self.assertEqual(self.style.for_context(None)["id"], personal["id"])
        self.assertEqual(self.style.for_context(p["id"])["id"], personal["id"])  # inherited
        work = self.style.save_profile(p["id"], {"summary": "Flatter at work.", "guidelines": ["no jokes"]})
        self.assertEqual(self.style.for_context(p["id"])["id"], work["id"])
        # A disabled project voice falls back to the personal one rather than to nothing.
        self.style.save_profile(p["id"], {"enabled": False})
        self.assertEqual(self.style.for_context(p["id"])["id"], personal["id"])

    def test_save_profile_patches_only_given_keys(self) -> None:
        self.style.save_profile(None, {"summary": "A.", "guidelines": ["one"], "traits": {"warmth": "high"}})
        self.style.save_profile(None, {"enabled": False})
        p = self.style.profile(None)
        self.assertEqual(p["summary"], "A.")
        self.assertEqual(p["guidelines"], ["one"])
        self.assertEqual(p["enabled"], 0)

    def test_delete_profile_can_keep_the_samples(self) -> None:
        self.style.add_sample(None, PROSE)
        self.style.save_profile(None, {"summary": "A."})
        self.style.delete_profile(None)
        self.assertIsNone(self.style.profile(None))
        self.assertEqual(self.style.stats(None)["samples"], 1)
        self.style.delete_profile(None, with_samples=True)
        self.assertEqual(self.style.stats(None)["samples"], 0)


class RelearnTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(self.tmp.name)
        self.style = WritingStyle(self.db)
        self.calls: list[list[dict[str, str]]] = []
        self.reply = '{"summary": "Direct and plain.", "guidelines": ["open with the ask"], "traits": {"formality": "low"}}'
        self.real_complete = mod.llm.complete

        async def fake_complete(settings: dict[str, Any], model: str, messages: list[dict[str, str]], kind: str = "learn") -> str:
            self.calls.append(messages)
            return self.reply

        mod.llm.complete = fake_complete  # type: ignore[assignment]

    def tearDown(self) -> None:
        mod.llm.complete = self.real_complete  # type: ignore[assignment]
        self.tmp.cleanup()

    def relearn(self, **kw: Any) -> Any:
        return run(self.style.relearn(settings={}, project_id=None, model="m", **kw))

    def test_first_sample_learns_then_waits_for_the_threshold(self) -> None:
        self.style.add_sample(None, PROSE)
        self.assertTrue(self.style.should_relearn(None))  # no profile yet: one sample is enough to start
        p = self.relearn()
        self.assertEqual(p["summary"], "Direct and plain.")
        self.assertEqual(p["sample_count"], 1)
        self.assertEqual(len(self.calls), 1)
        # Folded samples do not trigger another call, and the next one or two are not worth a model round.
        self.assertFalse(self.style.should_relearn(None))
        self.assertIsNone(self.relearn())
        self.style.add_sample(None, f"{SECOND_PROSE} One.")
        self.assertFalse(self.style.should_relearn(None))
        for i in range(mod.RELEARN_EVERY - 1):
            self.style.add_sample(None, f"{SECOND_PROSE} More {i}.")
        self.assertTrue(self.style.should_relearn(None))
        self.assertIsNotNone(self.relearn())
        self.assertEqual(len(self.calls), 2)

    def test_hand_edited_profile_is_not_overwritten_unless_forced(self) -> None:
        self.style.add_sample(None, PROSE)
        self.relearn()
        self.style.save_profile(None, {"summary": "My own words.", "edited": True})
        for i in range(mod.RELEARN_EVERY):
            self.style.add_sample(None, f"{SECOND_PROSE} Number {i}.")
        self.assertFalse(self.style.should_relearn(None))
        self.assertIsNone(self.relearn())
        self.assertEqual(self.style.profile(None)["summary"], "My own words.")
        forced = self.relearn(force=True)
        self.assertEqual(forced["summary"], "Direct and plain.")
        self.assertEqual(forced["edited"], 0)

    def test_unusable_model_output_leaves_the_profile_alone(self) -> None:
        self.style.add_sample(None, PROSE)
        self.relearn()
        self.reply = "I'm sorry, I can't do that."
        self.style.add_sample(None, f"{SECOND_PROSE} Again.")
        self.assertIsNone(self.relearn(force=True))
        self.assertEqual(self.style.profile(None)["summary"], "Direct and plain.")

    def test_nothing_to_learn_from(self) -> None:
        self.assertIsNone(self.relearn(force=True))
        self.assertEqual(self.calls, [])

    def test_exchange_hook_ignores_non_prose(self) -> None:
        banked = run(learn_style_from_exchange(settings={}, style=self.style, project_id=None,
                                              user_text="add a feature to learn writing style", model="m"))
        self.assertIsNone(banked)
        self.assertEqual(self.style.stats(None)["samples"], 0)
        self.assertEqual(self.calls, [])

    def test_exchange_hook_banks_prose_and_learns(self) -> None:
        banked = run(learn_style_from_exchange(settings={}, style=self.style, project_id=None, user_text=PROSE, model="m"))
        self.assertIsNotNone(banked)
        self.assertEqual(banked["profile"]["summary"], "Direct and plain.")
        self.assertEqual(self.style.stats(None)["samples"], 1)

    def test_analysis_prompt_carries_the_samples(self) -> None:
        self.style.add_sample(None, PROSE)
        self.relearn()
        sent = self.calls[0][1]["content"]
        self.assertIn(PROSE[:60], sent)


class ContextTests(unittest.TestCase):
    """build_context's half: the block lands in the prompt, and both switches can turn it off."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(self.tmp.name)
        self.style = WritingStyle(self.db)
        self.style.save_profile(None, {"summary": "Short, direct sentences.", "guidelines": ["open with the ask"]})
        self.kw: dict[str, Any] = {
            "memories": Memories(self.db), "graph": Graph(self.db), "documents": Documents(self.db),
            "project": None, "project_id": None, "query": "draft the email", "settings": {},
            "global_system_prompt": "You are the assistant.", "style": self.style,
        }

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_block_is_injected_and_recorded(self) -> None:
        system, used = build_context(conv_settings={}, **self.kw)
        self.assertIn(mod.STYLE_HEADER, system)
        self.assertIn("open with the ask", system)
        self.assertEqual(used["style"]["summary"], "Short, direct sentences.")

    def test_chat_can_opt_out(self) -> None:
        system, used = build_context(conv_settings={"useStyle": False}, **self.kw)
        self.assertNotIn(mod.STYLE_HEADER, system)
        self.assertIsNone(used["style"])

    def test_disabled_profile_is_not_injected(self) -> None:
        self.style.save_profile(None, {"enabled": False})
        system, used = build_context(conv_settings={}, **self.kw)
        self.assertNotIn(mod.STYLE_HEADER, system)
        self.assertIsNone(used["style"])

    def test_no_style_repo_is_fine(self) -> None:
        system, used = build_context(conv_settings={}, **{**self.kw, "style": None})
        self.assertNotIn(mod.STYLE_HEADER, system)
        self.assertIsNone(used["style"])


if __name__ == "__main__":
    unittest.main()
