"""A user turn sent with uploaded files keeps them on its row, and the model reads their text inline, under a cap.

Runs under pytest, or directly: python backend/tests/test_message_attachments.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os.db import Database  # noqa: E402
from personal_os.repos import ATTACH_INLINE_CHARS, Conversations, Documents  # noqa: E402


def test_attachments_stored_and_inlined_for_the_model() -> None:
    with tempfile.TemporaryDirectory() as d:
        db = Database(d)
        convos, docs = Conversations(db), Documents(db)
        doc = docs.create(None, "ps2.pdf", "application/pdf", 10, "/x/ps2.pdf", "Problem Set #2 text")
        cid = convos.create(None, "New chat", "m")["id"]
        att = [{"id": doc["id"], "name": doc["name"], "mime": doc["mime"], "size": 10}]
        um = convos.add_message(cid, "user", "what is q1 asking", attachments=att)
        assert um["attachments"] == att
        # The row comes back with the files; the stored content is only what the user typed.
        row = convos.get(cid)["messages"][0]
        assert row["attachments"] == att and row["content"] == "what is q1 asking"
        # The model sees the typed text, then the file's text under its name.
        h = convos.history(cid)
        assert h[0]["content"].startswith("what is q1 asking\n\n<attached_file name=\"ps2.pdf\"")
        assert "Problem Set #2 text" in h[0]["content"]
        assert convos.history_rows(cid)[0]["content"] == h[0]["content"]
        assert convos.for_model(um) == h[0]["content"]
        # A files-only turn is still a turn (history skips empty rows otherwise).
        convos.add_message(cid, "user", "", attachments=att)
        assert len(convos.history(cid)) == 2
        assert convos.history(cid)[1]["content"].startswith("<attached_file")
        # A deleted file leaves its name, not its text; an unattached row is untouched.
        docs.delete(doc["id"])
        assert "Problem Set #2 text" not in convos.history(cid)[0]["content"]
        plain = convos.add_message(cid, "user", "plain")
        assert convos.for_model(plain) == "plain"


def test_long_file_is_capped_with_a_pointer() -> None:
    with tempfile.TemporaryDirectory() as d:
        db = Database(d)
        convos, docs = Conversations(db), Documents(db)
        doc = docs.create(None, "big.txt", "text/plain", 1, "/x/big.txt", "x" * (ATTACH_INLINE_CHARS + 500))
        cid = convos.create(None, "New chat", "m")["id"]
        convos.add_message(cid, "user", "summarize", attachments=[{"id": doc["id"], "name": "big.txt", "mime": "text/plain"}])
        body = convos.history(cid)[0]["content"]
        inlined = body[body.index(">\n") + 2:body.index("\n[... ")]
        assert inlined == "x" * ATTACH_INLINE_CHARS  # only the cap's worth of file text
        assert "500 more characters" in body and f"read_document id={doc['id']}" in body


def test_finish_message_keeps_the_files_a_reply_sent() -> None:
    with tempfile.TemporaryDirectory() as d:
        db = Database(d)
        convos, docs = Conversations(db), Documents(db)
        doc = docs.create(None, "shot.png", "image/png", 10, "/x/shot.png", "")
        cid = convos.create(None, "New chat", "m")["id"]
        att = [{"id": doc["id"], "name": "shot.png", "mime": "image/png", "size": 10}]
        am = convos.add_message(cid, "assistant", "", model="m")
        convos.finish_message(am["id"], "here", None, None, attachments=att)
        row = convos.get(cid)["messages"][0]
        assert row["attachments"] == att and row["content"] == "here"
        # A later finish without the kwarg (a steer's next segment, an error) leaves them as they were.
        convos.finish_message(am["id"], "here, done", None, None)
        assert convos.get(cid)["messages"][0]["attachments"] == att
        # They are files the assistant sent, not text for the model: replay carries the words only.
        assert convos.history(cid) == [{"role": "assistant", "content": "here, done"}]
        assert convos.for_model(convos.get(cid)["messages"][0]) == "here, done"
        # A reply that is only a file has nothing to replay.
        only = convos.add_message(cid, "assistant", "", model="m")
        convos.finish_message(only["id"], "", None, None, attachments=att)
        assert len(convos.history(cid)) == 1 and all(r["role"] == "assistant" and "attached_file" not in r["content"] for r in convos.history_rows(cid))


if __name__ == "__main__":
    test_attachments_stored_and_inlined_for_the_model()
    test_finish_message_keeps_the_files_a_reply_sent()
    test_long_file_is_capped_with_a_pointer()
    print("ok")
