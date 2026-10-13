"""Embedding model auto-pick, the lock-on-vectors rule, migrations 41 and 42, and keyword expansion for recall."""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="embpick-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import HTTPException  # noqa: E402

from personal_os import app as appmod, migrations, provider_route, query_expand  # noqa: E402
from personal_os.db import Database  # noqa: E402
from personal_os.memory_index import MemoryIndex  # noqa: E402
from personal_os.repos import Graph, Memories  # noqa: E402

FW = {"provider": "fireworks", "baseUrl": "https://api.fireworks.ai/inference/v1"}
OA = {"provider": "openai", "baseUrl": "https://api.openai.com/v1"}
AN = {"provider": "anthropic", "baseUrl": "https://api.anthropic.com/v1"}


def _keys(**k: str) -> None:
    provider_route.set_key_source(lambda: k)


def test_auto_pick_by_keyed_provider() -> None:
    _keys(fireworks="k")
    assert provider_route.first_embedding({**FW, "apiKey": "k"}) == "qwen3-embedding-8b"
    _keys(openai="k")
    assert provider_route.first_embedding({**OA, "apiKey": "k"}) == "text-embedding-3-small"
    _keys(anthropic="k", openai="k2")  # the active provider has none: another keyed one serves
    assert provider_route.first_embedding({**AN, "apiKey": "k"}) == "text-embedding-3-small"
    _keys(anthropic="k")
    assert provider_route.first_embedding({**AN, "apiKey": "k"}) == ""
    assert not provider_route.can_embed({**AN, "apiKey": "k"}, "qwen3-embedding-8b")
    _keys(anthropic="k", fireworks="k2")
    assert provider_route.can_embed({**AN, "apiKey": "k"}, "qwen3-embedding-8b")


def test_local_provider_picks_a_listed_embedding_model() -> None:
    async def lister(_cfg: dict) -> list[dict]:
        return [{"id": "llama"}, {"id": "nomic-embed", "mode": "embedding"}]

    async def broken(_cfg: dict) -> list[dict]:
        raise RuntimeError("down")

    ollama = {"provider": "ollama", "baseUrl": "http://localhost:11434/v1"}
    assert asyncio.run(provider_route.local_embedding(ollama, lister)) == "nomic-embed"
    assert asyncio.run(provider_route.local_embedding(ollama, broken)) == ""
    assert asyncio.run(provider_route.local_embedding({**FW}, lister)) == ""


def test_ensure_saves_once_and_never_overrides() -> None:
    db = appmod.db
    with db.tx() as c:
        c.execute("DELETE FROM settings WHERE key='embeddingModel'")
    db.set_settings(FW)
    _keys(fireworks="k")
    appmod.ensure_embedding_model()
    assert db.get_settings()["embeddingModel"] == "qwen3-embedding-8b"
    db.set_settings({"embeddingModel": "mine"})
    appmod.ensure_embedding_model()
    assert db.get_settings()["embeddingModel"] == "mine"
    assert appmod.public_settings()["embeddingKeywordOnly"] is True  # "mine" is served by nobody


def test_lock_follows_the_index() -> None:
    db = appmod.db
    db.set_settings({"embeddingModel": "a-model"})
    with db.tx() as c:
        c.execute("DELETE FROM memory_vectors")
    appmod._lock_embedding({"embeddingModel": "b-model"})  # no vectors yet: allowed
    assert appmod.public_settings()["embeddingLocked"] is False
    with db.tx() as c:
        c.execute("INSERT INTO memories(id,content,kind,source,pinned,created_at,updated_at) VALUES('m1','x','fact','auto',0,1,1)")
        c.execute("INSERT INTO memory_vectors(memory_id,model,dim,vec,content_hash,updated_at) VALUES('m1','a-model',1,x'00',?,1)", ("h",))
    assert appmod.public_settings()["embeddingLocked"] is True
    try:
        appmod._lock_embedding({"embeddingModel": "b-model"})
        raise AssertionError("expected 409")
    except HTTPException as e:
        assert e.status_code == 409
    appmod._lock_embedding({"embeddingModel": "a-model"})  # unchanged is fine


