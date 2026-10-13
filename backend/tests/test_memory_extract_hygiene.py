"""Extractor hygiene: tolerant JSON, code-free reply, background context, event time, lexical judge fallback, batch dedupe."""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from personal_os import learn  # noqa: E402
from personal_os.db import Database  # noqa: E402
from personal_os.embed import Embedder  # noqa: E402
from personal_os.memory_index import MemoryIndex  # noqa: E402
from personal_os.repos import Conversations, Graph, Memories  # noqa: E402


class Env:
    def __init__(self, tmp: str) -> None:
        self.db = Database(tmp)
        self.memories, self.graph, self.convs = Memories(self.db), Graph(self.db), Conversations(self.db)
        self.prompts: list[str] = []
        self.reply: Any = {"memories": []}
        self.judge_reply: Any = {"decisions": []}
        self.judge_calls = 0

    def run(self, user_text: str = "hi", assistant_text: str = "ok", index: Any = None, **kw: Any) -> dict[str, Any]:
        async def fake_complete(settings: Any, model: str, messages: Any, kind: str = "learn", **_: Any) -> str:
            if messages[0]["content"].startswith(learn.JUDGE_PROMPT[:20]):
                self.judge_calls += 1
                self.judge_prompt = messages[1]["content"]
                return json.dumps(self.judge_reply)
            self.prompts.append(messages[1]["content"])
            return self.reply if isinstance(self.reply, str) else json.dumps(self.reply)

        learn.llm.complete = fake_complete  # restored by the fixture
        return asyncio.run(learn.learn_from_exchange(settings={}, memories=self.memories, graph=self.graph, project_id=None,
                                                     user_text=user_text, assistant_text=assistant_text, model="m", index=index, **kw))


@pytest.fixture()
def env():
    orig = learn.llm.complete
    with tempfile.TemporaryDirectory() as tmp:
        yield Env(tmp)
    learn.llm.complete = orig


def test_parse_json_tolerates_raw_control_chars() -> None:
    assert learn._parse_json('{"memories": [{"content": "line one\nline two\tx"}]}')["memories"][0]["content"] == "line one\nline two\tx"


def test_unparseable_reply_logs_reason(env: Env, caplog: Any) -> None:
    env.reply = "sorry, no json"
    with caplog.at_level("INFO", logger="personal_os"):
        assert env.run()["memories"] == []
    assert "reply not parseable" in caplog.text


def test_assistant_code_is_omitted_user_text_is_not(env: Env) -> None:
    env.run(user_text="```keep me```", assistant_text="Here:\n```py\nsecret_code()\n```\nand\n```unterminated\nmore_code()")
    p = env.prompts[0]
    assert "secret_code" not in p and "more_code" not in p and p.count("[code omitted]") == 2 and "keep me" in p


def test_background_is_fenced_filtered_and_capped(env: Env) -> None:
    c = env.convs.create(None, "t", "m")["id"]
    for i in range(6):
        env.convs.add_message(c, "user", f"earlier user {i}")
        env.convs.add_message(c, "assistant", f"earlier reply {i}")
    env.convs.add_message(c, "user", "hidden nudge", kind="nudge")
    cur = env.convs.add_message(c, "user", "that one, please")
    env.run(user_text="that one, please", conversation_id=c, message_id="a1", user_message_id=cur["id"])
    p = env.prompts[0]
    assert "Background, earlier in this chat (context only; never extract from it)" in p
    assert "hidden nudge" not in p and p.count("earlier user") + p.count("earlier reply") == 4 and "earlier reply 5" in p and "earlier user 0" not in p
    env.run(user_text="x", conversation_id=c, message_id="a1", user_message_id=cur["id"], user_only=True)
    assert "earlier reply" not in env.prompts[1] and "earlier user 5" in env.prompts[1]
    assert "Background" in learn.EXTRACT_PROMPT and "Never extract a memory from it" in learn.EXTRACT_PROMPT


def test_when_sets_valid_from_and_bad_values_keep_the_memory(env: Env) -> None:
    future = (date.today() + timedelta(days=30)).isoformat()
    env.reply = {"memories": [
        {"content": "User moved to Oslo in March 2025", "kind": "fact", "when": "2025-03-01"},
        {"content": "User plans to move to Rome", "kind": "fact", "when": future},
        {"content": "User owns a red bicycle", "kind": "fact", "when": "last spring"},
    ]}
    out = {m["content"]: m for m in env.run()["memories"]}
    assert len(out) == 3
    assert date.fromtimestamp(out["User moved to Oslo in March 2025"]["valid_from"]) == date(2025, 3, 1)
    for k in ("User plans to move to Rome", "User owns a red bicycle"):
        assert out[k]["valid_from"] == out[k]["created_at"]


def test_batch_near_duplicates_store_once(env: Env) -> None:
    env.reply = {"memories": [{"content": "User prefers short answers", "kind": "preference"},
                              {"content": "User prefers short answers.", "kind": "preference"}]}
    assert [m["content"] for m in env.run()["memories"]] == ["User prefers short answers"]


class NoEmbed(Embedder):
    def model(self, settings: Any) -> str:  # embeddings off
        return ""


def test_lexical_candidates_feed_the_judge_without_embeddings(env: Env) -> None:
    old = env.memories.create(None, "User wants short answers always", kind="preference")
    pinned = env.memories.create(None, "User wants short answers in email", kind="preference", pinned=True)
    unrelated = env.memories.create(None, "User owns a red bicycle")
    index = MemoryIndex(env.db, env.memories, env.graph, NoEmbed(None))
    env.reply = {"memories": [{"content": "User likes short answers", "kind": "preference"}]}
    env.judge_reply = {"decisions": [{"n": 1, "action": "update", "target_ids": ["C1"], "content": "User likes short answers always"}]}
    out = env.run(index=index)
    assert env.judge_calls == 1 and "short answers always" in env.judge_prompt
    assert "email" not in env.judge_prompt and "bicycle" not in env.judge_prompt  # pinned and unrelated rows never shown
    assert [m["content"] for m in env.memories.list(None) if m["id"] in (old["id"],)] == []  # superseded
    assert out["updated"][0]["content"] == "User likes short answers always"
    assert env.memories.get(pinned["id"])["invalid_at"] is None and env.memories.get(unrelated["id"])["invalid_at"] is None
