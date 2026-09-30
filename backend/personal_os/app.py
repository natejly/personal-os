"""Personal OS backend API."""
from __future__ import annotations

import asyncio
import hmac
import html
import json
import logging
import os
import re
import secrets
import shutil
import sqlite3
import time
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from . import llm, tools
from .context import build_context, estimate_tokens
from .db import Database, data_dir_from_env, new_id
from .extract_text import extract_text
from .learn import learn_from_exchange
from .repos import ALL, Conversations, Documents, Graph, Memories, Projects
from .boards import Boards
from .canvas import SNAP_MODES, WIDGET_KINDS, WINDOW_STATES, Canvases
from .dashboards import Dashboards, generate_recap, generate_summary, generate_widget_code
from .google import Google, GoogleNotConnected, json_safe
from .notes import Notes
from .runs import Run, RunBus
from .todos import Todos
from .tools import Toolbox, summarize_result
from .trace import Tracer, now_ms
from .usage import Pricing, Usage

log = logging.getLogger("personal_os")

db = Database(data_dir_from_env())
projects = Projects(db)
convos = Conversations(db)
memories = Memories(db)
graph = Graph(db)
documents = Documents(db)


def _resolve_auth_token() -> str:
    """Env wins (Electron passes it in); otherwise reuse/mint <data_dir>/.auth_token (0600)."""
    path = db.data_dir / ".auth_token"
    tok = (os.environ.get("PERSONAL_OS_AUTH_TOKEN") or "").strip()
    if not tok:
        try:  # --reload re-imports this module in a child: minting again would invalidate the renderer's token
            tok = path.read_text().strip()
        except OSError:
            tok = ""
    if not tok:
        tok = secrets.token_urlsafe(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # no try/except: unwritable must crash, not run open
    with os.fdopen(fd, "w") as f:
        f.write(tok)
    os.chmod(path, 0o600)
    return tok


AUTH_TOKEN = _resolve_auth_token()
PUBLIC_PATHS = ("/health", "/integrations/google/callback")


def _token_eq(sent: str, expected: str) -> bool:
    try:
        return secrets.compare_digest(sent, expected)
    except TypeError:  # non-ascii header value
        return False


WIDGET_TOKEN_TTL = 12 * 3600
# A widget iframe is a separate browsing context: it inherits none of the renderer's CSP, so the generated code gets
# its own. connect-src 'self' keeps a widget's data inside the sidecar - it cannot POST anywhere else on the internet.
WIDGET_CSP = ("default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src 'self' data:; "
              "font-src data:; connect-src 'self'; base-uri 'none'; form-action 'none'")


def _widget_fetch_token(wid: str, exp: int) -> str:
    """Capability handed to one widget's iframe: scoped to that widget's sources, and expiring, because it rides in the URL."""
    return hmac.new(AUTH_TOKEN.encode(), f"widget:{wid}:{exp}".encode(), "sha256").hexdigest()[:32]


def _widget_fetch_ok(source_id: str, wid: str, wt: str, we: str) -> bool:
    try:
        exp = int(we)
    except ValueError:
        return False
    if not (wid and wt) or exp < time.time() or not _token_eq(wt, _widget_fetch_token(wid, exp)):
        return False
    w = dashboards.widget(wid)
    return bool(w and source_id in (w.get("source_ids") or []))


app = FastAPI(title="Personal OS", version="0.1.0")


@app.exception_handler(sqlite3.IntegrityError)
async def _integrity_error(request: Request, exc: Exception) -> JSONResponse:  # type: ignore[override]
    """Safety net for the writers wsid() cannot cover (a card whose column is gone, a window whose canvas is gone).
    A stale id from a window that has not refreshed is the client's problem to retry, not a server fault, so it gets a
    409 and a usable message rather than a bare 500."""
    detail = ("Something this refers to no longer exists - reload and try again."
              if "FOREIGN KEY" in str(exc).upper() else f"That change conflicts with what is already stored ({exc})")
    log.info("integrity error on %s %s: %s", request.method, request.url.path, exc)
    return JSONResponse({"detail": detail}, status_code=409)


@app.middleware("http")
async def _require_token(request: Request, call_next):  # type: ignore[no-untyped-def]
    p = request.url.path
    if request.method == "OPTIONS" or p in PUBLIC_PATHS or (p.startswith("/widgets/") and p.endswith("/render")):
        return await call_next(request)
    auth = request.headers.get("authorization", "")
    sent = request.headers.get("x-personal-os-token") or (auth[7:].strip() if auth[:7].lower() == "bearer " else "")
    if _token_eq(sent, AUTH_TOKEN):
        return await call_next(request)
    if p.startswith("/sources/") and p.endswith("/fetch") and _widget_fetch_ok(
            p.split("/")[2], request.query_params.get("w") or "", request.query_params.get("wt") or "",
            request.query_params.get("we") or ""):
        return await call_next(request)
    return JSONResponse({"detail": "Unauthorized"}, status_code=401)


# CORS is added last so it is outermost: a preflight must be answered before auth can 401 it.
# Vite's dev server hops to 5174+ when 5173 is taken, so the default covers a small range; auth is
# the token header either way — CORS here only decides which local origins may even ask.
ALLOWED_ORIGINS = [o for o in (os.environ.get("PERSONAL_OS_ALLOWED_ORIGINS") or "").split(",") if o] or [
    "null", "file://",
    *(f"http://{h}:{p}" for h in ("localhost", "127.0.0.1") for p in range(5173, 5181))]
app.add_middleware(CORSMiddleware, allow_origins=ALLOWED_ORIGINS, allow_credentials=False,
                   allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
                   allow_headers=["Content-Type", "X-Personal-OS-Token", "Authorization"])

# Live runs, one per conversation, each owning its own task. Any number of clients may watch one.
bus = RunBus()
# Active chat streams so they can be aborted from the client: message_id -> that run's stop event.
_active: dict[str, asyncio.Event] = {}
# Pending tool-call approvals: call_id -> Future[decision]
_approvals: dict[str, asyncio.Future] = {}


ENV_SEED = {
    "baseUrl": "PERSONAL_OS_BASE_URL",
    "apiKey": "PERSONAL_OS_API_KEY",
    "defaultModel": "PERSONAL_OS_DEFAULT_MODEL",
    "extractionModel": "PERSONAL_OS_EXTRACTION_MODEL",
}


def _seed_settings_from_env() -> None:
    """First launch: take provider defaults from the environment (.env) if nothing is stored yet."""
    stored = db.get_settings()
    patch = {k: os.environ[v] for k, v in ENV_SEED.items() if k not in stored and os.environ.get(v)}
    if patch:
        db.set_settings(patch)


_seed_settings_from_env()


def settings() -> dict[str, Any]:
    return {**llm.DEFAULT_SETTINGS, **db.get_settings()}


todos = Todos(db)
boards = Boards(db)
dashboards = Dashboards(db)
google = Google(settings, db.set_settings)
usage = Usage(db)
pricing = Pricing()


def _record_usage(ev: dict[str, Any]) -> None:
    """llm.on_usage listener: persist one row per model call with a best-effort cost."""
    try:
        cfg = settings()
        pt, ct = int(ev.get("prompt_tokens") or 0), int(ev.get("completion_tokens") or 0)
        usage.record(model=ev.get("model", ""), kind=ev.get("kind", "chat"), prompt_tokens=pt, completion_tokens=ct,
                     duration_ms=int(ev.get("duration_ms") or 0), cost=pricing.cost(cfg, ev.get("model", ""), pt, ct),
                     estimated=bool(ev.get("estimated")), conversation_id=ev.get("conversation_id"), project_id=ev.get("project_id"))
    except Exception:  # noqa: BLE001 - accounting must never break a reply
        pass


if not any(getattr(f, "__name__", "") == "_record_usage" for f in llm._usage_listeners):
    llm.on_usage(_record_usage)
toolbox = Toolbox(memories, graph, documents, settings, todos=todos, google=google, boards=boards)


def sid(project_id: str | None) -> str | None:
    """Normalise the project query param: '' / 'personal' means personal (global) scope, 'all' means every scope."""
    if project_id in (None, "", "global", "personal", "null"):
        return None
    if project_id == "all":
        return ALL
    return project_id


def wsid(project_id: str | None) -> str | None:
    """sid() for a writer. A project id nothing matches means the client is holding a stale id (the project was
    deleted in another window), which would otherwise surface as a foreign-key 500; 'all' is a filter, not a scope
    a row can live in."""
    s = sid(project_id)
    if s is ALL:
        raise HTTPException(400, "'all' is a filter, not a scope you can save into")
    if s is not None and not projects.get(s):
        raise HTTPException(404, "No such project")
    return s


# ---------------- health / settings / models ----------------
@app.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True}


