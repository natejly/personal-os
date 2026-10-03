"""Full-text search over message bodies (GET /conversations/search).

Run: PYTHONPATH=backend python -m pytest backend/tests/test_chat_search.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_runs as T  # noqa: E402  (sets the data dir)

from personal_os import app as app_mod  # noqa: E402

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


def test_job_transcripts_excluded() -> None:
    cid = conv("hiddenterm content")
    app_mod.convos.update(cid, {"settings": {"job_id": "j1"}})
    check(search("hiddenterm") == [], "job chat excluded")