def _con(saved: dict, vectors: str | None = None) -> sqlite3.Connection:
    d = Path(tempfile.mkdtemp(prefix="embpick-mig-"))
    db = Database(d)
    db.set_settings(saved)
    con = sqlite3.connect(next(d.glob("*.db")))
    if vectors:
        con.execute("CREATE TABLE IF NOT EXISTS memory_vectors(memory_id TEXT, model TEXT)")
        con.execute("INSERT INTO memory_vectors VALUES('m', ?)", (vectors,))
    return con


def _stored(con: sqlite3.Connection) -> dict:
    return {k: json.loads(v) for k, v in con.execute("SELECT key, value FROM settings")}


def test_migration_41_keeps_the_old_default_only_when_vectors_used_it() -> None:
    con = _con({}, "qwen3-embedding-8b")
    migrations._embedding_model_unset(con)
    assert _stored(con)["embeddingModel"] == "qwen3-embedding-8b"
    con = _con({}, "other")
    migrations._embedding_model_unset(con)
    assert "embeddingModel" not in _stored(con)
    con = _con({"embeddingModel": "x"}, "qwen3-embedding-8b")
    migrations._embedding_model_unset(con)
    assert _stored(con)["embeddingModel"] == "x"
    names = {m[0]: m[1] for m in migrations.MIGRATIONS}
    assert (names[41], names[42]) == ("embedding_model_unset", "tiers_replace_legacy_models")


def test_migration_42_folds_legacy_models_into_tiers() -> None:
    fw = {"provider": "fireworks", "baseUrl": "https://api.fireworks.ai/inference/v1"}
    same = "accounts/fireworks/models/deepseek-v4p1-flash"  # what both lower tiers give on Fireworks
    con = _con({**fw, "extractionModel": same, "fastModel": ""})
    migrations._tiers_replace_legacy_models(con)
    s = _stored(con)
    assert "extractionModel" not in s and "fastModel" not in s and "modelLow" not in s
    con = _con({**fw, "extractionModel": "custom-low", "fastModel": "custom-mid", "modelMedium": "set"})
    migrations._tiers_replace_legacy_models(con)
    s = _stored(con)
    assert s["modelLow"] == "custom-low" and "extractionModel" not in s  # tier was unset: copied
    assert s["fastModel"] == "custom-mid" and s["modelMedium"] == "set"  # tier differs: ambiguous, kept


def _index(expand) -> tuple[MemoryIndex, Memories]:
    db = Database(Path(tempfile.mkdtemp(prefix="embpick-idx-")))
    mem = Memories(db)
    idx = MemoryIndex(db, mem, Graph(db))
    idx.expand_fn = expand
    return idx, mem


def test_expansion_reaches_a_paraphrase_and_falls_back_on_error() -> None:
    calls: list[str] = []

    async def ok(_s: dict, q: str) -> list[str]:
        calls.append(q)
        return ["vehicle", "mechanic"]

    async def boom(_s: dict, q: str) -> list[str]:
        raise RuntimeError("model down")

    cfg = {"retrievalMode": "hybrid"}
    idx, mem = _index(ok)
    mem.create(None, "The mechanic fixed the vehicle brakes", "fact")
    hits = asyncio.run(idx.search_reranked(None, "car trouble", None, settings=cfg))
    assert [m["content"] for m in hits] == ["The mechanic fixed the vehicle brakes"]
    asyncio.run(idx.search_reranked(None, "car trouble", None, settings=cfg))
    assert calls == ["car trouble"]  # cached per query
    plain, mem2 = _index(boom)
    mem2.create(None, "The mechanic fixed the vehicle brakes", "fact")
    assert asyncio.run(plain.search_reranked(None, "car trouble", None, settings=cfg)) == []
    mem2.create(None, "My car is red", "fact")
    assert len(asyncio.run(plain.search_reranked(None, "car trouble", None, settings=cfg))) == 1  # plain FTS still works
    idx2, _ = _index(ok)
    assert asyncio.run(idx2.expansion({"retrievalMode": "bm25"}, "q")) == []


def test_parse_drops_the_querys_own_words() -> None:
    assert query_expand.parse("Car, vehicle, automobile\n- repair shop", "my car") == ["vehicle", "automobile", "repair shop"]