# Google OAuth material lives in settings but never leaves the backend.
PRIVATE_SETTINGS = {"googleToken", "googleAuthPending"}


@app.get("/settings")
def get_settings() -> dict[str, Any]:
    return {k: v for k, v in settings().items() if k not in PRIVATE_SETTINGS}


@app.put("/settings")
def put_settings(patch: dict[str, Any]) -> dict[str, Any]:
    db.set_settings({k: v for k, v in patch.items() if k in llm.DEFAULT_SETTINGS and k not in PRIVATE_SETTINGS})
    return {k: v for k, v in settings().items() if k not in PRIVATE_SETTINGS}


@app.get("/models")
async def models() -> list[dict[str, str]]:
    try:
        return await llm.list_models(settings())
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, str(e)) from e


# ---------------- projects ----------------
class ProjectIn(BaseModel):
    name: str
    description: str = ""
    system_prompt: str = ""
    color: str = "#d97757"


class ProjectPatch(BaseModel):
    name: str | None = None
    description: str | None = None
    system_prompt: str | None = None
    color: str | None = None
    tools: dict[str, str] | None = None


@app.get("/tools")
def list_tools() -> dict[str, Any]:
    """Available tools + the global on/off map (missing = on)."""
    cfg = settings()
    modes = toolbox.effective(cfg.get("tools") or {}, None, None)
    return {"tools": toolbox.list(), "modes": modes}


@app.get("/projects")
def list_projects() -> list[dict[str, Any]]:
    return [{**s, "stats": projects.stats(s["id"])} for s in projects.list()]


@app.post("/projects")
def create_project(body: ProjectIn) -> dict[str, Any]:
    return projects.create(body.name, body.description, body.system_prompt, body.color)


@app.put("/projects/{id}")
def update_project(id: str, body: ProjectPatch) -> dict[str, Any]:
    s = projects.update(id, body.model_dump())
    if not s:
        raise HTTPException(404)
    return s


@app.delete("/projects/{id}")
def delete_project(id: str) -> dict[str, bool]:
    projects.delete(id)
    return {"ok": True}


@app.get("/projects/global/stats")
def global_stats() -> dict[str, int]:
    return projects.stats(None)


# ---------------- conversations ----------------
class ConvIn(BaseModel):
    project_id: str | None = None
    title: str = "New chat"
    model: str | None = None


class ConvPatch(BaseModel):
    title: str | None = None
    model: str | None = None
    settings: dict[str, Any] | None = None


@app.get("/conversations")
def list_conversations(project_id: str | None = None) -> list[dict[str, Any]]:
    return convos.list(sid(project_id))


@app.post("/conversations")
def create_conversation(body: ConvIn) -> dict[str, Any]:
    return convos.create(wsid(body.project_id), body.title, body.model or settings()["defaultModel"])


@app.get("/conversations/{id}")
def get_conversation(id: str) -> dict[str, Any]:
    c = convos.get(id)
    if not c:
        raise HTTPException(404)
    return c


@app.patch("/conversations/{id}")
def patch_conversation(id: str, body: ConvPatch) -> dict[str, Any]:
    c = convos.update(id, body.model_dump(exclude_none=True))
    if not c:
        raise HTTPException(404)
    return c


@app.delete("/conversations/{id}")
def delete_conversation(id: str) -> dict[str, bool]:
    convos.delete(id)
    return {"ok": True}


@app.delete("/conversations/{id}/messages/{mid}")
def delete_message(id: str, mid: str) -> dict[str, bool]:
    convos.delete_message(mid)
    return {"ok": True}


class ChatIn(BaseModel):
    content: str | None = None  # None = regenerate from existing history
    model: str | None = None


RENDER_HINT = """## Rendering
Besides normal markdown, the UI renders two fenced code blocks inline:
- ```chart — a small JSON spec for a data chart: {"type": "bar" | "line" | "area" | "pie" | "scatter", "title": "...", "x": "<key used for the x axis / category>", "series": ["<numeric key>", ...], "data": [{"<x key>": ..., "<numeric key>": ..., ...}, ...], "stacked": false, "xLabel": "...", "yLabel": "...", "unit": ""}. `data` is an array of objects (one per x value); keep it under 200 rows. Use a chart whenever numbers would be clearer that way (comparisons, trends, breakdowns).
- ```mermaid — diagrams (flowchart, sequenceDiagram, gantt, mindmap, timeline, ...).
Only chart real values you have or computed; never invent data for decoration. Text before and after a block is shown as usual."""

TOOLS_HINT = "You have tools. Use them when they would make the answer more accurate or current; otherwise answer directly. After using tools, write the final answer for the user."


BUDGET_STOP = ("Out of budget ({axis}): this tool call was not executed and no further tool calls will run. "
               "Write the best final answer you can from what you already have, and say in one line what is still missing.")
SOFT_NUDGE = ("Budget check: about {pct}% of this reply's budget is used. "
              "Make at most one or two more tool calls, then write the final answer.")
LOOP_STOP = ("{name} has been called with identical arguments {n} times in a row, so this reply is stopping tool use. "
             "Answer with what you already have, and say in one line what you could not finish.")
REPEAT_LIMIT = 5
TOOL_ERROR_LIMIT = 3


class Budget:
    """Rounds / tokens / wall-clock / USD for one reply. 0 on any axis means unlimited; approval waits do not count."""

    def __init__(self, cfg: dict[str, Any]):
        self.max_rounds = int(cfg.get("maxToolRounds") or 0)
        self.max_tokens = int(cfg.get("maxRunTokens") or 0)
        self.max_seconds = float(cfg.get("maxRunSeconds") or 0)
        self.max_cost = float(cfg.get("maxRunCost") or 0)
        self.t0, self.paused = time.monotonic(), 0.0
        self.rounds = self.tokens = 0
        self.cost = 0.0
        self.nudged = False

    def add(self, pt: int, ct: int, cost: float | None) -> None:
        self.tokens += pt + ct
        self.cost += cost or 0.0  # an unpriced model simply does not use the cost axis

    def elapsed(self) -> float:
        return time.monotonic() - self.t0 - self.paused

    def _ratios(self) -> dict[str, float]:
        return {k: v / lim for k, v, lim in (("rounds", self.rounds, self.max_rounds), ("tokens", self.tokens, self.max_tokens),
                                             ("time", self.elapsed(), self.max_seconds), ("cost", self.cost, self.max_cost)) if lim > 0}

    def fraction(self) -> float:
        return max(self._ratios().values(), default=0.0)

    def exceeded(self) -> str | None:
        return next((k for k, r in self._ratios().items() if r >= 1.0), None)


def _urls(text: str) -> set[str]:
    """URLs the user typed this turn: still fetchable, whole, once the reply has read untrusted content."""
    return {u for u in (m.rstrip(".,;:!?") for m in re.findall(r"https?://[^\s<>\"')]+", text or "")) if u}


def _short(args: dict[str, Any], limit: int = 300) -> dict[str, Any]:
    """Tool arguments for the trace: long strings (code, content) truncated."""
    return {k: (v[:limit] + "…" if isinstance(v, str) and len(v) > limit else v) for k, v in args.items()}


def _title_from(text: str) -> str:
    t = " ".join(text.split())
    return (t[:48].rstrip() + "…") if len(t) > 48 else (t or "New chat")


