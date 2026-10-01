"""Auto-learn: after an exchange, extract memories and knowledge-graph triples with the LLM."""
from __future__ import annotations

import json
import re
from typing import Any

from . import llm
from .repos import Graph, Memories

EXTRACT_PROMPT = """You maintain a personal memory and knowledge graph for a user.
Given the latest exchange, extract durable, useful information and keep the existing memories current.

Return ONLY a JSON object with this shape:
{
  "memories": [{"content": "...", "kind": "fact|preference|goal|note"}],
  "updates": [{"id": "M3", "content": "...", "kind": "fact|preference|goal|note"}],
  "forget": ["M5"],
  "entities": [{"label": "...", "type": "person|project|organization|tool|place|concept|other"}],
  "relations": [{"source": "<entity label>", "target": "<entity label>", "relation": "short verb phrase"}]
}

Rules:
- Memories are about the USER (their life, work, preferences, goals, decisions) and must come from what the USER said. Write them in third person ("User prefers ..."). Skip anything already in the existing list.
- Durability test: only keep what will still matter in a month. Stable preferences, habits, identity facts, relationships, long-running goals and firm decisions pass; one-off requests, moods and session mechanics do not.
- Preferences are the highest-value memories and are often stated casually ("I hate long emails", "always use metric", "don't bother me before 10"). Capture them as kind "preference", staying faithful to what was said — never generalize beyond it.
- When the user contradicts, refines or restates an existing memory, return it in "updates" with that memory's id and the corrected content instead of adding a near-duplicate.
- Use "forget" only when the user explicitly retracts something or asks you to forget it.
- Do NOT turn the assistant's answer, or content that was merely retrieved from documents/notes, into memories. Only what the user revealed about themselves counts.
- Entities are concrete named things the user cares about (people, projects, tools, orgs, places, concepts); relations link them (e.g. "works on", "uses", "is friends with").
- Never create an entity for the user themselves ("User", "me", their name); facts about the user belong in memories instead.
- Return empty arrays when nothing durable was said. Never invent facts.
"""

KINDS = {"fact", "preference", "goal", "note"}


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
    # Tag existing memories with short stable ids the model can reference in "updates"/"forget".
    tagged = {f"M{i + 1}": m for i, m in enumerate(existing)}
    existing_list = "\n".join(f"[{tag}] ({m['kind']}) {m['content']}" for tag, m in tagged.items()) or "(none)"
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

    updated_memories = []
    for u in data.get("updates") or []:
        if not isinstance(u, dict):
            continue
        target = tagged.get(str(u.get("id") or "").strip())
        content = (u.get("content") or "").strip()
        if not target or len(content) < 6 or content == target["content"]:
            continue
        patch: dict[str, Any] = {"content": content}
        if u.get("kind") in KINDS:
            patch["kind"] = u["kind"]
        mem = memories.update(target["id"], patch)
        if mem:
            updated_memories.append(mem)

    removed_memories = []
    for fid in data.get("forget") or []:
        target = tagged.get(str(fid).strip())
        # Pinned memories are user-curated; the extractor may rewrite but never drop them.
        if target and not target["pinned"]:
            memories.delete(target["id"])
            removed_memories.append(target)

    added_memories = []
    for m in data.get("memories") or []:
        content = (m.get("content") if isinstance(m, dict) else str(m)) or ""
        if len(content.strip()) < 6:
            continue
        kind = m.get("kind", "fact") if isinstance(m, dict) else "fact"
        if kind not in KINDS:
            kind = "fact"
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

    return {"memories": added_memories, "updated": updated_memories, "removed": removed_memories,
            "nodes": added_nodes, "edges": added_edges}
