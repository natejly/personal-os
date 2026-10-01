"""Grain backend API. (The package keeps the personal_os name for compatibility.)"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import html
import json
import logging
import math
import os
import re
import secrets
import shutil
import sqlite3
import time
from pathlib import Path
from typing import Annotated, Any, AsyncIterator

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from pydantic import AfterValidator, BaseModel, Field

from . import activity, assist, llm, tools
from .context import build_context, estimate_tokens
from .db import Database, data_dir_from_env, new_id
from .extract_text import extract_text
from .learn import learn_from_exchange
from .repos import ALL, Conversations, Documents, Graph, Memories, Projects
from .boards import Boards
from .canvas import SNAP_MODES, WIDGET_KINDS, WINDOW_STATES, Canvases
from .dashboards import Dashboards, generate_recap, generate_summary, generate_widget_code
from .docs import Docs, unified_diff
from .google import Google, GoogleNotConnected, json_safe
from .microvm import Sandboxes
from .notes import Notes
from .gtasks import TasksSync
from .presets import CanvasPresets
from .cowork import (AUTONOMY, DESK_CONTINUE, DESK_HINT, DESK_RESUME, LIVE as DESK_LIVE,
                     STATUSES as DESK_STATUSES, DeskRuntime, Desks)
from .plans import PLAN_SAFE_DANGER, PLAN_TOOL, ActionPlans, CallPolicy, autoplan, decide_call, normalize_plan, parse_plan_edits
from .runlog import ACTIVE as RUN_ACTIVE, PROMOTE_STEP, RunStore
from .workspace import MAX_PREVIEW, Workspace, WorkspaceError
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
docs = Docs(db)
# Construction order is schema-creation order and is load-bearing: action_plans.run_id and
# desks.conversation_id carry real foreign keys, so agent_runs and conversations must exist first.
# The workspace is a directory, not a table, but it comes before Desks so a desk's root exists at
# create time and the row can store the relative "cowork/<id>" rel_root() hands back.
run_store = RunStore(db)
aplans = ActionPlans(db)
workspace = Workspace(db.data_dir)
desks = Desks(db, workspace)


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


# `docs_url` is moved off /docs: that prefix belongs to the user's own documents (see docs.py).
app = FastAPI(title="Grain", version="0.1.0", docs_url="/api-docs", redoc_url=None,
              swagger_ui_oauth2_redirect_url=None)  # its default sits under /docs too


def _json_safe(v: Any) -> Any:
    """Strip what json.dumps(allow_nan=False) would refuse, recursively."""
    if isinstance(v, float) and not math.isfinite(v):
        return str(v)
    if isinstance(v, dict):
        return {k: _json_safe(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_json_safe(x) for x in v]
    return v


@app.exception_handler(RequestValidationError)
async def _validation_error(request: Request, exc: Exception) -> JSONResponse:  # type: ignore[override]
    """422s echo the rejected input back, and Starlette's JSONResponse sets allow_nan=False, so a body carrying inf
    or NaN could not serialise its own rejection -- the 422 became a 500. Scrub the echo instead."""
    errors = jsonable_encoder(exc.errors()) if isinstance(exc, RequestValidationError) else []
    return JSONResponse({"detail": _json_safe(errors)}, status_code=422)


@app.exception_handler(sqlite3.IntegrityError)
async def _integrity_error(request: Request, exc: Exception) -> JSONResponse:  # type: ignore[override]
    """Safety net for the writers wsid() cannot cover (a card whose column is gone, a window whose canvas is gone).
    A stale id from a window that has not refreshed is the client's problem to retry, not a server fault, so it gets a
    409 and a usable message rather than a bare 500."""
    detail = ("Something this refers to no longer exists - reload and try again."
              if "FOREIGN KEY" in str(exc).upper() else f"That change conflicts with what is already stored ({exc})")
    log.info("integrity error on %s %s: %s", request.method, request.url.path, exc)
    return JSONResponse({"detail": detail}, status_code=409)


@app.exception_handler(sqlite3.IntegrityError)
async def _integrity_error(request: Request, exc: Exception) -> JSONResponse:  # type: ignore[override]
    """Safety net for the writers wsid() cannot cover (a card whose column is gone, a widget whose dashboard is gone).
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
bus = RunBus(run_store)
# Active chat streams so they can be aborted from the client: message_id -> that run's stop event.
_active: dict[str, asyncio.Event] = {}
# Pending tool-call approvals: call_id -> Future[decision]
_approvals: dict[str, asyncio.Future] = {}
# Fast-wake only. The `approvals` row is the source of truth; a restart loses the Future and the
# waiting run falls back to polling the row, so no card is ever orphaned.
_answers: dict[str, str] = {}
# One supervisor task per desk, so a chained turn is owned by something outside the run it follows.
_desk_tasks: dict[str, asyncio.Task[None]] = {}


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
tasks_sync = TasksSync(todos, google, settings, db.set_settings)
# Any todo write (routes here or assistant tools) nudges the sync loop.
todos.on_change = tasks_sync.poke
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
sandboxes = Sandboxes(settings)
monitor = activity.Monitor(db, settings, llm.complete)
toolbox = Toolbox(memories, graph, documents, settings, todos=todos, google=google, boards=boards, sandboxes=sandboxes, docs=docs, activity=monitor,
                  desks=desks, plans=aplans, workspace=workspace)


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


def sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


# ---------------- health / settings / models ----------------
@app.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True}


# Google OAuth material lives in settings but never leaves the backend.
PRIVATE_SETTINGS = {"googleToken", "googleAuthPending"}
# Readable through /settings, but only writable through its own route: a plain PUT would replace the
# whole nested dict and silently drop the signal switches and exclusion lists.
SETTINGS_READ_ONLY = {"activity", "googleTasksSync"}


@app.get("/settings")
def get_settings() -> dict[str, Any]:
    return {k: v for k, v in settings().items() if k not in PRIVATE_SETTINGS}