async def _chat_stream(conv_id: str, body: ChatIn, stop: asyncio.Event, steers: list[dict[str, Any]] | None = None) -> AsyncIterator[tuple[str, Any]]:
    """Yield (event, payload) pairs. The run bus formats them and fans them out; see runs.sse."""
    conv = convos.get(conv_id)
    if not conv:
        yield "error", {"message": "Conversation not found"}
        return
    cfg = settings()
    model = body.model or conv["model"]
    if body.model and body.model != conv["model"]:
        convos.update(conv_id, {"model": body.model})

    if body.content is not None:
        user_text = body.content.strip()
        if not user_text:
            yield "error", {"message": "Empty message"}
            return
        um = convos.add_message(conv_id, "user", user_text)
        yield "user_message", um
        if conv["title"] == "New chat" and not [m for m in conv["messages"] if m["role"] == "user"]:
            title = _title_from(user_text)
            convos.update(conv_id, {"title": title})
            yield "title", {"id": conv_id, "title": title}
    else:
        # regenerate: drop trailing assistant message
        msgs = conv["messages"]
        if msgs and msgs[-1]["role"] == "assistant":
            convos.delete_message(msgs[-1]["id"])
            yield "removed_message", {"id": msgs[-1]["id"]}
        users = [m for m in msgs if m["role"] == "user"]
        if not users:
            yield "error", {"message": "Nothing to regenerate"}
            return
        user_text = users[-1]["content"]

    tracer = Tracer()
    llm.usage_context.set({"conversation_id": conv_id, "project_id": conv["project_id"]})
    await pricing.refresh(cfg)
    history = convos.history(conv_id)
    project = projects.get(conv["project_id"]) if conv["project_id"] else None
    cspan = tracer.start("context", "Assemble context", {"model": model})
    system, used = build_context(
        memories=memories, graph=graph, documents=documents,
        project=project, project_id=conv["project_id"], query=user_text,
        settings=cfg, conv_settings=conv["settings"], global_system_prompt=cfg["systemPrompt"],
    )
    tracer.end(cspan, {"memories": len(used["memories"]), "entities": len(used["nodes"]), "excerpts": len(used["chunks"]),
                       "history_messages": len(history)})

    am = convos.add_message(conv_id, "assistant", "", model=model)

    _active[am["id"]] = stop
    buf: list[str] = []
    error: str | None = None
    tool_events: list[dict[str, Any]] = []
    tool_ctx: dict[str, Any] = {
        "project_id": conv["project_id"], "conversation_id": conv_id,
        # Taint is sticky for the whole conversation: the injected instructions live on in the replayed history, so
        # waiting one turn must not re-arm a standing 'always' grant. Only the user clears it (Context -> this chat).
        "tainted": bool(conv["settings"].get("tainted")), "taint_sources": list(conv["settings"].get("taint_sources") or []),
        "allowed_urls": _urls(user_text), "settings": cfg,
    }
    modes = toolbox.effective(cfg.get("tools") or {}, (project or {}).get("tools"), conv["settings"].get("tools")) if conv["settings"].get("useTools", True) else {}
    tool_schemas = toolbox.schemas(modes)
    system = "\n\n".join(p for p in (system, RENDER_HINT, TOOLS_HINT if tool_schemas else "") if p)
    used["system_prompt"] = system
    used["tokens_estimate"] = estimate_tokens(system)
    messages = [{"role": "system", "content": system}] + history
    yield "assistant_message", {**am, "context_used": used}
    yield "span", {"message_id": am["id"], "span": cspan}

    budget = Budget(cfg)
    partial: str | None = None
    last_sig: str | None = None
    repeats = 0
    tool_errors: dict[str, int] = {}
    blocked: set[str] = set()
    _round = 0

    async def _final_round() -> AsyncIterator[tuple[str, Any]]:
        """Closing answer after a budget or breaker stop: one tool-free call, itself exempt from the budget."""
        if buf and buf[-1] and not buf[-1].endswith("\n"):
            buf.append("\n\n")
            yield "delta", {"id": am["id"], "text": "\n\n"}
        span = tracer.start("llm", model, {"round": _round, "final": True, "messages": len(messages), "tools": len(tool_schemas)})
        yield "span", {"message_id": am["id"], "span": span}
        start, fin = len(buf), {}
        # tools are still declared, with tool_choice "none": the history holds tool_calls, and some OpenAI-compatible
        # backends reject that when no tool list is sent. "none" is the portable way to say "answer, do not call".
        async for ev in llm.stream_chat(cfg, model, messages, tool_schemas or None,
                                        effort=str(conv["settings"].get("effort") or "default"), tool_choice="none"):
            if stop.is_set():
                break
            if ev["type"] == "delta":
                buf.append(ev["text"])
                yield "delta", {"id": am["id"], "text": ev["text"]}
            else:
                fin = ev
        tracer.end(span, {"finish_reason": fin.get("finish_reason"), "usage": fin.get("usage"),
                          "output_chars": len("".join(buf[start:]))},
                   error="Stopped by user" if stop.is_set() else None)
        yield "span", {"message_id": am["id"], "span": span}

    try:
        while True:
            if stop.is_set():
                break
            # Steered messages fold in at a round boundary: tool results are already appended, so the
            # user turn lands after them and the assistant/tool message ordering stays legal. The
            # current reply segment closes with its own `done`, and a fresh assistant message answers.
            if steers:
                steered, steers[:] = list(steers), []
                # A segment that already streamed or ran tools closes cleanly; an untouched one is reused.
                if buf or tool_events:
                    convos.finish_message(am["id"], "".join(buf).strip(), None, used, tool_events, tracer.spans)
                    _active.pop(am["id"], None)
                    yield "done", {"id": am["id"], "error": None, "context_used": used, "tool_events": tool_events,
                                   "trace": tracer.spans, "stopped": False, "partial": partial,
                                   "tainted": tool_ctx["tainted"], "taint_sources": tool_ctx["taint_sources"]}
                    am = convos.add_message(conv_id, "assistant", "", model=model)
                    _active[am["id"]] = stop
                    buf = []
                    tool_events = []
                    tracer = Tracer()
                    yield "assistant_message", {**am, "context_used": used}
                for um in steered:
                    messages.append({"role": "user", "content": um["content"]})
                    user_text = um["content"]
                    tool_ctx["allowed_urls"] |= _urls(um["content"])
            _round += 1
            budget.rounds = _round - 1  # rounds already completed: the Nth round's tool calls must still be allowed to run
            round_start = len(buf)
            end: dict[str, Any] = {}
            lspan = tracer.start("llm", model, {"round": _round, "messages": len(messages), "tools": len(tool_schemas)})
            yield "span", {"message_id": am["id"], "span": lspan}
            first_token: int | None = None
            async for ev in llm.stream_chat(cfg, model, messages, tool_schemas or None, effort=str(conv["settings"].get("effort") or "default")):
                if stop.is_set():
                    break
                if ev["type"] == "delta":
                    if first_token is None:
                        first_token = now_ms()
                    buf.append(ev["text"])
                    yield "delta", {"id": am["id"], "text": ev["text"]}
                else:
                    end = ev
            calls = end.get("tool_calls") or []
            u = end.get("usage") or end.get("usage_est") or {}
            pt, ct = int(u.get("prompt_tokens") or 0), int(u.get("completion_tokens") or 0)
            budget.add(pt, ct, pricing.cost(cfg, model, pt, ct))
            tracer.end(lspan, {"finish_reason": end.get("finish_reason"), "usage": end.get("usage"),
                               "ttft_ms": (first_token - lspan["start"]) if first_token else None,
                               "output_chars": len("".join(buf[round_start:])), "tool_calls": [c["name"] for c in calls]},
                       error="Stopped by user" if stop.is_set() else None)
            yield "span", {"message_id": am["id"], "span": lspan}
            if stop.is_set():
                break
            if not calls:
                # A steer that arrived while this answer streamed: keep the model's own turn in its
                # context, then loop back so the top of the loop closes this segment and a new one
                # replies to it.
                if steers:
                    messages.append({"role": "assistant", "content": "".join(buf[round_start:]).strip() or ""})
                    continue
                break
            turn = {"role": "assistant", "content": "".join(buf[round_start:]).strip() or None,
                    "tool_calls": [{"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": c["arguments"] or "{}"}} for c in calls]}
            over = budget.exceeded()
            if over:
                # Out of budget: never drop the pending calls silently — answer each one, then let the model close out.
                partial = over
                messages.append(turn)
                for c in calls:
                    messages.append({"role": "tool", "tool_call_id": c["id"], "content": BUDGET_STOP.format(axis=over)})
                async for chunk in _final_round():
                    yield chunk
                break
            # execute tool calls, then continue the loop with their results
            messages.append(turn)
            if buf and buf[-1] and not buf[-1].endswith("\n"):
                buf.append("\n\n")
                yield "delta", {"id": am["id"], "text": "\n\n"}
            for c in calls:
                try:
                    args = json.loads(c["arguments"] or "{}")
                    if not isinstance(args, dict):
                        args = {}
                except ValueError:
                    args = {"_raw": c["arguments"]}
                sig = tools.call_key(c["name"], args)
                repeats = repeats + 1 if sig == last_sig else 1
                last_sig = sig
                if repeats >= REPEAT_LIMIT:  # before the approval gate: denying the same call forever is still a loop
                    partial = "loop"
                if partial == "loop":  # every pending call still needs a tool message, executed or not
                    messages.append({"role": "tool", "tool_call_id": c["id"], "content": LOOP_STOP.format(name=c["name"], n=REPEAT_LIMIT)})
                    continue
                raw_mode = modes.get(c["name"], "off")
                mode = toolbox.gate(c["name"], raw_mode, tool_ctx)
                forced = mode != raw_mode  # untrusted content in this reply upgraded on -> ask
                # The provider's call id is only unique within one request -- llm.stream_chat falls back
                # to "call_<idx>" when the provider omits one -- so two conversations streaming at once
                # both produce "call_0". Key anything cross-conversation by the message id too, or one
                # chat's approval resolves another chat's call. The model still sees c["id"].
                uid = f"{am['id']}:{c['id']}"
                yield "tool_call", {"message_id": am["id"], "id": uid, "name": c["name"], "arguments": args,
                                    "needs_approval": mode == "ask", "forced": forced}
                tspan = tracer.start("tool", c["name"], {"round": _round, "arguments": _short(args), "mode": mode, "forced": forced})
                yield "span", {"message_id": am["id"], "span": tspan}
                t0 = time.time()
                decision = "allow"
                if mode == "ask":
                    # Pause the reply until the user approves or denies this call (POST /approvals/{call_id}).
                    approval_t0 = time.time()
                    fut: asyncio.Future = asyncio.get_event_loop().create_future()
                    _approvals[uid] = fut
                    try:
                        waited = 0.0
                        while not fut.done():
                            if stop.is_set():
                                fut.set_result("deny")
                                break
                            try:
                                # Short, so an approval-blocked run notices a stop or a shutdown promptly.
                                await asyncio.wait_for(asyncio.shield(fut), timeout=2)
                            except asyncio.TimeoutError:
                                waited += 2
                                if waited >= 600:
                                    fut.set_result("deny")
                        decision = fut.result()
                    finally:
                        _approvals.pop(uid, None)
                    budget.paused += time.time() - approval_t0  # a slow approval must not blow the wall clock
                    t0 = time.time()  # don't count waiting time as tool time
                    granted = decision in ("always_chat", "always_global")
                    if forced and granted:
                        decision = "allow"  # one-shot: a tainted reply cannot buy a standing grant
                    elif decision == "always_chat":
                        convos.update(conv_id, {"settings": {"tools": {**(conv["settings"].get("tools") or {}), c["name"]: "on"}}})
                        conv["settings"].setdefault("tools", {})[c["name"]] = "on"
                        modes[c["name"]] = "on"
                        decision = "allow"
                    elif decision == "always_global":
                        db.set_settings({"tools": {**(cfg.get("tools") or {}), c["name"]: "on"}})
                        modes[c["name"]] = "on"
                        decision = "allow"
                    if granted and not forced:
                        tool_schemas = toolbox.schemas(modes)  # the grant changed modes; keep the schemas in step
                was_tainted, was_blocked = tool_ctx["tainted"], c["name"] in blocked
                if was_blocked:
                    result: Any = tools.denied(c["name"], f"failing {TOOL_ERROR_LIMIT} times in a row and disabled for the rest of this reply")
                elif mode == "off":
                    result = tools.denied(c["name"], "turned off for this chat")
                elif decision != "allow":
                    result = tools.denied(c["name"], "just declined by the user")
                else:
                    result = await toolbox.call(c["name"], args, tool_ctx)
                ms = int((time.time() - t0) * 1000)
                # images (e.g. matplotlib figures from run_python) go to the UI, not to the model
                images = result.pop("images", None) if isinstance(result, dict) else None
                preview = summarize_result(result)
                err = result.get("error") if isinstance(result, dict) else None
                tool_errors[c["name"]] = tool_errors.get(c["name"], 0) + 1 if err else 0  # reset on success = consecutive
                if tool_errors[c["name"]] >= TOOL_ERROR_LIMIT:
                    blocked.add(c["name"])
                tainted = decision == "allow" and not err and toolbox.taints(c["name"])
                if tainted:
                    if not was_tainted:
                        yield "taint", {"message_id": am["id"], "source": c["name"]}
                    tool_ctx["taint_sources"].append(c["name"])
                event = {"id": uid, "name": c["name"], "arguments": args, "result_preview": preview, "duration_ms": ms,
                         "error": err, "images": images or None, "approval": (decision if mode == "ask" else None),
                         "forced": forced, "tainted": tainted, "blocked": c["name"] if was_blocked else None, "breaker": partial}
                tracer.end(tspan, {"result_chars": len(preview), "images": len(images or [])}, error=err)
                tool_events.append(event)
                yield "tool_result", {"message_id": am["id"], **event}
                yield "span", {"message_id": am["id"], "span": tspan}
                for_model = {**result, "images_shown_to_user": [i["name"] for i in images]} if images and isinstance(result, dict) else result
                messages.append({"role": "tool", "tool_call_id": c["id"], "content": summarize_result(for_model, 24000)})
            if partial == "loop":
                async for chunk in _final_round():
                    yield chunk
                break
            if not budget.nudged and budget.fraction() >= 0.6:
                budget.nudged = True
                messages.append({"role": "system", "content": SOFT_NUDGE.format(pct=int(budget.fraction() * 100))})
    except asyncio.CancelledError:
        # Shutdown or a dropped task, not a user Stop: persist what was written and re-raise.
        text = "".join(buf).strip()
        convos.finish_message(am["id"], text, None if text else "Cancelled", used, tool_events, tracer.spans)
        convos.touch(conv_id)
        raise
    except Exception as e:  # noqa: BLE001
        error = str(e)
        for s in tracer.fail_open(error):
            yield "span", {"message_id": am["id"], "span": s}
    finally:
        _active.pop(am["id"], None)

    text = "".join(buf).strip()
    convos.finish_message(am["id"], text, error, used, tool_events, tracer.spans)
    convos.touch(conv_id)
    if tool_ctx["tainted"]:
        srcs = sorted(set(tool_ctx["taint_sources"]))
        if not conv["settings"].get("tainted") or srcs != sorted(set(conv["settings"].get("taint_sources") or [])):
            convos.update(conv_id, {"settings": {"tainted": True, "taint_sources": srcs}})
    yield "done", {"id": am["id"], "error": error, "context_used": used, "tool_events": tool_events,
                   "trace": tracer.spans, "stopped": stop.is_set(), "partial": partial,
                   "tainted": tool_ctx["tainted"], "taint_sources": tool_ctx["taint_sources"]}
    if tool_ctx.get("learned"):
        yield "learned", tool_ctx["learned"]

    if not error and text and cfg.get("autoLearn", True) and conv["settings"].get("autoLearn", True):
        lspan = tracer.start("learn", cfg.get("extractionModel") or model)
        yield "span", {"message_id": am["id"], "span": lspan}
        try:
            learned = await learn_from_exchange(
                settings=cfg, memories=memories, graph=graph, project_id=conv["project_id"],
                user_text=user_text, assistant_text=text, model=model,
            )
            tracer.end(lspan, {"memories": len(learned["memories"]), "entities": len(learned["nodes"]), "relations": len(learned["edges"])})
            yield "span", {"message_id": am["id"], "span": lspan}
            if learned["memories"] or learned["nodes"] or learned["edges"]:
                yield "learned", learned
        except Exception as e:  # noqa: BLE001
            tracer.end(lspan, error=str(e))
            yield "span", {"message_id": am["id"], "span": lspan}
            yield "learn_error", {"message": str(e)}
        convos.set_trace(am["id"], tracer.spans)


