"""Data-layer robustness: exact folder prefixes, zip bombs, board integrity, stale learn snapshots."""
from __future__ import annotations

import asyncio
import io
import json
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os import extract_text as et  # noqa: E402
from personal_os import learn, style as style_mod  # noqa: E402
from personal_os.boards import Boards  # noqa: E402
from personal_os.db import Database  # noqa: E402
from personal_os.docs import Docs  # noqa: E402
from personal_os.repos import Documents, Graph, Memories, Projects  # noqa: E402


def _folders(docs: Docs) -> set[str]:
    return {f["path"] for f in docs.folders() if f["scope"] == ""}


def test_rename_does_not_touch_wildcard_lookalikes() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        docs = Docs(Database(tmp))
        for p in ("my_docs/child", "myXdocs/child", "Work/x", "work/y"):
            docs.create_folder(p)
        a = docs.create("a", folder="my_docs/child")
        b = docs.create("b", folder="myXdocs/child")
        docs.rename_folder("my_docs", "mine")
        assert {"mine", "mine/child", "myXdocs", "myXdocs/child"} <= _folders(docs)
        assert docs.get(a["id"])["folder"] == "mine/child"
        assert docs.get(b["id"])["folder"] == "myXdocs/child"
        docs.rename_folder("work", "play")
        assert {"Work", "Work/x", "play", "play/y"} <= _folders(docs)


def test_delete_matches_exactly() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        docs = Docs(Database(tmp))
        for p in ("100%/x", "1000/x", "work/x", "Work/x"):
            docs.create_folder(p)
        keep1 = docs.create("k1", folder="1000/x")
        keep2 = docs.create("k2", folder="Work/x")
        gone = docs.create("g", folder="100%/x")
        docs.delete_folder("100%", delete_docs=True)
        docs.delete_folder("work", delete_docs=True)
        assert docs.get(gone["id"]) is None
        assert docs.get(keep1["id"]) and docs.get(keep2["id"])
        assert {"1000", "1000/x", "Work", "Work/x"} <= _folders(docs)


def test_revisions_negative_limit_clamped() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        docs = Docs(Database(tmp))
        d = docs.create("t")
        assert len(docs.revisions(d["id"], limit=-1)) <= 1


def test_docx_zip_bomb_refused(monkeypatch: Any) -> None:
    monkeypatch.setattr(et, "MAX_UNZIPPED_BYTES", 1000)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", "a" * 100_000)
    out = et.extract_text("x.docx", buf.getvalue())
    assert "No text could be extracted" in out
    assert et._parsed(".docx", "", b"not a zip") is None


def test_board_columns_must_belong_to_board() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        boards = Boards(Database(tmp))
        b1, b2 = boards.create("one"), boards.create("two")
        card = boards.add_card(b1["id"], None, "c")
        other_col = b2["columns"][0]["id"]
        with pytest.raises(ValueError):
            boards.move_card(card["id"], other_col)
        with pytest.raises(ValueError):
            boards.update_card(card["id"], {"column_id": other_col})
        with pytest.raises(ValueError):
            boards.add_card(b1["id"], other_col, "x")
        with pytest.raises(KeyError):
            boards.move_card(card["id"], "nope")
        with pytest.raises(KeyError):
            boards.move_card("nope", b1["columns"][0]["id"])
        with pytest.raises(KeyError):
            boards.add_card("nope", None, "x")
        ok = boards.move_card(card["id"], b1["columns"][1]["id"])
        assert ok["column_id"] == b1["columns"][1]["id"]
        assert boards.delete_column(b1["columns"][1]["id"]) == 1


def test_relearn_discards_when_hand_edited_during_call(monkeypatch: Any) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        st = style_mod.WritingStyle(Database(tmp))
        for i in range(4):
            st.add_sample(None, f"Sample number {i}. " + "I write short, plain sentences about my day and plans. " * 4, check=False)

        async def fake(settings: Any, model: str, messages: Any, kind: str = "") -> str:
            st.save_profile(None, {"summary": "mine", "guidelines": ["g"], "edited": 1})
            return json.dumps({"summary": "model", "guidelines": ["x"]})

        monkeypatch.setattr(style_mod.llm, "complete", fake)
        assert asyncio.run(st.relearn(settings={}, project_id=None, model="m")) is None
        assert st.profile(None)["summary"] == "mine"