@app.put("/settings")
def put_settings(patch: dict[str, Any]) -> dict[str, Any]:
    db.set_settings({k: v for k, v in patch.items()
                     if k in llm.DEFAULT_SETTINGS and k not in PRIVATE_SETTINGS and k not in SETTINGS_READ_ONLY})
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
def list_conversations(project_id: str | None = None, include_desks: bool = False) -> list[dict[str, Any]]:
    """A desk's own conversation is hidden by default: it belongs on the Cowork rail, not in Recent
    chats. `include_desks=true` is there so one can still be listed on demand."""
    return convos.list(sid(project_id), include_desks)


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
Besides normal markdown, the UI renders three fenced code blocks inline:
- ```chart — a small JSON spec for a data chart: {"type": "bar" | "line" | "area" | "pie" | "scatter", "title": "...", "x": "<key used for the x axis / category>", "series": ["<numeric key>", ...], "data": [{"<x key>": ..., "<numeric key>": ..., ...}, ...], "stacked": false, "xLabel": "...", "yLabel": "...", "unit": ""}. `data` is an array of objects (one per x value); keep it under 200 rows. Use a chart whenever numbers would be clearer that way (comparisons, trends, breakdowns).
- ```interactive — a chart the user steers with sliders and other controls; it recomputes instantly as they drag, with no new request to you: {"title": "Compound growth", "type": "line", "controls": [{"id": "rate", "label": "Annual return", "type": "slider", "min": 0, "max": 15, "step": 0.25, "value": 7, "unit": "%"}, {"id": "start", "label": "Starting amount", "type": "number", "value": 5000, "unit": "$"}], "x": {"id": "year", "label": "Year", "from": 0, "to": 30, "steps": 120}, "series": [{"key": "balance", "label": "Balance", "expr": "start * pow(1 + rate/100, year)"}], "readouts": [{"label": "Final balance", "expr": "balance_last", "unit": "$"}], "unit": "$", "yLabel": "Balance"}
  - `controls` (max 12): `type` is slider (the default), number, select (needs "options": [...]), or toggle. Every `id` must be a plain name, because the formulas reference it by that name.
  - `x` is either a swept range — `from`/`to`/`steps` (max 400), each a number or a formula over the controls — or `{"id": "...", "values": [...]}` for fixed categories. To drive real rows instead, pass `"data": [{...}, ...]` and set `"x"` to the column name; formulas then also see that row's columns.
  - `series[].expr` (max 8) is a formula over the control ids, the x variable (also available as `x`), `index` and `n`.
  - `readouts` (optional, max 6) are scalars shown under the chart. Besides the controls they can use `<series key>_last`, `_first`, `_min`, `_max`, `_sum`, `_mean`.
  - Formulas may use `+ - * / % ^`, comparisons, `&& || !`, `cond ? a : b`, `pi`, `e`, and only these functions: abs sqrt cbrt exp log ln log2 log10 sin cos tan asin acos atan sinh cosh tanh sign floor ceil trunc round(x[,digits]) sqr pow atan2 mod logb lerp clamp step min max hypot if(cond,a,b). There is nothing else — no assignment, no indexing, no other names.
  Reach for it when the interesting part of an answer is an assumption worth playing with (a rate, a price, a threshold, a growth curve); use ```chart for numbers that are already fixed.
- ```mermaid — diagrams (flowchart, sequenceDiagram, gantt, mindmap, timeline, ...).
Only chart real values you have or computed; never invent data for decoration. Text before and after a block is shown as usual."""

TOOLS_HINT = "You have tools. Use them when they would make the answer more accurate or current; otherwise answer directly. After using tools, write the final answer for the user."

PLAN_MODE_HINT = """## Planning mode
You cannot change anything yet. Only read-only tools are available: everything that writes, runs code,
sends, or touches anything outside this app is withheld until the user approves a plan.
Investigate with the read-only tools if you need to, then call `propose_plan` once with the ordered
steps you intend to take. One step per real action, with the exact tool name and the exact arguments
you will call it with — approving a step approves those arguments and nothing else. If the task needs
no actions at all, just answer; do not propose an empty plan."""

PLAN_BOUND_HINT = ("The plan is approved. Run its steps with exactly the arguments that were approved; "
                   "anything else still asks the user. If reality differs from the plan, say so and "
                   "propose a new one rather than improvising around it.")

# PLAN_BLOCKED lives in plans.py and DESK_HINT / DESK_CONTINUE / DESK_RESUME in cowork.py, each in
# the module that enforces it: decide_call formats the first and the desk runner sends the rest, so
# a second copy here would be a string that drifts out of step with the rule it describes.
GATE_TOOLS = (PLAN_TOOL, "desk_ask")   # exempt from the chat approval timeout; they park instead

# What a desk may spend. 0 means unlimited on an axis, so it loses to any positive limit: a desk's
# own budget may only make the user's settings stricter, never looser.
_DESK_CAPS = ("deskMaxTurns", "deskMaxCost", "deskMaxLive")


def _caps(cfg: dict[str, Any], override: dict[str, Any] | None) -> dict[str, Any]:
    """Downward only: a desk may be stricter than the user's settings, never looser. 0 = unlimited,
    so it loses to any positive limit."""
    out = {k: cfg.get(k, llm.DEFAULT_SETTINGS[k]) for k in _DESK_CAPS}
    for key, src in (("deskMaxTurns", "maxTurns"), ("deskMaxCost", "maxCost")):
        want = (override or {}).get(src)
        if want is None:
            continue
        try:
            want = float(want)
        except (TypeError, ValueError):
            continue
        have = float(out[key] or 0)
        if want > 0 and (have <= 0 or want < have):
            out[key] = want
    out["deskMaxTurns"] = int(out["deskMaxTurns"] or 0)
    out["deskMaxCost"] = float(out["deskMaxCost"] or 0)
    out["deskMaxLive"] = int(out["deskMaxLive"] or 0)
    return out


BUDGET_STOP = ("Out of budget ({axis}): this tool call was not executed and no further tool calls will run. "
               "Write the best final answer you can from what you already have, and say in one line what is still missing.")
SOFT_NUDGE = ("Budget check: about {pct}% of this reply's budget is used. "
              "Make at most one or two more tool calls, then write the final answer.")
LOOP_STOP = ("{name} has been called with identical arguments {n} times in a row, so this reply is stopping tool use. "
             "Answer with what you already have, and say in one line what you could not finish.")
PARKED_TOOL = ("{name} is waiting for the user's approval and was not executed. The card is still open, so do "
               "not try another way around it: say what you are waiting on and stop.")
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


async def _chat_stream(conv_id: str, body: ChatIn, stop: asyncio.Event, steers: list[dict[str, Any]] | None = None,
                       run: Run | None = None) -> AsyncIterator[tuple[str, Any]]:
    """Yield (event, payload) pairs. The run bus formats them and fans them out; see runs.sse.

    `run` is the Run this reply belongs to. It is optional only so the generator stays callable on
    its own; with one, every tool call is taped through run_store.call_once, approvals become rows,
    and the reply reports `partial`/`cost`/`steps_consumed` back onto it for the desk supervisor."""
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

    # Where planning mode comes from (§4.1). A desk plans its first run unless its autonomy says
    # otherwise; an ordinary chat follows its own planMode, falling back to the global setting.
    # 'auto' deliberately does NOT start the reply in planning: it arms on the first mutating call
    # instead (§4.7), which is the whole difference between the two modes.
    desk_id = conv["settings"].get("deskId")
    desk = desks.get(desk_id) if desk_id else None
    plan = aplans.active(conv_id)                                  # the approved, unconsumed plan, or None
    mode_pref = conv["settings"].get("planMode") or cfg["planMode"]
    autonomy = str((desk or {}).get("autonomy") or "plan")
    # A desk's autonomy is the authority on whether it plans, not the conversation's planMode:
    # 'ask as it goes' is the one mode that deliberately does NOT plan first (decide_call rule 8b
    # cards each change instead), and the user can change autonomy long after the desk was created.
    planning = plan is None and (autonomy != "ask" if desk else mode_pref == "always")

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
        activity=monitor, ledger=run_store.ledger(desk_id) if desk_id else None,
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
        # Planning and desk context. `plan_phase` and `proposal_only` are read by BOTH gates — the
        # one here and Toolbox.call's — so a call site that forgets one is still held by the other.
        # `desk_id` is what every desk_* handler derives its workspace root from; without it they
        # refuse outright, by design.
        "plan_phase": planning, "proposal_only": autonomy == "propose",
        "desk_id": desk_id, "workspace": workspace, "plan_id": (plan or {}).get("plan_id"),
        "run_id": run.run_id if run else None, "message_id": am["id"], "call_id": None,
    }
    modes = toolbox.effective(cfg.get("tools") or {}, (project or {}).get("tools"), conv["settings"].get("tools")) if conv["settings"].get("useTools", True) else {}

    def _schemas() -> list[dict[str, Any]]:
        """One function, because the always_chat/always_global grant path recomputes tool_schemas
        below; a plan-mode filter applied at only one of the two sites lets a granted write tool
        reappear mid-plan. `modes` is mutated in place by that grant path, so this filters a copy and
        reads it fresh each time rather than rebuilding from a stale snapshot — otherwise the user's
        'Always' click is silently discarded on the next recompute."""
        if not modes:
            return []
        m = dict(modes)
        if not desk_id:
            m = {n: v for n, v in m.items() if toolbox.specs[n].group != "desk"}
        if tool_ctx["plan_phase"]:
            m = {n: v for n, v in m.items()
                 if n == PLAN_TOOL or toolbox.specs[n].danger in PLAN_SAFE_DANGER}
            m[PLAN_TOOL] = "ask"
        return toolbox.schemas(m)

    tool_schemas = _schemas()
    system = "\n\n".join(p for p in (system, RENDER_HINT, DESK_HINT if desk else "",
                                     TOOLS_HINT if tool_schemas else "",
                                     PLAN_MODE_HINT if planning else (PLAN_BOUND_HINT if plan else "")) if p)
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
    plan_msg: dict[str, Any] | None = None

    def _reinject_plan() -> None:
        """The approved plan as the LAST system message of each round. convos.history() replays
        prose only, so from the second turn of a chained desk the plan is otherwise invisible and
        the agent redoes step one. The previous copy is removed by identity first, so a twelve-round
        reply carries one checklist rather than twelve."""
        nonlocal plan_msg
        block = aplans.block(plan["plan_id"]) if plan else None
        if plan_msg is not None:
            for i, m in enumerate(messages):
                if m is plan_msg:
                    del messages[i]
                    break
            plan_msg = None
        if block:
            plan_msg = {"role": "system", "content": block}
            messages.append(plan_msg)

    async def _final_round() -> AsyncIterator[tuple[str, Any]]:
        """Closing answer after a budget or breaker stop: one tool-free call, itself exempt from the budget."""
        _reinject_plan()
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
                    # `segment` says this `done` closes one reply segment, not the run: the run
                    # carries on with a fresh assistant message. Anything that counts a turn —
                    # _run_desk's charge, and so the desk's budget — must skip it, or a steered
                    # desk is billed twice for one turn.
                    yield "done", {"id": am["id"], "error": None, "context_used": used, "tool_events": tool_events,
                                   "trace": tracer.spans, "stopped": False, "partial": partial, "segment": True,
                                   "tainted": tool_ctx["tainted"], "taint_sources": tool_ctx["taint_sources"]}
                    am = convos.add_message(conv_id, "assistant", "", model=model)
                    _active[am["id"]] = stop
                    tool_ctx["message_id"] = am["id"]
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
            # Last, so the checklist is the final system message the model reads. At the TOP of the
            # round rather than the bottom of the previous one, because the round that needs it most
            # is the FIRST of a chained turn — the one that would otherwise redo step one.
            _reinject_plan()
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
            # A hint raised mid-loop is stashed, not appended: `turn` is already in `messages` and
            # every one of its tool_calls must be answered by a `tool` message with nothing in
            # between. A system message wedged in there is a sequence providers reject. These are
            # flushed below, once the loop has emitted the last reply.
            hints: list[str] = []
            for ci, c in enumerate(calls):
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
                # §4.7 'auto': the first mutating call of a reply arms planning rather than running.
                # The flip mutates this reply's in-memory conversation settings only — the same
                # documented in-memory mutation the always_chat grant below performs — because
                # _chat_stream read `conv` once and keeps using that copy for the whole reply.
                if autoplan(c["name"], specs=toolbox.specs, mode_pref=mode_pref, planning=planning,
                            plan=plan):
                    planning = True
                    conv["settings"]["planMode"] = "always"
                    tool_ctx["plan_phase"] = True
                    tool_schemas = _schemas()
                    hints.append(PLAN_MODE_HINT)
                # The provider's call id is only unique within one request -- llm.stream_chat falls back
                # to "call_<idx>" when the provider omits one -- so two conversations streaming at once
                # both produce "call_0". Key anything cross-conversation by the message id too, or one
                # chat's approval resolves another chat's call. The model still sees c["id"].
                # The round is in the key for the same reason one level down: that fallback restarts at
                # "call_0" every round, so without it round two's first call would reuse round one's
                # id — and now that approvals are rows rather than a popped dict entry, it would find
                # the already-approved row and run without a card.
                uid = f"{am['id']}:{_round}:{c['id']}"
                tool_ctx["call_id"] = uid
                # Every rule that can stop a call lives in one pure function, so the one contested
                # region of this loop has a testable centre and both call sites cannot drift.
                pol: CallPolicy = decide_call(
                    c["name"], args, raw_mode=raw_mode, specs=toolbox.specs, ctx=tool_ctx,
                    planning=planning, plan=plan, autonomy=autonomy, gate=toolbox.gate,
                    plans=aplans, call_id=uid)
                mode, forced = pol.mode, pol.forced
                spec = toolbox.specs.get(c["name"])
                # Rule 3 is the only one that both denies and forces, so it is what `plan_mode` means.
                blocked_by = "plan_mode" if (pol.deny and pol.forced) else None
                yield "tool_call", {"message_id": am["id"], "id": uid, "name": c["name"], "arguments": args,
                                    "needs_approval": mode == "ask", "forced": forced,
                                    "off_plan": pol.off_plan, "plan_step": pol.claimed_step,
                                    "blocked_by": blocked_by}
                tspan = tracer.start("tool", c["name"], {"round": _round, "arguments": _short(args), "mode": mode, "forced": forced})
                yield "span", {"message_id": am["id"], "span": tspan}
                t0 = time.time()
                decision = "allow"
                parked: str | None = None
                plan_row: dict[str, Any] | None = None
                forced_result: Any = None
                if pol.is_plan:
                    # Validate against the UNFILTERED mode map before any card is shown, so the user
                    # is never asked to approve a plan that could not execute once approved.
                    steps, plan_error = normalize_plan(args, modes, toolbox.specs, available=toolbox.available)
                    if plan_error is not None:
                        mode, forced_result = "on", plan_error
                    else:
                        aplans.supersede(conv_id)
                        plan_row = aplans.open(conversation_id=conv_id, desk_id=desk_id,
                                               run_id=run.run_id if run else "", message_id=am["id"],
                                               call_id=uid, title=str(args.get("title") or ""),
                                               intent=str(args.get("intent") or ""), steps=steps,
                                               tainted=bool(tool_ctx["tainted"]))
                        yield "plan", {"message_id": am["id"], "call_id": uid, "plan": plan_row}
                if mode == "ask":
                    # Pause the reply until the user approves or denies this call (POST /approvals/{call_id}).
                    approval_t0 = time.time()
                    # The row is the truth and the Future is only a fast wake: a decision made in
                    # another window, or before a restart, is picked up by polling the row.
                    row = run_store.open_approval(
                        call_id=uid, run_id=run.run_id, conversation_id=conv_id, desk_id=desk_id,
                        message_id=am["id"], tool=c["name"], args=args,
                        danger=spec.danger if spec else "safe", forced=forced,
                        plan_id=plan_row["plan_id"] if plan_row else None) if run else None
                    if run is not None:
                        run.set_status("awaiting")
                    if desk_id:
                        desks.set_status(desk_id, "awaiting_plan" if pol.is_plan else "needs_approval",
                                         run_id=run.run_id if run else None)
                    park_after = float(cfg.get("parkAfterSeconds") or 0)
                    approval_wait = float(cfg.get("approvalWaitSeconds") or 0)
                    fut: asyncio.Future = asyncio.get_event_loop().create_future()
                    _approvals[uid] = fut
                    try:
                        waited = 0.0
                        while not fut.done():
                            if stop.is_set():
                                if row:
                                    run_store.decide(uid, "deny", by="stop")
                                fut.set_result("deny")
                                break
                            cur = run_store.approval(uid) if row else None
                            if cur and cur["status"] != "pending":
                                fut.set_result(cur["decision"] or "deny")
                                break
                            # Park, do not auto-deny, and never park in front of somebody who is
                            # looking at the card: with a viewer attached the run waits indefinitely.
                            if (row and park_after > 0 and waited >= park_after and run is not None
                                    and run.watchers == 0 and (desk_id or c["name"] in GATE_TOOLS)):
                                run_store.park(uid)
                                parked = uid
                                break
                            if not desk_id and c["name"] not in GATE_TOOLS and approval_wait > 0 and waited >= approval_wait:
                                if row:
                                    run_store.decide(uid, "deny", by="timeout")
                                fut.set_result("deny")
                                break
                            try:
                                # Short, so an approval-blocked run notices a stop or a shutdown promptly.
                                await asyncio.wait_for(asyncio.shield(fut), timeout=2)
                            except asyncio.TimeoutError:
                                waited += 2
                        decision = fut.result() if fut.done() else "deny"
                    finally:
                        _approvals.pop(uid, None)
                    if run is not None and not parked:
                        run.set_status("running")
                    budget.paused += time.time() - approval_t0  # a slow approval must not blow the wall clock
                    t0 = time.time()  # don't count waiting time as tool time
                    granted = decision in ("always_chat", "always_global")
                    if pol.is_plan:
                        # A plan card is never a permission card: approving a plan cannot turn
                        # propose_plan, or anything else, on for the rest of the chat.
                        granted = False
                        decision = "deny" if decision == "deny" else "allow"
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
                        tool_schemas = _schemas()  # the grant changed modes; keep the schemas in step
                if parked:
                    # Parking is not an exception. Every pending call still gets a tool message —
                    # the same reason the budget path answers them all — so the message list stays
                    # legal and the run leaves through _final_round rather than through a raise.
                    partial = "blocked"
                    if desk_id:
                        # The park timer is the writer here (§3.7). settle() cannot do it: a desk
                        # sitting in awaiting_plan is already outside LIVE, so settle leaves it be.
                        desks.set_status(desk_id, "blocked", reason="plan" if pol.is_plan else "approval",
                                         headline="")
                    for rest in calls[ci:]:
                        messages.append({"role": "tool", "tool_call_id": rest["id"],
                                         "content": PARKED_TOOL.format(name=rest["name"])})
                    tracer.end(tspan, {"parked": True})
                    yield "span", {"message_id": am["id"], "span": tspan}
                    break
                if pol.is_plan and plan_row is not None:
                    # decide() is first-wins, so this only writes when the decision came from
                    # somewhere other than POST /cowork/plans — a Stop, or a plain approval card.
                    aplans.decide(plan_row["plan_id"], "reject" if decision == "deny" else "approve",
                                  decided_by="stop" if (decision == "deny" and stop.is_set()) else "user")
                    fresh = aplans.get(plan_row["plan_id"]) or plan_row
                    forced_result = aplans.model_result(fresh)
                    # Re-read after ANY decision, not only an approval: opening this plan
                    # superseded whatever was live, so on a rejection the in-memory snapshot is a
                    # dead plan whose steps would otherwise keep answering for the one the user
                    # just said no to.
                    plan = aplans.active(conv_id)
                    if fresh["status"] == "approved":
                        planning = False
                        tool_ctx["plan_phase"] = False
                        tool_ctx["plan_id"] = fresh["plan_id"]
                        tool_schemas = _schemas()
                        hints.append(PLAN_BOUND_HINT)
                        if desk_id:
                            desks.set_status(desk_id, "working", reason="plan", plan_id=fresh["plan_id"],
                                             run_id=run.run_id if run else None)
                    elif desk_id and not stop.is_set():
                        # Only a rejection the USER made puts the desk back in planning. A plan
                        # rejected because the run was stopped must not drag a paused or stopped
                        # desk back to a live status the route just decided it out of.
                        desks.set_status(desk_id, "planning", reason="rejected",
                                         run_id=run.run_id if run else None)
                    yield "plan_decision", {"message_id": am["id"], "plan_id": fresh["plan_id"], "call_id": uid,
                                            "decision": "reject" if fresh["status"] == "rejected" else "approve",
                                            "by": fresh.get("decided_by") or "user", "note": fresh.get("note") or ""}
                was_tainted, was_blocked = tool_ctx["tainted"], c["name"] in blocked
                if forced_result is not None:
                    result: Any = forced_result
                elif was_blocked:
                    result = tools.denied(c["name"], f"failing {TOOL_ERROR_LIMIT} times in a row and disabled for the rest of this reply")
                elif pol.deny:
                    # A plan-mode or propose-only refusal, or a step the user already rejected. The
                    # tool's ALTERNATIVE rides along, so the model puts it in a plan step rather
                    # than retrying the same call into the repeat breaker. The plan-mode refusal is
                    # the one that already says how to get the call authorised, so it does not also
                    # carry "Do not retry it" — the model cannot obey both.
                    result = tools.denied(c["name"], pol.deny, may_retry=blocked_by == "plan_mode")
                elif mode == "off":
                    result = tools.denied(c["name"], "turned off for this chat")
                elif decision != "allow":
                    result = tools.denied(c["name"], "just declined by the user")
                elif run is not None:
                    # Committed at 'started' and COMMITTED before the call is awaited, so a crash
                    # mid-write leaves an honest 'unknown' row rather than a silent repeat on resume.
                    result = await run_store.call_once(run.run_id, _round * 1000 + ci, c["name"], args,
                                                       lambda: toolbox.call(c["name"], args, tool_ctx),
                                                       call_id=uid, desk_id=desk_id)
                else:
                    result = await toolbox.call(c["name"], args, tool_ctx)
                note = _answers.pop(uid, "")
                if note and isinstance(result, dict) and "user_note" not in result:
                    result = {**result, "user_note": note}
                ms = int((time.time() - t0) * 1000)
                # images (e.g. matplotlib figures from run_python) go to the UI, not to the model
                images = result.pop("images", None) if isinstance(result, dict) else None
                preview = summarize_result(result)
                err = result.get("error") if isinstance(result, dict) else None
                tool_errors[c["name"]] = tool_errors.get(c["name"], 0) + 1 if err else 0  # reset on success = consecutive
                if tool_errors[c["name"]] >= TOOL_ERROR_LIMIT:
                    blocked.add(c["name"])
                if pol.claimed_step:
                    # The step was consumed before the call; this is what turns it into done|failed,
                    # and what `progress` means to the desk supervisor.
                    aplans.finish(uid, not err, err if isinstance(err, str) else None)
                    if run is not None:
                        run.steps_consumed += 1
                tainted = decision == "allow" and not err and toolbox.taints(c["name"])
                if tainted:
                    if not was_tainted:
                        yield "taint", {"message_id": am["id"], "source": c["name"]}
                    tool_ctx["taint_sources"].append(c["name"])
                event = {"id": uid, "name": c["name"], "arguments": args, "result_preview": preview, "duration_ms": ms,
                         "error": err, "images": images or None, "approval": (decision if mode == "ask" else None),
                         "forced": forced, "tainted": tainted, "blocked": c["name"] if was_blocked else None, "breaker": partial,
                         "off_plan": pol.off_plan, "plan_step": pol.claimed_step, "blocked_by": blocked_by}
                tracer.end(tspan, {"result_chars": len(preview), "images": len(images or [])}, error=err)
                tool_events.append(event)
                yield "tool_result", {"message_id": am["id"], **event}
                yield "span", {"message_id": am["id"], "span": tspan}
                for_model = {**result, "images_shown_to_user": [i["name"] for i in images]} if images and isinstance(result, dict) else result
                messages.append({"role": "tool", "tool_call_id": c["id"], "content": summarize_result(for_model, 24000)})
            # Every `tool` reply is out, including the parked break's, so the stashed hints can
            # land as system messages without splitting the assistant turn from its answers.
            for hint in hints:
                messages.append({"role": "system", "content": hint})
            hints = []
            if partial in ("loop", "blocked"):
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
    if run is not None:
        # Three plain attributes the desk supervisor reads to decide whether another bounded turn
        # is worth starting; they also land on the agent_runs row at end().
        run.partial, run.cost, run.rounds = partial, budget.cost, budget.rounds
        run.budget = {"rounds": budget.rounds, "tokens": budget.tokens, "cost": round(budget.cost, 6),
                      "seconds": round(budget.elapsed(), 3), "partial": partial}
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
    async for event, data in _chat_stream(run.conversation_id, body, run.stop, run.steers, run=run):
        if event == "assistant_message":
            run.message_id = data.get("id")
        run.publish(event, data)


async def _run_desk(run: Run, desk_id: str, body: ChatIn) -> dict[str, Any]:
    """A desk turn is an ordinary reply with run.desk_id set. Returns the settled desk row; the
    supervisor decides whether to chain. Everything autonomous about it — the loop, the budget, the
    breakers, the approval pause — is _chat_stream's, unchanged."""
    rt = DeskRuntime(desks, desk_id)
    partial: str | None = None
    stopped = False
    chain = False
    err: str | None = None
    desks.update_run(desk_id, run.run_id)
    try:
        async for event, data in _chat_stream(run.conversation_id, body, run.stop, run.steers, run=run):
            if event == "assistant_message":
                run.message_id = data.get("id")
            if event == "done" and not data.get("segment"):
                # A steered reply closes its current segment with its own `done` and keeps going;
                # only the run's real final `done` ends the turn, so only it may charge one.
                partial, stopped = data.get("partial"), bool(data.get("stopped"))
                err = err or data.get("error")
                # Charge BEFORE deciding, so the row both _should_chain calls read — this one and
                # the supervisor's after settle — has already counted this turn. Deciding on two
                # different rows is how a desk ends up `working` with nothing driving it.
                desks.charge(desk_id, run.cost, 1)
                chain = _should_chain(desks.get(desk_id) or {}, run)
                if chain:
                    # The successor is announced before this stream closes: a chained run is a new
                    # Run with a new run_id and seq restarting at 0, and no client is subscribed to
                    # it yet. The renderer treats this as a promise and re-attaches, bounded.
                    run.publish("desk_handoff", {"desk_id": desk_id, "conversation_id": run.conversation_id,
                                                 "turn": int((desks.get(desk_id) or {}).get("turn") or 0)})
            run.publish(event, data)
            try:
                if (upd := rt.observe(event, data)) is not None:
                    run.publish("desk_status", upd)
            except Exception:  # noqa: BLE001 - a rail label must never kill a run
                pass
    except asyncio.CancelledError:
        # Only a desk still LIVE was interrupted by this cancellation. One that already settled
        # itself — Stop writes `stopped` before it cancels, pause writes `paused` — keeps the
        # decision it made; a crash outranks a turn, never a user.
        if (desks.get(desk_id, False) or {}).get("status") in DESK_LIVE:
            desks.set_status(desk_id, "interrupted", reason="restart", headline="")
        raise
    if (desks.get(desk_id) or {}).get("status") == "paused":
        # The pause route already decided how this turn ends; its cooperative stop must not be
        # read back as a user Stop and overwrite `paused` with `stopped`.
        stopped = False
    settled = desks.settle(desk_id, partial=partial, stopped=stopped, error=err, chain=chain)
    try:
        run.publish("desk_status", settled)
    except Exception:  # noqa: BLE001 - a rail label must never kill a run
        pass
    return settled