async def _run_chat(run: Run, body: ChatIn) -> None:
    async for event, data in _chat_stream(run.conversation_id, body, run.stop, run.steers):
        if event == "assistant_message":
            run.message_id = data.get("id")
        run.publish(event, data)


@app.post("/conversations/{id}/chat")
async def chat(id: str, body: ChatIn) -> dict[str, Any]:
    """Start the reply as a background task. Watch it on GET /conversations/{id}/stream?since=seq."""
    if not convos.get(id):
        raise HTTPException(404, "Conversation not found")
    running = bus.live(id)
    if running:
        raise HTTPException(409, {"message": "That conversation already has a running reply",
                                 "run_id": running.run_id, "seq": running.seq})
    run = bus.start(id, lambda r: _run_chat(r, body))
    return {"run_id": run.run_id, "seq": run.seq}


class SteerIn(BaseModel):
    content: str


@app.post("/conversations/{id}/steer")
async def steer_run(id: str, body: SteerIn) -> dict[str, Any]:
    """Inject a user message into the live run. The message is persisted and published here, so it
    survives even if the run ends before folding it in; the run answers it in a fresh segment."""
    if not convos.get(id):
        raise HTTPException(404, "Conversation not found")
    text = (body.content or "").strip()
    if not text:
        raise HTTPException(400, "Empty message")
    run = bus.live(id)
    if not run:
        raise HTTPException(409, {"message": "No running reply to steer"})
    um = convos.add_message(id, "user", text)
    run.publish("user_message", um)
    run.steers.append(um)
    return {"ok": True, "run_id": run.run_id, "message": um}


