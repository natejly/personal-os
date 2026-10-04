"""Each [n] in a reply is checked against excerpt n with no model call: the best-matching excerpt sentence is the
quote, and a sentence the excerpt barely shares words with is 'weak'."""
from __future__ import annotations

import json
import tempfile

from personal_os.context import cite_check, cite_slim
from personal_os.db import Database
from personal_os.repos import Conversations

LEASE = ("This lease starts on the first of March. The tenant must give thirty days written notice before moving out. "
         "Rent is due on the fifth of each month.")


def ref(n: int, text: str) -> dict:
    return {"n": n, "chunk_id": f"c{n}", "document_id": "d", "name": f"doc{n}.txt", "idx": 0, "text": text}


def test_paraphrase_quotes_the_matching_sentence() -> None:
    refs = [ref(1, LEASE)]
    out = cite_check("You need to give thirty days of written notice before you move out [1].", refs)
    assert out[1] == {"quote": "The tenant must give thirty days written notice before moving out.", "support": "ok"}
    assert refs[0]["support"] == "ok" and refs[0]["quote"].startswith("The tenant")


def test_unrelated_sentence_is_weak() -> None:
    refs = [ref(1, LEASE)]
    assert cite_check("Zebras sleep standing up in the savanna [1].", refs)[1]["support"] == "weak"
    assert refs[0]["support"] == "weak"


def test_unknown_number_is_invalid_and_years_ignored() -> None:
    refs = [ref(1, LEASE), ref(2, "Other."), ref(3, "More.")]
    out = cite_check("Notice is thirty days [9]. Founded in [2024].", refs)
    assert out == {9: {"quote": "", "support": "invalid"}}
    assert all("support" not in r for r in refs)


def test_code_is_skipped() -> None:
    refs = [ref(1, LEASE)]
    assert cite_check("```\nrent due on the fifth [1]\n```\nAnd `x[1]` too.", refs) == {}
    assert "support" not in refs[0]


def test_quote_past_400_chars_survives_a_second_finish() -> None:
    # A parked card finishes the same row twice, and a steer's segments share one ledger: each finish must check
    # against the whole excerpt, not the 400-char prefix the previous one saved.
    long = "Filler words about nothing in particular. " * 12 + "The boiler is serviced every October by the landlord."
    assert len(long) > 400
    used = {"memories": [], "nodes": [], "chunks": [ref(1, long)]}
    reply = "The landlord services the boiler every October [1]."
    with tempfile.TemporaryDirectory() as d:
        convos = Conversations(Database(d))
        conv = convos.create(None, "t", "m")
        mids = [convos.add_message(conv["id"], "assistant", "")["id"] for _ in range(2)]
        convos.finish_message(mids[0], reply, None, used)
        convos.finish_message(mids[0], reply, None, used)  # parked: the same row again
        convos.finish_message(mids[1], reply, None, used)  # steered: the next segment, same ledger
        with convos.db.tx() as c:
            saved = [json.loads(c.execute("SELECT context_used FROM messages WHERE id=?", (m,)).fetchone()[0]) for m in mids]
    for row in saved:
        chunk = row["chunks"][0]
        assert chunk["quote"] == "The boiler is serviced every October by the landlord." and chunk["support"] == "ok"
        assert len(chunk["text"]) == 400
    assert used["chunks"][0]["text"] == long


def test_marker_before_the_claim_scores_the_whole_sentence() -> None:
    for reply in ("As stated in [1] the tenant must give thirty days written notice.",
                  "[1] says the tenant must give thirty days written notice."):
        refs = [ref(1, LEASE)]
        assert cite_check(reply, refs)[1]["support"] == "ok", reply


def test_event_copy_is_trimmed_and_live_ledger_is_not() -> None:
    used = {"chunks": [ref(1, "x" * 900)]}
    slim = cite_slim(used)
    assert len(slim["chunks"][0]["text"]) == 400 and len(used["chunks"][0]["text"]) == 900
    assert cite_slim(None) is None