def _should_chain(desk: dict[str, Any], run: Run) -> bool:
    """Five guards, all of which must hold. `progress` is the one that stops a desk burning twelve
    turns re-reading the same file: a turn that consumed no plan step is not progress."""
    caps = _caps(settings(), desk.get("budget"))
    turns, cost = caps["deskMaxTurns"], caps["deskMaxCost"]
    return (desk.get("status") == "working"
            and run.partial == "rounds"               # only a budget-window stop chains
            and not run.stop.is_set()
            and bool(aplans.remaining(desk.get("plan_id") or ""))
            and run.steps_consumed > 0
            # 0 on either axis means unlimited, the same reading _caps gives it.
            and (turns <= 0 or int(desk.get("turn") or 0) + 1 < turns)
            and (cost <= 0 or float(desk.get("cost") or 0) < cost))


def _missed_wake(desk_id: str) -> dict[str, Any] | None:
    """The desk whose parked card was decided while its own run was still closing, claimed for
    another turn — or None when there is nothing to retry.

    POST /approvals writes the decision and calls _wake_desk, but _launch_desk refuses while a run
    is live on the conversation, and a run that parked a card stays live for _final_round and the
    auto-learn tail. The route answered `resumed: False` and the desk sat blocked with nobody
    coming back for it. park() deliberately leaves the row 'pending', so a card the user has not
    actually answered is still visible here and is not mistaken for a missed wake."""
    desk = desks.get(desk_id, with_outputs=False)
    if not desk or desk["status"] != "blocked" or desk["status_reason"] not in ("approval", "plan"):
        return None
    if run_store.approvals(status="pending", desk_id=desk_id):
        return None
    return desks.claim_run(desk_id, RESUME_FROM)


