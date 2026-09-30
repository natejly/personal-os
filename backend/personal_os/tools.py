"""Built-in tools the assistant can call, with a simple permission model.

Permission resolution for a tool in a chat:
  chat override ('on'|'off'|'inherit') → project override → global setting (bool, default on).
"""
from __future__ import annotations

import asyncio
import html
import ipaddress
import json
import logging
import re
import socket
import time
import urllib.parse
from typing import Any, Awaitable, Callable

import httpx

from .learn import SELF_LABELS
from .repos import Documents, Graph, Memories
from .sandbox import run_python

ToolFn = Callable[..., Awaitable[Any]]
log = logging.getLogger(__name__)


# danger levels: safe (read-only, in-app) · writes (in-app write) · network (reads the internet)
#                executes (sandboxed code) · external (writes to systems outside the app → asks by default)
DEFAULT_MODE = {"safe": "on", "writes": "on", "network": "on", "executes": "on", "external": "ask"}


class ToolSpec:
    def __init__(self, name: str, description: str, parameters: dict[str, Any], fn: ToolFn, group: str, danger: str = "safe",
                 examples: list[dict[str, Any]] | None = None, taints: bool = False):
        self.name, self.description, self.parameters, self.fn, self.group, self.danger = name, description, parameters, fn, group, danger
        self.examples, self.taints = examples or [], taints

    @property
    def default_mode(self) -> str:
        return DEFAULT_MODE.get(self.danger, "on")

    def schema(self) -> dict[str, Any]:
        d = self.description
        if self.examples:  # examples belong in the prose: these models read descriptions, not JSON Schema annotations
            d += "\nExample arguments: " + " ".join(json.dumps(e, ensure_ascii=False) for e in self.examples)
        return {"type": "function", "function": {"name": self.name, "description": d, "parameters": self.parameters}}

    def info(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description, "group": self.group, "danger": self.danger,
                "default_mode": self.default_mode, "taints": self.taints}