@app.get("/conversations/{id}/stream")
async def stream_conversation(id: str, since: int = 0) -> StreamingResponse:
    """Any number of clients may attach; detaching one never touches the run."""
    run = bus.get(id)
    return StreamingResponse(run.subscribe(since) if run else iter(()), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/runs")
async def list_runs() -> list[dict[str, Any]]:
    return bus.list()


# async, so the run's asyncio.Event is set on the loop that owns it rather than from a threadpool.
@app.post("/conversations/{id}/stop")
async def stop_run(id: str, run_id: str | None = None) -> dict[str, bool]:
    """Stop before the assistant message exists: detaching a stream would only drop a viewer."""
    return {"ok": bus.stop(id, run_id)}


class ApprovalIn(BaseModel):
    decision: str  # allow | deny | always_chat | always_global


@app.post("/approvals/{call_id}")
def approve_tool_call(call_id: str, body: ApprovalIn) -> dict[str, bool]:
    fut = _approvals.get(call_id)
    if not fut or fut.done():
        raise HTTPException(404, "No pending approval for that call")
    if body.decision not in ("allow", "deny", "always_chat", "always_global"):
        raise HTTPException(400, "Bad decision")
    fut.set_result(body.decision)
    return {"ok": True}


# ---------------- usage / cost ----------------
@app.get("/usage")
async def usage_report(days: int = 30) -> dict[str, Any]:
    cfg = settings()
    await pricing.refresh(cfg)
    return {**usage.report(days), "prices": pricing.table(cfg)}


class PricesIn(BaseModel):
    modelPrices: dict[str, dict[str, float | None]]


@app.put("/usage/prices")
async def put_prices(body: PricesIn) -> dict[str, Any]:
    """Save per-model price overrides ($ per million tokens) and re-price the whole log."""
    clean = {m: {"input": float(p.get("input") or 0), "output": float(p.get("output") or 0)} for m, p in body.modelPrices.items()
             if p.get("input") is not None or p.get("output") is not None}
    db.set_settings({"modelPrices": clean})
    cfg = settings()
    await pricing.refresh(cfg, force=True)
    return {"repriced": usage.reprice(pricing, cfg), "prices": pricing.table(cfg)}


@app.post("/messages/{mid}/stop")
def stop_message(mid: str) -> dict[str, bool]:
    ev = _active.get(mid)
    if ev:
        ev.set()
    return {"ok": ev is not None}


class ContextPreviewIn(BaseModel):
    project_id: str | None = None
    query: str = ""
    conv_settings: dict[str, Any] = {}


@app.post("/context/preview")
def context_preview(body: ContextPreviewIn) -> dict[str, Any]:
    cfg = settings()
    project = projects.get(body.project_id) if sid(body.project_id) else None
    _, used = build_context(
        memories=memories, graph=graph, documents=documents, project=project, project_id=sid(body.project_id),
        query=body.query, settings=cfg, conv_settings={"useMemory": True, "useGraph": True, "useDocuments": True, **body.conv_settings},
        global_system_prompt=cfg["systemPrompt"],
    )
    return used


# ---------------- memories ----------------
class MemoryIn(BaseModel):
    project_id: str | None = None
    content: str
    kind: str = "fact"
    pinned: bool = False


class MemoryPatch(BaseModel):
    content: str | None = None
    kind: str | None = None
    pinned: bool | None = None
    project_id: str | None = None
    move_to_global: bool = False


@app.get("/memories")
def list_memories(project_id: str | None = None, q: str = "", include_global: bool = True) -> list[dict[str, Any]]:
    return memories.list(sid(project_id), q, include_global)


@app.post("/memories")
def create_memory(body: MemoryIn) -> dict[str, Any]:
    if not body.content.strip():
        raise HTTPException(400, "Empty memory")
    return memories.create(wsid(body.project_id), body.content, body.kind, "user", body.pinned)


@app.put("/memories/{id}")
def update_memory(id: str, body: MemoryPatch) -> dict[str, Any]:
    patch = body.model_dump(exclude_none=True, exclude={"move_to_global"})
    if body.move_to_global:
        patch["project_id"] = None
    elif "project_id" in patch:
        patch["project_id"] = wsid(patch["project_id"])
    m = memories.update(id, patch)
    if not m:
        raise HTTPException(404)
    return m


@app.delete("/memories/{id}")
def delete_memory(id: str) -> dict[str, bool]:
    memories.delete(id)
    return {"ok": True}


# ---------------- knowledge graph ----------------
class NodeIn(BaseModel):
    project_id: str | None = None
    label: str
    type: str = "entity"
    properties: dict[str, Any] = {}


class NodePatch(BaseModel):
    label: str | None = None
    type: str | None = None
    properties: dict[str, Any] | None = None


class EdgeIn(BaseModel):
    project_id: str | None = None
    source_id: str
    target_id: str
    relation: str
    properties: dict[str, Any] = {}


class EdgePatch(BaseModel):
    relation: str | None = None
    properties: dict[str, Any] | None = None


@app.get("/graph")
def get_graph(project_id: str | None = None, include_global: bool = True) -> dict[str, Any]:
    return graph.get(sid(project_id), include_global)


@app.post("/graph/nodes")
def create_node(body: NodeIn) -> dict[str, Any]:
    if not body.label.strip():
        raise HTTPException(400, "Empty label")
    return graph.upsert_node(wsid(body.project_id), body.label, body.type, body.properties)


@app.put("/graph/nodes/{id}")
def update_node(id: str, body: NodePatch) -> dict[str, Any]:
    n = graph.update_node(id, body.model_dump(exclude_none=True))
    if not n:
        raise HTTPException(404)
    return n


@app.delete("/graph/nodes/{id}")
def delete_node(id: str) -> dict[str, bool]:
    graph.delete_node(id)
    return {"ok": True}


@app.post("/graph/edges")
def create_edge(body: EdgeIn) -> dict[str, Any]:
    if body.source_id == body.target_id:
        raise HTTPException(400, "Self-loops not allowed")
    if not (graph.get_node(body.source_id) and graph.get_node(body.target_id)):
        raise HTTPException(404, "Node not found")
    return graph.upsert_edge(wsid(body.project_id), body.source_id, body.target_id, body.relation, body.properties)


@app.put("/graph/edges/{id}")
def update_edge(id: str, body: EdgePatch) -> dict[str, Any]:
    e = graph.update_edge(id, body.model_dump(exclude_none=True))
    if not e:
        raise HTTPException(404)
    return e


@app.delete("/graph/edges/{id}")
def delete_edge(id: str) -> dict[str, bool]:
    graph.delete_edge(id)
    return {"ok": True}


# ---------------- documents ----------------
@app.get("/documents")
def list_documents(project_id: str | None = None, include_global: bool = True) -> list[dict[str, Any]]:
    return documents.list(sid(project_id), include_global)


@app.get("/documents/{id}")
def get_document(id: str) -> dict[str, Any]:
    d = documents.get(id)
    if not d:
        raise HTTPException(404)
    return d


@app.post("/documents")
async def upload_document(file: UploadFile = File(...), project_id: str | None = Form(None)) -> dict[str, Any]:
    data = await file.read()
    name = file.filename or "untitled"
    try:
        text = extract_text(name, data, file.content_type or "")
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, str(e)) from e
    dest = db.data_dir / "uploads" / f"{new_id()}-{Path(name).name}"
    dest.write_bytes(data)
    return documents.create(wsid(project_id), name, file.content_type or "", len(data), str(dest), text)


@app.delete("/documents/{id}")
def delete_document(id: str) -> dict[str, bool]:
    path = documents.delete(id)
    if path:
        try:
            Path(path).unlink()
        except OSError:
            pass
    return {"ok": True}


@app.get("/documents/{id}/search")
def search_documents(id: str, q: str) -> list[dict[str, Any]]:  # convenience for a single doc
    d = documents.get(id)
    if not d:
        raise HTTPException(404)
    return [h for h in documents.search(d["project_id"], q, limit=20) if h["document_id"] == id]