async def _desk_supervisor(desk_id: str, run: Run) -> None:
    """Owns the chain. A chained turn is started here, never from inside the previous run's
    teardown, so the next turn is owned by something that outlives the run it follows."""
    try:
        while True:
            # Shielded: cancelling the supervisor ends the CHAIN, not the turn already in flight.
            # Without it, Stop's task.cancel() reaches the run through this await and the run dies
            # as `interrupted` instead of settling cooperatively as `stopped`.
            await (asyncio.shield(run.task) if run.task else asyncio.sleep(0))
            state = desks.get(desk_id)
            if not state or not _should_chain(state, run):
                # The run has really ended now, so a wake that lost the race against it can be
                # retried here (§F5): _launch_desk refuses while a run is live on the conversation,
                # and a run that parked a card stays live for _final_round and the learn tail.
                state = _missed_wake(desk_id)
                if not state:
                    return
            body = ChatIn(content=DESK_CONTINUE)
            run = bus.start(state["conversation_id"], lambda r, b=body: _run_desk(r, desk_id, b),
                            kind="desk", desk_id=desk_id, turn=int(state["turn"] or 0),
                            input={"content": body.content})
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - a dead supervisor must not take the backend with it
        log.exception("desk supervisor %s failed", desk_id)
        desks.set_status(desk_id, "failed", reason="supervisor")
    finally:
        _desk_tasks.pop(desk_id, None)


# Which statuses each entry point may claim a desk out of. `claim_run`'s rowcount is the lock, so a
# double-clicked Start makes one run, not two.
START_FROM = ("draft",)
# `awaiting_plan` is resumable too: a restart strands a desk on a plan card nobody is waiting on,
# and Desks.recover() sweeps that status, so Resume has to be able to claim out of it.
RESUME_FROM = ("awaiting_plan", "blocked", "paused", "interrupted", "review")
# A typed message may also wake a desk the user had let finish — the same box, awake or not.
MESSAGE_FROM = (*START_FROM, *RESUME_FROM, "done", "failed", "stopped")


def _launch_desk(desk_id: str, content: str | None, from_statuses: tuple[str, ...]) -> Run | None:
    """Claim the desk, start its first turn now — so the route can hand back a run_id — and give
    the chain to a supervisor task. None means the claim was lost or a run is already live."""
    desk = desks.get(desk_id, with_outputs=False)
    if not desk or bus.live(desk["conversation_id"]):
        return None
    claimed = desks.claim_run(desk_id, from_statuses)
    if not claimed:
        return None
    body = ChatIn(content=(content or "").strip() or claimed["brief"])
    run = bus.start(claimed["conversation_id"], lambda r: _run_desk(r, desk_id, body), kind="desk",
                    desk_id=desk_id, turn=int(claimed["turn"] or 0), input={"content": body.content})
    _desk_tasks[desk_id] = asyncio.create_task(_desk_supervisor(desk_id, run), name=f"desk:{desk_id}")
    return run


def _wake_desk(desk_id: str) -> Run | None:
    """Resume a desk that is not running: used by the resume route, by a decided approval that had
    parked, and by a plan approved after its run had already let go."""
    desk = desks.get(desk_id, with_outputs=False)
    if not desk or desk["status"] not in RESUME_FROM:
        return None
    return _launch_desk(desk_id, DESK_RESUME if desk["status"] == "interrupted" else DESK_CONTINUE, RESUME_FROM)


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
async def stream_conversation(id: str, since: int = 0, run_id: str | None = None) -> StreamingResponse:
    """Any number of clients may attach; detaching one never touches the run.

    A run that has left the bus — ended more than RETAIN_S ago, or died with the process — is
    replayed from the tape instead. A `run_id` that does not match the live run is ALWAYS served
    from the tape, so a stale `since` from a previous turn can never be applied to a new run's seq
    space and silently swallow its first events."""
    run = bus.get(id)
    if run is not None and (not run_id or run_id == run.run_id):
        return StreamingResponse(run.subscribe(since), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
    rid = run_id or (run_store.latest(id) or {}).get("run_id")
    gen: Any = run_store.tail(rid, since) if rid else _empty_stream()
    return StreamingResponse(gen, media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


async def _empty_stream() -> AsyncIterator[str]:
    """An async generator, because StreamingResponse picks its path from the body's type and the
    tape branch is one."""
    return
    yield ""  # pragma: no cover - unreachable, but it is what makes this a generator


@app.get("/runs")
async def list_runs(status: str | None = None, kind: str | None = None, conversation_id: str | None = None,
                    desk_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    """Table-backed, so a run blocked on an approval still appears after it has left the bus. The
    live bus is merged over the rows, because `seq` and `live` are only known in memory.

    `status` is a comma-separated list and defaults to the ACTIVE set, because the renderer attaches
    to everything this returns: a finished run listed here would be watched forever. `status=all`,
    or an explicit list, is how history is asked for."""
    sel: tuple[str, ...] | None
    if status == "all":
        sel = None
    elif status:
        sel = tuple(s.strip() for s in status.split(",") if s.strip())
    else:
        sel = RUN_ACTIVE
    rows = run_store.list(status=sel, kind=kind, conversation_id=conversation_id, desk_id=desk_id, limit=limit)
    live = {r["run_id"]: r for r in bus.list()}
    return [{**r, "live": False, **live.get(r["run_id"], {})} for r in rows]


@app.get("/runs/{run_id}")
async def get_run(run_id: str) -> dict[str, Any]:
    row = run_store.get(run_id)
    if not row:
        raise HTTPException(404, "No such run")
    live = next((r for r in bus.list() if r["run_id"] == run_id), None)
    return {**row, "live": False, **(live or {}),
            "executed": run_store.executed(run_id), "approvals": run_store.approvals(status=None, run_id=run_id)}


@app.get("/approvals")
async def list_approvals(status: str | None = "pending", desk_id: str | None = None) -> list[dict[str, Any]]:
    """The durable pending list: decidable from any window, and a restart does not orphan it."""
    return run_store.approvals(status=status or None, desk_id=desk_id)


# async, so the run's asyncio.Event is set on the loop that owns it rather than from a threadpool.
@app.post("/conversations/{id}/stop")
async def stop_run(id: str, run_id: str | None = None) -> dict[str, bool]:
    """Stop before the assistant message exists: detaching a stream would only drop a viewer."""
    return {"ok": bus.stop(id, run_id)}


class ApprovalIn(BaseModel):
    decision: str  # allow | deny | always_chat | always_global
    note: str | None = None


# async, so the Future is resolved on the loop that owns it rather than from a threadpool.
@app.post("/approvals/{call_id}")
async def approve_tool_call(call_id: str, body: ApprovalIn) -> dict[str, Any]:
    """The row is written first and first-decision-wins; the Future is only a fast wake. A card
    whose run died — parked, or lost to a restart — still decides cleanly, and resumes its desk."""
    if body.decision not in ("allow", "deny", "always_chat", "always_global"):
        raise HTTPException(400, "Bad decision")
    row = run_store.approval(call_id)
    fut = _approvals.get(call_id)
    if row is None and (fut is None or fut.done()):
        raise HTTPException(404, "No pending approval for that call")
    if row is not None:
        decided = run_store.decide(call_id, body.decision, by="user", note=body.note or "")
        if decided is None:
            return {"ok": False, "error": "already decided", "resumed": False}
    if body.note:
        _answers[call_id] = body.note
    if fut is not None and not fut.done():
        fut.set_result(body.decision)
    resumed = False
    did = (row or {}).get("desk_id")
    if did:
        resumed = _wake_desk(did) is not None
    return {"ok": True, "resumed": resumed}


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
        query=body.query, settings=cfg,
        conv_settings={"useMemory": True, "useGraph": True, "useDocuments": True, "useActivity": True, **body.conv_settings},
        global_system_prompt=cfg["systemPrompt"], activity=monitor, ledger=None,
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
    await asyncio.to_thread(sandboxes.shutdown)  # after the runs: a live sandbox_exec would just see its container vanish


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
    calendar_event_id: str | None = None
    calendar_link: str | None = None


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
        "You can close this tab and return to Grain.",
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
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"Google API error: {e}") from e


@app.get("/integrations/google/calendars")
def google_calendars() -> Any:
    return _gcall(google.calendars)


@app.get("/integrations/google/calendar")
def google_calendar(days: int = 2, start: str | None = None, calendars: str = "primary") -> Any:
    ids = None if calendars in ("", "primary") else [c.strip() for c in calendars.split(",") if c.strip()]
    return _gcall(google.calendar_events, days, "primary", 60, start, ids)


class AttendeeIn(BaseModel):
    email: str
    optional: bool = False
    # Carried through so a patched guest list does not reset everyone's RSVP.
    response: str | None = None


class ReminderIn(BaseModel):
    method: str = "popup"  # popup | email
    minutes: int = 10


class RemindersIn(BaseModel):
    use_default: bool = False
    overrides: list[ReminderIn] = []


class EventIn(BaseModel):
    """Create/patch body; on PATCH only the fields sent change."""
    summary: str | None = None
    start: str | None = None  # YYYY-MM-DD (all-day) or ISO datetime
    end: str | None = None
    time_zone: str | None = None
    description: str | None = None
    location: str | None = None
    attendees: list[AttendeeIn] | None = None
    recurrence: list[str] | None = None  # RRULE lines; [] clears
    reminders: RemindersIn | None = None
    color_id: str | None = None
    visibility: str | None = None  # default | public | private
    transparency: str | None = None  # opaque (busy) | transparent (free)
    guests_can_invite_others: bool | None = None
    guests_can_modify: bool | None = None
    guests_can_see_other_guests: bool | None = None
    create_meet: bool = False
    clear_meet: bool = False
    calendar_id: str = "primary"
    move_to_calendar_id: str | None = None
    send_updates: str = "none"  # none | all | externalOnly


def _event_fields(body: EventIn) -> dict[str, Any]:
    f = body.model_dump(exclude_none=True, exclude={"calendar_id", "send_updates"})
    # False is meaningless on a patch (nothing to undo) and harmless on create.
    for k in ("create_meet", "clear_meet"):
        if not f.get(k):
            f.pop(k, None)
    return f


@app.post("/integrations/google/calendar")
def google_calendar_create(body: EventIn) -> Any:
    if not (body.summary or "").strip() or not body.start:
        raise HTTPException(400, "An event needs at least a summary and a start.")
    return _gcall(google.calendar_create, _event_fields(body), body.calendar_id, body.send_updates)


# Registered before the {event_id} routes so "colors" is not read as an event id.
@app.get("/integrations/google/calendar/colors")
def google_calendar_colors() -> Any:
    return _gcall(google.calendar_colors)


@app.get("/integrations/google/calendar/{event_id}")
def google_calendar_get(event_id: str, calendar_id: str = "primary") -> Any:
    return _gcall(google.calendar_get, event_id, calendar_id)


@app.patch("/integrations/google/calendar/{event_id}")
def google_calendar_update(event_id: str, body: EventIn) -> Any:
    return _gcall(google.calendar_update, event_id, _event_fields(body), body.calendar_id, body.send_updates)


@app.delete("/integrations/google/calendar/{event_id}")
def google_calendar_delete(event_id: str, calendar_id: str = "primary", send_updates: str = "none") -> Any:
    return _gcall(google.calendar_delete, event_id, calendar_id, send_updates)


class RespondIn(BaseModel):
    response: str  # accepted | declined | tentative | needsAction
    calendar_id: str = "primary"
    send_updates: str = "all"


@app.post("/integrations/google/calendar/{event_id}/respond")
def google_calendar_respond(event_id: str, body: RespondIn) -> Any:
    return _gcall(google.calendar_respond, event_id, body.response, body.calendar_id, body.send_updates)


@app.get("/integrations/google/gmail")
def google_gmail(q: str = "is:unread in:inbox newer_than:14d", max_results: int = 12) -> Any:
    return _gcall(google.gmail_search, q, max_results)


# Registered before the {message_id} route so "labels" is not read as a message id.
@app.get("/integrations/google/gmail/labels")
def google_gmail_labels() -> Any:
    return _gcall(google.gmail_labels)


@app.get("/integrations/google/gmail/{message_id}")
def google_gmail_message(message_id: str) -> Any:
    return _gcall(google.gmail_get, message_id)


class GmailModifyIn(BaseModel):
    mark_read: bool | None = None
    archive: bool = False
    star: bool | None = None


@app.post("/integrations/google/gmail/{message_id}/modify")
def google_gmail_modify(message_id: str, body: GmailModifyIn) -> Any:
    return _gcall(google.gmail_modify, message_id, body.mark_read, body.archive, body.star)


class GmailComposeIn(BaseModel):
    to: str
    subject: str = ""
    body: str = ""
    reply_to_message_id: str | None = None


@app.post("/integrations/google/gmail/draft")
def google_gmail_draft(body: GmailComposeIn) -> Any:
    return _gcall(google.gmail_draft, body.to, body.subject, body.body, body.reply_to_message_id)


@app.post("/integrations/google/gmail/send")
def google_gmail_send(body: GmailComposeIn) -> Any:
    return _gcall(google.gmail_send, body.to, body.subject, body.body, body.reply_to_message_id)


# ---------------- assist (inline completion + draft review) ----------------
class CompleteIn(BaseModel):
    kind: str = "text"
    before: str
    after: str = ""
    context: str = ""


@app.post("/assist/complete")
async def assist_complete(body: CompleteIn) -> dict[str, str]:
    try:
        return {"completion": await assist.complete_text(settings(), body.kind, body.before, body.after, body.context)}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"Completion failed: {e}") from e


class MailReviewIn(BaseModel):
    to: str = ""
    subject: str = ""
    body: str
    reply_context: str = ""


@app.post("/assist/mail-review")
async def assist_mail_review(body: MailReviewIn) -> dict[str, Any]:
    try:
        return await assist.review_email(settings(), body.to, body.subject, body.body, body.reply_context)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"Review failed: {e}") from e


