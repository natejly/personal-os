"""Assemble the context block injected into each chat turn, and record what was used."""
from __future__ import annotations

from typing import Any

from .repos import Documents, Graph, Memories
from .style import context_block as style_block


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
    skills: Any = None,
    page: dict[str, Any] | None = None,
    style: Any = None,
    meetings: Any = None,
) -> tuple[str, dict[str, Any]]:
    """Returns (system_prompt, context_used)."""
    parts: list[str] = [global_system_prompt.strip()] if global_system_prompt.strip() else []
    used: dict[str, Any] = {"memories": [], "nodes": [], "edges": [], "chunks": [], "project": None, "activity": None,
                            "skills": [], "page": None, "style": None, "meetings": None}

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

    # Procedural memory. Only skills the user approved by hand are ever injected, and the block says so
    # inside the prompt: a model-written procedure is data, never a second set of instructions.
    if skills is not None and conv_settings.get("useSkills", True):
        approved = [s for s in skills.list(status="approved", project_id=project_id) if (s["procedure"] or "").strip()]
        if approved:
            from .learn import MAX_INJECTED_SKILLS, MAX_MANIFEST_SKILLS, skill_block, skill_manifest

            # Progressive disclosure: past a size budget (or when the chat asks) the prompt carries an
            # index and the model reads a body with skill_view. Same invariant either way: approved rows only.
            block = skill_block(approved)
            mode = conv_settings.get("skillsDisclosure") or "auto"
            budget = int(settings.get("skillsInlineBudget", 6000) or 0)
            if mode == "manifest" or (mode == "auto" and len(block) > budget):
                parts.append(skill_manifest(approved))
                used["skills"] = [{"id": s["id"], "name": s["name"], "description": s["description"], "disclosure": "manifest"}
                                  for s in approved[:MAX_MANIFEST_SKILLS]]
            else:
                parts.append(block)
                used["skills"] = [{"id": s["id"], "name": s["name"], "description": s["description"]} for s in approved[:MAX_INJECTED_SKILLS]]

    # The user's own voice, for drafting on their behalf (see style.py). One profile per chat — the
    # project's when it has one — and the block itself tells the model not to *reply* in that voice.
    if style is not None and conv_settings.get("useStyle", True):
        profile = style.for_context(project_id)
        block = style_block(profile)
        if block:
            parts.append(block)
            used["style"] = {"project_id": profile["project_id"], "summary": profile["summary"],
                             "guidelines": profile["guidelines"], "block": block}

    # Observed computer activity. Off unless the user turned the monitor on, and skippable per chat
    # like every other context source.
    if activity is not None and conv_settings.get("useActivity", True):
        block = activity.context_block()
        if block:
            parts.append(block)
            used["activity"] = block

    # Recent meetings: titles and accepted notes, never raw transcript. Off per chat like the rest,
    # and empty until the user records something.
    if meetings is not None and conv_settings.get("useMeetings", True):
        block = meetings.context_block()
        if block:
            parts.append(block)
            used["meetings"] = block

    system = "\n\n".join(parts)
    used["system_prompt"] = system
    used["tokens_estimate"] = estimate_tokens(system)
    return system, used