@app.on_event("shutdown")
async def _shutdown() -> None:
    await bus.shutdown()  # before the rmtree: a live run's sandboxed run_python writes in there
    shutil.rmtree(db.data_dir / "tmp", ignore_errors=True)


# ---------------- todos ----------------
class TodoIn(BaseModel):
    title: str
    project_id: str | None = None
    notes: str = ""
    due: str | None = None
    priority: int = 2


class TodoPatch(BaseModel):
    title: str | None = None
    notes: str | None = None
    due: str | None = None
    priority: int | None = None
    done: bool | None = None
    project_id: str | None = None
    clear_due: bool = False
    clear_project: bool = False


@app.get("/todos")
def list_todos(project_id: str | None = "all", include_done: bool = False, q: str = "") -> list[dict[str, Any]]:
    scope = "__all__" if project_id in (None, "all") else sid(project_id)
    return todos.list(scope, include_done, q)


@app.post("/todos")
def create_todo(body: TodoIn) -> dict[str, Any]:
    if not body.title.strip():
        raise HTTPException(400, "Empty title")
    return todos.create(body.title, wsid(body.project_id), body.notes, body.due, body.priority)


@app.put("/todos/{id}")
def update_todo(id: str, body: TodoPatch) -> dict[str, Any]:
    patch = body.model_dump(exclude_none=True, exclude={"clear_due", "clear_project"})
    if body.clear_due:
        patch["due"] = None
    if body.clear_project:
        patch["project_id"] = None
    elif "project_id" in patch:
        patch["project_id"] = wsid(patch["project_id"])
    t = todos.update(id, patch)
    if not t:
        raise HTTPException(404)
    return t


@app.delete("/todos/{id}")
def delete_todo(id: str) -> dict[str, bool]:
    todos.delete(id)
    return {"ok": True}


# ---------------- Google integration ----------------
@app.get("/integrations/google/status")
def google_status() -> dict[str, Any]:
    return google.status()


@app.post("/integrations/google/auth/start")
def google_auth_start(request: Request) -> dict[str, str]:
    redirect = str(request.base_url).rstrip("/") + "/integrations/google/callback"
    try:
        return {"url": google.start_auth(redirect)}
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


def _oauth_page(title: str, body: str, ok: bool = False) -> str:
    return (
        "<html><body style='font-family:system-ui;padding:40px;background:#1a1918;color:#ecebe8'>"
        f"<h2>{html.escape(title)}</h2><p style='max-width:52ch;line-height:1.5;color:#b9b6b0'>{body}</p>"
        + ("<script>setTimeout(() => window.close(), 2000)</script>" if ok else "")
        + "</body></html>"
    )


@app.get("/integrations/google/callback", response_class=HTMLResponse)
def google_callback(state: str = "", code: str = "", error: str = "", error_description: str = "") -> str:
    if error or not code:
        detail = error_description or error or "Google did not send an authorization code."
        if error == "access_denied":
            detail += " If the consent screen is in testing mode, add this account as a test user."
        log.warning("Google OAuth callback error: %s", detail)
        return _oauth_page("Google sign-in failed", html.escape(detail))
    try:
        st = google.finish_auth(state, code)
    except Exception as e:  # noqa: BLE001
        log.warning("Google OAuth callback could not finish: %s", e)
        return _oauth_page("Could not complete sign-in", html.escape(str(e)))
    return _oauth_page(
        f"Connected {st.get('email') or 'Google account'} ✓",
        "You can close this tab and return to Personal OS.",
        ok=True,
    )


@app.post("/integrations/google/disconnect")
def google_disconnect() -> dict[str, Any]:
    google.disconnect()
    return google.status()


def _gcall(fn, *args):  # type: ignore[no-untyped-def]
    try:
        return json_safe(fn(*args))
    except GoogleNotConnected as e:
        raise HTTPException(409, str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"Google API error: {e}") from e


@app.get("/integrations/google/calendar")
def google_calendar(days: int = 2, start: str | None = None) -> Any:
    return _gcall(google.calendar_events, days, "primary", 60, start)


class EventIn(BaseModel):
    summary: str
    start: str
    end: str | None = None
    description: str = ""
    location: str = ""


@app.post("/integrations/google/calendar")
def google_calendar_create(body: EventIn) -> Any:
    return _gcall(google.calendar_create, body.summary, body.start, body.end, body.description, body.location)


@app.get("/integrations/google/gmail")
def google_gmail(q: str = "is:unread newer_than:3d", max_results: int = 12) -> Any:
    return _gcall(google.gmail_search, q, max_results)


@app.get("/integrations/google/gmail/{message_id}")
def google_gmail_message(message_id: str) -> Any:
    return _gcall(google.gmail_get, message_id)


@app.get("/integrations/google/tasks")
def google_tasks(show_completed: bool = False) -> Any:
    return _gcall(google.tasks_list, "@default", show_completed)


# ---------------- dashboard ----------------
@app.get("/dashboard")
async def dashboard() -> dict[str, Any]:
    st = google.status()
    out: dict[str, Any] = {
        "google": st,
        "todos": todos.list("__all__", include_done=False)[:12],
        "todo_stats": todos.stats(),
        "projects": [{**p, "stats": projects.stats(p["id"])} for p in projects.list()],
        "recent_memories": memories.list(ALL)[:6],
        "recent_conversations": convos.list(ALL)[:6],
        "calendar": None, "gmail": None, "errors": {},
    }
    if st["connected"]:
        async def cal() -> None:
            try:
                out["calendar"] = json_safe(await asyncio.to_thread(google.calendar_events, 2))
            except Exception as e:  # noqa: BLE001
                out["errors"]["calendar"] = str(e)

        async def mail() -> None:
            try:
                out["gmail"] = json_safe(await asyncio.to_thread(google.gmail_search, "is:unread newer_than:3d", 10))
            except Exception as e:  # noqa: BLE001
                out["errors"]["gmail"] = str(e)
        await asyncio.gather(cal(), mail())
    return out


# ---------------- boards (kanban) ----------------
class BoardIn(BaseModel):
    name: str
    project_id: str | None = None
    columns: list[str] | None = None


class BoardPatch(BaseModel):
    name: str | None = None
    project_id: str | None = None


class ColumnIn(BaseModel):
    name: str


class ColumnPatch(BaseModel):
    name: str | None = None
    position: int | None = None
    wip_limit: int | None = None


class CardIn(BaseModel):
    title: str
    column_id: str | None = None
    description: str = ""
    due: str | None = None
    priority: int = 2
    labels: list[str] = []


class CardPatch(BaseModel):
    title: str | None = None
    description: str | None = None
    due: str | None = None
    priority: int | None = None
    labels: list[str] | None = None
    clear_due: bool = False


class MoveIn(BaseModel):
    column_id: str
    before_card_id: str | None = None


@app.get("/boards")
def list_boards() -> list[dict[str, Any]]:
    return boards.list()


@app.post("/boards")
def create_board(body: BoardIn) -> dict[str, Any]:
    return boards.create(body.name, wsid(body.project_id), body.columns)


@app.get("/boards/{id}")
def get_board(id: str) -> dict[str, Any]:
    b = boards.get(id)
    if not b:
        raise HTTPException(404)
    return b


@app.put("/boards/{id}")
def update_board(id: str, body: BoardPatch) -> dict[str, Any]:
    b = boards.update(id, body.model_dump(exclude_none=True))
    if not b:
        raise HTTPException(404)
    return b


@app.delete("/boards/{id}")
def delete_board(id: str) -> dict[str, bool]:
    boards.delete(id)
    return {"ok": True}


@app.post("/boards/{id}/columns")
def add_column(id: str, body: ColumnIn) -> dict[str, Any]:
    return boards.add_column(id, body.name)


@app.put("/boards/columns/{cid}")
def update_column(cid: str, body: ColumnPatch) -> dict[str, bool]:
    boards.update_column(cid, body.model_dump(exclude_none=True))
    return {"ok": True}


@app.delete("/boards/columns/{cid}")
def delete_column(cid: str) -> dict[str, bool]:
    boards.delete_column(cid)
    return {"ok": True}


@app.post("/boards/{id}/cards")
def add_card(id: str, body: CardIn) -> dict[str, Any]:
    if not body.title.strip():
        raise HTTPException(400, "Empty title")
    return boards.add_card(id, body.column_id, body.title, body.description, body.due, body.priority, body.labels)