@app.get("/integrations/google/tasks")
def google_tasks(show_completed: bool = False) -> Any:
    return _gcall(google.tasks_list, "@default", show_completed)


# ---------------- Google Tasks <-> todos sync ----------------
@app.get("/integrations/google/tasklists")
def google_tasklists() -> Any:
    return _gcall(google.tasks_lists)


class TasksSyncIn(BaseModel):
    enabled: bool | None = None
    tasklist: str | None = None
    intervalMinutes: int | None = None


@app.get("/integrations/google/tasks-sync")
def google_tasks_sync_status() -> dict[str, Any]:
    return tasks_sync.status()


@app.put("/integrations/google/tasks-sync")
def google_tasks_sync_config(body: TasksSyncIn) -> dict[str, Any]:
    tasks_sync.set_config(body.model_dump(exclude_none=True))
    return tasks_sync.status()


@app.post("/integrations/google/tasks-sync/run")
async def google_tasks_sync_run() -> dict[str, Any]:
    try:
        await asyncio.to_thread(tasks_sync.sync_once)
    except GoogleNotConnected as e:
        raise HTTPException(409, str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"Google Tasks sync failed: {e}") from e
    return tasks_sync.status()


@app.on_event("startup")
async def _tasks_sync_startup() -> None:
    app.state.gtasks_task = asyncio.create_task(tasks_sync.loop())


@app.on_event("shutdown")
async def _tasks_sync_shutdown() -> None:
    task = getattr(app.state, "gtasks_task", None)
    if task:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task


@app.get("/integrations/google/drive")
def google_drive(q: str = "", max_results: int = 20) -> Any:
    return _gcall(google.drive_files, q, max_results)


def _google_has(status: dict[str, Any], scope_tail: str) -> bool:
    """Whether the connected token was granted a scope (endswith, so 'tasks' or 'drive.readonly')."""
    return any(s.endswith(scope_tail) for s in status.get("scopes") or [])


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
        "recent_conversations": convos.list(ALL)[:6],   # desks excluded: a desk belongs on the Cowork rail
        "calendar": None, "gmail": None, "tasks": None, "drive": None, "errors": {},
    }
    if st["connected"]:
        async def fetch(key: str, fn, *args) -> None:  # type: ignore[no-untyped-def]
            try:
                out[key] = json_safe(await asyncio.to_thread(fn, *args))
            except Exception as e:  # noqa: BLE001
                out["errors"][key] = str(e)

        jobs = [
            fetch("calendar", google.calendar_events, 2),
            fetch("gmail", google.gmail_search, "is:unread in:inbox newer_than:14d", 10),
            fetch("tasks", google.tasks_list, "@default", False),
        ]
        # Drive is a newer scope; before the user reconnects, skip the call instead of
        # surfacing a 403 — the card reads missing_scopes and offers Reconnect.
        if _google_has(st, "drive.readonly"):
            jobs.append(fetch("drive", google.drive_files, "", 10))
        await asyncio.gather(*jobs)
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
        "calendar": [], "gmail": [], "tasks": [], "drive": [],
    }
    if st["connected"]:
        try:
            out["calendar"] = json_safe(await asyncio.to_thread(google.calendar_events, 3))
        except Exception as e:  # noqa: BLE001
            out["calendar_error"] = str(e)
        try:
            out["gmail"] = json_safe(await asyncio.to_thread(google.gmail_search, "is:unread in:inbox newer_than:14d", 15))
        except Exception as e:  # noqa: BLE001
            out["gmail_error"] = str(e)
        try:
            out["tasks"] = json_safe(await asyncio.to_thread(google.tasks_list, "@default", False))
        except Exception as e:  # noqa: BLE001
            out["tasks_error"] = str(e)
        if _google_has(st, "drive.readonly"):
            try:
                out["drive"] = json_safe(await asyncio.to_thread(google.drive_files, "", 15))
            except Exception as e:  # noqa: BLE001
                out["drive_error"] = str(e)
    return out


@app.get("/sources")
def list_sources() -> dict[str, Any]:
    return {"sources": dashboards.sources(), "internal": ["todos", "calendar", "gmail", "tasks", "drive", "memories", "projects", "boards"]}


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
    except tools.UrlBlocked as e:  # the source's own URL, or something it redirected to, is not a public address
        raise HTTPException(400, f"That source's URL was refused: {e}") from e
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
    recent_convs = [c for c in convos.list(ALL, include_desks=True) if c["updated_at"] >= since][:10]  # a desk's work happened too
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
presets = CanvasPresets(db, canvases)
# 'popped' rows are NOT reset here: import runs before the main process can restore them (it clears the ones it declines).


def _finite(v: float) -> float:
    """A coordinate that is not a real number is not a position.

    SQLite stores inf happily and pydantic then serialises it back as JSON null, so one Infinity -- a drag divided by
    a zero zoom, say -- left a window with x/y/w/h of null that the canvas could no longer place, and the damage
    persisted across reloads. Reject it at the edge instead.
    """
    if not math.isfinite(v):
        raise ValueError("must be a finite number")
    return v


Finite = Annotated[float, AfterValidator(_finite)]
# Zoom divides drag deltas, so zero or negative is both meaningless and the thing that mints the infinities above.
# The renderer clamps the range it actually uses (Canvas.tsx MIN_ZOOM/MAX_ZOOM); this only rules out the impossible.
PositiveFinite = Annotated[float, AfterValidator(_finite), Field(gt=0)]


class CanvasIn(BaseModel):
    name: str = "Desk"
    project_id: str | None = None
    copy_from: str | None = None


class CanvasPatch(BaseModel):
    name: str | None = None
    project_id: str | None = None
    position: int | None = None
    snap_mode: str | None = None
    grid_size: Annotated[int, Field(ge=1)] | None = None
    zoom: PositiveFinite | None = None
    pan_x: Finite | None = None
    pan_y: Finite | None = None
    wallpaper: str | None = None
    clear_project: bool = False


class WindowIn(BaseModel):
    kind: str
    ref_id: str | None = None
    project_id: str | None = None
    title: str = ""
    x: Finite = 0
    y: Finite = 0
    w: Finite = 520
    h: Finite = 640
    config: dict[str, Any] = {}


