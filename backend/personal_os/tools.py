"""Built-in tools the assistant can call, with a simple permission model.

Permission resolution for a tool in a chat:
  chat override ('on'|'off'|'inherit') → project override → global setting (bool, default on).
"""
from __future__ import annotations

import asyncio
import html
import json
import re
import time
from typing import Any, Awaitable, Callable

import httpx

from .learn import SELF_LABELS
from .repos import Documents, Graph, Memories
from .sandbox import run_python

ToolFn = Callable[..., Awaitable[Any]]


# danger levels: safe (read-only, in-app) · writes (in-app write) · network (reads the internet)
#                executes (sandboxed code) · external (writes to systems outside the app → asks by default)
DEFAULT_MODE = {"safe": "on", "writes": "on", "network": "on", "executes": "on", "external": "ask"}


class ToolSpec:
    def __init__(self, name: str, description: str, parameters: dict[str, Any], fn: ToolFn, group: str, danger: str = "safe"):
        self.name, self.description, self.parameters, self.fn, self.group, self.danger = name, description, parameters, fn, group, danger

    @property
    def default_mode(self) -> str:
        return DEFAULT_MODE.get(self.danger, "on")

    def schema(self) -> dict[str, Any]:
        return {"type": "function", "function": {"name": self.name, "description": self.description, "parameters": self.parameters}}

    def info(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description, "group": self.group, "danger": self.danger, "default_mode": self.default_mode}


