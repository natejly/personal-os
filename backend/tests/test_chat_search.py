"""Full-text search over message bodies (GET /conversations/search).

Run: PYTHONPATH=backend python -m pytest backend/tests/test_chat_search.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_runs as T  # noqa: E402  (sets the data dir)

from personal_os import app as app_mod, backups, migrations  # noqa: E402
from personal_os.db import Database  # noqa: E402

client, j, check = T.client, T.j, T.check

try:
    import pytest
except ImportError:
    pytest = None
else:
    @pytest.fixture(scope="module", autouse=True)
    def _portal():  # type: ignore[no-untyped-def]
        with client:
            yield


def conv(*bodies: str) -> str:
    cid = T.new_conv()
    for i, b in enumerate(bodies):
        app_mod.convos.add_message(cid, "user" if i % 2 == 0 else "assistant", b)
    return cid


def search(q: str) -> list[dict]:
    return client.get("/conversations/search", params={"q": q}).json()


def test_finds_words_with_snippet_and_prefix() -> None:
    cid = conv("how do I configure the zebrafish pipeline", "use the quokka flag")
    r = search("zebra")
    check([x["id"] for x in r] == [cid], f"prefix hit: {r}")
    check("\x02" in r[0]["snippets"][0]["text"], "match is marked")
    r = search("quokka flag")
    check(r and r[0]["id"] == cid and r[0]["snippets"][0]["role"] == "assistant", "AND of words")
    check(search("quokka missingword") == [], "AND excludes")


def test_a_silent_marker_row_is_not_a_hit() -> None:
    cid = conv("ask", "NO_REPLY", "ask again", "the okapiword answer")
    check(search("no_reply") == [], "marker-only row has no hit")
    r = search("okapiword")
    check([x["id"] for x in r] == [cid], f"real row still found: {r}")
    check(all("NO_REPLY" not in s["text"] for s in r[0]["snippets"]), "no marker in snippets")


def test_short_query_and_route_order() -> None:
    check(search("a") == [], "one char is empty")
    check(client.get("/conversations/search?q=zz").status_code == 200, "not matched as an id")


def test_edits_deletes_and_trash_are_reflected() -> None:
    cid = conv("alpacaterm here")
    mid = app_mod.convos.get(cid)["messages"][0]["id"]
    check(search("alpacaterm"), "indexed")
    with app_mod.db.tx() as c:
        c.execute("UPDATE messages SET content='llamaterm' WHERE id=?", (mid,))
    check(search("alpacaterm") == [] and search("llamaterm"), "update reindexes")
    with app_mod.db.tx() as c:
        c.execute("UPDATE messages SET superseded_at=1 WHERE id=?", (mid,))
    check(search("llamaterm") == [], "superseded excluded")
    cid2 = conv("vicunaterm here")
    j("DELETE", f"/conversations/{cid2}")
    check(search("vicunaterm") == [], "trashed excluded")


def test_cjk_falls_back_to_like() -> None:
    cid = conv("我们讨论了数据库迁移计划")
    r = search("数据库")
    check(r and r[0]["id"] == cid and "\x02数据库\x03" in r[0]["snippets"][0]["text"], f"cjk: {r}")


def test_like_fallback_marks_by_offset_not_by_lowercasing() -> None:
    cid = conv("in İstanbul today")  # lower() makes the capital I two characters; offsets must not shift
    r = search("İstanbul")
    check(r and r[0]["id"] == cid and r[0]["snippets"][0]["text"] == "in \x02İstanbul\x03 today", f"exact span: {r}")
    conv("a café here")
    r = search("Café")
    check(r and "\x02café\x03" in r[0]["snippets"][0]["text"], f"ascii-folded and marked: {r}")


def _integrity_ok(path: Path) -> bool:
    import sqlite3
    c = sqlite3.connect(path)
    try:
        c.execute("INSERT INTO messages_fts(messages_fts, rank) VALUES ('integrity-check', 1)")
        return True
    except sqlite3.DatabaseError:
        return False
    finally:
        c.close()


def test_backfill_purge_and_backup_keep_the_index_whole() -> None:
    cid = conv("backfillterm was said before the index existed")
    # A database from before step 2: drop the index and its triggers, rewind user_version, reopen.
    with app_mod.db.tx() as c:
        for t in ("messages_fts_ai", "messages_fts_ad", "messages_fts_au"):
            c.execute(f"DROP TRIGGER {t}")
        c.execute("DROP TABLE messages_fts")
        c.execute("PRAGMA user_version = 1")
    Database(app_mod.db.data_dir)
    with app_mod.db.tx() as c:
        check(migrations.current(c) == migrations.latest(), "migrated again")
    check([x["id"] for x in search("backfillterm")] == [cid], "pre-existing rows are searchable after the rebuild")
    # Purge removes the messages through the conversation's FK cascade; the delete trigger must follow.
    app_mod.trash.trash("conversation", cid)
    app_mod.trash.purge("conversation", cid)
    check(search("backfillterm") == [], "purged text no longer matches")
    check(_integrity_ok(app_mod.db.path), "live index matches the messages table")
    b = backups.create(app_mod.db.data_dir, "manual")
    check(_integrity_ok(backups.backup_dir(app_mod.db.data_dir) / b["name"]), "backup keeps rowids and the index")


def test_job_transcripts_excluded() -> None:
    cid = conv("hiddenterm content")
    app_mod.convos.update(cid, {"settings": {"job_id": "j1"}})
    check(search("hiddenterm") == [], "job chat excluded")


def test_archived_chat_is_found_and_marked() -> None:
    cid = conv("zebrafinch roost notes")
    app_mod.convos.update(cid, {"archived": True})
    hit = next(h for h in search("zebrafinch") if h["id"] == cid)
    assert hit["archived"] is True