class WindowPatch(BaseModel):
    title: str | None = None
    # Re-point the window at another referent (a chat window switching conversations).
    ref_id: str | None = None
    config: dict[str, Any] | None = None
    state: str | None = None
    pinned: bool | None = None
    x: Finite | None = None
    y: Finite | None = None
    w: Finite | None = None
    h: Finite | None = None
    z: int | None = None
    canvas_id: str | None = None
    restore_bounds: dict[str, Any] | None = None
    popout_bounds: dict[str, Any] | None = None  # PopoutBounds.display is an int; dict[str, float] would round it
    clear_restore_bounds: bool = False
    clear_popout_bounds: bool = False


class WindowLayoutIn(BaseModel):
    id: str
    x: Finite | None = None
    y: Finite | None = None
    w: Finite | None = None
    h: Finite | None = None
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


# ---------------- docs: long-form markdown notes with reviewable revisions ----------------


class DocIn(BaseModel):
    title: str = "Untitled"
    content: str = ""
    folder: str = ""
    project_id: str | None = None


class DocSave(BaseModel):
    """The editor's autosave. `content` and `title` are both optional so either can be saved alone."""
    content: str | None = None
    title: str | None = None
    summary: str = ""


class DocMetaPatch(BaseModel):
    title: str | None = None
    folder: str | None = None
    starred: bool | None = None
    project_id: str | None = None
    clear_project: bool = False


@app.get("/docs")
def list_docs(project_id: str | None = "all", q: str = "") -> list[dict[str, Any]]:
    scope = "__all__" if project_id in (None, "all") else sid(project_id)
    return docs.list(scope, q)


@app.get("/docs/pending")
def docs_pending() -> dict[str, int]:
    """Badge count for the sidebar: assistant edits waiting to be reviewed."""
    return {"pending": docs.pending_count()}


@app.post("/docs")
def create_doc(body: DocIn) -> dict[str, Any]:
    return docs.create(body.title, body.content, sid(body.project_id), body.folder)


@app.get("/docs/{id}")
def get_doc(id: str) -> dict[str, Any]:
    d = docs.get(id)
    if not d:
        raise HTTPException(404)
    return d


@app.put("/docs/{id}")
def save_doc(id: str, body: DocSave) -> dict[str, Any]:
    d = docs.save(id, body.content, body.title, body.summary)
    if not d:
        raise HTTPException(404)
    return d


@app.patch("/docs/{id}")
def patch_doc(id: str, body: DocMetaPatch) -> dict[str, Any]:
    patch = body.model_dump(exclude_none=True, exclude={"clear_project"})
    if body.clear_project:
        patch["project_id"] = None
    elif "project_id" in patch:
        patch["project_id"] = sid(patch["project_id"])
    d = docs.update_meta(id, patch)
    if not d:
        raise HTTPException(404)
    return d


@app.delete("/docs/{id}")
def delete_doc(id: str) -> dict[str, bool]:
    docs.delete(id)
    return {"ok": True}


@app.get("/docs/{id}/revisions")
def doc_revisions(id: str, limit: int = 100) -> list[dict[str, Any]]:
    if not docs.get(id):
        raise HTTPException(404)
    return docs.revisions(id, limit)


@app.get("/docs/revisions/{rev_id}")
def doc_revision(rev_id: str) -> dict[str, Any]:
    r = docs.revision(rev_id)
    if not r:
        raise HTTPException(404)
    return {**r, "patch": unified_diff(r["before"], r["after"])}


@app.post("/docs/revisions/{rev_id}/accept")
def accept_revision(rev_id: str) -> dict[str, Any]:
    d = docs.accept(rev_id)
    if not d:
        raise HTTPException(404, "No pending revision with that id")
    return d


@app.post("/docs/revisions/{rev_id}/reject")
def reject_revision(rev_id: str) -> dict[str, Any]:
    d = docs.reject(rev_id)
    if not d:
        raise HTTPException(404, "No pending revision with that id")
    return d


@app.post("/docs/revisions/{rev_id}/restore")
def restore_revision(rev_id: str) -> dict[str, Any]:
    d = docs.restore(rev_id)
    if not d:
        raise HTTPException(404)
    return d


# ---------------- activity monitor ----------------
#
# Everything here is inert until the user turns it on. The renderer drives it from the Activity
# panel; the summaries it produces land in <data_dir>/context/activity.md and, when the user
# leaves injection on, in each chat's context block.


class ActivityConfigIn(BaseModel):
    """A partial patch, deep-merged over the stored config."""

    enabled: bool | None = None
    signals: dict[str, bool] | None = None
    sampleSeconds: int | None = None
    idleSeconds: int | None = None
    rollupMinutes: int | None = None
    retentionHours: float | None = None
    summaryRetentionDays: float | None = None
    contextDays: int | None = None
    injectContext: bool | None = None
    redact: bool | None = None
    excludeApps: list[str] | None = None
    excludeTitlePatterns: list[str] | None = None
    audio: dict[str, Any] | None = None
    summaryModel: str | None = None
    profileEveryHours: float | None = None


class PauseIn(BaseModel):
    minutes: float = 30.0


class PurgeIn(BaseModel):
    scope: str = "expired"  # expired | events | summaries | all


@app.get("/activity/status")
def activity_status() -> dict[str, Any]:
    return monitor.status()


@app.put("/activity/config")
def activity_config(body: ActivityConfigIn) -> dict[str, Any]:
    monitor.set_config(body.model_dump(exclude_none=True))
    return monitor.status()


@app.post("/activity/start")
def activity_start() -> dict[str, Any]:
    if not activity.IS_MAC:
        raise HTTPException(400, "The activity collectors are macOS-only.")
    return monitor.start()


@app.post("/activity/stop")
def activity_stop() -> dict[str, Any]:
    return monitor.stop()


@app.post("/activity/pause")
def activity_pause(body: PauseIn) -> dict[str, Any]:
    return monitor.pause(body.minutes)


@app.post("/activity/resume")
def activity_resume() -> dict[str, Any]:
    return monitor.resume()


@app.get("/activity/events")
def activity_events(limit: int = 200, hours: float = 24.0, kind: str = "") -> list[dict[str, Any]]:
    """The raw log, newest first - so the user can see exactly what was recorded about them."""
    kinds = [k for k in kind.split(",") if k] or None
    return monitor.store.recent(limit=min(int(limit), 2000), since=time.time() - max(0.1, hours) * 3600, kinds=kinds)


@app.delete("/activity/events/{eid}")
def activity_delete_event(eid: str) -> dict[str, bool]:
    monitor.store.delete_event(eid)
    return {"ok": True}


@app.get("/activity/summaries")
def activity_summaries(day: str | None = None, days: float = 7.0, limit: int = 200) -> list[dict[str, Any]]:
    since = None if day else time.time() - max(0.1, days) * 86400
    return monitor.store.summaries(day=day, since=since, limit=min(int(limit), 500))


@app.delete("/activity/summaries/{sid_}")
def activity_delete_summary(sid_: str) -> dict[str, bool]:
    monitor.store.delete_summary(sid_)
    monitor.write_markdown()
    return {"ok": True}


@app.post("/activity/rollup")
async def activity_rollup() -> dict[str, Any]:
    """Summarize whatever is pending right now instead of waiting for the interval."""
    s = await monitor.rollup_once(force=True)
    return {"summary": s, "status": monitor.status()}


@app.post("/activity/profile")
async def activity_profile() -> dict[str, Any]:
    return {"profile": await monitor.refresh_profile()}


@app.get("/activity/context")
def activity_context() -> dict[str, Any]:
    """The markdown file plus the trimmed block chats actually see."""
    monitor.write_markdown()
    return {"path": str(monitor.md_path), "markdown": monitor.read_markdown(), "injected": monitor.context_block()}


@app.get("/activity/devices")
def activity_devices() -> list[dict[str, str]]:
    return activity.audio_devices()


@app.post("/activity/purge")
def activity_purge(body: PurgeIn) -> dict[str, Any]:
    cfg = monitor.config()
    out = monitor.store.purge(body.scope, float(cfg["retentionHours"]), float(cfg["summaryRetentionDays"]))
    monitor.write_markdown()
    return {"deleted": out, "status": monitor.status()}


@app.on_event("startup")
async def _activity_startup() -> None:
    """Resume the monitor if it was on when the app last quit, and run the rollup loop."""
    if monitor.config().get("enabled") and activity.IS_MAC:
        try:
            monitor.start()
        except Exception as e:  # noqa: BLE001 - a failing probe must not stop the backend booting
            log.warning("activity: could not resume: %s", e)
    app.state.activity_task = asyncio.create_task(monitor.loop())


@app.on_event("shutdown")
async def _activity_shutdown() -> None:
    task = getattr(app.state, "activity_task", None)
    if task:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task
    monitor.stop(persist=False)  # keep `enabled` so the next launch resumes
# ---------------- space presets: named templates of a canvas ----------------
class PresetIn(BaseModel):
    canvas_id: str
    name: str = ""


class PresetPatch(BaseModel):
    name: str | None = None


class PresetInstantiateIn(BaseModel):
    name: str | None = None


@app.get("/canvas-presets")
def list_canvas_presets() -> list[dict[str, Any]]:
    return presets.list()


@app.get("/canvas-presets/{pid}")
def get_canvas_preset(pid: str) -> dict[str, Any]:
    p = presets.get(pid)
    if not p:
        raise HTTPException(404, "No such preset")
    return p


@app.post("/canvas-presets")
def create_canvas_preset(body: PresetIn) -> dict[str, Any]:
    p = presets.create_from_canvas(body.canvas_id, body.name)
    if not p:
        raise HTTPException(404, "Unknown canvas")
    return p


@app.put("/canvas-presets/{pid}")
def update_canvas_preset(pid: str, body: PresetPatch) -> dict[str, Any]:
    patch = body.model_dump(exclude_none=True)
    if "name" in patch:
        patch["name"] = patch["name"].strip()
        if not patch["name"]:
            raise HTTPException(400, "Preset name required")
    p = presets.update(pid, patch)
    if not p:
        raise HTTPException(404, "No such preset")
    return p


@app.delete("/canvas-presets/{pid}")
def delete_canvas_preset(pid: str) -> dict[str, bool]:
    presets.delete(pid)
    return {"ok": True}


@app.post("/canvas-presets/{pid}/instantiate")
def instantiate_canvas_preset(pid: str, body: PresetInstantiateIn) -> dict[str, Any]:
    c = presets.instantiate(pid, body.name)
    if not c:
        raise HTTPException(404, "No such preset")
    return c


# ---------------- cowork: desks, plans and workspaces ----------------
class DeskIn(BaseModel):
    brief: str
    title: str | None = None
    project_id: str | None = None
    autonomy: str = "plan"
    budget: dict[str, Any] | None = None
    start: bool = True


class DeskPatch(BaseModel):
    title: str | None = None
    autonomy: str | None = None
    project_id: str | None = None
    archived: bool | None = None
    budget: dict[str, Any] | None = None
    clear_project: bool = False


class DeskMessageIn(BaseModel):
    content: str


class DeskResumeIn(BaseModel):
    reason: str | None = None