def test_samples_negative_limit_clamped() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        st = style_mod.WritingStyle(Database(tmp))
        for i in range(3):
            st.add_sample(None, f"distinct sample {i} " * 20, check=False)
        assert len(st.samples(None, limit=-1)) == 1


def _learn(memories: Memories, graph: Graph, reply: Any, monkeypatch: Any, before=None) -> dict[str, Any]:
    async def fake(settings: Any, model: str, messages: Any, kind: str = "learn") -> str:
        if before:
            before()
        return json.dumps(reply)

    monkeypatch.setattr(learn.llm, "complete", fake)
    return asyncio.run(learn.learn_from_exchange(settings={}, memories=memories, graph=graph, project_id=None,
                                                 user_text="hi", assistant_text="yo", model="m"))


def test_learn_survives_non_strings_and_keeps_rest(monkeypatch: Any) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(tmp)
        memories, graph = Memories(db), Graph(db)
        out = _learn(memories, graph, {
            "updates": [{"id": 1, "content": 5}], "forget": [None, 3],
            "memories": [{"content": 42}, {"content": "User likes tea a lot"}],
            "entities": [{"label": 7}, {"label": "Tea", "type": 9}],
            "relations": [{"source": 1, "target": "Tea", "relation": "x"}],
        }, monkeypatch)
        assert [m["content"] for m in out["memories"]] == ["User likes tea a lot"]
        assert [n["label"] for n in out["nodes"]] == ["Tea"]


def test_learn_rechecks_pin_at_apply_time(monkeypatch: Any) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(tmp)
        memories, graph = Memories(db), Graph(db)
        m1 = memories.create(None, "User is learning French", kind="goal")
        m2 = memories.create(None, "User prefers dark mode", kind="preference")
        out = _learn(memories, graph, {"forget": ["M1", "M2"], "updates": [{"id": "M2", "content": "User prefers light mode"}]},
                     monkeypatch, before=lambda: (memories.update(m1["id"], {"pinned": True}), memories.update(m2["id"], {"pinned": True})))
        assert out["removed"] == [] and out["updated"] == []
        assert memories.get(m1["id"]) and memories.get(m2["id"])["content"] == "User prefers dark mode"


def test_project_delete_clears_fts_and_files() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(tmp)
        projects, documents, memories = Projects(db), Documents(db), Memories(db)
        p = projects.create("P")
        f = Path(tmp) / "up.txt"
        f.write_text("hello zebra")
        documents.create(p["id"], "up.txt", "text/plain", 5, str(f), "hello zebra")
        memories.create(p["id"], "zebra memory here")
        projects.delete(p["id"])
        assert not f.exists()
        with db.tx() as c:
            assert c.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0] == 0
            assert c.execute("SELECT COUNT(*) FROM memories_fts").fetchone()[0] == 0


def test_memory_update_refuses_blank() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        memories = Memories(Database(tmp))
        m = memories.create(None, "something real")
        with pytest.raises(ValueError):
            memories.update(m["id"], {"content": "   "})


def test_board_wip_limit_warns_without_blocking() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        boards = Boards(Database(tmp))
        b = boards.create("w")
        todo, doing = b["columns"][1]["id"], b["columns"][2]["id"]
        a = boards.add_card(b["id"], doing, "a")
        assert a["over_limit"] is False  # no limit set
        boards.update_column(doing, {"wip_limit": 1})
        assert boards.add_card(b["id"], doing, "b")["over_limit"] is True
        c = boards.add_card(b["id"], todo, "c")
        assert c["over_limit"] is False
        assert boards.move_card(c["id"], doing)["over_limit"] is True
