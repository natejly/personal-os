"""Auto-learn: after an exchange, extract memories and knowledge-graph triples with the LLM."""
from __future__ import annotations

import json
import re
from typing import Any

from . import llm
from .repos import Graph, Memories

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
