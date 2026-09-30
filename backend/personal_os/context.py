"""Assemble the context block injected into each chat turn, and record what was used."""
from __future__ import annotations

from typing import Any

from .repos import Documents, Graph, Memories


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


# The page block is the one context source the user can see for themselves, so it is capped rather
# than retrieved: a 200-page doc must not crowd out memory, graph and document excerpts.
PAGE_DETAIL_LIMIT = 6000
PAGE_SELECTION_LIMIT = 2000


def _clip(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[:limit] + "\n\n\u2026(truncated)"


def page_block(page: dict[str, Any]) -> str:
    """The 'what is on screen' block for a page-agent turn. Empty when the page said nothing useful."""
    label = str(page.get("label") or "").strip()
    if not label:
        return ""
    lines = [f"## What the user is looking at\nThe user asked this from the {label} screen of their Grain workspace.",
             "Answer about what is on that screen, and use your tools to act on it when they ask you to."]
    refs = [r for r in (page.get("refs") or []) if isinstance(r, dict) and r.get("id")]
    if refs:
        lines.append("Items on screen:\n" + "\n".join(
            f"- {r.get('kind', 'item')} `{r['id']}`" + (f" \u2014 {r['name']}" if r.get("name") else "") for r in refs[:40]))
    selection = _clip(str(page.get("selection") or ""), PAGE_SELECTION_LIMIT)
    if selection:
        lines.append("The user's current selection:\n```\n" + selection + "\n```")
    detail = _clip(str(page.get("detail") or ""), PAGE_DETAIL_LIMIT)
    if detail:
        lines.append("Screen contents:\n" + detail)
    return "\n\n".join(lines)


def build_context(
    *,
    memories: Memories,
    graph: Graph,
    documents: Documents,
    project: dict[str, Any] | None,
    project_id: str | None,
    query: str,
    settings: dict[str, Any],
    conv_settings: dict[str, Any],
    global_system_prompt: str,
    activity: Any = None,
    page: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Returns (system_prompt, context_used)."""
    parts: list[str] = [global_system_prompt.strip()] if global_system_prompt.strip() else []
    used: dict[str, Any] = {"memories": [], "nodes": [], "edges": [], "chunks": [], "project": None, "activity": None, "page": None}

    if project:
        used["project"] = {"id": project["id"], "name": project["name"]}
        parts.append(f"You are currently working in the project \"{project['name']}\"." + (f" {project['description']}" if project.get("description") else ""))
        if project.get("system_prompt", "").strip():
            parts.append(project["system_prompt"].strip())

    if page:
        block = page_block(page)
        if block:
            parts.append(block)
            used["page"] = page

    if conv_settings.get("useMemory", True):
        mems = memories.for_context(project_id, query)
        if mems:
            lines = [f"- {m['content']}" for m in mems]
            parts.append("## What you remember about the user\n" + "\n".join(lines))
            used["memories"] = [{"id": m["id"], "content": m["content"], "project_id": m["project_id"]} for m in mems]

    if conv_settings.get("useGraph", True):
        sub = graph.neighborhood(project_id, query)
        if sub["nodes"]:
            by_id = {n["id"]: n for n in sub["nodes"]}
            triples = [f"- {by_id[e['source_id']]['label']} —[{e['relation']}]→ {by_id[e['target_id']]['label']}" for e in sub["edges"]]
            ents = [f"- {n['label']} ({n['type']})" + (f": {n['properties']}" if n["properties"] else "") for n in sub["nodes"]]
            parts.append("## Knowledge graph (relevant entities)\n" + "\n".join(ents) + ("\n\nRelations:\n" + "\n".join(triples) if triples else ""))
            used["nodes"] = [{"id": n["id"], "label": n["label"], "type": n["type"]} for n in sub["nodes"]]
            used["edges"] = [{"id": e["id"], "relation": e["relation"], "source_id": e["source_id"], "target_id": e["target_id"]} for e in sub["edges"]]

    if conv_settings.get("useDocuments", True):
        hits = documents.search(project_id, query)
        if hits:
            blocks = [f"### {h['name']} (chunk {h['idx'] + 1})\n{h['text']}" for h in hits]
            parts.append("## Relevant document excerpts\n" + "\n\n".join(blocks))
            used["chunks"] = [{"chunk_id": h["chunk_id"], "document_id": h["document_id"], "name": h["name"], "idx": h["idx"], "text": h["text"][:400]} for h in hits]

    # Observed computer activity. Off unless the user turned the monitor on, and skippable per chat
    # like every other context source.
    if activity is not None and conv_settings.get("useActivity", True):
        block = activity.context_block()
        if block:
            parts.append(block)
            used["activity"] = block

    system = "\n\n".join(parts)
    used["system_prompt"] = system
    used["tokens_estimate"] = estimate_tokens(system)
    return system, used