@app.put("/boards/cards/{cid}")
def update_card(cid: str, body: CardPatch) -> dict[str, Any]:
    patch = body.model_dump(exclude_none=True, exclude={"clear_due"})
    if body.clear_due:
        patch["due"] = None
    c = boards.update_card(cid, patch)
    if not c:
        raise HTTPException(404)
    return c


@app.post("/boards/cards/{cid}/move")
def move_card(cid: str, body: MoveIn) -> dict[str, Any]:
    c = boards.move_card(cid, body.column_id, body.before_card_id)
    if not c:
        raise HTTPException(404)
    return c


@app.delete("/boards/cards/{cid}")
def delete_card(cid: str) -> dict[str, bool]:
    boards.delete_card(cid)
    return {"ok": True}


# ---------------- dashboards, data sources, widgets, recap ----------------
class SourceIn(BaseModel):
    name: str
    kind: str = "http"
    config: dict[str, Any] = {}
    secret: str = ""
    description: str = ""


class SourcePatch(BaseModel):
    name: str | None = None
    kind: str | None = None
    config: dict[str, Any] | None = None
    secret: str | None = None
    description: str | None = None
    clear_secret: bool = False


class DashboardIn(BaseModel):
    name: str
    description: str = ""


class WidgetIn(BaseModel):
    title: str = ""
    kind: str = "html"           # html | summary | markdown
    prompt: str = ""
    source_ids: list[str] = []
    code: str = ""
    output: str = ""
    width: int = 1
    height: int = 280
    refresh_minutes: int = 60


class WidgetPatch(BaseModel):
    title: str | None = None
    prompt: str | None = None
    source_ids: list[str] | None = None
    code: str | None = None
    output: str | None = None
    width: int | None = None
    height: int | None = None
    position: int | None = None
    refresh_minutes: int | None = None


async def _internal_data() -> dict[str, Any]:
    """Data for 'internal' sources (and for summaries/recaps)."""
    st = google.status()
    out: dict[str, Any] = {
        "todos": todos.list("__all__", include_done=False),
        "memories": memories.list(ALL)[:40],
        "projects": [{**p, "stats": projects.stats(p["id"])} for p in projects.list()],
        "boards": [{**b, **{"cards": (boards.get(b["id"]) or {}).get("cards", [])}} for b in boards.list()],
        "calendar": [], "gmail": [],
    }
    if st["connected"]:
        try:
            out["calendar"] = json_safe(await asyncio.to_thread(google.calendar_events, 3))
        except Exception as e:  # noqa: BLE001
            out["calendar_error"] = str(e)
        try:
            out["gmail"] = json_safe(await asyncio.to_thread(google.gmail_search, "is:unread newer_than:2d", 15))
        except Exception as e:  # noqa: BLE001
            out["gmail_error"] = str(e)
    return out


@app.get("/sources")
def list_sources() -> dict[str, Any]:
    return {"sources": dashboards.sources(), "internal": ["todos", "calendar", "gmail", "memories", "projects", "boards"]}


@app.post("/sources")
def create_source(body: SourceIn) -> dict[str, Any]:
    if body.kind in ("http", "rss") and not body.config.get("url"):
        raise HTTPException(400, "URL required")
    return dashboards.create_source(body.name, body.kind, body.config, body.secret, body.description)


@app.put("/sources/{id}")
def update_source(id: str, body: SourcePatch) -> dict[str, Any]:
    s_ = dashboards.update_source(id, body.model_dump(exclude_none=True))
    if not s_:
        raise HTTPException(404)
    return s_


@app.delete("/sources/{id}")
def delete_source(id: str) -> dict[str, bool]:
    dashboards.delete_source(id)
    return {"ok": True}


@app.get("/sources/{id}/fetch")
async def fetch_source(id: str) -> Any:
    try:
        internal = await _internal_data() if (dashboards.source(id) or {}).get("kind") == "internal" else None
        return await dashboards.fetch_source(id, internal)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, str(e)) from e


@app.get("/dashboards")
def list_dashboards() -> list[dict[str, Any]]:
    return dashboards.list()


@app.post("/dashboards")
def create_dashboard(body: DashboardIn) -> dict[str, Any]:
    return dashboards.create(body.name, body.description)


@app.get("/dashboards/{id}")
def get_dashboard(id: str) -> dict[str, Any]:
    d = dashboards.get(id)
    if not d:
        raise HTTPException(404)
    return d


@app.put("/dashboards/{id}")
def update_dashboard(id: str, body: DashboardIn) -> dict[str, Any]:
    d = dashboards.update(id, body.model_dump())
    if not d:
        raise HTTPException(404)
    return d


@app.delete("/dashboards/{id}")
def delete_dashboard(id: str) -> dict[str, bool]:
    dashboards.delete(id)
    return {"ok": True}


async def _samples(source_ids: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    internal = None
    for sid_ in source_ids:
        try:
            src = dashboards.source(sid_)
            if src and src["kind"] == "internal" and internal is None:
                internal = await _internal_data()
            out[sid_] = await dashboards.fetch_source(sid_, internal)
        except Exception as e:  # noqa: BLE001
            out[sid_] = {"error": str(e)}
    return out


async def _run_widget(w: dict[str, Any], request: Request, regenerate_code: bool = False) -> dict[str, Any]:
    cfg = settings()
    model = cfg.get("extractionModel") or cfg["defaultModel"]
    srcs = [s_ for s_ in (dashboards.source(i) for i in w["source_ids"]) if s_]
    if w["kind"] == "html":
        if regenerate_code or not w["code"]:
            samples = await _samples(w["source_ids"])
            code = await generate_widget_code(cfg, cfg["defaultModel"], w["prompt"] or w["title"], srcs, str(request.base_url).rstrip("/"), w["width"], w["height"], samples)
            w = dashboards.update_widget(w["id"], {"code": code, "refreshed_at": time.time()}) or w
    elif w["kind"] == "summary":
        data = await _samples(w["source_ids"])
        text = await generate_summary(cfg, model, w["prompt"], data)
        w = dashboards.update_widget(w["id"], {"output": text, "refreshed_at": time.time()}) or w
    return w


@app.post("/dashboards/{id}/widgets")
async def create_widget(id: str, body: WidgetIn, request: Request) -> dict[str, Any]:
    if not dashboards.get(id):
        raise HTTPException(404)
    title = body.title.strip() or (body.prompt.strip()[:40] or "Widget")
    w = dashboards.create_widget(id, title, body.kind, body.prompt, body.source_ids, body.code, body.output, body.width, body.height, body.refresh_minutes)
    if body.kind in ("html", "summary"):
        try:
            w = await _run_widget(w, request, regenerate_code=(body.kind == "html" and not body.code))
        except Exception as e:  # noqa: BLE001
            w = dashboards.update_widget(w["id"], {"output": f"Generation failed: {e}"}) or w
    return w


@app.put("/widgets/{wid}")
def update_widget(wid: str, body: WidgetPatch) -> dict[str, Any]:
    w = dashboards.update_widget(wid, body.model_dump(exclude_none=True))
    if not w:
        raise HTTPException(404)
    return w


@app.post("/widgets/{wid}/refresh")
async def refresh_widget(wid: str, request: Request, regenerate: bool = False) -> dict[str, Any]:
    w = dashboards.widget(wid)
    if not w:
        raise HTTPException(404)
    try:
        return await _run_widget(w, request, regenerate_code=regenerate)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, str(e)) from e


@app.post("/widgets/{wid}/revise")
async def revise_widget(wid: str, body: dict[str, str], request: Request) -> dict[str, Any]:
    """Vibe-code iteration: apply a natural-language change request to an html widget."""
    w = dashboards.widget(wid)
    if not w:
        raise HTTPException(404)
    cfg = settings()
    instruction = (body.get("instruction") or "").strip()
    if not instruction:
        raise HTTPException(400, "instruction required")
    new_prompt = f"{w['prompt']}\n\nRevision: {instruction}"
    w = dashboards.update_widget(wid, {"prompt": new_prompt}) or w
    try:
        return await _run_widget(w, request, regenerate_code=True)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, str(e)) from e


@app.delete("/widgets/{wid}")
def delete_widget(wid: str) -> dict[str, bool]:
    dashboards.delete_widget(wid)
    return {"ok": True}


