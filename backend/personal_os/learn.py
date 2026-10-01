"""Auto-learn: after an exchange, extract memories and knowledge-graph triples with the LLM."""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Callable

from . import llm
from .repos import Graph, Memories
from .trace import Tracer

log = logging.getLogger("personal_os")

EXTRACT_PROMPT = """You maintain a personal memory and knowledge graph for a user.
Given the latest exchange, extract durable, useful information.

Return ONLY a JSON object with this shape:
{
  "memories": [{"content": "...", "kind": "fact|preference|goal|note"}],
  "entities": [{"label": "...", "type": "person|project|organization|tool|place|concept|other"}],
  "relations": [{"source": "<entity label>", "target": "<entity label>", "relation": "short verb phrase"}]
}

Rules:
- Memories are about the USER (their life, work, preferences, goals, decisions) and must come from what the USER said. Write them in third person ("User prefers ..."). Skip trivia and anything already in the existing list.
- Do NOT turn the assistant's answer, or content that was merely retrieved from documents/notes, into memories. Only what the user revealed about themselves counts.
- Entities are concrete named things the user cares about (people, projects, tools, orgs, places, concepts); relations link them (e.g. "works on", "uses", "is friends with").
- Never create an entity for the user themselves ("User", "me", their name); facts about the user belong in memories instead.
- Return empty arrays when nothing durable was said. Never invent facts.
"""


SELF_LABELS = {"user", "the user", "me", "myself", "i"}


def _parse_json(text: str) -> dict[str, Any]:
    text = text.strip()
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except ValueError:
        return {}


async def learn_from_exchange(
    *,
    settings: dict[str, Any],
    memories: Memories,
    graph: Graph,
    project_id: str | None,
    user_text: str,
    assistant_text: str,
    model: str,
) -> dict[str, Any]:
    existing = memories.for_context(project_id, user_text, limit=60)
    existing_list = "\n".join(f"- {m['content']}" for m in existing) or "(none)"
    extraction_model = settings.get("extractionModel") or model
    messages = [
        {"role": "system", "content": EXTRACT_PROMPT},
        {
            "role": "user",
            "content": f"Existing memories:\n{existing_list}\n\n---\nUser said:\n{user_text[:4000]}\n\nAssistant replied:\n{assistant_text[:3000]}",
        },
    ]
    raw = await llm.complete(settings, extraction_model, messages)
    data = _parse_json(raw)

    added_memories = []
    for m in data.get("memories") or []:
        content = (m.get("content") if isinstance(m, dict) else str(m)) or ""
        if len(content.strip()) < 6:
            continue
        kind = m.get("kind", "fact") if isinstance(m, dict) else "fact"
        before = {x["id"] for x in memories.list(project_id, include_global=False)}
        mem = memories.create(project_id, content, kind=kind, source="auto")
        if mem["id"] not in before:
            added_memories.append(mem)

    label_to_id: dict[str, str] = {}
    added_nodes = []
    for e in data.get("entities") or []:
        label = (e.get("label") if isinstance(e, dict) else str(e)) or ""
        if not label.strip() or label.strip().lower() in SELF_LABELS:
            continue
        node = graph.upsert_node(project_id, label, type=(e.get("type") if isinstance(e, dict) else "entity") or "entity")
        label_to_id[label.strip().lower()] = node["id"]
        added_nodes.append(node)

    added_edges = []
    for r in data.get("relations") or []:
        if not isinstance(r, dict):
            continue
        s, t, rel = (r.get("source") or "").strip(), (r.get("target") or "").strip(), (r.get("relation") or "").strip()
        if not (s and t and rel) or s.lower() in SELF_LABELS or t.lower() in SELF_LABELS:
            continue
        sid = label_to_id.get(s.lower()) or graph.upsert_node(project_id, s)["id"]
        tid = label_to_id.get(t.lower()) or graph.upsert_node(project_id, t)["id"]
        if sid == tid:
            continue
        added_edges.append(graph.upsert_edge(project_id, sid, tid, rel))

    return {"memories": added_memories, "nodes": added_nodes, "edges": added_edges}


@dataclass
class LearnJob:
    """One finished exchange, waiting to be mined. Everything is a snapshot: the chat has moved on."""

    conversation_id: str
    message_id: str
    project_id: str | None
    user_text: str
    assistant_text: str
    model: str
    settings: dict[str, Any]
    spans: list[dict[str, Any]]


class LearnWorker:
    """Auto-learn, off the reply's critical path.

    Extraction is another LLM call. Running it inside the chat generator kept the run alive past
    `done`: the conversation stayed 409-locked against the next message and its SSE stayed open, so
    a reply the user could already read still counted as busy. Jobs are queued here instead and
    drained by one task, serially — a burst of replies must not fan out into a burst of extraction
    calls — and the results reach the UI on the app topic, which outlives any run.
    """

    def __init__(
        self,
        *,
        memories: Memories,
        graph: Graph,
        set_trace: Callable[[str, list[dict[str, Any]]], None],
        publish: Callable[[str, Any], None],
        depth: int = 32,
    ) -> None:
        self._memories = memories
        self._graph = graph
        self._set_trace = set_trace
        self._publish = publish
        self._q: asyncio.Queue[LearnJob] = asyncio.Queue(depth)
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._drain(), name="auto-learn")

    async def stop(self) -> None:
        """Drop what is still queued and cancel the one in flight; nothing here is worth a wait."""
        task, self._task = self._task, None
        if task and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    def submit(self, job: LearnJob) -> bool:
        """Never blocks and never raises: a reply must not fail over its own bookkeeping."""
        self.start()  # a worker that died on an unexpected error comes back with the next reply
        try:
            self._q.put_nowait(job)
            return True
        except asyncio.QueueFull:
            log.warning("auto-learn queue full; dropping message %s", job.message_id)
            return False

    async def _drain(self) -> None:
        while True:
            job = await self._q.get()
            try:
                await self._run(job)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - one bad exchange must not take the worker down
                log.exception("auto-learn failed for message %s", job.message_id)
            finally:
                self._q.task_done()

    async def _run(self, job: LearnJob) -> None:
        tracer = Tracer(job.spans)
        span = tracer.start("learn", job.settings.get("extractionModel") or job.model)
        try:
            learned = await learn_from_exchange(
                settings=job.settings, memories=self._memories, graph=self._graph,
                project_id=job.project_id, user_text=job.user_text,
                assistant_text=job.assistant_text, model=job.model,
            )
            tracer.end(span, {"memories": len(learned["memories"]), "entities": len(learned["nodes"]),
                              "relations": len(learned["edges"])})
            if learned["memories"] or learned["nodes"] or learned["edges"]:
                self._publish("learned", {"conversation_id": job.conversation_id,
                                          "message_id": job.message_id, **learned})
        except asyncio.CancelledError:
            tracer.end(span, error="Cancelled")  # shutdown: keep the trace honest about the gap
            self._set_trace(job.message_id, tracer.spans)
            raise
        except Exception as e:  # noqa: BLE001
            tracer.end(span, error=str(e))
            self._publish("learn_error", {"conversation_id": job.conversation_id,
                                          "message_id": job.message_id, "message": str(e)})
        self._set_trace(job.message_id, tracer.spans)