def _obj(props: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": required}


class Toolbox:
    def __init__(self, memories: Memories, graph: Graph, documents: Documents, settings_fn: Callable[[], dict[str, Any]], todos: Any = None, google: Any = None, boards: Any = None, activity: Any = None):
        self.memories, self.graph, self.documents, self.settings = memories, graph, documents, settings_fn
        self.todos, self.google, self.boards, self.activity = todos, google, boards, activity
        self.specs: dict[str, ToolSpec] = {}
        self._register()
        if todos is not None:
            self._register_todos()
        if boards is not None:
            self._register_boards()
        if google is not None:
            self._register_google()
        if activity is not None:
            self._register_activity()

    def available(self, name: str) -> bool:
        """Some tools need an integration to be connected."""
        spec = self.specs.get(name)
        if spec and spec.group == "google":
            return bool(self.google and self.google.status()["connected"])
        return spec is not None

    # ---- permission model: mode per tool = on | ask | off ----
    @staticmethod
    def _norm(v: Any) -> str | None:
        if v is True:
            return "on"
        if v is False:
            return "off"
        return v if v in ("on", "ask", "off") else None

    def effective(self, global_tools: dict[str, Any], project_tools: dict[str, str] | None, chat_tools: dict[str, str] | None) -> dict[str, str]:
        """Resolve chat override → project override → global setting → tool default."""
        out: dict[str, str] = {}
        for name, spec in self.specs.items():
            v = self._norm(global_tools.get(name)) or spec.default_mode
            v = self._norm((project_tools or {}).get(name)) or v
            v = self._norm((chat_tools or {}).get(name)) or v
            out[name] = v
        return out

    def schemas(self, modes: dict[str, str]) -> list[dict[str, Any]]:
        return [s.schema() for n, s in self.specs.items() if modes.get(n) in ("on", "ask") and self.available(n)]

    def list(self) -> list[dict[str, Any]]:
        return [{**s.info(), "available": self.available(s.name)} for s in self.specs.values()]

    async def call(self, name: str, args: dict[str, Any], ctx: dict[str, Any]) -> Any:
        spec = self.specs.get(name)
        if not spec:
            return {"error": f"Unknown tool {name}"}
        try:
            return await spec.fn(ctx, **args)
        except TypeError as e:
            return {"error": f"Bad arguments: {e}"}
        except Exception as e:  # noqa: BLE001
            return {"error": f"{type(e).__name__}: {e}"}

    # ---- tool implementations ----
    def _register(self) -> None:
        R = self.specs.__setitem__

        async def search_documents(ctx: dict[str, Any], query: str, limit: int = 8) -> Any:
            hits = self.documents.search(ctx["project_id"], query, limit=min(int(limit), 20))
            return [{"document_id": h["document_id"], "document": h["name"], "chunk": h["idx"], "text": h["text"]} for h in hits]
        R("search_documents", ToolSpec("search_documents", "Full-text search over the user's uploaded documents (project knowledge + personal documents). Returns the best matching excerpts. Use it when the user asks about something that may be in their files.",
            _obj({"query": {"type": "string", "description": "Search terms or a short question"}, "limit": {"type": "integer", "default": 8}}, ["query"]), search_documents, "knowledge"))

        async def read_document(ctx: dict[str, Any], document_id: str, offset: int = 0, length: int = 6000) -> Any:
            d = self.documents.get(document_id)
            if not d:
                return {"error": "No such document"}
            text = d["text"]
            off = max(0, int(offset))
            return {"name": d["name"], "total_chars": len(text), "offset": off, "text": text[off: off + min(int(length), 20000)]}
        R("read_document", ToolSpec("read_document", "Read a slice of a document's full text by id (ids come from search_documents or the document list). Page through long documents with offset.",
            _obj({"document_id": {"type": "string"}, "offset": {"type": "integer", "default": 0}, "length": {"type": "integer", "default": 6000}}, ["document_id"]), read_document, "knowledge"))

        async def list_documents(ctx: dict[str, Any]) -> Any:
            return [{"document_id": d["id"], "name": d["name"], "chunks": d["chunk_count"], "scope": "project" if d["project_id"] else "personal"} for d in self.documents.list(ctx["project_id"])]
        R("list_documents", ToolSpec("list_documents", "List the documents available in this chat's scope.", _obj({}, []), list_documents, "knowledge"))

        async def search_memory(ctx: dict[str, Any], query: str) -> Any:
            return [{"id": m["id"], "content": m["content"], "kind": m["kind"], "scope": "project" if m["project_id"] else "personal"} for m in self.memories.list(ctx["project_id"], query)[:20]]
        R("search_memory", ToolSpec("search_memory", "Search what you remember about the user (long-term memory) for a topic.", _obj({"query": {"type": "string"}}, ["query"]), search_memory, "memory"))

        async def save_memory(ctx: dict[str, Any], content: str, kind: str = "fact", personal: bool = False) -> Any:
            m = self.memories.create(None if personal else ctx["project_id"], content, kind=kind, source="auto")
            ctx.setdefault("learned", {"memories": [], "nodes": [], "edges": []})["memories"].append(m)
            return {"saved": m["id"], "content": m["content"]}
        R("save_memory", ToolSpec("save_memory", "Explicitly remember something durable about the user (a fact, preference or goal) for future chats. Use when the user says 'remember that…' or shares something clearly worth keeping.",
            _obj({"content": {"type": "string", "description": "Third person, e.g. 'User prefers dark mode'"}, "kind": {"type": "string", "enum": ["fact", "preference", "goal", "note"], "default": "fact"},
                  "personal": {"type": "boolean", "description": "true = available in every chat, false = only this project", "default": False}}, ["content"]), save_memory, "memory", "writes"))

        async def graph_search(ctx: dict[str, Any], query: str) -> Any:
            sub = self.graph.neighborhood(ctx["project_id"], query, max_nodes=40)
            by_id = {n["id"]: n for n in sub["nodes"]}
            return {"entities": [{"id": n["id"], "label": n["label"], "type": n["type"], "properties": n["properties"]} for n in sub["nodes"]],
                    "relations": [f"{by_id[e['source_id']]['label']} -[{e['relation']}]-> {by_id[e['target_id']]['label']}" for e in sub["edges"]]}
        R("graph_search", ToolSpec("graph_search", "Find entities in the user's knowledge graph matching a query, with their direct relations (1 hop).", _obj({"query": {"type": "string"}}, ["query"]), graph_search, "graph"))

        async def graph_traverse(ctx: dict[str, Any], entity: str, depth: int = 2) -> Any:
            g = self.graph.get(ctx["project_id"])
            by_id = {n["id"]: n for n in g["nodes"]}
            start = next((n for n in g["nodes"] if n["label"].lower() == entity.strip().lower()), None) or next((n for n in g["nodes"] if entity.strip().lower() in n["label"].lower()), None)
            if not start:
                return {"error": f"No entity matching '{entity}'", "known": [n["label"] for n in g["nodes"]][:60]}
            seen, frontier, rels = {start["id"]}, {start["id"]}, []
            for _ in range(max(1, min(int(depth), 4))):
                nxt = set()
                for e in g["edges"]:
                    if e["source_id"] in frontier or e["target_id"] in frontier:
                        rels.append(f"{by_id[e['source_id']]['label']} -[{e['relation']}]-> {by_id[e['target_id']]['label']}")
                        nxt.update({e["source_id"], e["target_id"]})
                frontier = nxt - seen
                seen |= nxt
                if not frontier:
                    break
            return {"start": start["label"], "entities": [{"label": by_id[i]["label"], "type": by_id[i]["type"]} for i in seen if i in by_id][:80], "relations": sorted(set(rels))[:120]}
        R("graph_traverse", ToolSpec("graph_traverse", "Walk the knowledge graph outward from a named entity up to `depth` hops and return everything connected.",
            _obj({"entity": {"type": "string"}, "depth": {"type": "integer", "default": 2}}, ["entity"]), graph_traverse, "graph"))

        async def graph_add(ctx: dict[str, Any], source: str, relation: str, target: str, source_type: str = "entity", target_type: str = "entity") -> Any:
            if source.strip().lower() in SELF_LABELS or target.strip().lower() in SELF_LABELS:
                return {"error": "The user is not a graph entity. Store facts about the user with save_memory; use graph_add only for relations between named things (people, projects, tools...)."}
            s = self.graph.upsert_node(ctx["project_id"], source, source_type)
            t = self.graph.upsert_node(ctx["project_id"], target, target_type)
            e = self.graph.upsert_edge(ctx["project_id"], s["id"], t["id"], relation)
            learned = ctx.setdefault("learned", {"memories": [], "nodes": [], "edges": []})
            learned["nodes"] += [s, t]
            learned["edges"].append(e)
            return {"added": f"{s['label']} -[{e['relation']}]-> {t['label']}"}
        R("graph_add", ToolSpec("graph_add", "Add a relation (and the entities if new) to the knowledge graph.",
            _obj({"source": {"type": "string"}, "relation": {"type": "string"}, "target": {"type": "string"}, "source_type": {"type": "string", "default": "entity"}, "target_type": {"type": "string", "default": "entity"}}, ["source", "relation", "target"]), graph_add, "graph", "writes"))

        async def web_search(ctx: dict[str, Any], query: str, max_results: int = 6) -> Any:
            cfg = self.settings()
            n = max(1, min(int(max_results), 10))
            if cfg.get("braveApiKey"):
                async with httpx.AsyncClient(timeout=20) as c:
                    r = await c.get("https://api.search.brave.com/res/v1/web/search", params={"q": query, "count": n},
                                    headers={"X-Subscription-Token": cfg["braveApiKey"], "Accept": "application/json"})
                    r.raise_for_status()
                    return [{"title": w.get("title"), "url": w.get("url"), "snippet": w.get("description")} for w in r.json().get("web", {}).get("results", [])[:n]]
            if cfg.get("tavilyApiKey"):
                async with httpx.AsyncClient(timeout=25) as c:
                    r = await c.post("https://api.tavily.com/search", json={"api_key": cfg["tavilyApiKey"], "query": query, "max_results": n})
                    r.raise_for_status()
                    return [{"title": w.get("title"), "url": w.get("url"), "snippet": w.get("content")} for w in r.json().get("results", [])[:n]]
            # keyless fallback: DuckDuckGo
            from ddgs import DDGS

            def _ddg() -> list[dict[str, Any]]:
                with DDGS() as d:
                    return [{"title": r.get("title"), "url": r.get("href"), "snippet": r.get("body")} for r in d.text(query, max_results=n)]
            return await asyncio.to_thread(_ddg)
        R("web_search", ToolSpec("web_search", "Search the web for current information. Returns titles, URLs and snippets; call fetch_url to read a result in full.",
            _obj({"query": {"type": "string"}, "max_results": {"type": "integer", "default": 6}}, ["query"]), web_search, "web", "network"))

        async def fetch_url(ctx: dict[str, Any], url: str, max_chars: int = 12000) -> Any:
            async with httpx.AsyncClient(timeout=25, follow_redirects=True, headers={"User-Agent": "PersonalOS/0.1 (+desktop assistant)"}) as c:
                r = await c.get(url)
            ctype = r.headers.get("content-type", "")
            body = r.text
            text: str
            try:
                import trafilatura

                text = trafilatura.extract(body, output_format="markdown", include_links=False, include_tables=True) or ""
            except Exception:  # noqa: BLE001
                text = ""
            if not text:
                text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", body, flags=re.S | re.I)
                text = html.unescape(re.sub(r"<[^>]+>", " ", text))
                text = re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n\n", text)).strip()
            return {"url": str(r.url), "status": r.status_code, "content_type": ctype, "text": text[: max(1000, min(int(max_chars), 40000))], "truncated": len(text) > max_chars}
        R("fetch_url", ToolSpec("fetch_url", "Fetch a web page and return its main text as markdown.", _obj({"url": {"type": "string"}, "max_chars": {"type": "integer", "default": 12000}}, ["url"]), fetch_url, "web", "network"))

        async def run_python_tool(ctx: dict[str, Any], code: str, timeout: int = 30) -> Any:
            return await asyncio.to_thread(run_python, code, max(1, min(int(timeout), 120)))
        R("run_python", ToolSpec("run_python", "Run a Python 3 script in an isolated sandbox (no network, temp working dir, CPU/memory/time limits) and return stdout/stderr. Use for calculations, data wrangling, quick prototypes. Print what you want to see. numpy and matplotlib are installed: any figure saved with plt.savefig('name.png') is shown to the user inline (prefer a ```chart block for simple bar/line/pie charts of small data; use matplotlib for anything it can't express).",
            _obj({"code": {"type": "string"}, "timeout": {"type": "integer", "default": 30}}, ["code"]), run_python_tool, "code", "executes"))

        async def current_time(ctx: dict[str, Any]) -> Any:
            return {"iso": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "unix": int(time.time()), "timezone": time.strftime("%Z")}
        R("current_time", ToolSpec("current_time", "Get the current local date and time.", _obj({}, []), current_time, "utility"))


def summarize_result(result: Any, limit: int = 1500) -> str:
    """Short, JSON-ish preview of a tool result for the UI."""
    try:
        s = json.dumps(result, ensure_ascii=False)
    except (TypeError, ValueError):
        s = str(result)
    return s if len(s) <= limit else s[:limit] + "…"


# ---------------- todo + google tool registration ----------------
def _register_todos(self: Toolbox) -> None:
    R = self.specs.__setitem__

    async def todo_list(ctx: dict[str, Any], include_done: bool = False, all_projects: bool = False) -> Any:
        scope = "__all__" if all_projects else ctx["project_id"]
        items = self.todos.list(scope, include_done=include_done) if not all_projects else self.todos.list("__all__", include_done=include_done)
        if not all_projects and ctx["project_id"] is not None:
            items = self.todos.list(ctx["project_id"], include_done=include_done) + self.todos.list(None, include_done=include_done)
        return [{"id": t["id"], "title": t["title"], "due": t["due"], "priority": t["priority"], "done": bool(t["done"]), "notes": t["notes"][:200]} for t in items][:100]
    R("todo_list", ToolSpec("todo_list", "List the user's todos (open by default) in this chat's scope: the project's todos plus personal ones.",
        _obj({"include_done": {"type": "boolean", "default": False}, "all_projects": {"type": "boolean", "default": False}}, []), todo_list, "todos"))

    async def todo_add(ctx: dict[str, Any], title: str, due: str | None = None, notes: str = "", priority: int = 2, personal: bool = False) -> Any:
        t = self.todos.create(title, None if personal else ctx["project_id"], notes=notes, due=due, priority=priority)
        return {"id": t["id"], "title": t["title"], "due": t["due"]}
    R("todo_add", ToolSpec("todo_add", "Add a todo for the user. Dates as YYYY-MM-DD. Priority 1 (high) to 3 (low).",
        _obj({"title": {"type": "string"}, "due": {"type": "string"}, "notes": {"type": "string"}, "priority": {"type": "integer", "default": 2}, "personal": {"type": "boolean", "default": False}}, ["title"]), todo_add, "todos", "writes"))

    async def todo_update(ctx: dict[str, Any], id: str, done: bool | None = None, title: str | None = None, due: str | None = None, priority: int | None = None, notes: str | None = None) -> Any:
        patch = {k: v for k, v in {"done": done, "title": title, "due": due, "priority": priority, "notes": notes}.items() if v is not None}
        t = self.todos.update(id, patch)
        return t or {"error": "No such todo"}
    R("todo_update", ToolSpec("todo_update", "Update or complete a todo by id (from todo_list).",
        _obj({"id": {"type": "string"}, "done": {"type": "boolean"}, "title": {"type": "string"}, "due": {"type": "string"}, "priority": {"type": "integer"}, "notes": {"type": "string"}}, ["id"]), todo_update, "todos", "writes"))

    async def todo_delete(ctx: dict[str, Any], id: str) -> Any:
        t = self.todos.get(id)
        if not t:
            return {"error": "No such todo"}
        self.todos.delete(id)
        return {"deleted": t["title"]}
    R("todo_delete", ToolSpec("todo_delete", "Delete a todo permanently by id. Prefer todo_update(done=true) to complete; delete only when the user asks to remove it.", _obj({"id": {"type": "string"}}, ["id"]), todo_delete, "todos", "writes"))


def _register_google(self: Toolbox) -> None:
    R = self.specs.__setitem__
    g = self.google
    run = asyncio.to_thread

    async def calendar_events(ctx: dict[str, Any], days: int = 2, start: str | None = None) -> Any:
        return await run(g.calendar_events, days, "primary", 30, start)
    R("calendar_events", ToolSpec("calendar_events", "List upcoming Google Calendar events (default: next 2 days). `start` is an ISO datetime to look from.",
        _obj({"days": {"type": "integer", "default": 2}, "start": {"type": "string"}}, []), calendar_events, "google"))

    async def calendar_create(ctx: dict[str, Any], summary: str, start: str, end: str | None = None, description: str = "", location: str = "", attendees: list[str] | None = None) -> Any:
        return await run(g.calendar_create, summary, start, end, description, location, attendees)
    R("calendar_create", ToolSpec("calendar_create", "Create a Google Calendar event. Use ISO datetimes (YYYY-MM-DDTHH:MM) in the user's local time, or YYYY-MM-DD for all-day.",
        _obj({"summary": {"type": "string"}, "start": {"type": "string"}, "end": {"type": "string"}, "description": {"type": "string"}, "location": {"type": "string"}, "attendees": {"type": "array", "items": {"type": "string"}}}, ["summary", "start"]), calendar_create, "google", "external"))

    async def gmail_search(ctx: dict[str, Any], query: str = "is:unread newer_than:2d", max_results: int = 15) -> Any:
        return await run(g.gmail_search, query, max_results)
    R("gmail_search", ToolSpec("gmail_search", "Search Gmail with Gmail query syntax (e.g. 'is:unread', 'from:alice newer_than:7d', 'subject:invoice'). Returns headers and snippets.",
        _obj({"query": {"type": "string", "default": "is:unread newer_than:2d"}, "max_results": {"type": "integer", "default": 15}}, []), gmail_search, "google"))

    async def gmail_read(ctx: dict[str, Any], message_id: str) -> Any:
        return await run(g.gmail_get, message_id)
    R("gmail_read", ToolSpec("gmail_read", "Read the full body of an email by id (from gmail_search).", _obj({"message_id": {"type": "string"}}, ["message_id"]), gmail_read, "google"))

    async def gmail_draft(ctx: dict[str, Any], to: str, subject: str, body: str, reply_to_message_id: str | None = None) -> Any:
        return await run(g.gmail_draft, to, subject, body, reply_to_message_id)
    R("gmail_draft", ToolSpec("gmail_draft", "Create a Gmail draft (never sends). Prefer this over gmail_send unless the user explicitly asked to send.",
        _obj({"to": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"}, "reply_to_message_id": {"type": "string"}}, ["to", "subject", "body"]), gmail_draft, "google", "external"))

    async def gmail_send(ctx: dict[str, Any], to: str, subject: str, body: str) -> Any:
        return await run(g.gmail_send, to, subject, body)
    R("gmail_send", ToolSpec("gmail_send", "Send an email from the user's Gmail. Only when the user explicitly asked to send it.",
        _obj({"to": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"}}, ["to", "subject", "body"]), gmail_send, "google", "external"))

    async def gmail_modify(ctx: dict[str, Any], message_id: str, mark_read: bool | None = None, archive: bool = False, star: bool | None = None) -> Any:
        return await run(g.gmail_modify, message_id, mark_read, archive, star)
    R("gmail_modify", ToolSpec("gmail_modify", "Mark an email read/unread, star it, or archive it.",
        _obj({"message_id": {"type": "string"}, "mark_read": {"type": "boolean"}, "archive": {"type": "boolean", "default": False}, "star": {"type": "boolean"}}, ["message_id"]), gmail_modify, "google", "external"))

    async def gtasks_list(ctx: dict[str, Any], show_completed: bool = False) -> Any:
        return await run(g.tasks_list, "@default", show_completed)
    R("google_tasks_list", ToolSpec("google_tasks_list", "List the user's Google Tasks (default list).", _obj({"show_completed": {"type": "boolean", "default": False}}, []), gtasks_list, "google"))

    async def gtasks_add(ctx: dict[str, Any], title: str, notes: str = "", due: str | None = None) -> Any:
        return await run(g.tasks_add, title, notes, due)
    R("google_tasks_add", ToolSpec("google_tasks_add", "Add a task to Google Tasks (due as YYYY-MM-DD).", _obj({"title": {"type": "string"}, "notes": {"type": "string"}, "due": {"type": "string"}}, ["title"]), gtasks_add, "google", "external"))

    async def gtasks_complete(ctx: dict[str, Any], task_id: str) -> Any:
        return await run(g.tasks_complete, task_id)
    R("google_tasks_complete", ToolSpec("google_tasks_complete", "Mark a Google Task complete.", _obj({"task_id": {"type": "string"}}, ["task_id"]), gtasks_complete, "google", "external"))


def _register_boards(self: Toolbox) -> None:
    R = self.specs.__setitem__

    async def board_list(ctx: dict[str, Any], board: str | None = None) -> Any:
        if board:
            b = self.boards.find_board(board)
            if not b:
                return {"error": f"No board '{board}'", "boards": [x["name"] for x in self.boards.list()]}
            cols = {c["id"]: c["name"] for c in b["columns"]}
            return {"board": b["name"], "id": b["id"], "columns": [{"id": c["id"], "name": c["name"]} for c in b["columns"]],
                    "cards": [{"id": c["id"], "title": c["title"], "column": cols.get(c["column_id"]), "due": c["due"], "priority": c["priority"]} for c in b["cards"]]}
        return [{"id": b["id"], "name": b["name"], "cards": b["card_count"]} for b in self.boards.list()]
    R("board_list", ToolSpec("board_list", "List kanban boards, or the columns and cards of one board (by name or id).", _obj({"board": {"type": "string"}}, []), board_list, "boards"))

    async def board_add_card(ctx: dict[str, Any], board: str, title: str, column: str | None = None, description: str = "", due: str | None = None, priority: int = 2) -> Any:
        b = self.boards.find_board(board)
        if not b:
            return {"error": f"No board '{board}'"}
        col = next((c for c in b["columns"] if column and c["name"].lower() == column.lower()), None)
        card = self.boards.add_card(b["id"], col["id"] if col else None, title, description, due, priority)
        return {"added": card["title"], "id": card["id"], "column": (col or b["columns"][0])["name"]}
    R("board_add_card", ToolSpec("board_add_card", "Add a card to a kanban board (optionally into a named column).",
        _obj({"board": {"type": "string"}, "title": {"type": "string"}, "column": {"type": "string"}, "description": {"type": "string"}, "due": {"type": "string"}, "priority": {"type": "integer", "default": 2}}, ["board", "title"]), board_add_card, "boards", "writes"))

    async def board_move_card(ctx: dict[str, Any], board: str, card: str, column: str) -> Any:
        b = self.boards.find_board(board)
        if not b:
            return {"error": f"No board '{board}'"}
        c = next((x for x in b["cards"] if x["id"] == card or x["title"].lower() == card.lower()), None)
        col = next((x for x in b["columns"] if x["id"] == column or x["name"].lower() == column.lower()), None)
        if not c or not col:
            return {"error": "Card or column not found", "columns": [x["name"] for x in b["columns"]]}
        self.boards.move_card(c["id"], col["id"])
        return {"moved": c["title"], "to": col["name"]}
    R("board_move_card", ToolSpec("board_move_card", "Move a card (by title or id) to another column on a board.",
        _obj({"board": {"type": "string"}, "card": {"type": "string"}, "column": {"type": "string"}}, ["board", "card", "column"]), board_move_card, "boards", "writes"))

    async def board_create(ctx: dict[str, Any], name: str, columns: list[str] | None = None) -> Any:
        b = self.boards.create(name, ctx.get("project_id"), columns)
        return {"created": b["name"], "id": b["id"], "columns": [c["name"] for c in b["columns"]]}
    R("board_create", ToolSpec("board_create", "Create a new kanban board (default columns: Backlog, To do, In progress, Done).",
        _obj({"name": {"type": "string"}, "columns": {"type": "array", "items": {"type": "string"}}}, ["name"]), board_create, "boards", "writes"))


def _register_activity(self: Toolbox) -> None:
    R = self.specs.__setitem__

    async def activity_recent(ctx: dict[str, Any], hours: float = 8.0) -> Any:
        m = self.activity
        summaries = m.store.summaries(since=time.time() - max(0.25, float(hours)) * 3600, limit=40)
        return {
            "monitoring": m.running and not m.paused,
            "right_now": m.now_line(),
            "how_they_work": m.store.profile()["content"],
            "periods": [{"day": s["day"], "from": s["period_start"], "to": s["period_end"],
                         "headline": s["headline"], "summary": s["body"], "apps": s["apps"]} for s in summaries],
        }
    R("activity_recent", ToolSpec("activity_recent", "What the user has actually been doing on their computer recently, from the local activity monitor: a live line about the current window, the durable profile of how they work, and the summarized periods. Empty when the monitor is off. Use it when the user asks what they were doing, where their time went, or to ground advice in their real workflow.",
        _obj({"hours": {"type": "number", "default": 8}}, []), activity_recent, "activity"))

    async def activity_access(ctx: dict[str, Any]) -> Any:
        """Read-only: which macOS permissions the monitor has, so the assistant can answer "why is
        nothing being recorded?" without the user hunting through System Settings."""
        from . import activity as act

        rows = act.permissions()
        return {
            "signals_on": [k for k, v in (self.activity.config().get("signals") or {}).items() if v],
            "palantir_mode": bool(self.activity.config().get("palantir")),
            "permissions": [{"id": r["id"], "label": r["label"], "state": r["state"],
                             "gates": r["signals"], "fix": r["fix"]} for r in rows],
            "missing": [r["label"] for r in rows if not r["ok"]],
            "note": "Grants live in the Activity panel; macOS attributes them to the app bundle, "
                    "and the app has to be restarted after a grant for the keystroke tap to work.",
        }
    R("activity_access", ToolSpec("activity_access", "Which macOS permissions the activity monitor currently has (Accessibility, Input Monitoring, Screen Recording, browser Automation, Microphone, Full Disk Access), which signals each one gates, and what is missing. Use it when the user asks why the monitor is not recording something, or what access it has.",
        _obj({}, []), activity_access, "activity"))

    async def activity_insights(ctx: dict[str, Any], limit: int = 5) -> Any:
        """Read-only on purpose. The assistant may bring a suggestion up in conversation, but it
        cannot accept one on the user's behalf: applying is a button in the Activity panel."""
        return self.activity.insights.brief(limit=int(limit))
    R("activity_insights", ToolSpec("activity_insights", "The habits the activity monitor has noticed about how this person works, the patterns behind them, and the automation suggestions it has on offer but the user has not accepted yet. Use it when the user asks how they could save time, what you have noticed about their workflow, or what to automate - and when you are about to suggest a workflow change, so you can ground it in their real patterns instead of guessing. Read-only: never treat a suggestion as approved.",
        _obj({"limit": {"type": "integer", "default": 5}}, []), activity_insights, "activity"))

    async def activity_pause(ctx: dict[str, Any], minutes: float = 30.0) -> Any:
        return {"paused_until": self.activity.pause(minutes)["pause_until"]}
    R("activity_pause", ToolSpec("activity_pause", "Pause the activity monitor for a while, so nothing about the user's screen, typing or audio is recorded. Use it whenever the user asks you to stop watching.",
        _obj({"minutes": {"type": "number", "default": 30}}, []), activity_pause, "activity", "writes"))


Toolbox._register_todos = _register_todos  # type: ignore[attr-defined]
Toolbox._register_boards = _register_boards  # type: ignore[attr-defined]
Toolbox._register_google = _register_google  # type: ignore[attr-defined]
Toolbox._register_activity = _register_activity  # type: ignore[attr-defined]