class AcceptItem(BaseModel):
    output_id: str
    destination: str                      # doc | doc_append | document | download
    title: str | None = None
    doc_id: str | None = None
    project_id: str | None = None


class AcceptIn(BaseModel):
    outputs: list[AcceptItem]


class RejectIn(BaseModel):
    output_ids: list[str] | None = None   # None = every undecided output
    note: str | None = None


class PlanDecisionIn(BaseModel):
    decision: str                          # approve | edit | reject
    steps: Any = None                      # [{idx, arguments} | {idx, drop: true}]
    note: str | None = None


UNDECIDED = ("proposed", "stale", "promote_failed")


def _desk_or_404(id: str, with_outputs: bool = True) -> dict[str, Any]:
    desk = desks.get(id, with_outputs)
    if not desk:
        raise HTTPException(404, "No such desk")
    return desk


def _read_whole(desk_id: str, rel: str) -> str:
    """The whole file, paged. Workspace.read is a window by design; promotion needs all of it."""
    out: list[str] = []
    off = 0
    while True:
        chunk = workspace.read(desk_id, rel, off, MAX_PREVIEW)
        out.append(chunk["text"])
        nxt = int(chunk.get("next_offset") or 0)
        if not chunk.get("truncated") or nxt <= off:
            break
        off = nxt
    return "".join(out)


def _outputs_view(desk_id: str) -> list[dict[str, Any]]:
    """Each output with its sha re-checked against the disk, so the review UI can never preview
    bytes the agent has since rewritten. The row itself is not rewritten: `stale` is a statement
    about this moment, and claim_output accepts a stale row anyway."""
    rows: list[dict[str, Any]] = []
    for o in desks.outputs(desk_id):
        row = dict(o)
        if o["status"] in ("proposed", "stale"):
            try:
                row["status"] = "proposed" if workspace.sha(desk_id, o["path"]) == o["sha256"] else "stale"
            except WorkspaceError as e:
                row["status"], row["error"] = "stale", str(e)
        rows.append(row)
    return rows


@app.get("/cowork/desks")
def list_desks(project_id: str | None = None, status: str | None = None, archived: bool = False) -> list[dict[str, Any]]:
    if status and status not in DESK_STATUSES:
        raise HTTPException(400, f"Unknown desk status {status!r}")
    scope = sid(project_id)
    return desks.list("__all__" if project_id is None or scope is ALL else scope, status, archived)


@app.post("/cowork/desks")
async def create_desk(body: DeskIn) -> dict[str, Any]:
    brief = (body.brief or "").strip()
    if not brief:
        raise HTTPException(400, "A desk needs a brief")
    if body.autonomy not in AUTONOMY:
        raise HTTPException(400, f"Unknown autonomy {body.autonomy!r}")
    pid = wsid(body.project_id)
    cfg = settings()
    live_cap = int(cfg.get("deskMaxLive") or 0)
    if body.start and live_cap > 0 and desks.live_count() >= live_cap:
        raise HTTPException(409, {"message": "Too many desks are running at once",
                                  "live": desks.live_count(), "max": live_cap})
    conv = convos.create(pid, _title_from(body.title or brief), cfg["defaultModel"])
    try:
        desk = desks.create(conversation_id=conv["id"], brief=brief, title=(body.title or "").strip(),
                            project_id=pid, autonomy=body.autonomy or "plan", budget=body.budget)
    except ValueError as e:
        convos.delete(conv["id"])        # the conversation exists only to hold this desk's transcript
        raise HTTPException(400, str(e)) from e
    # Marked here rather than in Desks: `deskId` is what keeps the conversation out of Recent
    # chats. planMode follows the autonomy the user picked — 'ask as it goes' cards each change
    # instead of planning first, so writing 'always' for it would make the two modes identical and
    # the chat view's own plan toggle a lie. _chat_stream reads the desk's autonomy, not this, so a
    # later change of autonomy still takes effect; this keeps the stored setting honest.
    convos.update(conv["id"], {"settings": {"deskId": desk["id"],
                                            "planMode": "off" if desk["autonomy"] == "ask" else "always"}})
    out: dict[str, Any] = {"desk": desk, "conversation_id": conv["id"]}
    if body.start:
        run = _launch_desk(desk["id"], brief, START_FROM)
        if run is not None:
            out["run_id"], out["seq"] = run.run_id, run.seq
        out["desk"] = desks.get(desk["id"]) or desk
    return out


@app.get("/cowork/desks/{id}")
def get_desk(id: str) -> dict[str, Any]:
    desk = _desk_or_404(id)
    plan = aplans.get(desk["plan_id"]) if desk.get("plan_id") else aplans.for_desk(id)
    return {**desk, "plan": plan, "outputs": _outputs_view(id), "events": desks.events(id),
            "runs": run_store.list(desk_id=id, limit=20)}


@app.patch("/cowork/desks/{id}")
def patch_desk(id: str, body: DeskPatch) -> dict[str, Any]:
    if body.autonomy is not None and body.autonomy not in AUTONOMY:
        raise HTTPException(400, f"Unknown autonomy {body.autonomy!r}")
    patch = body.model_dump(exclude_none=True)
    patch.pop("clear_project", None)
    if body.clear_project:
        patch["project_id"] = None
    elif "project_id" in patch:
        patch["project_id"] = wsid(patch["project_id"])
    try:
        desk = desks.update(id, patch)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    if not desk:
        raise HTTPException(404, "No such desk")
    return desk


@app.delete("/cowork/desks/{id}")
async def delete_desk(id: str, purge: bool = False) -> dict[str, bool]:
    """The workspace is kept by default: a deleted desk's files are the one thing the user cannot
    regenerate. The conversation is deleted explicitly, because the cascade runs conversation ->
    desk and not the other way round, and that is what takes the runs and their tapes with it."""
    desk = _desk_or_404(id, False)
    bus.stop(desk["conversation_id"])
    task = _desk_tasks.pop(id, None)
    if task is not None:
        task.cancel()
    desks.delete(id)
    convos.delete(desk["conversation_id"])
    if purge:
        workspace.purge(id)
    return {"ok": True}


@app.post("/cowork/desks/{id}/start")
async def start_desk(id: str) -> dict[str, Any]:
    desk = _desk_or_404(id, False)
    live_cap = int(settings().get("deskMaxLive") or 0)
    if live_cap > 0 and desks.live_count() >= live_cap:
        raise HTTPException(409, {"message": "Too many desks are running at once",
                                  "live": desks.live_count(), "max": live_cap})
    run = _launch_desk(id, desk["brief"], START_FROM)
    if run is None:
        raise HTTPException(409, {"message": "That desk is already running, or is not startable",
                                  "status": (desks.get(id, False) or {}).get("status")})
    return {"run_id": run.run_id, "seq": run.seq, "conversation_id": desk["conversation_id"]}


@app.post("/cowork/desks/{id}/resume")
async def resume_desk(id: str, body: DeskResumeIn | None = None) -> dict[str, Any]:
    _desk_or_404(id, False)
    run = _wake_desk(id)
    if run is None:
        raise HTTPException(409, {"message": "That desk cannot be resumed from here",
                                  "status": (desks.get(id, False) or {}).get("status")})
    return {"run_id": run.run_id, "seq": run.seq}


@app.post("/cowork/desks/{id}/message")
async def message_desk(id: str, body: DeskMessageIn) -> dict[str, Any]:
    """The same box whether the agent is awake or not: live, it steers the running reply; asleep,
    it is the content of the next turn. An answer to desk_ask clears the question either way."""
    desk = _desk_or_404(id, False)
    text = (body.content or "").strip()
    if not text:
        raise HTTPException(400, "Empty message")
    if desk["question"]:
        desks.set_status(id, desk["status"], reason=desk["status_reason"], question="", event=False)
    run = bus.live(desk["conversation_id"])
    if run is not None:
        um = convos.add_message(desk["conversation_id"], "user", text)
        run.publish("user_message", um)
        run.steers.append(um)
        return {"ok": True, "steered": True, "run_id": run.run_id}
    started = _launch_desk(id, text, MESSAGE_FROM)
    if started is None:
        raise HTTPException(409, {"message": "That desk could not take a message right now",
                                  "status": (desks.get(id, False) or {}).get("status")})
    return {"ok": True, "steered": False, "run_id": started.run_id}


@app.post("/cowork/desks/{id}/pause")
async def pause_desk(id: str) -> dict[str, Any]:
    """Paused before the run is stopped, so settle() sees a desk that is no longer LIVE and leaves
    the decision alone rather than reading the cooperative stop back as a user Stop."""
    desk = _desk_or_404(id, False)
    out = desks.set_status(id, "paused", reason="paused", headline="")
    bus.stop(desk["conversation_id"])
    return out or desk


@app.post("/cowork/desks/{id}/stop")
async def stop_desk(id: str) -> dict[str, Any]:
    """Stopped before the run is, for the same reason pause is: settle() and the cancellation
    handler both read the row back, and whichever of them runs last must find the decision the
    user made, not overwrite it."""
    desk = _desk_or_404(id, False)
    out = desks.set_status(id, "stopped", reason="stopped", headline="")
    bus.stop(desk["conversation_id"])
    task = _desk_tasks.pop(id, None)
    if task is not None:
        task.cancel()
    return out or desk


@app.get("/cowork/desks/{id}/events")
def desk_event_list(id: str, limit: int = 200) -> list[dict[str, Any]]:
    _desk_or_404(id, False)
    return desks.events(id, limit)


@app.post("/cowork/desks/{id}/seen")
def desk_mark_seen(id: str) -> dict[str, Any]:
    """Clear this desk's Needs-you badge: every unseen needs_you event of the desk, marked seen."""
    _desk_or_404(id, False)
    desks.mark_desk_seen(id)
    return get_desk(id)


@app.get("/cowork/desks/{id}/files")
def desk_file_tree(id: str, path: str = "") -> dict[str, Any]:
    _desk_or_404(id, False)
    try:
        return {"files": workspace.tree(id, path), "usage": workspace.usage(id)}
    except WorkspaceError as e:
        raise HTTPException(400, str(e)) from e


@app.get("/cowork/desks/{id}/file")
def desk_file_preview(id: str, path: str, offset: int = 0, length: int = 6000) -> dict[str, Any]:
    _desk_or_404(id, False)
    try:
        return {**workspace.read(id, path, offset, length), "binary": False, "state": workspace.state(id, path)}
    except WorkspaceError as e:
        if "not a text file" not in str(e):
            raise HTTPException(400, str(e)) from e
    # A binary file is named and sized, never decoded: the preview pane says what it is instead.
    p = workspace.resolve_in(id, path)
    return {"path": path, "text": "", "bytes": p.stat().st_size if p.is_file() else 0,
            "truncated": False, "binary": True, "state": workspace.state(id, path)}


@app.get("/cowork/desks/{id}/download")
def desk_file_download(id: str, path: str) -> FileResponse:
    """The one way bytes leave a desk, and what the `download` promotion destination promises.
    Without it that destination marked the row promoted and verified while handing over nothing:
    the preview route returns text only, and says of a binary file just its name and size."""
    _desk_or_404(id, False)
    try:
        p = workspace.resolve_in(id, path)
    except WorkspaceError as e:
        raise HTTPException(400, str(e)) from e
    if not p.is_file():
        raise HTTPException(404, "No such file")
    # octet-stream, always: a desk's workspace is agent-written, and letting the browser decide to
    # render an .html or .svg from it would be rendering agent output as a page.
    return FileResponse(p, filename=Path(path).name, media_type="application/octet-stream")


