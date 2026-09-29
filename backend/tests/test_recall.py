"""Unit tests for the conversational-recall primitives. Run: backend/.venv/bin/python -m unittest discover backend/tests"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from personal_os.db import Database
from personal_os.recall import cosine, pack, unpack
from personal_os.repos import Conversations, Turns


class TempDb(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.db = Database(Path(self._dir.name) / "t.db")
        self.convos = Conversations(self.db)
        self.turns = Turns(self.db)

    def tearDown(self) -> None:
        self._dir.cleanup()


class TestVectorMath(unittest.TestCase):
    def test_pack_roundtrip(self) -> None:
        v = [0.5, -1.25, 3.0, 0.0]
        self.assertEqual(len(pack(v)), 16)
        for a, b in zip(unpack(pack(v)), v):
            self.assertAlmostEqual(a, b, places=6)

    def test_cosine(self) -> None:
        self.assertAlmostEqual(cosine([1, 0], [1, 0]), 1.0, places=6)
        self.assertAlmostEqual(cosine([1, 0], [0, 1]), 0.0, places=6)
        self.assertAlmostEqual(cosine([1, 0], [-1, 0]), -1.0, places=6)

    def test_cosine_degenerate(self) -> None:
        # Mismatched or zero vectors must score 0, not raise: a bad stored blob should not
        # take down a reply.
        self.assertEqual(cosine([], [1.0]), 0.0)
        self.assertEqual(cosine([1.0, 2.0], [1.0]), 0.0)
        self.assertEqual(cosine([0.0, 0.0], [0.0, 0.0]), 0.0)


class TestHistoryWindow(TempDb):
    def _conv_with(self, n_pairs: int, chars: int) -> str:
        conv = self.convos.create(None, "t", "m")
        for i in range(n_pairs):
            self.convos.add_message(conv["id"], "user", f"u{i} " + "x" * chars)
            self.convos.add_message(conv["id"], "assistant", f"a{i} " + "y" * chars)
        return conv["id"]

    def test_zero_budget_is_unlimited(self) -> None:
        cid = self._conv_with(5, 100)
        msgs, dropped = self.convos.history_window(cid, 0)
        self.assertEqual(len(msgs), 10)
        self.assertEqual(dropped, [])

    def test_drops_oldest_first_and_keeps_tail(self) -> None:
        # Each message ~400 chars => ~100 tokens. A 250-token budget fits about two.
        cid = self._conv_with(5, 400)
        msgs, dropped = self.convos.history_window(cid, 250)
        self.assertLess(len(msgs), 10)
        self.assertGreater(len(dropped), 0)
        self.assertEqual(len(msgs) + len(dropped), 10)
        # Whatever survives must be the most recent messages, in order.
        self.assertTrue(msgs[-1]["content"].startswith("a4"))
        all_msgs, _ = self.convos.history_window(cid, 0)
        self.assertEqual(msgs, all_msgs[-len(msgs):])

    def test_always_keeps_latest_even_when_oversized(self) -> None:
        cid = self._conv_with(1, 40_000)
        msgs, dropped = self.convos.history_window(cid, 10)
        self.assertEqual(len(msgs), 1)
        self.assertTrue(msgs[0]["content"].startswith("a0"))
        self.assertEqual(len(dropped), 1)

    def test_empty_conversation(self) -> None:
        conv = self.convos.create(None, "t", "m")
        msgs, dropped = self.convos.history_window(conv["id"], 100)
        self.assertEqual(msgs, [])
        self.assertEqual(dropped, [])


class TestTurns(TempDb):
    def test_add_and_lexical_search(self) -> None:
        conv = self.convos.create(None, "t", "m")
        am = self.convos.add_message(conv["id"], "assistant", "hi")
        self.turns.add(conv["id"], am["id"], "Postgres connection pooling limits",
                       ["postgres", "pgbouncer", "pool_size"], "User: ...\nAssistant: ...")
        hits = self.turns.search(conv["id"], "pgbouncer pool")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["message_id"], am["id"])
        self.assertEqual(self.turns.count(conv["id"]), 1)

    def test_reindex_replaces_instead_of_duplicating(self) -> None:
        conv = self.convos.create(None, "t", "m")
        am = self.convos.add_message(conv["id"], "assistant", "hi")
        self.turns.add(conv["id"], am["id"], "first summary", ["alpha"], "raw one")
        self.turns.add(conv["id"], am["id"], "second summary", ["beta"], "raw two")
        self.assertEqual(self.turns.count(conv["id"]), 1)
        self.assertEqual(self.turns.search(conv["id"], "alpha"), [])
        hits = self.turns.search(conv["id"], "beta")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["raw"], "raw two")

    def test_vectors_only_returns_embedded_rows(self) -> None:
        conv = self.convos.create(None, "t", "m")
        a1 = self.convos.add_message(conv["id"], "assistant", "1")
        a2 = self.convos.add_message(conv["id"], "assistant", "2")
        self.turns.add(conv["id"], a1["id"], "with vector", ["v"], "raw", pack([0.1, 0.2]))
        self.turns.add(conv["id"], a2["id"], "without vector", ["n"], "raw", None)
        rows = self.turns.vectors(conv["id"])
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(unpack(rows[0]["embedding"])[0], 0.1, places=6)

    def test_search_is_scoped_to_one_conversation(self) -> None:
        c1 = self.convos.create(None, "one", "m")
        c2 = self.convos.create(None, "two", "m")
        a1 = self.convos.add_message(c1["id"], "assistant", "x")
        a2 = self.convos.add_message(c2["id"], "assistant", "y")
        self.turns.add(c1["id"], a1["id"], "kubernetes ingress", ["kubernetes"], "raw1")
        self.turns.add(c2["id"], a2["id"], "kubernetes ingress", ["kubernetes"], "raw2")
        self.assertEqual(len(self.turns.search(c1["id"], "kubernetes")), 1)
        self.assertEqual(self.turns.search(c1["id"], "kubernetes")[0]["raw"], "raw1")

    def test_blank_query_returns_nothing(self) -> None:
        conv = self.convos.create(None, "t", "m")
        am = self.convos.add_message(conv["id"], "assistant", "hi")
        self.turns.add(conv["id"], am["id"], "something", ["thing"], "raw")
        self.assertEqual(self.turns.search(conv["id"], "   "), [])


if __name__ == "__main__":
    unittest.main()