@app.get("/widgets/{wid}/render")
def render_widget(wid: str) -> HTMLResponse:
    w = dashboards.widget(wid)
    if not w:
        raise HTTPException(404)
    code = w["code"] or "<!doctype html><html><body style='font-family:system-ui;color:#9c9a94;padding:12px'>No code yet.</body></html>"
    # The iframe is unauthenticated, so give it a short-lived per-widget capability for its own sources, not the app token.
    exp = int(time.time()) + WIDGET_TOKEN_TTL
    wt = _widget_fetch_token(wid, exp)
    body = re.sub(r"(/sources/[0-9a-f]+/fetch)(\?)?",
                  lambda m: f"{m.group(1)}?wt={wt}&w={wid}&we={exp}" + ("&" if m.group(2) else ""), code)
    return HTMLResponse(body, headers={"Content-Security-Policy": WIDGET_CSP, "X-Content-Type-Options": "nosniff"})


@app.get("/recap")
async def recap(force: bool = False) -> dict[str, Any]:
    day = time.strftime("%Y-%m-%d")
    if not force:
        cached = dashboards.get_recap(day)
        if cached:
            return {**cached, "cached": True}
    cfg = settings()
    if not cfg.get("apiKey"):
        return {"day": day, "content": "", "cached": False}
    since = time.time() - 36 * 3600
    recent_convs = [c for c in convos.list(ALL) if c["updated_at"] >= since][:10]
    facts = {
        "recent_chats": [{"title": c["title"], "project_id": c["project_id"]} for c in recent_convs],
        "recent_memories": [m["content"] for m in memories.list(ALL) if m["created_at"] >= since][:15],
        "open_todos": [{"title": t["title"], "due": t["due"], "priority": t["priority"]} for t in todos.list("__all__")[:20]],
        "todo_stats": todos.stats(),
        "projects": [{"name": p["name"], **projects.stats(p["id"])} for p in projects.list()],
    }
    internal = await _internal_data()
    facts["calendar_next_3_days"] = internal.get("calendar")
    facts["unread_mail"] = [{"from": m["from"], "subject": m["subject"]} for m in (internal.get("gmail") or [])][:10]
    try:
        content = await generate_recap(cfg, cfg.get("extractionModel") or cfg["defaultModel"], facts)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, str(e)) from e
    return {**dashboards.save_recap(day, content), "cached": False}


# ---------------- canvas mode: spaces, windows, notes ----------------
canvases = Canvases(db)
notes = Notes(db)
# 'popped' rows are NOT reset here: import runs before the main process can restore them (it clears the ones it declines).


class CanvasIn(BaseModel):
    name: str = "Desk"
    project_id: str | None = None
    copy_from: str | None = None


class CanvasPatch(BaseModel):
    name: str | None = None
    project_id: str | None = None
    position: int | None = None
    snap_mode: str | None = None
    grid_size: int | None = None
    zoom: float | None = None
    pan_x: float | None = None
    pan_y: float | None = None
    wallpaper: str | None = None
    clear_project: bool = False


class WindowIn(BaseModel):
    kind: str
    ref_id: str | None = None
    project_id: str | None = None
    title: str = ""
    x: float = 0
    y: float = 0
    w: float = 520
    h: float = 640
    config: dict[str, Any] = {}


class WindowPatch(BaseModel):
    title: str | None = None
    config: dict[str, Any] | None = None
    state: str | None = None
    pinned: bool | None = None
    x: float | None = None
    y: float | None = None
    w: float | None = None
    h: float | None = None
    z: int | None = None
    canvas_id: str | None = None
    restore_bounds: dict[str, Any] | None = None
    popout_bounds: dict[str, Any] | None = None  # PopoutBounds.display is an int; dict[str, float] would round it
    clear_restore_bounds: bool = False
    clear_popout_bounds: bool = False


class WindowLayoutIn(BaseModel):
    id: str
    x: float | None = None
    y: float | None = None
    w: float | None = None
    h: float | None = None
    z: int | None = None
    state: str | None = None


class LayoutIn(BaseModel):
    windows: list[WindowLayoutIn] = []


class NoteIn(BaseModel):
    body: str = ""
    color: str = "yellow"
    project_id: str | None = None


class NotePatch(BaseModel):
    body: str | None = None
    color: str | None = None
    project_id: str | None = None
    clear_project: bool = False


@app.get("/canvases")
def list_canvases() -> list[dict[str, Any]]:
    return canvases.list()


@app.post("/canvases")
def create_canvas(body: CanvasIn) -> dict[str, Any]:
    c = canvases.create(body.name, wsid(body.project_id), body.copy_from)
    if not c:
        raise HTTPException(404, "Unknown copy_from canvas")
    return c


@app.get("/canvases/{id}")
def get_canvas(id: str) -> dict[str, Any]:
    c = canvases.get(id)
    if not c:
        raise HTTPException(404)
    return c


@app.put("/canvases/{id}")
def update_canvas(id: str, body: CanvasPatch) -> dict[str, Any]:
    patch = body.model_dump(exclude_none=True, exclude={"clear_project"})
    if patch.get("snap_mode") not in (None, *SNAP_MODES):
        raise HTTPException(400, f"Unknown snap_mode: {patch['snap_mode']}")
    if body.clear_project:
        patch["project_id"] = None
    elif "project_id" in patch:
        patch["project_id"] = wsid(patch["project_id"])
    c = canvases.update(id, patch)
    if not c:
        raise HTTPException(404)
    return c


@app.delete("/canvases/{id}")
def delete_canvas(id: str) -> dict[str, bool]:
    canvases.delete(id)
    return {"ok": True}


@app.post("/canvases/{id}/windows")
def add_canvas_window(id: str, body: WindowIn) -> dict[str, Any]:
    if body.kind not in WIDGET_KINDS:
        raise HTTPException(400, f"Unknown widget kind: {body.kind}")
    w = canvases.add_window(id, body.kind, body.ref_id, wsid(body.project_id), body.title, body.x, body.y, body.w, body.h, body.config)
    if not w:
        raise HTTPException(404)
    return w


@app.put("/canvases/{id}/layout")
def put_canvas_layout(id: str, body: LayoutIn) -> dict[str, Any]:
    for e in body.windows:
        if e.state is not None and e.state not in WINDOW_STATES:
            raise HTTPException(400, f"Unknown window state: {e.state}")
    return {"ok": True, "updated": canvases.set_layout(id, [e.model_dump() for e in body.windows])}


@app.get("/windows/{wid}")
def get_canvas_window(wid: str) -> dict[str, Any]:
    w = canvases.window(wid)
    if not w:
        raise HTTPException(404)
    return w


@app.put("/windows/{wid}")
def update_canvas_window(wid: str, body: WindowPatch) -> dict[str, Any]:
    patch = body.model_dump(exclude_none=True, exclude={"clear_restore_bounds", "clear_popout_bounds"})
    if patch.get("state") not in (None, *WINDOW_STATES):
        raise HTTPException(400, f"Unknown window state: {patch['state']}")
    if body.clear_restore_bounds:
        patch["restore_bounds"] = None
    if body.clear_popout_bounds:
        patch["popout_bounds"] = None
    w = canvases.update_window(wid, patch)
    if not w:
        raise HTTPException(404)
    return w


@app.post("/windows/{wid}/raise")
def raise_canvas_window(wid: str) -> dict[str, Any]:
    w = canvases.raise_window(wid)
    if not w:
        raise HTTPException(404)
    return w


@app.delete("/windows/{wid}")
def delete_canvas_window(wid: str) -> dict[str, bool]:
    canvases.delete_window(wid)
    return {"ok": True}


@app.get("/notes")
def list_notes(project_id: str | None = "all", q: str = "") -> list[dict[str, Any]]:
    scope = "__all__" if project_id in (None, "all") else sid(project_id)
    return notes.list(scope, q)


@app.post("/notes")
def create_note(body: NoteIn) -> dict[str, Any]:
    return notes.create(body.body, body.color, wsid(body.project_id))


@app.get("/notes/{id}")
def get_note(id: str) -> dict[str, Any]:
    n = notes.get(id)
    if not n:
        raise HTTPException(404)
    return n


@app.put("/notes/{id}")
def update_note(id: str, body: NotePatch) -> dict[str, Any]:
    patch = body.model_dump(exclude_none=True, exclude={"clear_project"})
    if body.clear_project:
        patch["project_id"] = None
    elif "project_id" in patch:
        patch["project_id"] = wsid(patch["project_id"])
    n = notes.update(id, patch)
    if not n:
        raise HTTPException(404)
    return n


@app.delete("/notes/{id}")
def delete_note(id: str) -> dict[str, bool]:
    notes.delete(id)
    canvases.delete_windows_for("note", id)
    return {"ok": True}