def _obj(props: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": required}


# ---- error shaping: no tracebacks to the model, always a way forward ----
ALTERNATIVE = {
    "gmail_send": "gmail_draft, which writes the same email without sending it",
    "gmail_draft": "write the email text in your reply so the user can send it",
    "gmail_modify": "gmail_read, then tell the user what you would change",
    "gmail_search": "ask the user to paste the email you need",
    "gmail_read": "gmail_search, whose snippets often carry the answer",
    "calendar_create": "calendar_events to show the free slot, and let the user create it",
    "calendar_events": "ask the user what is on their calendar",
    "google_tasks_add": "todo_add, the in-app todo list",
    "google_tasks_complete": "todo_update(done=true) on the in-app todo",
    "google_tasks_list": "todo_list",
    "fetch_url": "web_search, whose snippets often answer the question",
    "web_search": "search_documents and search_memory for what the user already has",
    "search_documents": "list_documents to see what exists, or ask the user to paste the text",
    "read_document": "search_documents for the relevant excerpt",
    "run_python": "do the arithmetic or the reasoning directly in your reply",
    "save_memory": "state the fact in your reply so the user can keep it",
    "graph_add": "save_memory, or just state the relation in your reply",
    "todo_add": "list the items in your reply so the user can add them",
    "todo_delete": "todo_update(done=true)",
    "board_add_card": "todo_add",
}


def tool_error(message: str, *, field: str | None = None, expected: str | None = None,
               example: dict[str, Any] | None = None, alternative: str | None = None) -> dict[str, Any]:
    e: dict[str, Any] = {"error": message}
    if field:
        e["field"] = field
    if expected:
        e["expected"] = expected
    if example is not None:
        e["example"] = example
    if alternative:
        e["try_instead"] = alternative
    return e


def denied(name: str, reason: str) -> dict[str, Any]:
    alt = ALTERNATIVE.get(name)
    return tool_error(f"{name} is {reason}. Do not retry it.",
                      alternative=alt or "continue without it, or ask the user what they would like instead")


def call_key(name: str, args: dict[str, Any]) -> str:
    """Canonical signature of one call: key order and whitespace do not matter."""
    try:
        return name + ":" + json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return f"{name}:{args!r}"


def _first_line(e: BaseException, cap: int = 200) -> str:
    lines = str(e).strip().splitlines()
    return (lines[0] if lines else type(e).__name__)[:cap]


# ---- pagination envelope for list-shaped results ----
def page(items: list[Any], *, offset: int = 0, limit: int = 20, key: str = "items", **extra: Any) -> dict[str, Any]:
    total = len(items)
    off = max(0, int(offset))
    lim = max(1, min(int(limit), 100))
    win = items[off: off + lim]
    out: dict[str, Any] = {key: win, "total": total, "offset": off, "count": len(win), "has_more": off + len(win) < total}
    if out["has_more"]:
        out["next_offset"] = off + len(win)
    return {**out, **extra}


# ---- SSRF guard ----
class UrlBlocked(Exception):
    def __init__(self, message: str, alternative: str | None = None):
        super().__init__(message)
        self.alternative = alternative


CGNAT = ipaddress.ip_network("100.64.0.0/10")  # not is_private on every 3.10 patch level


def _ip_reason(ip: Any) -> str | None:
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if ip.version == 6 and ip.sixtofour:
        ip = ip.sixtofour
    if ip.is_loopback:
        return "loopback"
    if ip.is_link_local:
        return "link-local (the cloud metadata range)"
    if ip.is_private:
        return "a private network"
    if ip.version == 4 and ip in CGNAT:
        return "carrier-grade NAT"
    if ip.is_multicast:
        return "multicast"
    if ip.is_reserved or ip.is_unspecified:
        return "a reserved address"
    if not ip.is_global:
        return "a non-public address"
    return None


def _as_ip(host: str) -> Any | None:
    try:
        return ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return None


def _allowed_hosts(settings: dict[str, Any]) -> set[str]:
    """The user's standing per-host trust, from settings only. A host the model or a fetched page surfaced is not enough."""
    return {h for h in (str(x).strip().lower().lstrip(".") for x in (settings.get("fetchAllowlist") or ())) if h}


def _norm_url(url: str) -> str | None:
    """Comparable form of a URL: scheme and fragment dropped, host lowercased, empty path = '/'. None if it has no host."""
    try:
        u = urllib.parse.urlsplit((url or "").strip())
        host, port = (u.hostname or "").lower(), u.port
    except ValueError:
        return None
    if not host:
        return None
    path = (u.path or "/").rstrip("/") or "/"  # a trailing slash is not data
    return f"{host}{f':{port}' if port else ''}{path}" + (f"?{u.query}" if u.query else "")


def _allowed_urls(ctx: dict[str, Any]) -> set[str]:
    return {n for u in (ctx.get("allowed_urls") or ()) if (n := _norm_url(str(u)))}


TAINTED_HINT = ("Fetch a result URL exactly as web_search returned it, or answer from what you already fetched. "
                "The user can also paste the link, or add the host under Settings → fetchAllowlist.")


def _check_url(url: str, ctx: dict[str, Any], settings: dict[str, Any], redirect: bool = False) -> tuple[str, str]:
    """(url, host) or raise UrlBlocked. Parsed with urllib, never string prefixes."""
    u = urllib.parse.urlsplit((url or "").strip())
    if u.scheme not in ("http", "https"):
        raise UrlBlocked(f"only http:// and https:// URLs can be fetched, got {u.scheme + ':' if u.scheme else 'a URL with no scheme'}")
    if u.username or u.password:
        raise UrlBlocked("credentials in the URL are not allowed")
    host = (u.hostname or "").strip().lower()
    if not host:
        raise UrlBlocked("the URL has no hostname")
    ip = _as_ip(host)
    if ip is not None and (reason := _ip_reason(ip)):
        raise UrlBlocked(f"{host} is {reason}; only public internet addresses can be fetched")
    # Tainted: the URL must match one the model did not author, whole. Allow-listing the *host* is not enough --
    # the path and the subdomain labels are model-authored bytes, i.e. an exfiltration channel to that host. Redirect
    # hops are chosen by the server, not by the model, so they carry no model-authored data and get the SSRF checks only.
    if ctx.get("tainted") and not redirect:
        if not any(host == e or host.endswith("." + e) for e in _allowed_hosts(settings)) and _norm_url(url) not in _allowed_urls(ctx):
            raise UrlBlocked("fetch_url is restricted: this reply has already read untrusted content, so it can only fetch a URL exactly as "
                             f"the user or a web search gave it, or any URL on an allow-listed host. '{url}' is neither", TAINTED_HINT)
    return urllib.parse.urlunsplit((u.scheme, u.netloc, u.path, u.query, u.fragment)), host


async def _resolve(host: str) -> None:
    """Every A/AAAA record must be public. Accepted residual: a DNS rebind between this check and the connect."""
    if _as_ip(host) is not None:
        return
    try:
        infos = await asyncio.to_thread(socket.getaddrinfo, host, None, 0, socket.SOCK_STREAM)
    except OSError as e:
        raise UrlBlocked(f"{host} does not resolve ({e.strerror or _first_line(e)})") from None
    for info in infos:
        ip = _as_ip(str(info[4][0]))
        if ip is None:  # fail closed: an address we cannot parse is an address we cannot judge
            raise UrlBlocked(f"{host} resolves to an address that cannot be validated")
        if reason := _ip_reason(ip):
            raise UrlBlocked(f"{host} resolves to {ip}, which is {reason}; only public internet addresses can be fetched")


def _allow_url(ctx: dict[str, Any], url: str | None) -> None:
    """A URL a web search surfaced stays fetchable once the run is tainted -- whole, exactly as it was returned."""
    if not url or not _norm_url(url):
        return
    bucket = ctx.get("allowed_urls")
    if bucket is None:
        bucket = ctx["allowed_urls"] = set()
    add = getattr(bucket, "add", None) or getattr(bucket, "append", None)
    if add:
        add(url)


class Toolbox:
    def __init__(self, memories: Memories, graph: Graph, documents: Documents, settings_fn: Callable[[], dict[str, Any]], todos: Any = None, google: Any = None, boards: Any = None):
        self.memories, self.graph, self.documents, self.settings = memories, graph, documents, settings_fn
        self.todos, self.google, self.boards = todos, google, boards
        self.specs: dict[str, ToolSpec] = {}
        self._register()
        if todos is not None:
            self._register_todos()
        if boards is not None:
            self._register_boards()
        if google is not None:
            self._register_google()

    def _google_ok(self) -> bool:
        return bool(self.google and self.google.status()["connected"])

    def available(self, name: str, google_ok: bool | None = None) -> bool:
        """Some tools need an integration to be connected. Pass google_ok to avoid one settings read per google tool."""
        spec = self.specs.get(name)
        if spec and spec.group == "google":
            return self._google_ok() if google_ok is None else google_ok
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
        gok = self._google_ok()
        return [s.schema() for n, s in self.specs.items() if modes.get(n) in ("on", "ask") and self.available(n, gok)]

    def list(self) -> list[dict[str, Any]]:
        gok = self._google_ok()
        return [{**s.info(), "available": self.available(s.name, gok)} for s in self.specs.values()]

    def taints(self, name: str) -> bool:
        """True if this tool's result carries untrusted third-party content."""
        return bool((s := self.specs.get(name)) and s.taints)

    def gate(self, name: str, mode: str, ctx: dict[str, Any]) -> str:
        """Effective mode for one call. Untrusted content in the run forces every external tool to ask."""
        spec = self.specs.get(name)
        if spec and spec.danger == "external" and mode == "on" and ctx.get("tainted"):
            return "ask"
        return mode

    async def call(self, name: str, args: dict[str, Any], ctx: dict[str, Any]) -> Any:
        spec = self.specs.get(name)
        if not spec:
            return tool_error(f"Unknown tool {name}.", alternative="use one of the tools listed in this request")
        try:
            out = await spec.fn(ctx, **args)
        except TypeError as e:  # backstop: signature mismatch, wrong types
            return tool_error(f"{name}: bad arguments — {_first_line(e)}",
                              expected="required: " + (", ".join(spec.parameters.get("required") or []) or "none"),
                              example=(spec.examples or [None])[0], alternative=ALTERNATIVE.get(name))
        except Exception as e:  # noqa: BLE001
            log.warning("tool %s failed", name, exc_info=True)
            if type(e).__name__ == "GoogleNotConnected":
                return tool_error(f"{name}: Google is not connected.",
                                  alternative="ask the user to connect Google under Settings → Integrations, then retry")
            if isinstance(e, httpx.HTTPStatusError):
                return tool_error(f"{name}: HTTP {e.response.status_code} from {e.request.url.host}", alternative=ALTERNATIVE.get(name))
            return tool_error(f"{name}: {type(e).__name__}: {_first_line(e)}", alternative=ALTERNATIVE.get(name))
        if spec.taints and not (isinstance(out, dict) and out.get("error")):
            ctx["tainted"] = True  # monotonic: never cleared for the rest of the run
        return out

    # ---- tool implementations ----
    def _register(self) -> None:
        R = self.specs.__setitem__

        async def search_documents(ctx: dict[str, Any], query: str, limit: int = 8, offset: int = 0) -> Any:
            off, lim = max(0, int(offset)), max(1, min(int(limit), 20))
            hits = self.documents.search(ctx["project_id"], query, limit=off + lim)
            rows = [{"document_id": h["document_id"], "document": h["name"], "chunk": h["idx"], "text": h["text"]} for h in hits]
            return page(rows, offset=off, limit=lim, key="results")
        R("search_documents", ToolSpec("search_documents", "Full-text search over the user's uploaded documents (project knowledge + personal documents). Returns the best matching excerpts. Use it when the user asks about something that may be in their files.",
            _obj({"query": {"type": "string", "description": "Search terms or a short question"}, "limit": {"type": "integer", "default": 8}, "offset": {"type": "integer", "default": 0}}, ["query"]), search_documents, "knowledge",
            examples=[{"query": "notice period"}, {"query": "Q3 revenue forecast", "limit": 5}, {"query": "onboarding checklist", "limit": 8, "offset": 8}], taints=True))

        async def read_document(ctx: dict[str, Any], document_id: str, offset: int = 0, length: int = 6000) -> Any:
            d = self.documents.get(document_id)
            if not d:
                return tool_error(f"No document with id '{document_id}'.", field="document_id",
                                  expected="an id returned by search_documents or list_documents",
                                  example={"document_id": "doc_3f2a91", "offset": 0}, alternative=ALTERNATIVE["read_document"])
            text = d["text"]
            off = max(0, int(offset))
            return {"name": d["name"], "total_chars": len(text), "offset": off, "text": text[off: off + min(int(length), 20000)]}
        R("read_document", ToolSpec("read_document", "Read a slice of a document's full text by id (ids come from search_documents or the document list). Page through long documents with offset.",
            _obj({"document_id": {"type": "string"}, "offset": {"type": "integer", "default": 0}, "length": {"type": "integer", "default": 6000}}, ["document_id"]), read_document, "knowledge",
            examples=[{"document_id": "doc_3f2a91"}, {"document_id": "doc_3f2a91", "offset": 6000}, {"document_id": "doc_3f2a91", "offset": 0, "length": 2000}], taints=True))

        async def list_documents(ctx: dict[str, Any], offset: int = 0) -> Any:
            rows = [{"document_id": d["id"], "name": d["name"], "chunks": d["chunk_count"], "scope": "project" if d["project_id"] else "personal"} for d in self.documents.list(ctx["project_id"])]
            return page(rows, offset=offset, limit=50, key="documents")
        R("list_documents", ToolSpec("list_documents", "List the documents available in this chat's scope.", _obj({"offset": {"type": "integer", "default": 0}}, []), list_documents, "knowledge",
            examples=[{}, {"offset": 50}]))

        async def search_memory(ctx: dict[str, Any], query: str, offset: int = 0) -> Any:
            rows = [{"id": m["id"], "content": m["content"], "kind": m["kind"], "scope": "project" if m["project_id"] else "personal"} for m in self.memories.list(ctx["project_id"], query)]
            return page(rows, offset=offset, limit=20, key="memories")
        R("search_memory", ToolSpec("search_memory", "Search what you remember about the user (long-term memory) for a topic.",
            _obj({"query": {"type": "string"}, "offset": {"type": "integer", "default": 0}}, ["query"]), search_memory, "memory",
            examples=[{"query": "coffee"}, {"query": "work schedule"}, {"query": "preferences", "offset": 20}]))

        async def save_memory(ctx: dict[str, Any], content: str, kind: str = "fact", personal: bool = False) -> Any:
            m = self.memories.create(None if personal else ctx["project_id"], content, kind=kind, source="auto")
            ctx.setdefault("learned", {"memories": [], "nodes": [], "edges": []})["memories"].append(m)
            return {"saved": m["id"], "content": m["content"]}
        R("save_memory", ToolSpec("save_memory", "Explicitly remember something durable about the user (a fact, preference or goal) for future chats. Use when the user says 'remember that…' or shares something clearly worth keeping.",
            _obj({"content": {"type": "string", "description": "Third person, e.g. 'User prefers dark mode'"}, "kind": {"type": "string", "enum": ["fact", "preference", "goal", "note"], "default": "fact"},
                  "personal": {"type": "boolean", "description": "true = available in every chat, false = only this project", "default": False}}, ["content"]), save_memory, "memory", "writes",
            examples=[{"content": "User's daughter is called Mira", "kind": "fact", "personal": True},
                      {"content": "User prefers replies under 150 words", "kind": "preference", "personal": True},
                      {"content": "User wants the migration done before March", "kind": "goal"}]))

        async def graph_search(ctx: dict[str, Any], query: str) -> Any:
            sub = self.graph.neighborhood(ctx["project_id"], query, max_nodes=40)
            by_id = {n["id"]: n for n in sub["nodes"]}
            ents = [{"id": n["id"], "label": n["label"], "type": n["type"], "properties": n["properties"]} for n in sub["nodes"]]
            rels = [f"{by_id[e['source_id']]['label']} -[{e['relation']}]-> {by_id[e['target_id']]['label']}" for e in sub["edges"]]
            return {"entities": ents, "relations": rels, "total_entities": len(ents), "total_relations": len(rels),
                    "truncated": len(ents) >= 40}
        R("graph_search", ToolSpec("graph_search", "Find entities in the user's knowledge graph matching a query, with their direct relations (1 hop).",
            _obj({"query": {"type": "string"}}, ["query"]), graph_search, "graph",
            examples=[{"query": "Acme"}, {"query": "Mira"}, {"query": "migration project"}]))

        async def graph_traverse(ctx: dict[str, Any], entity: str, depth: int = 2) -> Any:
            g = self.graph.get(ctx["project_id"])
            by_id = {n["id"]: n for n in g["nodes"]}
            start = next((n for n in g["nodes"] if n["label"].lower() == entity.strip().lower()), None) or next((n for n in g["nodes"] if entity.strip().lower() in n["label"].lower()), None)
            if not start:
                labels = [n["label"] for n in g["nodes"]]
                return tool_error(f"No entity matching '{entity}' in the knowledge graph.", field="entity",
                                  expected="an exact entity label, e.g. one of the known ones",
                                  example={"entity": labels[0], "depth": 2} if labels else {"entity": "Acme", "depth": 2},
                                  alternative="graph_search for a looser match, or search_memory")
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
            ents = [{"label": by_id[i]["label"], "type": by_id[i]["type"]} for i in seen if i in by_id]
            uniq = sorted(set(rels))
            return {"start": start["label"], "entities": ents[:80], "total_entities": len(ents), "entities_truncated": len(ents) > 80,
                    "relations": uniq[:120], "total_relations": len(uniq), "relations_truncated": len(uniq) > 120}
        R("graph_traverse", ToolSpec("graph_traverse", "Walk the knowledge graph outward from a named entity up to `depth` hops and return everything connected.",
            _obj({"entity": {"type": "string"}, "depth": {"type": "integer", "default": 2}}, ["entity"]), graph_traverse, "graph",
            examples=[{"entity": "Acme"}, {"entity": "Mira", "depth": 1}, {"entity": "migration project", "depth": 3}]))

        async def graph_add(ctx: dict[str, Any], source: str, relation: str, target: str, source_type: str = "entity", target_type: str = "entity") -> Any:
            if source.strip().lower() in SELF_LABELS or target.strip().lower() in SELF_LABELS:
                return tool_error("The user is not a graph entity.", field="source",
                                  expected="two named things (people, projects, tools…), never the user",
                                  example={"source": "Mira", "relation": "works at", "target": "Acme"},
                                  alternative="save_memory for facts about the user")
            s = self.graph.upsert_node(ctx["project_id"], source, source_type)
            t = self.graph.upsert_node(ctx["project_id"], target, target_type)
            e = self.graph.upsert_edge(ctx["project_id"], s["id"], t["id"], relation)
            learned = ctx.setdefault("learned", {"memories": [], "nodes": [], "edges": []})
            learned["nodes"] += [s, t]
            learned["edges"].append(e)
            return {"added": f"{s['label']} -[{e['relation']}]-> {t['label']}"}
        R("graph_add", ToolSpec("graph_add", "Add a relation (and the entities if new) to the knowledge graph.",
            _obj({"source": {"type": "string"}, "relation": {"type": "string"}, "target": {"type": "string"}, "source_type": {"type": "string", "default": "entity"}, "target_type": {"type": "string", "default": "entity"}}, ["source", "relation", "target"]), graph_add, "graph", "writes",
            examples=[{"source": "Mira", "relation": "works at", "target": "Acme", "source_type": "person", "target_type": "company"},
                      {"source": "Personal OS", "relation": "uses", "target": "SQLite", "source_type": "project", "target_type": "tool"},
                      {"source": "Acme", "relation": "acquired", "target": "Globex"}]))

        async def web_search(ctx: dict[str, Any], query: str, max_results: int = 6, offset: int = 0) -> Any:
            cfg = self.settings()
            n = max(1, min(int(max_results), 10))
            off = max(0, int(offset))
            want = min(off + n, 25)
            rows: list[dict[str, Any]]
            if cfg.get("braveApiKey"):
                async with httpx.AsyncClient(timeout=20) as c:
                    r = await c.get("https://api.search.brave.com/res/v1/web/search", params={"q": query, "count": want},
                                    headers={"X-Subscription-Token": cfg["braveApiKey"], "Accept": "application/json"})
                    r.raise_for_status()
                    rows = [{"title": w.get("title"), "url": w.get("url"), "snippet": w.get("description")} for w in r.json().get("web", {}).get("results", [])[:want]]
            elif cfg.get("tavilyApiKey"):
                async with httpx.AsyncClient(timeout=25) as c:
                    r = await c.post("https://api.tavily.com/search", json={"api_key": cfg["tavilyApiKey"], "query": query, "max_results": want})
                    r.raise_for_status()
                    rows = [{"title": w.get("title"), "url": w.get("url"), "snippet": w.get("content")} for w in r.json().get("results", [])[:want]]
            else:
                # keyless fallback: DuckDuckGo
                from ddgs import DDGS

                def _ddg() -> list[dict[str, Any]]:
                    with DDGS() as d:
                        return [{"title": r.get("title"), "url": r.get("href"), "snippet": r.get("body")} for r in d.text(query, max_results=want)]
                rows = await asyncio.to_thread(_ddg)
            for row in rows:
                _allow_url(ctx, row.get("url"))
            return page(rows, offset=off, limit=n, key="results")
        R("web_search", ToolSpec("web_search", "Search the web for current information. Returns titles, URLs and snippets; call fetch_url to read a result in full.",
            _obj({"query": {"type": "string"}, "max_results": {"type": "integer", "default": 6}, "offset": {"type": "integer", "default": 0}}, ["query"]), web_search, "web", "network",
            examples=[{"query": "EU AI Act enforcement dates"}, {"query": "best espresso machine 2026", "max_results": 10},
                      {"query": "python 3.13 release notes", "max_results": 6, "offset": 6}], taints=True))

        async def fetch_url(ctx: dict[str, Any], url: str, max_chars: int = 12000) -> Any:
            cur, hops = url, 0
            try:
                async with httpx.AsyncClient(timeout=25, follow_redirects=False, transport=httpx.AsyncHTTPTransport(retries=0),
                                             headers={"User-Agent": "PersonalOS/0.1 (+desktop assistant)"}) as c:
                    while True:
                        cur, host = _check_url(cur, ctx, self.settings(), redirect=hops > 0)
                        await _resolve(host)  # validated, then reconnected by name: a DNS rebind in that window is accepted
                        r = await c.get(cur)
                        if r.status_code not in (301, 302, 303, 307, 308) or not r.headers.get("location"):
                            break
                        hops += 1
                        if hops > 5:
                            return tool_error(f"fetch_url: too many redirects (5) starting at {url}", field="url", alternative=ALTERNATIVE["fetch_url"])
                        cur = urllib.parse.urljoin(str(r.url), r.headers["location"])
            except UrlBlocked as e:
                return tool_error(f"fetch_url refused {url}: {e}", field="url", alternative=e.alternative or ALTERNATIVE["fetch_url"])
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
            return {"url": str(r.url), "status": r.status_code, "content_type": ctype, "text": text[: max(1000, min(int(max_chars), 40000))],
                    "truncated": len(text) > max_chars, "redirects": hops}
        R("fetch_url", ToolSpec("fetch_url", "Fetch a web page and return its main text as markdown. Public http(s) addresses only.",
            _obj({"url": {"type": "string"}, "max_chars": {"type": "integer", "default": 12000}}, ["url"]), fetch_url, "web", "network",
            examples=[{"url": "https://example.com/blog/post"}, {"url": "https://en.wikipedia.org/wiki/SQLite", "max_chars": 20000}], taints=True))

        async def run_python_tool(ctx: dict[str, Any], code: str, timeout: int = 30) -> Any:
            return await asyncio.to_thread(run_python, code, max(1, min(int(timeout), 120)))
        R("run_python", ToolSpec("run_python", "Run a Python 3 script in an isolated sandbox and return stdout/stderr. No network, no subprocesses, and writes only inside the temp working directory (CPU/memory/time limits apply). Use for calculations, data wrangling, quick prototypes. Print what you want to see. numpy and matplotlib are installed: any figure saved with plt.savefig('name.png') is shown to the user inline (prefer a ```chart block for simple bar/line/pie charts of small data; use matplotlib for anything it can't express).",
            _obj({"code": {"type": "string"}, "timeout": {"type": "integer", "default": 30}}, ["code"]), run_python_tool, "code", "executes",
            examples=[{"code": "print(sum(1 / n**2 for n in range(1, 10000)))"},
                      {"code": "import matplotlib\nmatplotlib.use('Agg')\nimport matplotlib.pyplot as plt\nplt.plot([1, 4, 9])\nplt.savefig('squares.png')", "timeout": 60}]))

        async def current_time(ctx: dict[str, Any]) -> Any:
            return {"iso": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "unix": int(time.time()), "timezone": time.strftime("%Z")}
        R("current_time", ToolSpec("current_time", "Get the current local date and time.", _obj({}, []), current_time, "utility", examples=[{}]))


def summarize_result(result: Any, limit: int = 1500) -> str:
    """Short JSON preview of a tool result. Never cuts mid-structure: it drops list items, or says what it cut."""
    try:
        s = json.dumps(result, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        s = str(result)
    if len(s) <= limit:
        return s
    if isinstance(result, dict):
        key = max((k for k, v in result.items() if isinstance(v, list)), key=lambda k: len(result[k]), default=None)
        if key is not None:
            items = result[key]
            lo, hi, best = 0, len(items), None
            while lo <= hi:  # largest prefix of the longest list that still fits
                mid = (lo + hi) // 2
                cand = json.dumps({**result, key: items[:mid],
                                   "truncated": {"field": key, "kept": mid, "of": len(items), "next_offset": mid}},
                                  ensure_ascii=False, default=str)
                if len(cand) <= limit:
                    best, lo = cand, mid + 1
                else:
                    hi = mid - 1
            if best:
                return best
    shown = max(0, limit - 200)
    return json.dumps({"truncated": True, "total_chars": len(s), "shown": shown, "preview": s[:shown]}, ensure_ascii=False)


# ---------------- todo + google tool registration ----------------
def _register_todos(self: Toolbox) -> None:
    R = self.specs.__setitem__

    async def todo_list(ctx: dict[str, Any], include_done: bool = False, all_projects: bool = False, offset: int = 0) -> Any:
        scope = "__all__" if all_projects else ctx["project_id"]
        items = self.todos.list(scope, include_done=include_done) if not all_projects else self.todos.list("__all__", include_done=include_done)
        if not all_projects and ctx["project_id"] is not None:
            items = self.todos.list(ctx["project_id"], include_done=include_done) + self.todos.list(None, include_done=include_done)
        rows = [{"id": t["id"], "title": t["title"], "due": t["due"], "priority": t["priority"], "done": bool(t["done"]), "notes": t["notes"][:200]} for t in items]
        return page(rows, offset=offset, limit=50, key="todos")
    R("todo_list", ToolSpec("todo_list", "List the user's todos (open by default) in this chat's scope: the project's todos plus personal ones.",
        _obj({"include_done": {"type": "boolean", "default": False}, "all_projects": {"type": "boolean", "default": False}, "offset": {"type": "integer", "default": 0}}, []), todo_list, "todos",
        examples=[{}, {"include_done": True}, {"all_projects": True, "offset": 50}]))

    async def todo_add(ctx: dict[str, Any], title: str, due: str | None = None, notes: str = "", priority: int = 2, personal: bool = False) -> Any:
        t = self.todos.create(title, None if personal else ctx["project_id"], notes=notes, due=due, priority=priority)
        return {"id": t["id"], "title": t["title"], "due": t["due"]}
    R("todo_add", ToolSpec("todo_add", "Add a todo for the user. Dates as YYYY-MM-DD. Priority 1 (high) to 3 (low).",
        _obj({"title": {"type": "string"}, "due": {"type": "string"}, "notes": {"type": "string"}, "priority": {"type": "integer", "default": 2}, "personal": {"type": "boolean", "default": False}}, ["title"]), todo_add, "todos", "writes",
        examples=[{"title": "Renew passport", "due": "2026-10-14", "priority": 1},
                  {"title": "Buy milk", "personal": True},
                  {"title": "Draft the migration plan", "notes": "start from the Q3 doc", "priority": 2}]))

    async def todo_update(ctx: dict[str, Any], id: str, done: bool | None = None, title: str | None = None, due: str | None = None, priority: int | None = None, notes: str | None = None) -> Any:
        patch = {k: v for k, v in {"done": done, "title": title, "due": due, "priority": priority, "notes": notes}.items() if v is not None}
        t = self.todos.update(id, patch)
        return t or tool_error(f"No todo with id '{id}'.", field="id", expected="an id from todo_list",
                               example={"id": "td_8c41a2", "done": True}, alternative="todo_list to get the current ids")
    R("todo_update", ToolSpec("todo_update", "Update or complete a todo by id (from todo_list).",
        _obj({"id": {"type": "string"}, "done": {"type": "boolean"}, "title": {"type": "string"}, "due": {"type": "string"}, "priority": {"type": "integer"}, "notes": {"type": "string"}}, ["id"]), todo_update, "todos", "writes",
        examples=[{"id": "td_8c41a2", "done": True}, {"id": "td_8c41a2", "due": "2026-11-01", "priority": 1}, {"id": "td_8c41a2", "title": "Renew passport (expedited)"}]))

    async def todo_delete(ctx: dict[str, Any], id: str) -> Any:
        t = self.todos.get(id)
        if not t:
            return tool_error(f"No todo with id '{id}'.", field="id", expected="an id from todo_list",
                              example={"id": "td_8c41a2"}, alternative="todo_list to get the current ids")
        self.todos.delete(id)
        return {"deleted": t["title"]}
    R("todo_delete", ToolSpec("todo_delete", "Delete a todo permanently by id. Prefer todo_update(done=true) to complete; delete only when the user asks to remove it.",
        _obj({"id": {"type": "string"}}, ["id"]), todo_delete, "todos", "writes", examples=[{"id": "td_8c41a2"}]))


def _register_google(self: Toolbox) -> None:
    R = self.specs.__setitem__
    g = self.google
    run = asyncio.to_thread

    async def calendar_events(ctx: dict[str, Any], days: int = 2, start: str | None = None, offset: int = 0) -> Any:
        rows = await run(g.calendar_events, days, "primary", 30, start)
        return page(rows, offset=offset, limit=30, key="events")
    R("calendar_events", ToolSpec("calendar_events", "List upcoming Google Calendar events (default: next 2 days). `start` is an ISO datetime to look from.",
        _obj({"days": {"type": "integer", "default": 2}, "start": {"type": "string"}, "offset": {"type": "integer", "default": 0}}, []), calendar_events, "google",
        examples=[{}, {"days": 7}, {"days": 1, "start": "2026-10-02T09:00"}]))

    async def calendar_create(ctx: dict[str, Any], summary: str, start: str, end: str | None = None, description: str = "", location: str = "", attendees: list[str] | None = None) -> Any:
        return await run(g.calendar_create, summary, start, end, description, location, attendees)
    R("calendar_create", ToolSpec("calendar_create", "Create a Google Calendar event. Use ISO datetimes (YYYY-MM-DDTHH:MM) in the user's local time, or YYYY-MM-DD for all-day.",
        _obj({"summary": {"type": "string"}, "start": {"type": "string"}, "end": {"type": "string"}, "description": {"type": "string"}, "location": {"type": "string"}, "attendees": {"type": "array", "items": {"type": "string"}}}, ["summary", "start"]), calendar_create, "google", "external",
        examples=[{"summary": "Dentist", "start": "2026-10-07T15:00", "end": "2026-10-07T16:00"},
                  {"summary": "Sprint review", "start": "2026-10-08T10:00", "attendees": ["mira@example.com"], "location": "Room 2"},
                  {"summary": "Holiday", "start": "2026-12-24"}]))

    async def gmail_search(ctx: dict[str, Any], query: str = "is:unread newer_than:2d", max_results: int = 15, offset: int = 0) -> Any:
        off, n = max(0, int(offset)), max(1, min(int(max_results), 100))
        rows = await run(g.gmail_search, query, off + n)
        return page(rows, offset=off, limit=n, key="messages")
    R("gmail_search", ToolSpec("gmail_search", "Search Gmail with Gmail query syntax (e.g. 'is:unread', 'from:alice newer_than:7d', 'subject:invoice'). Returns headers and snippets.",
        _obj({"query": {"type": "string", "default": "is:unread newer_than:2d"}, "max_results": {"type": "integer", "default": 15}, "offset": {"type": "integer", "default": 0}}, []), gmail_search, "google",
        examples=[{"query": "is:unread newer_than:2d"}, {"query": "from:mira@example.com subject:invoice", "max_results": 5},
                  {"query": "has:attachment newer_than:30d", "max_results": 15, "offset": 15}], taints=True))

    async def gmail_read(ctx: dict[str, Any], message_id: str) -> Any:
        return await run(g.gmail_get, message_id)
    R("gmail_read", ToolSpec("gmail_read", "Read the full body of an email by id (from gmail_search).",
        _obj({"message_id": {"type": "string"}}, ["message_id"]), gmail_read, "google",
        examples=[{"message_id": "18f2c1a9b7e4d0aa"}], taints=True))

    async def gmail_draft(ctx: dict[str, Any], to: str, subject: str, body: str, reply_to_message_id: str | None = None) -> Any:
        return await run(g.gmail_draft, to, subject, body, reply_to_message_id)
    R("gmail_draft", ToolSpec("gmail_draft", "Create a Gmail draft (never sends). Prefer this over gmail_send unless the user explicitly asked to send.",
        _obj({"to": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"}, "reply_to_message_id": {"type": "string"}}, ["to", "subject", "body"]), gmail_draft, "google", "external",
        examples=[{"to": "mira@example.com", "subject": "Invoice 42", "body": "Hi Mira,\n\nAttached is invoice 42.\n\nThanks"},
                  {"to": "team@example.com", "subject": "Re: sprint review", "body": "Works for me.", "reply_to_message_id": "18f2c1a9b7e4d0aa"}]))

    async def gmail_send(ctx: dict[str, Any], to: str, subject: str, body: str) -> Any:
        return await run(g.gmail_send, to, subject, body)
    R("gmail_send", ToolSpec("gmail_send", "Send an email from the user's Gmail. Only when the user explicitly asked to send it.",
        _obj({"to": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"}}, ["to", "subject", "body"]), gmail_send, "google", "external",
        examples=[{"to": "mira@example.com", "subject": "Running late", "body": "I will be 10 minutes late."}]))

    async def gmail_modify(ctx: dict[str, Any], message_id: str, mark_read: bool | None = None, archive: bool = False, star: bool | None = None) -> Any:
        return await run(g.gmail_modify, message_id, mark_read, archive, star)
    R("gmail_modify", ToolSpec("gmail_modify", "Mark an email read/unread, star it, or archive it.",
        _obj({"message_id": {"type": "string"}, "mark_read": {"type": "boolean"}, "archive": {"type": "boolean", "default": False}, "star": {"type": "boolean"}}, ["message_id"]), gmail_modify, "google", "external",
        examples=[{"message_id": "18f2c1a9b7e4d0aa", "mark_read": True}, {"message_id": "18f2c1a9b7e4d0aa", "archive": True}, {"message_id": "18f2c1a9b7e4d0aa", "star": True}]))

    async def gtasks_list(ctx: dict[str, Any], show_completed: bool = False, offset: int = 0) -> Any:
        rows = await run(g.tasks_list, "@default", show_completed)
        return page(rows, offset=offset, limit=50, key="tasks")
    R("google_tasks_list", ToolSpec("google_tasks_list", "List the user's Google Tasks (default list).",
        _obj({"show_completed": {"type": "boolean", "default": False}, "offset": {"type": "integer", "default": 0}}, []), gtasks_list, "google",
        examples=[{}, {"show_completed": True}, {"offset": 50}]))

    async def gtasks_add(ctx: dict[str, Any], title: str, notes: str = "", due: str | None = None) -> Any:
        return await run(g.tasks_add, title, notes, due)
    R("google_tasks_add", ToolSpec("google_tasks_add", "Add a task to Google Tasks (due as YYYY-MM-DD).",
        _obj({"title": {"type": "string"}, "notes": {"type": "string"}, "due": {"type": "string"}}, ["title"]), gtasks_add, "google", "external",
        examples=[{"title": "File the tax return", "due": "2026-10-31"}, {"title": "Call the landlord", "notes": "about the boiler"}]))

    async def gtasks_complete(ctx: dict[str, Any], task_id: str) -> Any:
        return await run(g.tasks_complete, task_id)
    R("google_tasks_complete", ToolSpec("google_tasks_complete", "Mark a Google Task complete.",
        _obj({"task_id": {"type": "string"}}, ["task_id"]), gtasks_complete, "google", "external", examples=[{"task_id": "MTIzNDU2Nzg5"}]))


def _register_boards(self: Toolbox) -> None:
    R = self.specs.__setitem__

    async def board_list(ctx: dict[str, Any], board: str | None = None, offset: int = 0) -> Any:
        names = [x["name"] for x in self.boards.list()]
        if board:
            b = self.boards.find_board(board)
            if not b:
                return tool_error(f"No board named '{board}'.", field="board", expected="a board name or id from board_list",
                                  example={"board": names[0]} if names else {"board": "Work"},
                                  alternative="board_list with no arguments to see the boards, or board_create")
            cols = {c["id"]: c["name"] for c in b["columns"]}
            cards = [{"id": c["id"], "title": c["title"], "column": cols.get(c["column_id"]), "due": c["due"], "priority": c["priority"]} for c in b["cards"]]
            return page(cards, offset=offset, limit=50, key="cards", board=b["name"], id=b["id"],
                        columns=[{"id": c["id"], "name": c["name"]} for c in b["columns"]])
        return page([{"id": b["id"], "name": b["name"], "cards": b["card_count"]} for b in self.boards.list()], offset=offset, limit=50, key="boards")
    R("board_list", ToolSpec("board_list", "List kanban boards, or the columns and cards of one board (by name or id).",
        _obj({"board": {"type": "string"}, "offset": {"type": "integer", "default": 0}}, []), board_list, "boards",
        examples=[{}, {"board": "Work"}, {"board": "Work", "offset": 50}]))

    async def board_add_card(ctx: dict[str, Any], board: str, title: str, column: str | None = None, description: str = "", due: str | None = None, priority: int = 2) -> Any:
        b = self.boards.find_board(board)
        if not b:
            return tool_error(f"No board named '{board}'.", field="board", expected="a board name or id from board_list",
                              example={"board": "Work", "title": "Fix the login bug"}, alternative="board_list to see the boards")
        col = next((c for c in b["columns"] if column and c["name"].lower() == column.lower()), None)
        card = self.boards.add_card(b["id"], col["id"] if col else None, title, description, due, priority)
        return {"added": card["title"], "id": card["id"], "column": (col or b["columns"][0])["name"]}
    R("board_add_card", ToolSpec("board_add_card", "Add a card to a kanban board (optionally into a named column).",
        _obj({"board": {"type": "string"}, "title": {"type": "string"}, "column": {"type": "string"}, "description": {"type": "string"}, "due": {"type": "string"}, "priority": {"type": "integer", "default": 2}}, ["board", "title"]), board_add_card, "boards", "writes",
        examples=[{"board": "Work", "title": "Fix the login bug", "column": "To do", "priority": 1},
                  {"board": "Home", "title": "Book the plumber", "due": "2026-10-10"}]))

    async def board_move_card(ctx: dict[str, Any], board: str, card: str, column: str) -> Any:
        b = self.boards.find_board(board)
        if not b:
            return tool_error(f"No board named '{board}'.", field="board", expected="a board name or id from board_list",
                              example={"board": "Work", "card": "Fix the login bug", "column": "Done"}, alternative="board_list to see the boards")
        c = next((x for x in b["cards"] if x["id"] == card or x["title"].lower() == card.lower()), None)
        col = next((x for x in b["columns"] if x["id"] == column or x["name"].lower() == column.lower()), None)
        if not c or not col:
            return tool_error(f"{'Card' if not c else 'Column'} not found on board '{b['name']}'.", field="card" if not c else "column",
                              expected="a card title/id and a column name from board_list(board=...)",
                              example={"board": b["name"], "card": card, "column": b["columns"][0]["name"] if b["columns"] else "Done"},
                              alternative=f"board_list(board='{b['name']}') to see the exact titles and columns")
        self.boards.move_card(c["id"], col["id"])
        return {"moved": c["title"], "to": col["name"]}
    R("board_move_card", ToolSpec("board_move_card", "Move a card (by title or id) to another column on a board.",
        _obj({"board": {"type": "string"}, "card": {"type": "string"}, "column": {"type": "string"}}, ["board", "card", "column"]), board_move_card, "boards", "writes",
        examples=[{"board": "Work", "card": "Fix the login bug", "column": "In progress"}, {"board": "Work", "card": "cd_7a1b90", "column": "Done"}]))

    async def board_create(ctx: dict[str, Any], name: str, columns: list[str] | None = None) -> Any:
        b = self.boards.create(name, ctx.get("project_id"), columns)
        return {"created": b["name"], "id": b["id"], "columns": [c["name"] for c in b["columns"]]}
    R("board_create", ToolSpec("board_create", "Create a new kanban board (default columns: Backlog, To do, In progress, Done).",
        _obj({"name": {"type": "string"}, "columns": {"type": "array", "items": {"type": "string"}}}, ["name"]), board_create, "boards", "writes",
        examples=[{"name": "Home renovation"}, {"name": "Q4 launch", "columns": ["Ideas", "Doing", "Shipped"]}]))


Toolbox._register_todos = _register_todos  # type: ignore[attr-defined]
Toolbox._register_boards = _register_boards  # type: ignore[attr-defined]
Toolbox._register_google = _register_google  # type: ignore[attr-defined]