@app.get("/cowork/desks/{id}/diff")
def desk_file_diff(id: str, path: str) -> dict[str, Any]:
    _desk_or_404(id, False)
    try:
        return workspace.diff(id, path)
    except WorkspaceError as e:
        raise HTTPException(400, str(e)) from e


@app.get("/cowork/desks/{id}/outputs")
def desk_output_list(id: str) -> list[dict[str, Any]]:
    _desk_or_404(id, False)
    return _outputs_view(id)


async def _promote(desk_id: str, out: dict[str, Any], item: AcceptItem) -> dict[str, Any]:
    """Promote one output and read it back. Returns {ref, verified, error}.

    Nothing here trusts the write: `verified` is the result of fetching the promoted copy again and
    comparing it to the bytes that were actually promoted, never to what was intended.
    """
    rel = out["path"]
    dest = item.destination
    if dest not in ("doc", "doc_append", "document", "download"):
        return {"ref": None, "verified": False, "error": f"unknown destination {dest!r}"}
    title = (item.title or out["title"] or Path(rel).name).strip()
    if dest == "download":
        # Nothing enters the app, so the hand-off IS the file: `ref` is the path GET
        # /cowork/desks/{id}/download serves, and the read-back is that file still being there and
        # still being the bytes this output was declared with. Without both, the row would read
        # promoted and verified having given the user nothing at all.
        try:
            p = workspace.resolve_in(desk_id, rel)
        except WorkspaceError as e:
            return {"ref": None, "verified": False, "error": str(e)}
        if not p.is_file():
            return {"ref": None, "verified": False, "error": "the file is no longer in the workspace"}
        ok = hashlib.sha256(p.read_bytes()).hexdigest() == out["sha256"]
        return {"ref": rel, "verified": ok,
                "error": None if ok else "the file has changed since it was delivered"}
    content = _read_whole(desk_id, rel)
    if dest == "doc":
        doc = docs.create(title, content, wsid(item.project_id) if item.project_id else None)
        fresh = docs.get(doc["id"])
        ok = bool(fresh) and fresh["content"] == content
        return {"ref": doc["id"], "verified": ok, "error": None if ok else "the saved doc does not match the file"}
    if dest == "doc_append":
        if not item.doc_id:
            return {"ref": None, "verified": False, "error": "doc_append needs a doc_id"}
        cur = docs.get(item.doc_id)
        if not cur:
            return {"ref": None, "verified": False, "error": "no such doc"}
        after = (cur["content"].rstrip() + "\n\n" + content) if cur["content"].strip() else content
        # propose, never apply: the doc the user wrote is untouched until they accept the revision
        # in the existing Docs review UI. An agent never overwrites a document the user wrote.
        rev = docs.propose(item.doc_id, after, f"Appended {rel} from a cowork desk", tool="cowork")
        if not rev:
            return {"ref": None, "verified": False, "error": "could not record the revision"}
        fresh = docs.revision(rev["id"])
        untouched = (docs.get(item.doc_id) or {}).get("content") == cur["content"]
        ok = bool(fresh) and fresh["after"] == after and untouched
        return {"ref": rev["id"], "verified": ok,
                "error": None if ok else "the pending revision does not match the file"}
    data = workspace.resolve_in(desk_id, rel).read_bytes()
    stored = db.data_dir / "uploads" / f"{new_id()}-{Path(rel).name}"
    stored.parent.mkdir(parents=True, exist_ok=True)
    stored.write_bytes(data)
    try:
        text = extract_text(Path(rel).name, data, "")
    except Exception as e:  # noqa: BLE001 - an unreadable file is a failed promotion, not a 500
        return {"ref": None, "verified": False, "error": str(e)}
    doc = documents.create(wsid(item.project_id) if item.project_id else None, title, "", len(data), str(stored), text)
    # Compared against the STORED UPLOAD FILE, not the chunk text: the chunks are a derived,
    # normalised representation, and comparing to them would report a failure on every upload.
    ok = stored.is_file() and hashlib.sha256(stored.read_bytes()).hexdigest() == hashlib.sha256(data).hexdigest()
    return {"ref": doc["id"], "verified": ok,
            "error": None if ok else "the stored upload does not match the file"}


@app.post("/cowork/desks/{id}/accept")
async def accept_outputs(id: str, body: AcceptIn) -> dict[str, Any]:
    """The only promotion path. Per output: claim (the rowcount is the lock, so a double-clicked
    Accept promotes once), book the work through call_once so a crash between the claim and the
    read-back leaves an auditable `unknown` row rather than a silent half-promotion, promote, then
    verify by reading the promoted copy back. A mismatch is `promote_failed` and stays retryable."""
    desk = _desk_or_404(id, False)
    results: list[dict[str, Any]] = []
    for item in body.outputs:
        out = desks.output(item.output_id)
        if not out or out["desk_id"] != id:
            results.append({"output_id": item.output_id, "ok": False, "verified": False,
                            "kind": item.destination, "ref": None, "error": "no such output"})
            continue
        claimed = desks.claim_output(item.output_id)
        if claimed is None:
            results.append({"output_id": item.output_id, "ok": False, "verified": False,
                            "kind": item.destination, "ref": None, "error": "already decided"})
            continue
        rid = out.get("run_id") or (run_store.latest(desk["conversation_id"]) or {}).get("run_id")
        # The claim's timestamp is in the args, so each user-initiated Accept gets its own
        # tool_calls key. A failed promotion is retryable (§6.4) and a constant key is not: the
        # cached failure would be replayed for ever and the retry would never run.
        args = {"output_id": item.output_id, "destination": item.destination, "doc_id": item.doc_id,
                "claimed_at": claimed.get("decided_at")}

        async def _fn(o: dict[str, Any] = out, it: AcceptItem = item) -> Any:
            return await _promote(id, o, it)

        try:
            booked = await (run_store.call_once(rid, PROMOTE_STEP, "promote", args, _fn) if rid else _fn())
        except Exception as e:  # noqa: BLE001 - one bad output must not abort the rest of the batch
            log.exception("promoting %s failed", item.output_id)
            booked = {"ref": None, "verified": False, "error": str(e)}
        ref, ok = booked.get("ref"), bool(booked.get("verified"))
        err = booked.get("error")
        try:
            desks.finish_output(item.output_id, kind=item.destination, ref=ref, verified=ok)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        results.append({"output_id": item.output_id, "ok": ok, "verified": ok,
                        "kind": item.destination, "ref": ref, **({"error": err} if err else {})})
    _close_review(id)
    return {"results": results}


@app.post("/cowork/desks/{id}/reject")
async def reject_outputs(id: str, body: RejectIn) -> dict[str, Any]:
    desk = _desk_or_404(id, False)
    ids = body.output_ids
    if ids is None:
        ids = [o["id"] for o in desks.outputs(id) if o["status"] in UNDECIDED]
    for oid in ids:
        desks.reject_output(oid)
    if body.note:
        desks.event(id, "note", body.note)
    return _close_review(id) or desk


def _close_review(desk_id: str) -> dict[str, Any] | None:
    """A desk in review is done once nothing is left to decide — the one place a desk reaches a
    terminal state without the agent saying so, because the user just did."""
    desk = desks.get(desk_id, False)
    if not desk or desk["status"] != "review":
        return desk
    if any(o["status"] in UNDECIDED for o in desks.outputs(desk_id)):
        return desk
    return desks.set_status(desk_id, "done", reason="reviewed", headline="")


@app.get("/cowork/inbox")
def cowork_inbox(limit: int = 40) -> list[dict[str, Any]]:
    return desks.inbox(limit)


@app.post("/cowork/inbox/{event_id}/seen")
def cowork_inbox_seen(event_id: str) -> dict[str, bool]:
    desks.mark_seen(event_id)
    return {"ok": True}


@app.get("/cowork/plans/{plan_id}")
def get_action_plan(plan_id: str) -> dict[str, Any]:
    plan = aplans.get(plan_id)
    if not plan:
        raise HTTPException(404, "No such plan")
    return plan


@app.get("/conversations/{id}/plan")
def conversation_plan(id: str) -> dict[str, Any] | None:
    """The newest plan of this conversation, so a reloaded chat still shows its card."""
    return aplans.latest(id)


@app.post("/cowork/plans/{plan_id}")
async def decide_action_plan(plan_id: str, body: PlanDecisionIn) -> dict[str, Any]:
    """The row is the truth, so it is written first: aplans.decide, then the approvals row, then
    the in-process Future if one still exists, then the desk. A plan whose run has died still
    decides cleanly; only a missing plan row is a 404."""
    if not aplans.get(plan_id, with_steps=False):
        raise HTTPException(404, "No such plan")
    if body.decision not in ("approve", "edit", "reject"):
        raise HTTPException(400, "Bad decision")
    try:
        edits = parse_plan_edits(body.steps) if body.decision == "edit" else None
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    decided = aplans.decide(plan_id, body.decision, steps=edits, note=body.note or "")
    if decided is None:
        return {"ok": False, "plan": aplans.get(plan_id), "resumed": False, "error": "already decided"}
    answer = "deny" if body.decision == "reject" else "allow"
    call_id = decided.get("call_id")
    if call_id:
        run_store.decide(call_id, answer, by="user", note=body.note or "")
        if body.note:
            _answers[call_id] = body.note
        fut = _approvals.get(call_id)
        if fut is not None and not fut.done():
            fut.set_result(answer)
    did = decided.get("desk_id")
    if did and body.decision != "reject":
        # The in-run approval writes desks.plan_id as it flips the desk to `working`; a plan decided
        # out of band — parked, or after its run let go — has to write it too, or claim_run reads an
        # empty plan_id and wakes the desk back into `planning`, and _should_chain never chains.
        current = desks.get(did, False)
        if current and not current.get("plan_id"):
            desks.set_status(did, current["status"], reason=current["status_reason"],
                             plan_id=plan_id, event=False)
    resumed = bool(did) and _wake_desk(did) is not None
    return {"ok": True, "plan": aplans.get(plan_id), "resumed": resumed}


@app.on_event("startup")
async def _cowork_startup() -> None:
    """Recovery must never stop the backend from starting. Nothing is resumed here: every active
    run becomes `interrupted`, every in-flight tool call becomes `unknown`, every live desk lands
    in Needs you, and the user presses Resume."""
    try:
        marked = run_store.recover()   # runs -> interrupted; tool_calls 'started' -> 'unknown';
                                       # salvage transcript() into any empty message row; prune()
        woken = desks.recover()        # LIVE -> interrupted + a needs_you event each
        if marked.get("runs") or woken:
            log.info("cowork recovery: %s runs, %s unknown calls, %s salvaged, %s desks",
                     marked.get("runs"), marked.get("calls"), marked.get("salvaged"), woken)
    except Exception:  # noqa: BLE001
        log.warning("cowork recovery failed", exc_info=True)
