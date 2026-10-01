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

from . import activity, assist, llm, mac, mcp_eval, tools
from .context import build_context, estimate_tokens
from .db import SECRET_SETTINGS, Database, data_dir_from_env, new_id
from .extract_text import extract_text
from .learn import MAX_INJECTED_SKILLS, LearnJob, LearnWorker, Skills, induce_skill, skill_block
from .repos import ALL, Conversations, Documents, Graph, Memories, Projects
from .boards import Boards
from .canvas import SNAP_MODES, WIDGET_KINDS, WINDOW_STATES, Canvases
from .dashboards import Dashboards, generate_recap, generate_summary, generate_widget_code
from .docs import Docs, unified_diff
from . import cache as google_cache
from .google import Google, GoogleNotConnected, json_safe
from .jobs import (KINDS, PROPOSAL_STATUSES, Jobs, Proposals, Scheduler, local_tz_name, spent, valid_cron,
                   valid_tz)
from . import skillbuild
from .mcp_client import McpClient, McpError
from .mcp_servers import MODES as MCP_MODES, RESERVED_PREFIX as MCP_PREFIX, SCOPES as MCP_SCOPES, McpServers
from .meeting_recorder import RecorderBusy
from .meetings import MeetingBlocked, Meetings, MeetingService
from .cowork import (AUTONOMY, DESK_CONTINUE, DESK_HINT, DESK_RESUME, LIVE as DESK_LIVE,
                     STATUSES as DESK_STATUSES, UNDECIDED_OUTPUTS, DeskRuntime, Desks)
from .workspace import MAX_PREVIEW, Workspace, WorkspaceError
from .microvm import Sandboxes
from .notes import Notes
from .plans import (MUTATING, PLAN_BLOCKED, PLAN_SAFE_DANGER, PLAN_TOOL, PROPOSE_ONLY, Plans,
                    normalize_plan, parse_plan_edits, taint_expected)
from .outbox import Outbox, router as outbox_router
from .setup import router as setup_router
from .presets import CanvasPresets
from .runs import ACTIVE, PROMOTE_STEP, STATUSES, Run, RunBus, RunStore, Topic
from .style import WritingStyle, learn_style_from_exchange, looks_like_prose
from .modules import Module, ModuleContext, build_modules, get as module_get
from .modules.todos import TodosModule
from .tools import Toolbox, summarize_result
from .trace import Tracer, now_ms
from .usage import Pricing, Usage
from .working import Plans as WorkPlans, ToolResults

log = logging.getLogger("personal_os")

db = Database(data_dir_from_env())
projects = Projects(db)
convos = Conversations(db)
memories = Memories(db)
graph = Graph(db)
documents = Documents(db)
docs = Docs(db)
style = WritingStyle(db)


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
    """Safety net for the writers wsid() cannot cover (a card whose column is gone, a window whose canvas is gone, a widget whose dashboard is gone).
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
    # Chromium sends Origin: null for a page loaded via file:// (the packaged renderer) and for sandboxed widget iframes,
    # so "null" must stay allowed or the packaged app cannot reach its own backend. Auth is the token header regardless.
    "null", "file://",
    *(f"http://{h}:{p}" for h in ("localhost", "127.0.0.1") for p in range(5173, 5181))]
app.add_middleware(CORSMiddleware, allow_origins=ALLOWED_ORIGINS, allow_credentials=False,
                   allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
                   allow_headers=["Content-Type", "X-Personal-OS-Token", "Authorization"])


# Live runs, one per conversation, each owning its own task. Any number of clients may watch one.
# Each run is also a row (agent_runs) with its event tape (run_events); the bus is the hot path over it.
run_store = RunStore(db)
bus = RunBus(run_store)
# Plan-level approvals (propose_plan): one card authorises a set of calls, each bound to its argument digest.
plans = Plans(db)
# A second, independent bus, keyed by MEETING id. Nothing in RunBus is conversation-specific - _runs
# is a plain dict[str, Run] and Run.__init__ only stores the id (runs.py:46-48, 123-127) - so a
# meeting's live segments get their own stream without sharing a key space with chat.
meeting_bus = RunBus()
# Active chat streams so they can be aborted from the client. A Run when the reply is on the bus
# (stop goes through the bus, which also wakes the provider read); a bare Event for a stream with no run.
_active: dict[str, asyncio.Event | Run] = {}
# Pending tool-call approvals: call_id -> Future[decision]. The durable record is the approvals table; this is
# only how POST /approvals wakes the run that is waiting in this process.
_approvals: dict[str, asyncio.Future] = {}
# Background work that outlives the run that queued it, and the topic it reports on.
events = Topic()
learner = LearnWorker(memories=memories, graph=graph, set_trace=convos.set_trace, publish=events.publish)


ENV_SEED = {
    "baseUrl": "PERSONAL_OS_BASE_URL",
    "apiKey": "PERSONAL_OS_API_KEY",
    "defaultModel": "PERSONAL_OS_DEFAULT_MODEL",
    "extractionModel": "PERSONAL_OS_EXTRACTION_MODEL",
}


def _seed_settings_from_env() -> None:
    """First launch: take provider defaults from the environment (.env) if nothing is stored yet.

    Dev only. The packaged app (Electron sets PERSONAL_OS_PACKAGED) starts empty and onboards instead."""
    if os.environ.get("PERSONAL_OS_PACKAGED"):
        return
    stored = db.get_settings()
    patch = {k: os.environ[v] for k, v in ENV_SEED.items() if k not in stored and os.environ.get(v)}
    if patch:
        db.set_settings(patch)


_seed_settings_from_env()


def settings() -> dict[str, Any]:
    return {**llm.DEFAULT_SETTINGS, **db.get_settings()}


jobs = Jobs(db)
proposals = Proposals(db)
boards = Boards(db)
dashboards = Dashboards(db)
google = Google(settings, db.set_settings)
app.include_router(setup_router(settings, db.set_settings, lambda: google.status()["connected"]))
# sid/wsid are defined further down, so the module context looks them up late.
modules: list[Module] = build_modules(ModuleContext(
    db=db, settings=settings, set_settings=db.set_settings, google=google,
    sid=lambda p: sid(p), wsid=lambda p: wsid(p)))
_todos_module = module_get(modules, "todos", TodosModule)
todos, tasks_sync, todo_calendar = _todos_module.store, _todos_module.tasks_sync, _todos_module.calendar_mirror
for _m in modules:
    if (_r := _m.router()) is not None:
        app.include_router(_r)
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
# A desk is one conversation plus one workspace plus one approved plan. The workspace is a plain
# directory per desk under the data dir, containment-checked after symlink resolution; Desks is the
# state machine and the timeline over it.
workspace = Workspace(db.data_dir)
desks = Desks(db, workspace)
# The supervisor task per live desk: it owns the CHAIN, not the turn in flight. Cancelling one ends
# the chain and leaves the running turn to settle cooperatively.
_desk_tasks: dict[str, asyncio.Task[None]] = {}
sandboxes = Sandboxes(settings)
monitor = activity.Monitor(db, settings, llm.complete)
# Every Gmail send is held here first so it can be undone (outbox.py); its own routes are included below.
outbox = Outbox(db, google, settings)
app.include_router(outbox_router(outbox))
# Working memory that is not the chat: the per-conversation plan, the full tool-result blobs behind
# their handles (working.py), and procedural memory awaiting review (learn.Skills). `work_plans` is the
# todo_write artifact and is a different thing from `plans`, the propose_plan approval record.
work_plans = WorkPlans(db)
tool_results = ToolResults(db)
skills = Skills(db)
# The repo first, then the service around it: both routes and the 45s tick read through one
# instance, so a meeting's rows are never written by two Meetings objects at once.
meeting_store = Meetings(db)
meeting_svc = MeetingService(db, settings, llm.complete, meeting_store, google=google, todos=todos)
toolbox = Toolbox(memories, graph, documents, settings, modules=modules, google=google, boards=boards, sandboxes=sandboxes, docs=docs, activity=monitor,
                  outbox=outbox, work_plans=work_plans, results=tool_results, skills=skills, jobs=jobs,
                  style=style, meetings=meeting_svc, desks=desks, workspace=workspace)
# The insights pass proposes automations, so it is told which tools this install actually has - an
# unwired integration must not turn into a suggestion that cannot be carried out.
monitor.insights.tools_fn = lambda: [t["name"] for t in toolbox.list() if t.get("available")]
mcp_store = McpServers(db)
# Third-party servers are supervised, not owned by the chat loop: a wedged server must not be able
# to hold a reply, so everything it offers goes through McpClient's bounded calls.
mcp = McpClient(mcp_store)


def mcp_is(name: str) -> bool:
    """Is this tool slug a third-party MCP tool? The prefix is reserved, so the check is exact."""
    return name.startswith(MCP_PREFIX)


def _mcp_tooling(project_id: str | None, conversation_id: str | None) -> tuple[dict[str, str], list[dict[str, Any]]]:
    """Modes + tool schemas for MCP tools whose server is connected right now.

    A tool whose server is down is left out rather than offered and then failed: the model should
    not spend a round discovering that a connector is offline. `effective_mode` has already decayed
    an 'on' back to 'ask' for a tool whose shape changed since it was approved.
    """
    ready = set(mcp.ready_slugs())
    if not ready:
        return {}, []
    names = {s["id"]: s["name"] for s in mcp_store.servers()}
    modes: dict[str, str] = {}
    schemas: list[dict[str, Any]] = []
    for tool in mcp_store.tools():
        slug = tool["slug"]
        if slug not in ready:
            continue
        mode = mcp_store.effective_mode(slug, project_id, conversation_id)["mode"]
        if mode == "off":
            continue
        modes[slug] = mode
        desc = (tool["description"] or tool["name"]).strip()
        # Provenance goes in the description: the model cannot otherwise tell a third-party tool from
        # a built-in one, and it should weigh what the tool says about itself accordingly.
        schemas.append({"type": "function", "function": {
            "name": slug,
            "description": f"[{names.get(tool['server_id'], 'MCP')} — third-party MCP connector] {desc}",
            "parameters": tool["parameters"] or {"type": "object", "properties": {}},
        }})
    return modes, schemas


def _gate(name: str, mode: str, ctx: dict[str, Any]) -> str:
    """Effective mode for one call. Untrusted content in the run forces every external tool to ask.

    Every MCP tool is `external` by construction (mcp_client.MCP_DANGER), so the taint rule the
    Toolbox applies to built-ins has to apply to them too - they are not in Toolbox.specs.
    """
    if mcp_is(name):
        return "ask" if mode == "on" and ctx.get("tainted") else mode
    return toolbox.gate(name, mode, ctx)


async def _mcp_call(slug: str, args: dict[str, Any]) -> dict[str, Any]:
    """One MCP tool call, with its failures turned into results the model can read and retry past."""
    try:
        out = await mcp.call(slug, args)
    except McpError as e:
        return tools.tool_error(f"{slug}: {e}", alternative="tell the user the connector is unavailable")
    except Exception as e:  # noqa: BLE001 - a third-party server must not be able to break a reply
        log.warning("MCP tool %s failed", slug, exc_info=True)
        return tools.tool_error(f"{slug}: {type(e).__name__}: {str(e).splitlines()[0][:200]}")
    if out.get("is_error"):
        return tools.tool_error(f"{slug}: {out.get('error') or 'the tool reported an error'}")
    return {k: v for k, v in out.items() if k != "is_error"}


def _mcp_server_view(row: dict[str, Any]) -> dict[str, Any]:
    """One server as the UI wants it: stored config, live supervisor state, its tools, its last report."""
    live = (mcp.status(row["id"]) or [{}])[0]
    return {**row,
            "live": {"status": live.get("status", row["status"]), "detail": live.get("detail", row["status_detail"]),
                     "running": bool(live.get("running")), "ready": bool(live.get("ready")),
                     "attempts": live.get("attempts", 0), "server_info": live.get("server_info") or {}},
            "tools": [{**t, "effective": mcp_store.effective_mode(t["slug"])}
                      for t in mcp_store.tools(row["id"], include_missing=True)],
            "eval": mcp_store.latest_eval(row["id"])}


def fscope(raw: str | None) -> str:
    """Normalise a Files-tree scope: '' is the personal tree, anything else a project id.

    Unlike `sid` there is no "every scope" here — a folder lives in exactly one tree, so 'all'
    arriving from a stale caller is read as personal rather than as a tree called '__all__'.
    """
    s = sid(raw)
    return "" if s in (None, ALL) else str(s)


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
SETTINGS_READ_ONLY = {"activity", "googleTasksSync", "googleTodoCalendar", "meetings"}


def public_settings() -> dict[str, Any]:
    """What the renderer may see: secret values are blanked and reported as <key>Set booleans instead."""
    out = {k: v for k, v in settings().items() if k not in PRIVATE_SETTINGS}
    for k in SECRET_SETTINGS:
        out[f"{k}Set"] = bool(out.get(k))
        out[k] = ""
    return out


@app.get("/settings")
def get_settings() -> dict[str, Any]:
    return public_settings()


# Numeric settings the Budget reads. A clamp keeps a cleared or mistyped field from becoming "unlimited"
# (0) or from wedging every reply (a string the int() in Budget cannot parse).
NUMERIC_SETTING_RANGES: dict[str, tuple[float, float]] = {
    "maxToolRounds": (1, 60),
    "maxRunTokens": (0, 10_000_000),
    "maxRunSeconds": (0, 86_400),
    "maxRunCost": (0, 1_000),
}


def _check_numeric_setting(key: str, value: Any) -> int | float:
    """A number settings key must stay a finite number of its default's kind, within its range."""
    default = llm.DEFAULT_SETTINGS[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise HTTPException(422, f"{key} must be a number")
    lo, hi = NUMERIC_SETTING_RANGES.get(key, (0, math.inf))
    if not lo <= value <= hi:
        raise HTTPException(422, f"{key} must be between {lo:g} and {hi:g}")
    return int(value) if isinstance(default, int) else float(value)


@app.put("/settings")
def put_settings(patch: dict[str, Any]) -> dict[str, Any]:
    clean = {k: v for k, v in patch.items()
             if k in llm.DEFAULT_SETTINGS and k not in PRIVATE_SETTINGS and k not in SETTINGS_READ_ONLY}
    for k, v in clean.items():
        d = llm.DEFAULT_SETTINGS[k]
        if isinstance(d, (int, float)) and not isinstance(d, bool):
            clean[k] = _check_numeric_setting(k, v)
    for k in SECRET_SETTINGS:
        if k in clean and clean[k] == "":  # blank means "unchanged" (the form never holds the saved key); null clears
            del clean[k]
        elif k in clean and clean[k] is not None and not isinstance(clean[k], str):
            raise HTTPException(422, f"{k} must be a string or null")
    db.set_settings(clean)
    return public_settings()


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


class PageBridgeIn(BaseModel):
    url: str
    token: str


@app.post("/bridge/page")
def register_page_bridge(body: PageBridgeIn) -> dict[str, Any]:
    """The Electron main process says where its offscreen page loader listens (open_page). Re-sent periodically."""
    try:
        mac.page_bridge.register(body.url, body.token)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"ok": True}


# ---------------- MCP connectors ----------------
# Third-party servers the user has added. Everything here is user-driven: a server is never
# auto-added, never auto-enabled, and its tools never default to running unasked.
class McpServerIn(BaseModel):
    name: str
    transport: str = "stdio"
    command: str = ""
    args: list[str] = Field(default_factory=list)
    cwd: str = ""
    env: dict[str, str] = Field(default_factory=dict)
    secrets: dict[str, str] = Field(default_factory=dict)
    url: str = ""
    headers: dict[str, str] = Field(default_factory=dict)
    description: str = ""
    enabled: bool = True


class McpServerPatch(BaseModel):
    name: str | None = None
    transport: str | None = None
    command: str | None = None
    args: list[str] | None = None
    cwd: str | None = None
    env: dict[str, str] | None = None
    secrets: dict[str, str] | None = None     # '' for a key means "leave the stored value alone"
    clear_secrets: list[str] = Field(default_factory=list)
    url: str | None = None
    headers: dict[str, str] | None = None
    description: str | None = None
    enabled: bool | None = None


class McpProbeIn(BaseModel):
    """A launch config that may never have been saved, so it can be checked before it is trusted."""
    transport: str = "stdio"
    command: str = ""
    args: list[str] = Field(default_factory=list)
    cwd: str = ""
    env: dict[str, str] = Field(default_factory=dict)
    secrets: dict[str, str] = Field(default_factory=dict)


class McpGrantIn(BaseModel):
    mode: str
    scope: str = "global"
    scope_id: str | None = None


@app.get("/mcp/servers")
def mcp_servers() -> list[dict[str, Any]]:
    """Every configured server with its live status. Secrets are returned as key names only."""
    return [_mcp_server_view(s) for s in mcp_store.servers()]


@app.post("/mcp/servers")
async def mcp_create_server(body: McpServerIn) -> dict[str, Any]:
    if body.transport not in ("stdio", "sse", "http"):
        raise HTTPException(400, "transport must be stdio, sse or http")
    row = mcp_store.create_server(name=body.name, transport=body.transport, command=body.command, args=body.args,
                                 env=body.env, secrets=body.secrets, cwd=body.cwd, url=body.url, headers=body.headers,
                                 description=body.description, enabled=body.enabled)
    await mcp.sync()
    return _mcp_server_view(mcp_store.server(row["id"]) or row)


@app.patch("/mcp/servers/{id}")
async def mcp_update_server(id: str, body: McpServerPatch) -> dict[str, Any]:
    if mcp_store.server(id) is None:
        raise HTTPException(404, "No such MCP server")
    row = mcp_store.update_server(id, body.model_dump(exclude_none=True))
    await mcp.sync()  # a changed launch config restarts the supervisor; an unchanged one is left alone
    return _mcp_server_view(mcp_store.server(id) or row or {})


@app.delete("/mcp/servers/{id}")
async def mcp_delete_server(id: str) -> dict[str, bool]:
    mcp_store.delete_server(id)
    await mcp.sync()  # stops and reaps the child process; grants survive, keyed by slug
    return {"ok": True}


@app.post("/mcp/servers/{id}/restart")
async def mcp_restart_server(id: str) -> dict[str, Any]:
    if mcp_store.server(id) is None:
        raise HTTPException(404, "No such MCP server")
    await mcp.restart(id)
    return _mcp_server_view(mcp_store.server(id) or {})


@app.get("/mcp/servers/{id}/logs")
def mcp_server_logs(id: str) -> dict[str, Any]:
    """The server's recent stderr. The only window into a connector that will not start."""
    if mcp_store.server(id) is None:
        raise HTTPException(404, "No such MCP server")
    return {"server_id": id, "stderr": mcp.stderr(id)}


@app.post("/mcp/servers/{id}/check")
async def mcp_check_server(id: str) -> dict[str, Any]:
    """Probe + static evaluation of a saved server, filed as its latest report."""
    if mcp_store.server(id) is None:
        raise HTTPException(404, "No such MCP server")
    return await mcp_eval.evaluate_server(mcp, mcp_store, id)


@app.post("/mcp/check")
async def mcp_check_config(body: McpProbeIn) -> dict[str, Any]:
    """Evaluate a config that has not been saved: the decision to trust comes before the decision to use."""
    cfg = body.model_dump()
    # Same precedence as McpServers.launch_env: a secret wins over a plain env var of the same name.
    cfg["env"] = {**(cfg.get("env") or {}), **cfg.pop("secrets", {})}
    return await mcp_eval.evaluate_config(mcp, cfg)


@app.get("/mcp/tools")
def mcp_tools(project_id: str | None = None, conversation_id: str | None = None) -> dict[str, Any]:
    """Known MCP tools with the mode each resolves to, and which are live right now."""
    ready = set(mcp.ready_slugs())
    rows = mcp_store.tools(include_missing=True)
    return {"tools": [{**t, "ready": t["slug"] in ready,
                       "effective": mcp_store.effective_mode(t["slug"], sid(project_id), conversation_id)}
                      for t in rows],
            "grants": mcp_store.grants()}


@app.put("/mcp/tools/{slug}/grant")
def mcp_set_grant(slug: str, body: McpGrantIn) -> dict[str, Any]:
    if body.mode not in MCP_MODES:
        raise HTTPException(400, f"mode must be one of {', '.join(MCP_MODES)}")
    if body.scope not in MCP_SCOPES:
        raise HTTPException(400, f"scope must be one of {', '.join(MCP_SCOPES)}")
    if mcp_store.tool(slug) is None:
        raise HTTPException(404, "No such MCP tool")
    mcp_store.set_grant(slug, body.mode, body.scope, body.scope_id)
    return mcp_store.effective_mode(slug, body.scope_id if body.scope == "project" else None,
                                    body.scope_id if body.scope == "chat" else None)


@app.delete("/mcp/tools/{slug}/grant")
def mcp_clear_grant(slug: str, scope: str = "global", scope_id: str | None = None) -> dict[str, Any]:
    mcp_store.clear_grant(slug, scope, scope_id)
    return mcp_store.effective_mode(slug)


@app.on_event("startup")
async def _mcp_startup() -> None:
    """Connect whatever is enabled. A server that will not start becomes a status, not a failed boot."""
    try:
        await mcp.start()
    except Exception:  # noqa: BLE001 - a broken connector must never stop the app from coming up
        log.warning("MCP startup failed", exc_info=True)


@app.on_event("shutdown")
async def _mcp_shutdown() -> None:
    # Before anything else tears down: this is what guarantees no child server outlives the app.
    with contextlib.suppress(Exception):
        await mcp.stop()


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
    # Its docs survive, demoted to personal; the folder rows for a tree that no longer exists do not.
    docs.forget_scope(id)
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
def list_conversations(project_id: str | None = None, include_jobs: bool = False,
                       include_desks: bool = False) -> list[dict[str, Any]]:
    """A scheduled job's own transcripts are left out unless asked for: the Agent Inbox is their
    index, and Cowork is a desk's. Both stay reachable through their own flag."""
    return convos.list(sid(project_id), include_jobs, include_desks)


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


class PageContextIn(BaseModel):
    """What the user had on screen when they asked, sent by the page agent (⌘I). See context.page_block."""
    view: str = ""
    label: str = ""
    detail: str | None = None
    selection: str | None = None
    refs: list[dict[str, Any]] = []
    hints: list[str] = []


class ChatIn(BaseModel):
    content: str | None = None  # None = regenerate from existing history
    model: str | None = None
    page_context: PageContextIn | None = None


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
# Only added when todo_write is actually available in this chat (see _chat_stream).
PLAN_HINT = ("When a request needs more than a couple of tool calls, open with todo_write to lay out the steps, then update it "
             "as each one lands. Your current plan is re-sent to you at the end of every round, so it — not your memory of "
             "earlier rounds — is what keeps a long task on track.")


BUDGET_STOP = ("Out of budget ({axis}): this tool call was not executed and no further tool calls will run. "
               "Write the best final answer you can from what you already have, and say in one line what is still missing.")
SOFT_NUDGE = ("Budget check: about {pct}% of this reply's budget is used. "
              "Make at most one or two more tool calls, then write the final answer.")
LOOP_STOP = ("{name} has been called with identical arguments {n} times in a row, so this reply is stopping tool use. "
             "Answer with what you already have, and say in one line what you could not finish.")
REPEAT_LIMIT = 5
TOOL_ERROR_LIMIT = 3


# Run kinds that may not complete an outward-facing side effect. A scheduled job proposes; the user executes.
PROPOSAL_ONLY_KINDS = ("job",)
# Caps for an unattended run, applied on top of the user's settings and only downward (see _caps). Tighter than
# interactive on purpose: nobody is watching, and a longer leash makes the answer worse, not better.
JOB_BUDGET = {"maxToolRounds": 8, "maxRunTokens": 60_000, "maxRunSeconds": 240, "maxRunCost": 0.20}
JOB_HINT = ("## This is a scheduled background run\nNobody is watching it. Anything that reaches outside this app "
            "(sending or drafting mail, calendar writes, Google Docs/Sheets/Tasks) cannot be executed here: such a "
            "call is recorded as a proposal for the user to accept, edit or reject, and that is enforced outside your "
            "control. So propose freely, do not retry a refused call, and write a short report of what you found and "
            "what you proposed. Reading, searching, todos, notes and memory work normally. You also cannot "
            "schedule further runs from in here: that too becomes a proposal.")


_DESK_CAPS = ("deskMaxTurns", "deskMaxCost", "deskMaxLive")


def _desk_caps(cfg: dict[str, Any], override: dict[str, Any] | None) -> dict[str, Any]:
    """A desk's budget: the user's settings, which a desk may tighten and never loosen.

    Not to be confused with `_caps` below, which bounds one reply of an unattended run. These bound
    the whole desk, across turns. 0 = unlimited, so it loses to any positive limit.
    """
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


def _caps(cfg: dict[str, Any], caps: dict[str, Any]) -> dict[str, Any]:
    """`cfg` with each cap applied downward: a stricter user setting wins, and 0 (unlimited) loses to the cap."""
    return {**cfg, **{k: (cap if not (cur := _num(cfg, k)) else min(cur, cap)) for k, cap in caps.items()}}


def proposal_only(run: Run | None) -> bool:
    return run is not None and run.kind in PROPOSAL_ONLY_KINDS


def _num(cfg: dict[str, Any], key: str) -> float:
    """`cfg[key]` as a non-negative finite number; anything else is the shipped default (0 for job caps)."""
    v = cfg.get(key)
    try:
        f = float(v) if v is not None and not isinstance(v, bool) else math.nan
    except (TypeError, ValueError):
        f = math.nan
    if not math.isfinite(f) or f < 0:
        return float(llm.DEFAULT_SETTINGS.get(key) or 0)
    return f


class Budget:
    """Rounds / tokens / wall-clock / USD for one reply. 0 on any axis means unlimited; approval waits do not count."""

    def __init__(self, cfg: dict[str, Any]):
        # A junk value already stored (from before PUT /settings validated) falls back to the default
        # instead of raising on every reply.
        self.max_rounds = int(_num(cfg, "maxToolRounds"))
        self.max_tokens = int(_num(cfg, "maxRunTokens"))
        self.max_seconds = _num(cfg, "maxRunSeconds")
        self.max_cost = _num(cfg, "maxRunCost")
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

    def snapshot(self) -> dict[str, Any]:
        """What agent_runs.budget stores: the limits and how much of each the run has used."""
        return {"max_rounds": self.max_rounds, "max_tokens": self.max_tokens, "max_seconds": self.max_seconds,
                "max_cost": self.max_cost, "rounds": self.rounds, "tokens": self.tokens, "cost": round(self.cost, 6),
                "seconds": round(self.elapsed(), 3), "paused_seconds": round(self.paused, 3)}


# Tools whose call changes something outside the reply: each runs at most once per (run, round, tool, args).
IDEMPOTENT_DANGER = ("writes", "external")


def _propose(run: Run, name: str, args: dict[str, Any], call_id: str, ctx: dict[str, Any]) -> dict[str, Any]:
    """Record an outward-facing call a background run made, instead of making it. The row is the whole effect."""
    p = proposals.create(run_id=run.run_id, tool=name, args=args, job_id=run.input.get("job_id"),
                         conversation_id=run.conversation_id, message_id=ctx.get("message_id"), call_id=call_id)
    log.info("run %s proposed %s (proposal %s)", run.run_id, name, p["id"])
    return {"proposed": True, "proposal_id": p["id"], "tool": name, "status": "pending",
            "note": f"{name} was NOT executed. This is a background run, so it was recorded as a proposal in the "
                    "user's Agent Inbox; they accept, edit or reject it there, and accepting is what runs it. "
                    "Do not call it again — say in your report what you proposed."}


async def _call_tool(run: Run | None, step: int, name: str, args: dict[str, Any], ctx: dict[str, Any], call_id: str) -> Any:
    """toolbox.call, through the executed_calls journal for side-effecting tools, so a retry or a replay of the same
    step returns the recorded result instead of sending the email twice. Read-only tools run directly.

    This is also where proposal-only runs are stopped: a job's outward-facing call becomes a proposals row and never
    reaches toolbox.call at all. Enforced here, on the one path every tool call takes, and again in Toolbox.call."""
    spec = toolbox.specs.get(name)
    if proposal_only(run) and toolbox.proposes(name):
        return _propose(run, name, args, call_id, ctx)  # type: ignore[arg-type]
    if run is None or run.store is None or spec is None or spec.danger not in IDEMPOTENT_DANGER:
        return await toolbox.call(name, args, ctx)
    result, replayed = await run.store.call_once(run.run_id, step, name, args, lambda: toolbox.call(name, args, ctx), call_id=call_id)
    if replayed:
        if isinstance(result, dict):
            result = {**result, "replayed": True}
        if spec.taints and not (isinstance(result, dict) and result.get("error")):
            ctx["tainted"] = True
    return result


def _urls(text: str) -> set[str]:
    """URLs the user typed this turn: still fetchable, whole, once the reply has read untrusted content."""
    return {u for u in (m.rstrip(".,;:!?") for m in re.findall(r"https?://[^\s<>\"')]+", text or "")) if u}


def _short(args: dict[str, Any], limit: int = 300) -> dict[str, Any]:
    """Tool arguments for the trace: long strings (code, content) truncated."""
    return {k: (v[:limit] + "…" if isinstance(v, str) and len(v) > limit else v) for k, v in args.items()}


def _title_from(text: str) -> str:
    t = " ".join(text.split())
    return (t[:48].rstrip() + "…") if len(t) > 48 else (t or "New chat")


def _bind_stop(message_id: str, stop: asyncio.Event, run: Run | None) -> None:
    _active[message_id] = run if run is not None else stop


def _stream_cancel(stop: asyncio.Event, steers: list[dict[str, Any]] | None, run: Run | None) -> asyncio.Event:
    """The event stream_chat closes its socket on. Cleared first, so only a stop or steer during this call fires it.

    A poke that arrives between the read of wake_gen and clear() is put back: wake_gen moved, so the flag is set again.
    """
    if run is None:
        return stop
    seen = run.wake_gen
    run.wake.clear()
    if run.wake_gen != seen or stop.is_set() or steers:
        run.wake.set()
    return run.wake


async def _chat_stream(conv_id: str, body: ChatIn, stop: asyncio.Event, steers: list[dict[str, Any]] | None = None,
                       run: Run | None = None) -> AsyncIterator[tuple[str, Any]]:
    """Yield (event, payload) pairs. The run bus formats them and fans them out; see runs.sse.
    `run` (when there is one) gets the durable side: approval rows, run status, budget snapshots, the idempotency journal."""
    conv = convos.get(conv_id)
    if not conv:
        yield "error", {"message": "Conversation not found"}
        return
    cfg = settings()
    model = body.model or conv["model"] or cfg["defaultModel"]
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
        activity=monitor, skills=skills, style=style, meetings=meeting_svc,
        page=body.page_context.model_dump() if body.page_context else None,
    )
    tracer.end(cspan, {"memories": len(used["memories"]), "entities": len(used["nodes"]), "excerpts": len(used["chunks"]),
                       "history_messages": len(history)})

    am = convos.add_message(conv_id, "assistant", "", model=model)

    _bind_stop(am["id"], stop, run)
    buf: list[str] = []
    # Chain-of-thought from reasoning models. Kept out of `buf` so it never becomes the reply, and
    # never goes back to the model: history() reads content only.
    rbuf: list[str] = []
    error: str | None = None
    tool_events: list[dict[str, Any]] = []
    # A meeting title is copied verbatim off a calendar invite by `adopt`, and the 45s nudge does
    # that for any invite anyone can send the user - so the meetings block carries text an outsider
    # chose, straight into the system prompt. Taint the turn when it is present: otherwise an
    # external tool pinned to 'on' by a standing grant would run with no approval card in a chat
    # that never called a meeting tool. The tool-shaped door is already gated by `taints` on the
    # meeting_* specs; this is the context-shaped one beside it.
    ctx_taints = [k for k in ("meetings",) if used.get(k)]
    tool_ctx: dict[str, Any] = {
        "project_id": conv["project_id"], "conversation_id": conv_id,
        # Taint is sticky for the whole conversation: the injected instructions live on in the replayed history, so
        # waiting one turn must not re-arm a standing 'always' grant. Only the user clears it (Context -> this chat).
        "tainted": bool(conv["settings"].get("tainted")) or bool(ctx_taints),
        "taint_sources": list(conv["settings"].get("taint_sources") or []) + [f"context:{k}" for k in ctx_taints],
        "allowed_urls": _urls(user_text), "settings": cfg,
        # Set for a scheduled job: Toolbox.call refuses every outward-facing tool outright, and _call_tool has
        # already turned the call into a proposals row before it got that far.
        "proposal_only": proposal_only(run), "message_id": am["id"],
    }
    use_tools = conv["settings"].get("useTools", True)
    modes = toolbox.effective(cfg.get("tools") or {}, (project or {}).get("tools"), conv["settings"].get("tools")) if use_tools else {}
    # MCP slugs all carry a reserved prefix no built-in may use, so the two mode maps cannot collide.
    mcp_modes, mcp_schemas = _mcp_tooling(conv["project_id"], conv_id) if use_tools else ({}, [])
    modes.update(mcp_modes)
    # ---- the desk this reply belongs to, if any, read once.
    # `autonomy` is read off the desk row rather than off conv["settings"], so changing a desk's
    # autonomy takes effect on its next turn without having to rewrite the conversation.
    desk_id = (run.desk_id if run else None) or conv["settings"].get("deskId")
    desk = desks.get(desk_id, with_outputs=False) if desk_id else None
    autonomy = str((desk or {}).get("autonomy") or "")
    # The plan already approved for this desk, with its steps: what a later turn binds against. A
    # chat has none - its plan, if any, is proposed and bound inside one reply.
    active_plan = plans.for_desk(desk_id) if desk_id else None
    # True while a plan is being drafted and nothing consequential may run. 'plan' autonomy starts a
    # desk here; an approved plan ends it, which is why `active_plan` turns it off.
    planning = bool(desk) and autonomy == "plan" and active_plan is None
    # The desk tools derive their workspace root from this and never take one as an argument, so
    # desk A cannot address desk B's files.
    tool_ctx["desk_id"] = desk_id

    def _schemas(withheld: bool = False) -> list[dict[str, Any]]:
        """One function, because the always_chat/always_global grant path recomputes the schemas; a
        filter applied at only one of the two sites lets a granted write tool reappear mid-plan.
        `modes` is mutated in place by that grant path, so this filters a copy and reads it fresh
        each time rather than rebuilding from a stale snapshot - otherwise the user's 'Always' click
        is silently discarded on the next recompute.
        """
        if not modes:
            return mcp_schemas
        m = dict(modes)
        # A desk's workspace tools exist only inside a desk: elsewhere there is no root to resolve.
        if not desk_id:
            m = {n: v for n, v in m.items() if toolbox.specs[n].group != "desk"}
        if planning and not withheld:
            # While a plan is being drafted the model is offered reading and the plan tool, nothing
            # else. Withholding them is kinder than denying them: a tool that is not offered costs no
            # round, where one that is offered and refused costs one every time. `withheld=True` asks
            # for the full set anyway, which is what a plan has to be judged against: its steps name
            # the tools it will use *after* approval.
            m = {n: v for n, v in m.items() if n == PLAN_TOOL or toolbox.specs[n].danger in PLAN_SAFE_DANGER}
            m[PLAN_TOOL] = "ask"
        return toolbox.schemas(m) + mcp_schemas

    tool_schemas = _schemas()
    tools_hint = (TOOLS_HINT + ("\n" + PLAN_HINT if any(s["function"]["name"] == "todo_write" for s in tool_schemas) else "")) if tool_schemas else ""
    system = "\n\n".join(p for p in (system, RENDER_HINT, tools_hint,
                                     JOB_HINT if proposal_only(run) else "") if p)
    used["system_prompt"] = system
    used["tokens_estimate"] = estimate_tokens(system)
    messages = [{"role": "system", "content": system}] + history
    yield "assistant_message", {**am, "context_used": used}
    yield "span", {"message_id": am["id"], "span": cspan}

    budget = Budget(_caps(cfg, JOB_BUDGET) if proposal_only(run) else cfg)
    partial: str | None = None
    last_sig: str | None = None
    repeats = 0
    tool_errors: dict[str, int] = {}
    blocked: set[str] = set()
    _round = 0
    awaiting: dict[str, Any] | None = None  # the tool event of a call blocked on approval, for a cancelled run to keep
    # The call this reply let go of rather than keep waiting on. Set once, and the reply ends there.
    parked: str | None = None
    # Whether this chat has any plan at all. One query per reply, so the plan lookups below stay off the hot path
    # of a chat that never proposed one.
    plan_seen = plans.any_in(conv_id)
    # The plan artifact (working.py) rides at the very end of the context, re-sent before every model
    # call rather than remembered: after a round of long tool results the task itself is the first
    # thing to fall out of attention. One slot, moved, never accumulated.
    plan_msg: dict[str, Any] | None = None

    def _reinject_plan() -> None:
        nonlocal plan_msg
        if plan_msg is not None:
            messages[:] = [m for m in messages if m is not plan_msg]
            plan_msg = None
        block = work_plans.block(conv_id)
        if block:
            plan_msg = {"role": "system", "content": block}
            messages.append(plan_msg)

    async def _final_round() -> AsyncIterator[tuple[str, Any]]:
        """Closing answer after a budget or breaker stop: one tool-free call, itself exempt from the budget."""
        _reinject_plan()
        # One newline, not a blank line: the transcript renders as markdown, where a blank line opens a
        # new paragraph and reads as an empty line dropped into the middle of the reply.
        if buf and buf[-1] and not buf[-1].endswith("\n"):
            buf.append("\n")
            yield "delta", {"id": am["id"], "text": "\n"}
        span = tracer.start("llm", model, {"round": _round, "final": True, "messages": len(messages), "tools": len(tool_schemas)})
        yield "span", {"message_id": am["id"], "span": span}
        start, fin = len(buf), {}
        # tools are still declared, with tool_choice "none": the history holds tool_calls, and some OpenAI-compatible
        # backends reject that when no tool list is sent. "none" is the portable way to say "answer, do not call".
        async for ev in llm.stream_chat(cfg, model, messages, tool_schemas or None,
                                        effort=str(conv["settings"].get("effort") or "default"), tool_choice="none",
                                        fast=bool(conv["settings"].get("fast")),
                                        cancel=_stream_cancel(stop, steers, run)):
            if stop.is_set():
                break
            if ev["type"] == "reasoning":
                rbuf.append(ev["text"])
                yield "reasoning", {"id": am["id"], "text": ev["text"]}
            elif ev["type"] == "delta":
                buf.append(ev["text"])
                yield "delta", {"id": am["id"], "text": ev["text"]}
            else:
                fin = ev
            if steers:
                break
        tracer.end(span, {"finish_reason": fin.get("finish_reason"), "usage": fin.get("usage"),
                          "output_chars": len("".join(buf[start:]))},
                   error="Stopped by user" if stop.is_set() else None)
        yield "span", {"message_id": am["id"], "span": span}

    try:
        while True:
            if stop.is_set():
                break
            # Steered messages fold in here. A steer also cancels the provider read, so this is reached
            # as soon as the message arrives, not after the model finishes the segment it was writing.
            # Tool results are already appended, so the user turn lands after them and the ordering stays
            # legal. The current reply segment closes with its own `done`, and a fresh assistant message answers.
            if steers:
                steered, steers[:] = list(steers), []
                # A segment that already streamed or ran tools closes cleanly; an untouched one is reused.
                if buf or tool_events or rbuf:
                    reasoning = "".join(rbuf).strip() or None
                    convos.finish_message(am["id"], "".join(buf).strip(), None, used, tool_events, tracer.spans, reasoning)
                    _active.pop(am["id"], None)
                    # A steer closes the current segment and the reply carries on in a fresh assistant
                    # message, so this `done` ends a segment, not the run. Anything supervising the run
                    # (a desk turn) must not read it as the end of the turn and charge for it.
                    yield "done", {"id": am["id"], "error": None, "context_used": used, "tool_events": tool_events,
                                   "trace": tracer.spans, "stopped": False, "partial": partial, "segment": True,
                                   "tainted": tool_ctx["tainted"], "taint_sources": tool_ctx["taint_sources"],
                                   "reasoning": reasoning}
                    am = convos.add_message(conv_id, "assistant", "", model=model)
                    _bind_stop(am["id"], stop, run)
                    tool_ctx["message_id"] = am["id"]
                    buf = []
                    rbuf = []
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
            _reinject_plan()  # last message in the context, after the previous round's tool results
            lspan = tracer.start("llm", model, {"round": _round, "messages": len(messages), "tools": len(tool_schemas)})
            yield "span", {"message_id": am["id"], "span": lspan}
            first_token: int | None = None
            async for ev in llm.stream_chat(cfg, model, messages, tool_schemas or None,
                                            effort=str(conv["settings"].get("effort") or "default"),
                                            fast=bool(conv["settings"].get("fast")),
                                            cancel=_stream_cancel(stop, steers, run)):
                if stop.is_set():
                    break
                if ev["type"] == "reasoning":
                    if first_token is None:
                        first_token = now_ms()
                    rbuf.append(ev["text"])
                    yield "reasoning", {"id": am["id"], "text": ev["text"]}
                elif ev["type"] == "delta":
                    if first_token is None:
                        first_token = now_ms()
                    buf.append(ev["text"])
                    yield "delta", {"id": am["id"], "text": ev["text"]}
                else:
                    end = ev
                if steers:
                    break
            calls = [] if end.get("finish_reason") == "cancelled" else (end.get("tool_calls") or [])
            u = end.get("usage") or end.get("usage_est") or {}
            pt, ct = int(u.get("prompt_tokens") or 0), int(u.get("completion_tokens") or 0)
            budget.add(pt, ct, pricing.cost(cfg, model, pt, ct))
            if run is not None:
                run.budget = budget.snapshot()
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
                if steers and not stop.is_set():
                    continue
                break
            # execute tool calls, then continue the loop with their results
            messages.append(turn)
            if buf and buf[-1] and not buf[-1].endswith("\n"):
                buf.append("\n")
                yield "delta", {"id": am["id"], "text": "\n"}
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
                spec = toolbox.specs.get(c["name"])
                danger = spec.danger if spec else "safe"
                mode = _gate(c["name"], raw_mode, tool_ctx)
                forced = mode != raw_mode  # untrusted content in this reply upgraded on -> ask
                blocked_reason: str | None = None
                # ---- plan mode, in priority order. Each rule can only ever make a call ask or stop;
                # none of them can turn a card off, so this is a narrowing of the gate above.
                if c["name"] != PLAN_TOOL and raw_mode != "off":
                    if planning and danger not in PLAN_SAFE_DANGER:
                        # Nothing consequential runs while a plan is being drafted. `forced` rides out
                        # on the tool_call event so the UI can say "blocked while planning" rather than
                        # "turned off".
                        mode, forced, blocked_reason = "off", True, PLAN_BLOCKED
                    elif autonomy == "propose" and danger == "external":
                        # A propose-only desk may plan an external action, never perform one.
                        mode, forced, blocked_reason = "off", True, PROPOSE_ONLY
                    elif (danger == "external" and tool_ctx["tainted"]
                          and not taint_expected(active_plan, tool_ctx["taint_sources"])):
                        # Taint the approved plan did not predict voids the pre-approval: ask, and
                        # consume nothing. Checked before any claim, because a step burnt on a call the
                        # user then denies can never be reclaimed.
                        mode, forced = "ask", True
                    elif autonomy == "ask" and danger in MUTATING:
                        # 'Ask as it goes': a desk that does not plan first cards every change instead,
                        # one at a time. Forced, so the card cannot buy a standing grant that would
                        # quietly switch the mode back off.
                        mode, forced = "ask", True
                # A background run never waits on an approval: there is nobody at the keyboard, and the call is not
                # going to happen either way. It becomes a proposal in _call_tool and the run carries on.
                proposing = proposal_only(run) and toolbox.proposes(c["name"]) and mode != "off"
                if proposing:
                    mode, forced = "on", False
                # The provider's call id is only unique within one request -- llm.stream_chat falls back
                # to "call_<idx>" when the provider omits one -- so two conversations streaming at once
                # both produce "call_0". Key anything cross-conversation by the message id too, or one
                # chat's approval resolves another chat's call. The model still sees c["id"].
                uid = f"{am['id']}:{c['id']}"
                plan: dict[str, Any] | None = None     # this call's own proposed plan, when it is propose_plan
                claimed: dict[str, Any] | None = None  # the approved plan step this call consumed instead of asking
                pre: Any = None                        # a result settled before the gate: nothing to approve
                if c["name"] == PLAN_TOOL and mode != "off":
                    # A plan is nothing but its card, so it asks whatever the mode says, and no standing grant
                    # below can turn that off. It is not a taint upgrade either, so it is not `forced`.
                    mode, forced = "ask", False
                    # A plan the user cannot act on never reaches them. A tool missing from this reply's schemas
                    # (an integration that is not connected) is as unusable as one that is off.
                    # Judged against every tool this reply could use once the plan is approved, not
                    # against the reduced planning-phase set: a plan whose steps are exactly the tools
                    # being withheld while it is drafted is the normal case, not an unusable plan.
                    offered = {s["function"]["name"] for s in _schemas(withheld=True)}
                    args, pre = normalize_plan(args, {n: (m if n in offered else "off") for n, m in modes.items()},
                                               specs={n: sp.danger for n, sp in toolbox.specs.items()},
                                               taints={n for n in modes if toolbox.taints(n) or mcp_is(n)})
                    if pre is None:
                        plan = plans.open(uid, args, run_id=run.run_id if run else None, conversation_id=conv_id,
                                          message_id=am["id"], tainted=bool(tool_ctx["tainted"]),
                                          desk_id=run.desk_id if run else None)
                        plan_seen = True
                        # Its own event, because the card needs the whole plan with its steps and
                        # their digests; the tool_call event carries only the raw arguments.
                        yield "plan_card", {"message_id": am["id"], "call_id": uid, "plan": plan}
                elif (plan_seen or active_plan) and c["name"] != PLAN_TOOL:
                    # Digest binding: an approved step whose arguments hash to the same thing stands in for the
                    # modal, exactly once. A forced approval never consults a plan -- untrusted content in this
                    # reply must always reach the user -- and a claim only matches inside its own run.
                    # In a chat a plan exists to stand in for the modal, so it is only consulted when
                    # there would have been one. A desk's plan is also its record of progress - what
                    # `_should_chain` reads - so an approved step is claimed there even for a call that
                    # was going to run anyway. A forced approval never consults a plan either way.
                    if not forced and (mode == "ask" or (active_plan is not None and mode == "on")):
                        claimed = plans.claim(run.run_id if run else None, c["name"], args, uid,
                                              desk_id=run.desk_id if run else None)
                        # Progress, for a supervisor deciding whether another turn is worth it: a
                        # turn that consumed no approved step did not advance the plan.
                        if claimed is not None and run is not None:
                            run.steps_consumed += 1
                    if claimed is None and plans.rejected(conv_id, c["name"], args):
                        pre = tools.denied(c["name"], "part of a plan you rejected")
                if blocked_reason and pre is None:
                    pre = tools.denied(c["name"], blocked_reason)
                asks = mode == "ask" and claimed is None and pre is None
                yield "tool_call", {"message_id": am["id"], "id": uid, "name": c["name"], "arguments": args,
                                    "needs_approval": asks, "forced": forced, "proposal": proposing or None,
                                    "plan": {"plan_id": claimed["plan_id"], "idx": claimed["idx"], "title": claimed["title"]} if claimed else None}
                tspan = tracer.start("tool", c["name"], {"round": _round, "arguments": _short(args), "mode": mode, "forced": forced,
                                                         "plan_step": f"{claimed['plan_id']}#{claimed['idx']}" if claimed else None})
                yield "span", {"message_id": am["id"], "span": tspan}
                t0 = time.time()
                decision = "allow"
                if asks:
                    # Pause the reply until the user approves or denies this call (POST /approvals/{call_id}).
                    # The approval is a row, and it waits as long as it takes: there is no auto-deny.
                    approval_t0 = time.time()
                    fut: asyncio.Future = asyncio.get_event_loop().create_future()
                    _approvals[uid] = fut
                    store = run.store if run is not None else None
                    if store is not None:
                        store.open_approval(uid, run.run_id, c["name"], args, conversation_id=conv_id, message_id=am["id"],
                                           forced=forced, desk_id=run.desk_id, danger=danger)
                        run.budget = budget.snapshot()
                        run.set_status("awaiting_approval")
                    awaiting = {"id": uid, "name": c["name"], "arguments": args, "result_preview": "", "duration_ms": 0,
                                "error": None, "pending": True, "needs_approval": True, "forced": forced}
                    if desk_id:
                        # The desk leaves the rail's "working" label and says what it is waiting for.
                        # These are the *live* waiting states: a run is still holding the card open.
                        desks.set_status(desk_id, "awaiting_plan" if plan is not None else "needs_approval",
                                         run_id=run.run_id if run else None)
                    # A desk's card may outlive the run that raised it: nobody is at the keyboard, and
                    # holding a run open for hours to wait is how a desk ends up pinned to a dead task.
                    # 0 means never park, which is what an ordinary chat always does.
                    park_after = float(cfg.get("parkAfterSeconds") or 0) if desk_id else 0.0
                    waited = 0.0
                    try:
                        while not fut.done():
                            if stop.is_set():
                                fut.set_result("deny")
                                if store is not None:
                                    store.decide(uid, "deny", by="stop")
                                if plan is not None:  # a stop means "stop", not "never": this no does not block later
                                    plans.decide(uid, "deny", by="stop", note="You stopped the reply before answering this plan.")
                                break
                            # Park, never auto-deny, and never in front of somebody who is looking at
                            # the card: with a viewer attached the run waits as long as it takes.
                            if (park_after > 0 and waited >= park_after and store is not None
                                    and run is not None and run.watchers == 0):
                                store.park(uid)
                                parked = uid
                                break
                            try:
                                # Short, so an approval-blocked run notices a stop or a shutdown promptly.
                                await asyncio.wait_for(asyncio.shield(fut), timeout=2)
                            except asyncio.TimeoutError:
                                waited += 2
                                row = store.approval(uid) if store is not None else None
                                if row and row["status"] != "pending" and not fut.done():  # decided on the row alone
                                    fut.set_result(row["decision"])
                        decision = fut.result() if fut.done() else "deny"
                    finally:
                        _approvals.pop(uid, None)
                    awaiting = None
                    if parked:
                        # The reply ends here, still owing this call an answer. The row stays pending
                        # and decidable; the desk moves from a live waiting state to `blocked`, which
                        # is the one that means "no run is coming back for this on its own".
                        if desk_id:
                            desks.set_status(desk_id, "blocked",
                                             reason="plan" if plan is not None else "approval",
                                             run_id=None)
                        yield "parked", {"message_id": am["id"], "call_id": uid, "name": c["name"]}
                        return
                    if run is not None:
                        run.set_status("running")
                    if desk_id:
                        # Carrying the plan id onto the desk is what makes the plan outlive this turn:
                        # `_should_chain` reads `plans.remaining(desk["plan_id"])`, and claim_run reads
                        # it to tell a desk that has a plan from one that still needs to draft one.
                        approved_plan = plans.by_call(uid) if plan is not None else None
                        desks.set_status(desk_id, "working", run_id=run.run_id if run else None,
                                         plan_id=(approved_plan or {}).get("plan_id")
                                         if (approved_plan or {}).get("status") == "approved" else None)
                    budget.paused += time.time() - approval_t0  # a slow approval must not blow the wall clock
                    t0 = time.time()  # don't count waiting time as tool time
                    granted = decision in ("always_chat", "always_global")
                    # A tainted reply cannot buy a standing grant, and neither can a plan card: 'always' on
                    # propose_plan would leave the plan with no approval at all.
                    standing = granted and not forced and c["name"] != PLAN_TOOL
                    if granted and not standing:
                        decision = "allow"  # one-shot
                    elif decision == "always_chat":
                        # MCP grants live in mcp_grants, not in the settings tool maps: a grant there is
                        # bound to the schema it approved, so a server that rewrites the tool re-asks.
                        if mcp_is(c["name"]):
                            mcp_store.set_grant(c["name"], "on", "chat", conv_id)
                        else:
                            convos.update(conv_id, {"settings": {"tools": {**(conv["settings"].get("tools") or {}), c["name"]: "on"}}})
                            conv["settings"].setdefault("tools", {})[c["name"]] = "on"
                        modes[c["name"]] = "on"
                        decision = "allow"
                    elif decision == "always_global":
                        if mcp_is(c["name"]):
                            mcp_store.set_grant(c["name"], "on", "global")
                        else:
                            db.set_settings({"tools": {**(cfg.get("tools") or {}), c["name"]: "on"}})
                        modes[c["name"]] = "on"
                        decision = "allow"
                    if standing:
                        # the grant changed modes; keep the schemas in step
                        tool_schemas = _schemas()
                    if plan is not None:
                        # The decision was recorded on the plan by whoever answered it (the route, or the stop
                        # above), including any step the user edited: re-read it rather than trust `args`.
                        plan = plans.by_call(uid) or plan
                        if desk_id and plan.get("status") == "approved":
                            # A plan approved in this very reply is the desk's active plan from here
                            # on: `active_plan` was read before it existed, and the calls that follow
                            # are exactly the ones it authorises. Drafting is over, so the block on
                            # consequential calls lifts with it - otherwise the plan the user just
                            # approved could never be carried out.
                            active_plan, planning = plan, False
                            # Drafting is over: the tools held back above are offered again.
                            tool_schemas = _schemas()
                        # Published on the run's own stream, so a window that was watching the card
                        # sees the answer even when it was given somewhere else.
                        yield "plan_decision", {"message_id": am["id"], "call_id": uid, "plan": plan}
                was_tainted, was_blocked = tool_ctx["tainted"], c["name"] in blocked
                if was_blocked:
                    result: Any = tools.denied(c["name"], f"failing {TOOL_ERROR_LIMIT} times in a row and disabled for the rest of this reply")
                elif mode == "off":
                    result = tools.denied(c["name"], "turned off for this chat")
                elif pre is not None:
                    result = pre  # an unusable plan, or a step of a plan the user rejected
                elif plan is not None:
                    result = plans.model_result(plan)  # the decision, and the arguments the user actually authorised
                elif decision != "allow":
                    result = tools.denied(c["name"], "just declined by the user")
                elif mcp_is(c["name"]):
                    result = await _mcp_call(c["name"], args)
                else:
                    result = await _call_tool(run, _round, c["name"], args, tool_ctx, uid)
                ms = int((time.time() - t0) * 1000)
                # images (e.g. matplotlib figures from run_python) go to the UI, not to the model
                images = result.pop("images", None) if isinstance(result, dict) else None
                preview = summarize_result(result)
                err = result.get("error") if isinstance(result, dict) else None
                tool_errors[c["name"]] = tool_errors.get(c["name"], 0) + 1 if err else 0  # reset on success = consecutive
                if tool_errors[c["name"]] >= TOOL_ERROR_LIMIT:
                    blocked.add(c["name"])
                # An MCP result is third-party text by definition, so it taints like a web fetch does.
                tainted = decision == "allow" and not err and (toolbox.taints(c["name"]) or mcp_is(c["name"]))
                if tainted:
                    tool_ctx["tainted"] = True  # Toolbox.call sets this for built-ins; _mcp_call cannot reach ctx
                    if not was_tainted:
                        yield "taint", {"message_id": am["id"], "source": c["name"]}
                    tool_ctx["taint_sources"].append(c["name"])
                event = {"id": uid, "name": c["name"], "arguments": args, "result_preview": preview, "duration_ms": ms,
                         "error": err, "images": images or None,
                         "approval": (("plan" if claimed else decision) if mode == "ask" else None),
                         "plan": {"plan_id": claimed["plan_id"], "idx": claimed["idx"], "title": claimed["title"]} if claimed else None,
                         "forced": forced, "tainted": tainted, "blocked": c["name"] if was_blocked else None, "breaker": partial,
                         "blocked_by": "plan_mode" if blocked_reason == PLAN_BLOCKED else None,
                         "proposal": (result.get("proposal_id") if proposing and isinstance(result, dict) else None)}
                tracer.end(tspan, {"result_chars": len(preview), "images": len(images or [])}, error=err)
                if claimed is not None:
                    # The step was spent at the gate; this records whether the call it authorised
                    # actually worked, so the plan can be read after the fact.
                    plans.finish(uid, not err, err)
                tool_events.append(event)
                yield "tool_result", {"message_id": am["id"], **event}
                yield "span", {"message_id": am["id"], "span": tspan}
                for_model = {**result, "images_shown_to_user": [i["name"] for i in images]} if images and isinstance(result, dict) else result
                # Small results go in whole; a big one is stored and replaced by a handle the model can
                # page with read_tool_result, so nothing is silently truncated away. See working.py.
                messages.append({"role": "tool", "tool_call_id": c["id"],
                                 "content": tool_results.for_model(conv_id, am["id"], c["name"], for_model)})
                if tool_ctx.pop("plan_changed", None):
                    yield "plan", {"conversation_id": conv_id, "steps": (work_plans.get(conv_id) or {}).get("steps") or []}
            if partial == "loop":
                async for chunk in _final_round():
                    yield chunk
                if steers and not stop.is_set():
                    continue
                break
            if not budget.nudged and budget.fraction() >= 0.6:
                budget.nudged = True
                messages.append({"role": "system", "content": SOFT_NUDGE.format(pct=int(budget.fraction() * 100))})
    except asyncio.CancelledError:
        # Shutdown or a dropped task, not a user Stop: persist what was written and re-raise.
        text = "".join(buf).strip()
        # A call still waiting on approval keeps its card: the approval row stays pending and can still be answered.
        kept = tool_events + ([awaiting] if awaiting else [])
        convos.finish_message(am["id"], text, None if text else "Cancelled", used, kept, tracer.spans, "".join(rbuf).strip() or None)
        convos.touch(conv_id)
        raise
    except Exception as e:  # noqa: BLE001
        error = str(e)
        for s in tracer.fail_open(error):
            yield "span", {"message_id": am["id"], "span": s}
    finally:
        _active.pop(am["id"], None)

    text = "".join(buf).strip()
    reasoning = "".join(rbuf).strip() or None
    convos.finish_message(am["id"], text, error, used, tool_events, tracer.spans, reasoning)
    convos.touch(conv_id)
    if tool_ctx["tainted"]:
        srcs = sorted(set(tool_ctx["taint_sources"]))
        if not conv["settings"].get("tainted") or srcs != sorted(set(conv["settings"].get("taint_sources") or [])):
            convos.update(conv_id, {"settings": {"tainted": True, "taint_sources": srcs}})
    if run is not None:
        # What this reply spent, for whoever is supervising it. A desk turn chains on these; an
        # ordinary chat never reads them back.
        run.partial, run.cost, run.rounds = partial, budget.cost, budget.rounds
    yield "done", {"id": am["id"], "error": error, "context_used": used, "tool_events": tool_events,
                   "trace": tracer.spans, "stopped": stop.is_set(), "partial": partial, "segment": False,
                   "tainted": tool_ctx["tainted"], "taint_sources": tool_ctx["taint_sources"],
                   "reasoning": reasoning}
    if tool_ctx.get("learned"):
        yield "learned", tool_ctx["learned"]

    # Auto-learn is another LLM call, and the run owns the conversation for as long as this
    # generator lives — a second message is a 409 until it returns. So the exchange is handed to the
    # worker and the run ends here; what the worker learns arrives on the app topic (GET /events).
    # A scheduled run never writes to long-term memory either way: it is one more model call nobody
    # asked for, on text the user has not read yet. What it found belongs in its report and the inbox.
    if not error and text and not proposal_only(run) and cfg.get("autoLearn", True) and conv["settings"].get("autoLearn", True):
        learner.submit(LearnJob(
            conversation_id=conv_id, message_id=am["id"], project_id=conv["project_id"],
            user_text=user_text, assistant_text=text, model=model, settings=cfg,
            spans=list(tracer.spans),
        ))

    # Writing style, from the user's half of the exchange only (style.py). Banking a sample is free;
    # the LLM re-reads the samples only on the message that crosses the threshold, so most turns add
    # a row and stop. A failure here is as quiet as a failed memory extraction.
    # The prose check runs before the span so an ordinary short instruction leaves no trace of a step
    # that did nothing — and never leaves a span open for the UI to show as still running.
    if not error and cfg.get("learnStyle", True) and conv["settings"].get("autoLearn", True) and looks_like_prose(user_text):
        sspan = tracer.start("style", "Learn writing style")
        yield "span", {"message_id": am["id"], "span": sspan}
        try:
            banked = await learn_style_from_exchange(
                settings=cfg, style=style, project_id=conv["project_id"], user_text=user_text, model=model,
            )
            tracer.end(sspan, {"sample_chars": (banked or {}).get("sample", {}).get("chars", 0),
                               "profile_updated": bool(banked and banked["profile"])})
            yield "span", {"message_id": am["id"], "span": sspan}
            if banked:
                yield "style_learned", {"project_id": conv["project_id"], "profile": banked["profile"],
                                        "sample_id": banked["sample"]["id"]}
        except Exception as e:  # noqa: BLE001
            tracer.end(sspan, error=str(e))
            yield "span", {"message_id": am["id"], "span": sspan}
        convos.set_trace(am["id"], tracer.spans)


async def _run_chat(run: Run, body: ChatIn) -> None:
    async for event, data in _chat_stream(run.conversation_id, body, run.stop, run.steers, run=run):
        if event == "assistant_message":
            run.message_id = data.get("id")
            # A steer opens a fresh segment, so the run is answering again.
            run.replied = False
        run.publish(event, data)
        # Published after the event, so a client that sees `done` and immediately posts finds the run
        # already closed to steers. The task runs on to auto-learn; it is no longer replying.
        if event == "done":
            run.replied = True


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
    caps = _desk_caps(settings(), desk.get("budget"))
    turns, cost = caps["deskMaxTurns"], caps["deskMaxCost"]
    return (desk.get("status") == "working"
            and run.partial == "rounds"               # only a budget-window stop chains
            and not run.stop.is_set()
            and bool(plans.remaining(desk.get("plan_id") or ""))
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
    # `answering`, not `live`: a run still auto-learning has finished its reply, and a new message
    # deserves a run of its own rather than a 409 the caller can only turn into a dropped steer.
    running = bus.answering(id)
    if running:
        raise HTTPException(409, {"message": "That conversation already has a running reply",
                                 "run_id": running.run_id, "seq": running.seq})
    run = bus.start(id, lambda r: _run_chat(r, body), input=body.model_dump())
    return {"run_id": run.run_id, "seq": run.seq}


class SteerIn(BaseModel):
    content: str


@app.post("/conversations/{id}/steer")
async def steer_run(id: str, body: SteerIn) -> dict[str, Any]:
    """Inject a user message into a run that is still answering; it replies in a fresh segment.

    The message is persisted and published here, so a window sees it immediately. Nothing can slip
    in after the round loop ends: `run.replied` is set in the same synchronous step that publishes
    `done`, with no await between, so a handler that observes `answering` still has a round coming.
    """
    if not convos.get(id):
        raise HTTPException(404, "Conversation not found")
    text = (body.content or "").strip()
    if not text:
        raise HTTPException(400, "Empty message")
    # Only a run that is still answering can fold the message into a round; past its `done` the loop
    # is over, so accepting one here would store a message nothing ever replies to.
    run = bus.answering(id)
    if not run:
        raise HTTPException(409, {"message": "No running reply to steer"})
    um = convos.add_message(id, "user", text)
    run.publish("user_message", um)
    run.steers.append(um)
    run.poke()
    return {"ok": True, "run_id": run.run_id, "message": um}


@app.get("/conversations/{id}/stream")
async def stream_conversation(id: str, since: int = 0, run_id: str | None = None) -> StreamingResponse:
    """Any number of clients may attach; detaching one never touches the run. A tail on the run's tape: past events
    come from run_events (?since= is the last seq the client has), then live ones follow. A run that is no longer in
    memory -- finished long ago, or cut off by a backend restart -- replays from the table and ends. `run_id` pins
    the run, so a reconnect cannot land on a newer run with a stale seq."""
    run = bus.get(id)
    body: Any = iter(())
    if run and (run_id is None or run.run_id == run_id):
        body = run.subscribe(since)
    else:
        row = run_store.get(run_id) if run_id else run_store.latest(id)
        if row and row["conversation_id"] == id:
            body = run_store.tail(row["run_id"], since)
    return StreamingResponse(body, media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/events")
async def stream_events(since: int = 0) -> StreamingResponse:
    """App-wide background events (auto-learn, so far), for work no single run is waiting on.

    Each event's SSE `id` is its seq; reconnect with `?since=<last id>` to replay what was missed.
    """
    return StreamingResponse(events.subscribe(since), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/runs")
async def list_runs(status: str | None = None, conversation_id: str | None = None, limit: int = 50,
                    desk_id: str | None = None) -> list[dict[str, Any]]:
    """Active runs by default (running / awaiting_approval). ?status=all, or a comma list of statuses, reads the
    history from the table, so finished and interrupted runs are still visible after a restart."""
    statuses: tuple[str, ...] | None
    if not status:
        statuses = ACTIVE
    elif status == "all":
        statuses = None
    else:
        statuses = tuple(x for x in status.split(",") if x)
        if not statuses or any(x not in STATUSES for x in statuses):
            raise HTTPException(400, f"status must be 'all' or a comma list of {', '.join(STATUSES)}")
    return bus.list(statuses, conversation_id, limit, desk_id)


@app.get("/runs/{run_id}")
async def get_run(run_id: str) -> dict[str, Any]:
    """One run row, with its approvals and its idempotency journal."""
    row = run_store.get(run_id)
    if not row:
        raise HTTPException(404, "No such run")
    mem = bus.get(row["conversation_id"]) if row["conversation_id"] else None
    over = mem.info() if mem is not None and mem.run_id == run_id else {"seq": row["last_seq"], "live": False}
    return {**row, **over, "approvals": run_store.approvals(None, run_id=run_id), "executed_calls": run_store.executed(run_id),
            "plans": plans.for_run(run_id)}


# async, so the run's asyncio.Event is set on the loop that owns it rather than from a threadpool.
@app.post("/conversations/{id}/stop")
async def stop_run(id: str, run_id: str | None = None) -> dict[str, bool]:
    """Stop before the assistant message exists: detaching a stream would only drop a viewer."""
    return {"ok": bus.stop(id, run_id)}


class ApprovalIn(BaseModel):
    decision: str  # allow | deny | always_chat | always_global
    # propose_plan only: the steps of the plan the user is authorising, as [{idx, arguments?}]. A step left out is
    # dropped (it asks again if the model calls it); replacement arguments re-derive that step's digest, so the
    # edited values are what gets authorised.
    steps: list[dict[str, Any]] | None = None
    note: str | None = None  # one line back to the model, e.g. why a plan was rejected


def _patch_tool_event(message_id: str | None, call_id: str, patch: dict[str, Any]) -> None:
    """Rewrite one persisted tool event in place: how an approval answered after its run died stops showing a card."""
    if not message_id:
        return
    with db.tx() as c:
        r = c.execute("SELECT tool_events FROM messages WHERE id=?", (message_id,)).fetchone()
        if not r or not r["tool_events"]:
            return
        evs = json.loads(r["tool_events"])
        hit = [e for e in evs if isinstance(e, dict) and e.get("id") == call_id]
        for e in hit:
            e.update(patch)
        if hit:
            c.execute("UPDATE messages SET tool_events=? WHERE id=?", (json.dumps(evs), message_id))


@app.get("/approvals")
async def list_approvals(status: str | None = "pending", run_id: str | None = None, limit: int = 100,
                         desk_id: str | None = None) -> list[dict[str, Any]]:
    """Approval rows, pending by default -- including ones whose run was interrupted, so they can still be answered.
    `live` says whether a run in this process is waiting on it."""
    if status not in (None, "", "all", "pending", "approved", "denied"):
        raise HTTPException(400, "status must be pending, approved, denied or all")
    rows = run_store.approvals(None if status in (None, "", "all") else status, run_id, limit, desk_id)
    # A plan card is read back through its plan, not through the approval row: action_plans stays the
    # one place a plan lives, and the row carries only the id that gets you there.
    return [{**a, "live": a["call_id"] in _approvals,
             "plan_id": (plans.by_call(a["call_id"]) or {}).get("plan_id") if a["tool"] == PLAN_TOOL else None}
            for a in rows]


# async, so the waiting run's future is resolved on the loop that owns it rather than from a threadpool.
@app.post("/approvals/{call_id}")
async def approve_tool_call(call_id: str, body: ApprovalIn) -> dict[str, Any]:
    """Record the decision on the approval row (first decision wins), then wake the run if one is waiting in this
    process. A run that died while waiting does not resume: the decision is recorded and its card is settled."""
    if body.decision not in ("allow", "deny", "always_chat", "always_global"):
        raise HTTPException(400, "Bad decision")
    pending = run_store.approval(call_id)
    is_plan = bool(pending and pending["tool"] == PLAN_TOOL)
    if body.steps is not None and not is_plan:
        raise HTTPException(400, "Only a propose_plan approval carries edited steps")
    try:  # shape-check the edit before anything is decided, so a bad payload leaves the row pending
        edits = parse_plan_edits(body.steps)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    fut = _approvals.get(call_id)
    row = run_store.decide(call_id, body.decision)
    live = bool(fut and not fut.done())
    if row is None and not live:
        raise HTTPException(404, "No pending approval for that call")
    if row is not None and is_plan:
        # The plan is decided before the run is woken: what it reads back is the user's answer, edits included.
        plans.decide(call_id, body.decision, edits=edits, note=body.note)
    if live:
        fut.set_result(body.decision)  # type: ignore[union-attr]
    elif row is not None and not (row.get("desk_id") and row.get("decided_by") == "park"):
        _patch_tool_event(row["message_id"], call_id, {
            "pending": False, "needs_approval": False, "approval": body.decision,
            "error": "Not run: the reply was interrupted before this was answered. The decision is recorded; ask again to run it."})
    # A desk's card can outlive the run that raised it (see parking), so answering one is also how a
    # desk is woken. Nothing here resumes a chat: a chat run that died stays dead, as above.
    resumed = False
    desk_id = (row or {}).get("desk_id")
    if desk_id and not live:
        if is_plan and body.decision != "deny":
            # The in-run approval writes desks.plan_id as it flips the desk back to `working`; a plan
            # decided after its run let go has to write it too, or claim_run reads an empty plan_id,
            # wakes the desk back into planning, and the approved steps are never carried out.
            plan = plans.by_call(call_id)
            current = desks.get(desk_id, with_outputs=False)
            if plan and current and not current.get("plan_id"):
                desks.set_status(desk_id, current["status"], reason=current["status_reason"],
                                 plan_id=plan["plan_id"], event=False)
        resumed = _wake_desk(desk_id) is not None
    return {"ok": True, "live": live, "resumed": resumed,
            "status": row["status"] if row else ("denied" if body.decision == "deny" else "approved")}


@app.on_event("startup")
async def _recover_runs() -> None:
    """Runs left active by the last process died with it: mark them interrupted and salvage their reply from the tape."""
    for r in run_store.recover(bus.live_ids()):
        mid = r.get("message_id")
        if not mid:
            continue
        try:
            with db.tx() as c:
                cur = c.execute("SELECT content, tool_events, context_used FROM messages WHERE id=?", (mid,)).fetchone()
            if cur is None or cur["content"] or cur["tool_events"]:
                continue  # already finished (the cancel path persisted it): the message is the better record
            text, tool_events = run_store.transcript(r["run_id"], mid)
            done = {e.get("id") for e in tool_events}
            for a in r["pending_approvals"]:
                if a["message_id"] == mid and a["call_id"] not in done:
                    tool_events.append({"id": a["call_id"], "name": a["tool"], "arguments": a["args"], "result_preview": "",
                                        "duration_ms": 0, "error": None, "pending": True, "needs_approval": True, "forced": a["forced"]})
            convos.finish_message(mid, text, r["error"], json.loads(cur["context_used"]) if cur["context_used"] else None, tool_events)
        except Exception:  # noqa: BLE001 - recovery must never stop the backend from starting
            log.warning("could not salvage the reply of run %s", r["run_id"], exc_info=True)


# ---------------- scheduled jobs, proposals, agent inbox ----------------
# A job fire is a chat run in its own conversation, with kind='job', so it gets the journal, the budget snapshot
# and the idempotency journal for free — and proposal_only() for free with them.
LATE_NOTICE = ("[This run was scheduled for {due}, and is only starting now, at {fired} — {late} late{skipped}. "
               "Say so in one line at the top of your report, and re-check anything time-sensitive rather than "
               "assuming it is still true.]")
# The journal step a proposal's execution is booked under. No chat round uses a negative one.
PROPOSAL_STEP = -1


def _stamp(ts: float) -> str:
    return time.strftime("%a %d %b %H:%M", time.localtime(ts))


def _span(seconds: float) -> str:
    m = int(seconds // 60)
    return f"{int(seconds)}s" if m < 1 else (f"{m} min" if m < 120 else f"{m // 60}h{m % 60:02d}")


def _job_prompt(job: dict[str, Any], fire: dict[str, Any]) -> str:
    """The run's user turn: the job's own prompt, and the late notice in front of it when the fire is late."""
    if not fire.get("late"):
        return job["prompt"]
    skipped = f", and {fire['missed_slots']} earlier run{'s' if fire['missed_slots'] > 1 else ''} were skipped while " \
              "this machine was asleep or the app was closed" if fire.get("missed_slots") else ""
    return LATE_NOTICE.format(due=_stamp(fire["due_at"]), fired=_stamp(fire["fired_at"]),
                              late=_span(fire["late_seconds"]), skipped=skipped) + "\n\n" + job["prompt"]


async def _launch_job(job: dict[str, Any], fire: dict[str, Any]) -> str | None:
    """One fire: a fresh conversation, then the ordinary chat runner over the job's prompt.

    Fresh each time on purpose. A morning brief that replayed its own back catalogue every day would get slower,
    dearer and worse at the actual job; one fire, one transcript, one tight budget.
    """
    cfg = settings()
    conv = convos.create(job["project_id"], f"{job['name']} · {_stamp(fire['due_at'])}", cfg.get("defaultModel") or "")
    # job_id keeps this transcript out of the sidebar's chat list; the Agent Inbox links to it instead.
    convos.update(conv["id"], {"settings": {"useTools": True, "autoLearn": False, "job_id": job["id"]}})
    body = ChatIn(content=_job_prompt(job, fire))
    run = bus.start(conv["id"], lambda r: _run_chat(r, body), input={**fire, "conversation_id": conv["id"]}, kind="job")
    log.info("job %s fired for %s as run %s", job["name"], _stamp(fire["due_at"]), run.run_id)
    return run.run_id


scheduler = Scheduler(jobs, _launch_job)


class JobIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    prompt: str = Field(min_length=1, max_length=8000)
    # kind='cron' wants `cron`; kind='once' wants `run_at`, a unix timestamp. Neither is required by the model
    # itself because which one is required depends on the other field; _check_schedule says so properly.
    kind: str = "cron"
    cron: str = Field(default="", max_length=120)
    run_at: float | None = None
    timezone: str | None = None
    enabled: bool = False
    project_id: str | None = None


class JobPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    prompt: str | None = Field(default=None, min_length=1, max_length=8000)
    kind: str | None = None
    cron: str | None = Field(default=None, max_length=120)
    run_at: float | None = None
    timezone: str | None = None
    enabled: bool | None = None
    project_id: str | None = None


# How far in the past a one-off may be set, on a write. The scheduler is happy to run a late task — that is the
# catch-up rule — but a *new* task dated yesterday is a mistake, and firing it instantly is not what was meant.
BACKDATE_GRACE_S = 120.0


def _check_schedule(kind: str, expr: str | None, tz: str | None, run_at: float | None, *, fresh_time: bool) -> None:
    """Reject a schedule the scheduler could not read. Always checked against the schedule the row would *end up*
    with, so switching kind without supplying the other field is a 400 and not a crash in the arming code.

    `fresh_time` also rejects a one-off set in the past, and is on only for a time this write supplies: an
    instant that went by while the job sat disabled is a catch-up, which the scheduler handles on purpose.
    """
    if kind not in KINDS:
        raise HTTPException(400, f"'{kind}' is not a schedule kind ('cron' for a repeating job, 'once' for a one-off)")
    if tz and not valid_tz(tz):
        raise HTTPException(400, f"'{tz}' is not a timezone name (e.g. 'Europe/Berlin')")
    if kind == "cron":
        if not valid_cron(expr or ""):
            raise HTTPException(400, f"'{expr}' is not a cron expression I can read (five fields, e.g. '30 7 * * *')"
                                     if expr else "A repeating job needs a cron expression (five fields, e.g. '30 7 * * *')")
        return
    if run_at is None:
        raise HTTPException(400, "A one-off task needs run_at, the unix timestamp to run it at")
    if fresh_time and run_at < time.time() - BACKDATE_GRACE_S:
        raise HTTPException(400, f"{_stamp(run_at)} has already passed — give a time in the future")


@app.get("/jobs")
def list_jobs() -> list[dict[str, Any]]:
    """Every scheduled job, with the slot it is waiting for. `timezone` defaults to this machine's on create."""
    return jobs.list()


@app.post("/jobs")
def create_job(body: JobIn) -> dict[str, Any]:
    _check_schedule(body.kind, body.cron, body.timezone, body.run_at, fresh_time=True)
    return jobs.create(body.name, body.cron, body.prompt, kind=body.kind, run_at=body.run_at,
                       timezone=body.timezone, enabled=body.enabled, project_id=wsid(body.project_id))


@app.patch("/jobs/{id}")
def update_job(id: str, body: JobPatch) -> dict[str, Any]:
    patch = body.model_dump(exclude_unset=True)
    if "project_id" in patch:
        patch["project_id"] = wsid(patch["project_id"])
    cur = jobs.get(id)
    if not cur:
        raise HTTPException(404, "No such job")
    merged = {**cur, **patch}
    _check_schedule(merged["kind"], merged["cron"], patch.get("timezone"), merged["run_at"],
                    fresh_time="run_at" in patch)
    # Switching a spent one-off back on is the one re-arm that cannot work: it has no instant left to wait for,
    # so say that instead of leaving the toggle on with nothing scheduled behind it.
    if patch.get("enabled") and merged["kind"] == "once" and "run_at" not in patch and spent(cur):
        raise HTTPException(400, f"That one-off already ran ({_stamp(cur['last_fired_at'])}). "
                                 "Give it a new run_at to schedule it again.")
    job = jobs.update(id, patch)
    if not job:
        raise HTTPException(404, "No such job")
    return job


@app.delete("/jobs/{id}")
def delete_job(id: str) -> dict[str, bool]:
    if not jobs.delete(id):
        raise HTTPException(404, "No such job")
    return {"ok": True}


@app.post("/jobs/{id}/run")
async def run_job_now(id: str) -> dict[str, Any]:
    """Fire a job by hand, disabled or not. It is still a job run: proposal-only, on the job budget. The schedule
    is untouched, so the next cron slot still fires on its own."""
    job = jobs.get(id)
    if not job:
        raise HTTPException(404, "No such job")
    t = time.time()
    fire = {"job_id": job["id"], "job": job["name"], "kind": job["kind"], "cron": job["cron"], "timezone": job["timezone"],
            "due_at": t, "fired_at": t, "late_seconds": 0.0, "missed_slots": 0, "late": False, "manual": True}
    run_id = await _launch_job(job, fire)
    jobs.mark_launched(job["id"], run_id)
    row = run_store.get(run_id) if run_id else None
    return {"ok": bool(run_id), "run_id": run_id, "conversation_id": (row or {}).get("conversation_id")}


class ProposalIn(BaseModel):
    args: dict[str, Any] | None = None  # the user's edit, accept only


@app.get("/proposals")
def list_proposals(status: str | None = "pending", run_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    """Outward-facing calls a background run recorded instead of making. Pending by default."""
    if status not in (None, "", "all", *PROPOSAL_STATUSES):
        raise HTTPException(400, "status must be pending, accepted, rejected or all")
    return proposals.list(None if status in (None, "", "all") else status, run_id, limit)


@app.post("/proposals/{pid}/accept")
async def accept_proposal(pid: str, body: ProposalIn | None = None) -> dict[str, Any]:
    """Execute a proposal, as the user. The pending -> accepted flip is the claim: it happens once, so a second
    accept (a double click, two windows) never reaches the tool. `args` replaces the call's arguments first."""
    p = proposals.get(pid)
    if not p:
        raise HTTPException(404, "No such proposal")
    if p["status"] != "pending":
        raise HTTPException(409, f"That proposal was already {p['status']}")
    args = (body.args if body and body.args is not None else None)
    claimed = proposals.claim(pid, args)
    if claimed is None:  # lost the race with another accept or a reject
        raise HTTPException(409, "That proposal was just decided somewhere else")
    conv = convos.get(claimed["conversation_id"], with_messages=False) if claimed["conversation_id"] else None
    ctx: dict[str, Any] = {"project_id": (conv or {}).get("project_id"), "conversation_id": claimed["conversation_id"],
                           "tainted": False, "taint_sources": [], "allowed_urls": set(), "settings": settings(),
                           "proposal_only": False, "message_id": claimed["message_id"]}
    result, replayed = await run_store.call_once(claimed["run_id"], PROPOSAL_STEP, claimed["tool"], claimed["args"],
                                                 lambda: toolbox.call(claimed["tool"], claimed["args"], ctx), call_id=pid)
    err = result.get("error") if isinstance(result, dict) else None
    row = proposals.record(pid, result, err)
    return {"ok": not err, "proposal": row, "replayed": replayed, "result": summarize_result(result, 2000)}


@app.post("/proposals/{pid}/reject")
def reject_proposal(pid: str) -> dict[str, Any]:
    p = proposals.get(pid)
    if not p:
        raise HTTPException(404, "No such proposal")
    row = proposals.reject(pid)
    if row is None:
        raise HTTPException(409, f"That proposal was already {p['status']}")
    return {"ok": True, "proposal": row}


INBOX_SUMMARY_CHARS = 1400


@app.get("/inbox")
def agent_inbox(hours: float = 72.0, limit: int = 20) -> dict[str, Any]:
    """The Agent Inbox, built from rows only: agent_runs + run_events + approvals + proposals.

    "Needs you" is the pending approvals and the pending proposals. "While you were away" is one entry per job run,
    whose late-fire notice, failure and counts all come from the journal — the reply text is shown as the body, but
    nothing about the entry is parsed out of it.
    """
    cutoff = time.time() - max(0.0, float(hours)) * 3600
    pending_approvals = []
    for a in run_store.approvals("pending", limit=100):
        row = run_store.get(a["run_id"]) if a["run_id"] else None
        pending_approvals.append({**a, "live": a["call_id"] in _approvals, "run_kind": (row or {}).get("kind"),
                                  "job": ((row or {}).get("input") or {}).get("job")})
    pending_proposals = proposals.list("pending", limit=100)

    runs = run_store.of_kind("job", since=cutoff, limit=limit)
    counts = proposals.counts([r["run_id"] for r in runs])
    away = []
    for r in runs:
        fire = r["input"] if isinstance(r.get("input"), dict) else {}
        ev = run_store.event_counts(r["run_id"])
        text = run_store.transcript(r["run_id"], r["message_id"])[0] if r["message_id"] else ""
        mine = counts.get(r["run_id"], {})
        away.append({
            "run_id": r["run_id"], "conversation_id": r["conversation_id"], "status": r["status"],
            "job_id": fire.get("job_id"), "job": fire.get("job") or "Scheduled job", "kind": fire.get("kind") or "cron",
            "due_at": fire.get("due_at"), "fired_at": fire.get("fired_at") or r["started_at"],
            "late": bool(fire.get("late")), "late_seconds": fire.get("late_seconds") or 0.0,
            "missed_slots": fire.get("missed_slots") or 0, "manual": bool(fire.get("manual")),
            "started_at": r["started_at"], "ended_at": r["ended_at"], "error": r["error"],
            "tool_calls": ev.get("tool_result", 0), "proposals": sum(mine.values()),
            "pending_proposals": mine.get("pending", 0),
            "summary": text[:INBOX_SUMMARY_CHARS] + ("…" if len(text) > INBOX_SUMMARY_CHARS else ""),
        })
    return {"needs_you": {"approvals": pending_approvals, "proposals": pending_proposals},
            "while_you_were_away": away,
            "counts": {"needs_you": len(pending_approvals) + len(pending_proposals),
                       "approvals": len(pending_approvals), "proposals": len(pending_proposals),
                       "runs": len(away), "late": sum(1 for a in away if a["late"]),
                       "failed": sum(1 for a in away if a["status"] in ("error", "interrupted"))},
            "scheduler": {"last_tick": scheduler.last_tick, "fires": scheduler.fires,
                          "next_due_at": jobs.earliest_due(), "timezone": local_tz_name()}}


@app.on_event("startup")
async def _jobs_startup() -> None:
    """Seed the shipped jobs once (disabled), arm whatever has no slot, then start the one scheduler loop."""
    try:
        jobs.seed()
        jobs.arm(time.time())
    except Exception:  # noqa: BLE001 - a bad job row must not stop the backend from starting
        log.warning("could not prepare scheduled jobs", exc_info=True)
    app.state.jobs_task = asyncio.create_task(scheduler.loop(), name="job-scheduler")


@app.on_event("shutdown")
async def _jobs_shutdown() -> None:
    task = getattr(app.state, "jobs_task", None)
    if task:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task


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
async def stop_message(mid: str) -> dict[str, bool]:
    """On the event loop. A sync endpoint would set the flag from a worker thread, which does not wake a waiter."""
    slot = _active.get(mid)
    if isinstance(slot, Run):
        return {"ok": bus.stop(slot.conversation_id, slot.run_id)}
    if isinstance(slot, asyncio.Event):
        slot.set()
        return {"ok": True}
    return {"ok": False}


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
        conv_settings={"useMemory": True, "useGraph": True, "useDocuments": True, "useActivity": True,
                       "useSkills": True, "useStyle": True, "useMeetings": True, **body.conv_settings},
        global_system_prompt=cfg["systemPrompt"], activity=monitor, skills=skills, style=style, meetings=meeting_svc,
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


# ---------------- writing style ----------------
class StylePatch(BaseModel):
    """Hand edits to a profile. Any content key sets `edited`, which stops auto-relearn overwriting it."""
    project_id: str | None = None
    summary: str | None = None
    guidelines: list[str] | None = None
    traits: dict[str, str] | None = None
    phrases: list[str] | None = None
    avoid: list[str] | None = None
    enabled: bool | None = None


class StyleSampleIn(BaseModel):
    project_id: str | None = None
    text: str
    source: str = "paste"
    ref: str = ""


class StyleLearnIn(BaseModel):
    project_id: str | None = None
    model: str | None = None


def _style_payload(project_id: str | None) -> dict[str, Any]:
    """What the Voice panel renders: the scope's own profile, the one a chat would actually use, and counts."""
    effective = style.for_context(project_id)
    return {"profile": style.profile(project_id), "stats": style.stats(project_id),
            "inherited": bool(project_id and effective and effective["project_id"] is None),
            "effective": effective}


@app.get("/style")
def get_style(project_id: str | None = None) -> dict[str, Any]:
    return _style_payload(sid(project_id))


@app.put("/style")
def put_style(body: StylePatch) -> dict[str, Any]:
    patch = body.model_dump(exclude_none=True, exclude={"project_id"})
    if not patch:
        raise HTTPException(400, "Nothing to update")
    # Toggling `enabled` is not authorship; editing the text is.
    if set(patch) - {"enabled"}:
        patch["edited"] = True
    style.save_profile(wsid(body.project_id), patch)
    return _style_payload(wsid(body.project_id))


@app.post("/style/learn")
async def learn_style(body: StyleLearnIn) -> dict[str, Any]:
    """Re-read the samples now. Forced, so it also refreshes a profile the user has hand-edited."""
    pid = wsid(body.project_id)
    cfg = settings()
    if not style.samples(pid, limit=1):
        raise HTTPException(400, "No writing samples in this scope yet. Add one, or let a long message be banked.")
    try:
        profile = await style.relearn(settings=cfg, project_id=pid, model=body.model or cfg["defaultModel"], force=True)
    except llm.LLMError as e:
        raise HTTPException(502, str(e)) from e
    if not profile:
        raise HTTPException(502, "The model did not return a usable style profile. Try again, or add more samples.")
    return _style_payload(pid)


@app.delete("/style")
def delete_style(project_id: str | None = None, with_samples: bool = False) -> dict[str, Any]:
    """Drop the profile. `with_samples` also throws away the writing it was derived from."""
    pid = sid(project_id)
    style.delete_profile(pid if pid is not ALL else None, with_samples=with_samples)
    return _style_payload(pid if pid is not ALL else None)


@app.get("/style/samples")
def list_style_samples(project_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    return style.samples(sid(project_id), limit=min(limit, 200))


@app.post("/style/samples")
def add_style_sample(body: StyleSampleIn) -> dict[str, Any]:
    """Add a passage by hand. The prose filter is skipped: a deliberate paste is already a decision."""
    s = style.add_sample(wsid(body.project_id), body.text, source=body.source or "paste", ref=body.ref, check=False)
    if not s:
        raise HTTPException(400, "Empty sample")
    return s


@app.delete("/style/samples/{id}")
def delete_style_sample(id: str) -> dict[str, bool]:
    style.delete_sample(id)
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
    await learner.stop()  # after the runs, so nothing is still queueing work at it
    await meeting_bus.shutdown()
    shutil.rmtree(db.data_dir / "tmp", ignore_errors=True)
    await asyncio.to_thread(sandboxes.shutdown)  # after the runs: a live sandbox_exec would just see its container vanish


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


def _gcall(fn, *args, refresh: bool = False):  # type: ignore[no-untyped-def]
    """Call a Google method and map its failures onto HTTP.

    `refresh=True` serves the call from Google rather than the read cache - what a
    user-initiated reload should do.
    """
    try:
        with google_cache.bypass() if refresh else contextlib.nullcontext():
            return json_safe(fn(*args))
    except GoogleNotConnected as e:
        raise HTTPException(409, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"Google API error: {e}") from e


@app.get("/integrations/google/calendars")
def google_calendars(refresh: bool = False) -> Any:
    return _gcall(google.calendars, refresh=refresh)


@app.get("/integrations/google/calendar")
def google_calendar(days: int = 2, start: str | None = None, calendars: str = "primary", refresh: bool = False) -> Any:
    ids = None if calendars in ("", "primary") else [c.strip() for c in calendars.split(",") if c.strip()]
    return _gcall(google.calendar_events, days, "primary", 60, start, ids, refresh=refresh)


@app.get("/integrations/google/cache")
def google_cache_stats() -> dict[str, Any]:
    return google.cache_stats()


@app.post("/integrations/google/cache/clear")
def google_cache_clear(namespace: str = "") -> dict[str, Any]:
    """Forget cached Google reads - all of them, or one namespace (calendar, gmail, ...)."""
    dropped = google.invalidate(*([namespace] if namespace else []))
    return {"dropped": dropped, **google.cache_stats()}


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
def google_gmail(q: str = "is:unread in:inbox newer_than:14d", max_results: int = 12, refresh: bool = False) -> Any:
    return _gcall(google.gmail_search, q, max_results, refresh=refresh)


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
    # Queued, not sent: the hold is what makes Undo possible (see outbox.py). The response says so.
    return _gcall(outbox.queue, body.to, body.subject, body.body, body.reply_to_message_id, "app")


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
def google_tasks(show_completed: bool = False, refresh: bool = False) -> Any:
    return _gcall(google.tasks_list, "@default", show_completed, refresh=refresh)


@app.get("/integrations/google/tasklists")
def google_tasklists() -> Any:
    return _gcall(google.tasks_lists)


@app.on_event("startup")
async def _modules_startup() -> None:
    for m in modules:
        await m.start()


@app.on_event("shutdown")
async def _modules_shutdown() -> None:
    for m in modules:
        await m.stop()


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
        **{k: v for m in modules for k, v in m.today().items()},
        "projects": [{**p, "stats": projects.stats(p["id"])} for p in projects.list()],
        "recent_memories": memories.list(ALL)[:6],
        "recent_conversations": convos.list(ALL)[:6],
        "calendar": None, "gmail": None, "tasks": None, "drive": None, "errors": {},
    }
    if st["connected"]:
        async def fetch(key: str, fn, *args) -> None:  # type: ignore[no-untyped-def]
            try:
                out[key] = json_safe(await asyncio.to_thread(fn, *args))
            except Exception as e:  # noqa: BLE001
                out["errors"][key] = str(e)

        # Not `jobs`: that name is the scheduled-job repo at module scope.
        fetches = [
            fetch("calendar", google.calendar_events, 2),
            fetch("gmail", google.gmail_search, "is:unread in:inbox newer_than:14d", 10),
            fetch("tasks", google.tasks_list, "@default", False),
        ]
        # Drive is a newer scope; before the user reconnects, skip the call instead of
        # surfacing a 403 — the card reads missing_scopes and offers Reconnect.
        if _google_has(st, "drive.readonly"):
            fetches.append(fetch("drive", google.drive_files, "", 10))
        await asyncio.gather(*fetches)
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
    locked: bool | None = None
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
    opacity: Finite | None = None   # window alpha while popped; the store clamps it to [0.2, 1.0]
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
    # Where in the Files tree this doc now lives: '' is the personal tree, otherwise a project id.
    # Unlike `project_id` it can say "personal" out loud, so one patch can carry a whole drag —
    # project and folder together — without needing `clear_project` as a second flag.
    scope: str | None = None


@app.get("/docs")
def list_docs(project_id: str | None = "all", q: str = "") -> list[dict[str, Any]]:
    scope = "__all__" if project_id in (None, "all") else sid(project_id)
    return docs.list(scope, q)


@app.get("/docs/pending")
def docs_pending() -> dict[str, int]:
    """Badge count for the sidebar: assistant edits waiting to be reviewed."""
    return {"pending": docs.pending_count()}


class FolderIn(BaseModel):
    path: str
    # Which tree: '' is the personal one, otherwise a project id. Absent means personal, which is what
    # every folder made before projects had their own tree was.
    scope: str = ""


class FolderRename(BaseModel):
    path: str
    new_path: str
    scope: str = ""


# Declared above /docs/{id}: FastAPI matches in order, and "folders" would otherwise be read as a doc id.
@app.get("/docs/folders")
def list_doc_folders() -> list[dict[str, Any]]:
    return docs.folders()


@app.post("/docs/folders")
def create_doc_folder(body: FolderIn) -> list[dict[str, Any]]:
    try:
        return docs.create_folder(body.path, fscope(body.scope))
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.patch("/docs/folders")
def rename_doc_folder(body: FolderRename) -> list[dict[str, Any]]:
    """Rename and move are the same operation: both rewrite the path of a folder and its subtree."""
    try:
        return docs.rename_folder(body.path, body.new_path, fscope(body.scope))
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.delete("/docs/folders")
def delete_doc_folder(path: str, delete_docs: bool = False, scope: str = "") -> list[dict[str, Any]]:
    try:
        return docs.delete_folder(path, delete_docs, fscope(scope))
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


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
    # A doc the user wrote is the best evidence of their voice there is — far better than chat. Banked
    # under a stable ref, so editing one doc for a week refreshes one sample instead of adding seven.
    if settings().get("learnStyle", True):
        style.add_sample(d["project_id"], d["content"], source="doc", ref=f"doc:{id}")
    return d


@app.patch("/docs/{id}")
def patch_doc(id: str, body: DocMetaPatch) -> dict[str, Any]:
    patch = body.model_dump(exclude_none=True, exclude={"clear_project", "scope"})
    if body.scope is not None:
        patch["project_id"] = fscope(body.scope) or None
    elif body.clear_project:
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
    palantir: bool | None = None
    insights: dict[str, Any] | None = None


class PauseIn(BaseModel):
    minutes: float = 30.0


class PurgeIn(BaseModel):
    scope: str = "expired"  # expired | events | summaries | all


@app.get("/activity/status")
def activity_status() -> dict[str, Any]:
    return monitor.status()


@app.put("/activity/config")
def activity_config(body: ActivityConfigIn) -> dict[str, Any]:
    patch = body.model_dump(exclude_none=True)
    # Palantir mode never travels as a plain field: it has to go through set_palantir, which
    # snapshots the settings it is about to flatten so they can be put back.
    palantir = patch.pop("palantir", None)
    if patch:
        monitor.set_config(patch)
    if palantir is not None and bool(palantir) != bool(monitor.config().get("palantir")):
        monitor.set_palantir(bool(palantir))
    return monitor.status()


class PalantirIn(BaseModel):
    on: bool = True


@app.post("/activity/palantir")
def activity_palantir(body: PalantirIn) -> dict[str, Any]:
    """Record everything, or put back what was there before. The gate's discretionary filters go
    down with it, so the panel spells out what it does before anyone presses it."""
    if body.on and not activity.IS_MAC:
        raise HTTPException(400, "The activity collectors are macOS-only.")
    return monitor.set_palantir(bool(body.on))


class PermissionIn(BaseModel):
    id: str
    browser: str = ""


@app.get("/activity/permissions")
def activity_permissions() -> list[dict[str, Any]]:
    """The macOS permission rows on their own. Read-only: this never prompts."""
    return activity.permissions()


@app.post("/activity/permissions/request")
def activity_permission_request(body: PermissionIn) -> dict[str, Any]:
    """Ask macOS for one permission - the only route that can put a system dialog on screen, and
    it exists because the user pressed Grant."""
    out = activity.request_permission(body.id, body.browser)
    return {"result": out, "status": monitor.status()}


@app.post("/activity/permissions/open")
def activity_permission_open(body: PermissionIn) -> dict[str, bool]:
    """Open the Privacy & Security pane for one permission. Opening a pane grants nothing."""
    return {"ok": activity.open_settings(body.id)}


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
    # ttl=0 only here: audio_devices() is TTL-cached for 20s so one status() poll stops spawning
    # two 15-second ffmpeg probes, but this route answers an explicit "what is plugged in now" and
    # a device the user just connected must not be missing from the picker.
    return activity.audio_devices(ttl=0)


@app.post("/activity/purge")
def activity_purge(body: PurgeIn) -> dict[str, Any]:
    return {"deleted": monitor.purge(body.scope), "status": monitor.status()}


# ---------------- insights: habits and automation suggestions ----------------
#
# Everything under here is derived from the activity data: the patterns are mined locally with no
# model, the habits are written into the ordinary memory panel (and listed with a Forget button),
# and a suggestion is a proposal with a status. Nothing applies itself - /apply exists because the
# user pressed a button, and for the common `prompt` action it does not even act: it hands back the
# message for them to send, so the setup happens in a chat with the usual tool approvals.


class SuggestionStatusIn(BaseModel):
    status: str
    note: str = ""
    snooze_days: float = 7.0


@app.get("/activity/insights")
def activity_insights() -> dict[str, Any]:
    return monitor.insights.overview()


@app.post("/activity/insights/mine")
def activity_insights_mine() -> dict[str, Any]:
    """Re-mine the patterns without calling a model. Cheap, offline, and what the panel shows."""
    monitor.insights.mine_now()
    return monitor.insights.overview()


@app.post("/activity/insights/refresh")
async def activity_insights_refresh() -> dict[str, Any]:
    return await monitor.insights.refresh(force=True)


@app.post("/activity/insights/{sid_}/status")
def activity_insight_status(sid_: str, body: SuggestionStatusIn) -> dict[str, Any]:
    try:
        out = monitor.insights.set_status(sid_, body.status, body.note, body.snooze_days)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    if not out:
        raise HTTPException(404, "No such suggestion")
    return out


@app.post("/activity/insights/{sid_}/apply")
def activity_insight_apply(sid_: str) -> dict[str, Any]:
    try:
        return monitor.insights.apply(sid_)
    except KeyError as e:
        raise HTTPException(404, "No such suggestion") from e


@app.delete("/activity/insights/{sid_}")
def activity_insight_delete(sid_: str) -> dict[str, bool]:
    monitor.insights.delete(sid_)
    return {"ok": True}


@app.delete("/activity/habits/{hid}")
def activity_habit_forget(hid: str) -> dict[str, bool]:
    """Forget a habit and the memory it wrote. The memory panel's own delete still works too."""
    monitor.insights.forget_habit(hid, drop_memory=True)
    monitor.write_markdown()
    return {"ok": True}


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


# ---------------- meetings: recorded calls with reviewable enhanced notes ----------------
#
# Inert until the user turns it on AND acknowledges the recording notice: `consentedAt` is 0 by
# default and preflight blocks Start until it is stamped. Start is blocked rather than warned
# about, because recording an hour of audio nothing can transcribe is worse than refusing.
#
# Nothing here expires. /activity/purge runs a bare DELETE FROM activity_events, and no route
# below can be reached by it.


class MeetingIn(BaseModel):
    """A new meeting. `status` is `scheduled` rather than the repo's `notes_only` default because
    this row was made in order to be recorded; the one the 45s tick adopts says so for itself."""

    title: str = ""
    project_id: str | None = None
    template: str = "general"
    status: str = "scheduled"
    # Adopting a calendar candidate comes through here too: the partial unique index on
    # calendar_event_id makes a second POST for one event hand back the row that already exists.
    calendar_event_id: str | None = None
    calendar_id: str | None = None
    calendar_link: str = ""
    conference_link: str = ""
    # list[Any] because create() normalises either shape: a list of {email,name,...} dicts, or the
    # bare email strings a caller holding only a calendar event's `attendees` array would send.
    attendees: list[Any] = []
    scheduled_start: float | None = None
    scheduled_end: float | None = None


class MeetingPatch(BaseModel):
    """What the user owns. `started_at`, `sources`, `transcript`, `duration_ms` and `audio_dir` are
    deliberately absent: those go through the service, so no PATCH body can claim a meeting
    captured a channel it never opened."""

    title: str | None = None
    notes: str | None = None
    enhanced: str | None = None
    summary: str | None = None
    template: str | None = None
    keep_audio: bool | None = None
    conversation_id: str | None = None
    project_id: str | None = None
    clear_project: bool = False  # exclude_none=True would otherwise drop a null project_id


class MeetingConfigIn(BaseModel):
    """A partial patch, deep-merged over the stored config (ActivityConfigIn's shape).

    `consentedAt` is not here: it is stamped by POST /meetings/consent and nothing else, so a
    settings PUT cannot acknowledge the recording notice on the user's behalf.
    """

    enabled: bool | None = None
    autoRecord: bool | None = None
    nudgeSeconds: int | None = None
    micDevice: str | None = None
    micDeviceName: str | None = None
    outputDevice: str | None = None
    outputDeviceName: str | None = None
    sources: list[str] | None = None
    segmentSeconds: int | None = None
    maxMeetingSeconds: int | None = None
    drainSeconds: int | None = None
    sttBackend: str | None = None
    sttModel: str | None = None
    whisperModelPath: str | None = None
    template: str | None = None
    enhanceOnStop: bool | None = None
    enhanceModel: str | None = None
    maxTranscriptChars: int | None = None
    keepAudio: bool | None = None
    maxAudioBytes: int | None = None
    redactSecrets: bool | None = None
    injectContext: bool | None = None
    autoStopGraceSeconds: int | None = None
    calendarIds: list[str] | None = None
    minAttendees: int | None = None


class MeetingActionsIn(BaseModel):
    """Which proposed action items become todos. Empty `ids` means every one still proposed."""

    ids: list[str] = []
    project_id: str | None = None


# Every literal sub-path is registered BEFORE /meetings/{id}: FastAPI matches in declaration
# order, so a later /meetings/status would be read as a meeting id. Same trap as the one flagged
# at app.py:1302 and relied on by /docs/pending.
@app.get("/meetings/status")
def meeting_status() -> dict[str, Any]:
    """Cheap enough to poll at a second or two: no network call, and the device list is TTL-cached."""
    return meeting_svc.status()


# Both verbs on purpose. It reads like a GET, src/shared/types.ts documents it as a POST, and a 405
# here would show up in the panel as "Start is permanently blocked" with nothing to fix.
@app.get("/meetings/preflight")
@app.post("/meetings/preflight")
async def meeting_preflight(force: bool = False) -> dict[str, Any]:
    """Capabilities plus a real round trip, cached ten minutes. Threaded: it runs ffmpeg and an
    HTTP request with a 120s timeout, neither of which belongs on the event loop."""
    return await asyncio.to_thread(meeting_svc.preflight, force)


@app.put("/meetings/config")
def meeting_config(body: MeetingConfigIn) -> dict[str, Any]:
    """Deep-merged, and it never touches a live recording: picking a different microphone halfway
    through a call applies to the next segment instead of tearing the capture down."""
    meeting_svc.set_config(body.model_dump(exclude_none=True))
    return meeting_svc.status()


@app.get("/meetings/config")
def get_meeting_config() -> dict[str, Any]:
    return meeting_svc.config()


@app.post("/meetings/consent")
def meeting_consent() -> dict[str, Any]:
    """The one-time acknowledgement that the people on the call will be told."""
    meeting_svc.consent()
    return meeting_svc.status()


@app.post("/meetings/selftest")
async def meeting_selftest() -> dict[str, Any]:
    """Force the probe: a synthesized silent wav, recorded and transcribed for real. Returns the
    whole preflight, so a passing self-test also clears whatever it was blocking."""
    return await asyncio.to_thread(meeting_svc.preflight, True)


@app.get("/meetings/devices")
def meeting_devices(refresh: bool = False) -> list[dict[str, Any]]:
    """The audio inputs ffmpeg can see. refresh=true re-probes instead of using the 20s cache."""
    return meeting_svc.devices(refresh)


@app.get("/meetings/suggest")
async def meeting_suggest() -> list[dict[str, Any]]:
    """Calendar events happening right now that are worth taking notes on. No LLM, 60s cached.

    [] rather than an error whenever Google is not connected or the scope was never granted: a
    missing suggestion is a missing row in a panel, not a broken panel.
    """
    st = google.status()
    if not st["connected"] or not _google_has(st, "calendar"):
        return []
    return await meeting_svc.suggest()


@app.get("/meetings/search")
def search_meetings(q: str, project_id: str | None = "all", limit: int = 10) -> list[dict[str, Any]]:
    """FTS over titles, notes, enhanced notes and transcripts. Each hit's `field` says which one."""
    scope = "__all__" if project_id in (None, "all") else sid(project_id)
    return meeting_store.search(q, scope, limit)


@app.get("/meetings/pending")
def meetings_pending() -> dict[str, int]:
    """Badge count for the sidebar: enhance proposals waiting to be reviewed."""
    return {"pending": meeting_store.pending_count()}


@app.post("/meetings/revisions/{rev_id}/accept")
def accept_meeting_revision(rev_id: str) -> dict[str, Any]:
    """Writes `enhanced` and nothing else - what the user typed is never touched."""
    m = meeting_store.accept(rev_id)
    if not m:
        raise HTTPException(404, "No pending revision with that id")
    return m


@app.post("/meetings/revisions/{rev_id}/reject")
def reject_meeting_revision(rev_id: str) -> dict[str, Any]:
    m = meeting_store.reject(rev_id)
    if not m:
        raise HTTPException(404, "No pending revision with that id")
    return m


@app.get("/meetings")
def list_meetings(project_id: str | None = "all", q: str = "", status: str = "",
                  since_days: int = 0, limit: int = 100) -> list[dict[str, Any]]:
    """Preview rows: counts and the first 240 characters of the notes, never a body."""
    scope = "__all__" if project_id in (None, "all") else sid(project_id)
    return meeting_store.list(scope, q, status, since_days, limit)


@app.post("/meetings")
def create_meeting(body: MeetingIn) -> dict[str, Any]:
    return meeting_store.create(
        title=body.title, project_id=wsid(body.project_id), template=body.template,
        calendar_event_id=body.calendar_event_id, calendar_id=body.calendar_id,
        calendar_link=body.calendar_link, conference_link=body.conference_link,
        attendees=body.attendees, scheduled_start=body.scheduled_start,
        scheduled_end=body.scheduled_end, status=body.status)


@app.get("/meetings/{id}")
def get_meeting(id: str) -> dict[str, Any]:
    m = meeting_store.get(id)
    if not m:
        raise HTTPException(404)
    return m


@app.put("/meetings/{id}")
def update_meeting(id: str, body: MeetingPatch) -> dict[str, Any]:
    patch = body.model_dump(exclude_none=True, exclude={"clear_project"})
    if body.clear_project:
        patch["project_id"] = None
    elif "project_id" in patch:
        patch["project_id"] = wsid(patch["project_id"])
    m = meeting_store.patch(id, patch)
    if not m:
        raise HTTPException(404)
    return m


@app.delete("/meetings/{id}")
def delete_meeting(id: str) -> dict[str, bool]:
    """Idempotent, like delete_note: a missing id is already the state the caller asked for."""
    meeting_store.delete(id)
    # No `meeting` widget kind ships in this slice, so this sweep is a no-op today - but doing it
    # anyway is the cleanup delete_note does and delete_doc forgets, and the first widget to ship
    # would otherwise leave orphan windows pointing at a deleted meeting.
    with contextlib.suppress(Exception):
        canvases.delete_windows_for("meeting", id)
    return {"ok": True}


@app.post("/meetings/{id}/start")
async def start_meeting(id: str) -> dict[str, Any]:
    """Open the configured channels. Blocked, not warned: a failing preflight is a 409 carrying the
    checklist, and something already recording is a 409 naming the meeting that holds the mic."""
    if not activity.IS_MAC:
        row = next((r for r in meeting_svc.capabilities() if r["id"] == "platform"), {})
        raise HTTPException(400, row.get("fix") or "Recording is macOS-only.")
    try:  # preflight runs ffmpeg and a real HTTP round trip, and launching ffmpeg blocks too
        m = await asyncio.to_thread(meeting_svc.start, id)
    except MeetingBlocked as e:
        raise HTTPException(409, {"blockers": e.blockers}) from e
    except RecorderBusy as e:
        raise HTTPException(409, {"meeting_id": e.meeting_id, "blockers": [{
            "id": "busy", "label": "Already recording", "ok": False, "detail": str(e),
            "fix": "Stop the meeting that is recording before starting another."}]}) from e
    if not m:
        raise HTTPException(404)
    return m


@app.post("/meetings/{id}/stop")
async def stop_meeting(id: str) -> dict[str, Any]:
    """Captures down, queue drained (up to drainSeconds), transcript rolled up. The enhance pass is
    queued rather than awaited, so a slow model does not hold the stop request open."""
    m = await meeting_svc.stop(id)
    if not m:
        raise HTTPException(404)
    return m


@app.post("/meetings/{id}/pause")
def pause_meeting(id: str) -> dict[str, Any]:
    """ffmpeg keeps running so segment numbering stays monotonic; the worker discards the audio.
    Never a stop/start pair: that would restart the counter and overwrite earlier files."""
    st = meeting_svc.pause(id)
    if not st:
        raise HTTPException(409, "That meeting is not recording")
    return st


@app.post("/meetings/{id}/resume")
def resume_meeting(id: str) -> dict[str, Any]:
    st = meeting_svc.resume(id)
    if not st:
        raise HTTPException(409, "That meeting is not recording")
    return st


@app.get("/meetings/{id}/segments")
def meeting_segments(id: str, since: int = -1, offset: int = 0, limit: int = 200,
                     channel: str = "") -> list[dict[str, Any]]:
    """The live transcript pane's poll. `since` is a rowid cursor and each row carries the `cursor`
    to pass back, so since=0 is the whole tail with cursors; omitting it pages by t_start."""
    if since >= 0:
        return meeting_store.since(id, since, limit)
    return meeting_store.segments(id, offset, limit, channel)


@app.get("/meetings/{id}/stream")
async def stream_meeting(id: str, since: int = 0) -> StreamingResponse:
    """The seam for pushing segments instead of polling: the bus is keyed by meeting id and nothing
    publishes to it yet, so this answers an empty stream rather than 404ing."""
    run = meeting_bus.get(id)
    return StreamingResponse(run.subscribe(since) if run else iter(()), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/meetings/{id}/transcript")
def meeting_transcript(id: str, offset: int = 0, limit: int = 500) -> dict[str, Any]:
    """The rolled-up transcript, by line. An hour of speech is far more than one response should
    carry, and the column is only ever rewritten on finalize, so paging it is a pure read."""
    m = meeting_store.get(id)
    if not m:
        raise HTTPException(404)
    lines = (m["transcript"] or "").splitlines()
    off, lim = max(0, int(offset)), max(1, min(int(limit), 2000))
    win = lines[off:off + lim]
    return {"meeting_id": id, "text": "\n".join(win), "lines": win, "total": len(lines),
            "offset": off, "count": len(win), "has_more": off + len(win) < len(lines)}


@app.post("/meetings/{id}/enhance")
async def enhance_meeting(id: str, force: bool = False, template: str | None = None) -> dict[str, Any]:
    """Cache-or-generate, like /recap: an existing proposal is the answer unless ?force=true.

    Returns a REVISION, not the meeting. A degraded pass is a 200 carrying `degraded: true` - its
    mechanical fallback still holds the user's notes verbatim, so there is something to accept -
    and only a pass that could write no revision at all is a 502.
    """
    if not meeting_store.get(id):
        raise HTTPException(404)
    rev = await meeting_svc.enhance(id, force, template)
    if not rev:
        raise HTTPException(502, meeting_svc.last_error or "The enhance pass produced no revision")
    return rev


@app.get("/meetings/{id}/revisions")
def meeting_revisions(id: str, limit: int = 100) -> list[dict[str, Any]]:
    if not meeting_store.get(id):
        raise HTTPException(404)
    return meeting_store.revisions(id, limit)


@app.get("/meetings/{id}/actions")
def meeting_actions(id: str) -> list[dict[str, Any]]:
    if not meeting_store.get(id):
        raise HTTPException(404)
    return meeting_store.action_items(id)


@app.post("/meetings/{id}/actions/add-todos")
def meeting_actions_add_todos(id: str, body: MeetingActionsIn) -> list[dict[str, Any]]:
    """Promote proposed items into real todos. Idempotent per item - one that already carries a
    todo_id is left alone - and todos.on_change pushes each new task to Google within ~2s."""
    if not meeting_store.get(id):
        raise HTTPException(404)
    scope = wsid(body.project_id) if body.project_id else None  # None means "the meeting's own"
    want = set(body.ids)
    for a in meeting_store.action_items(id):
        if a["status"] == "proposed" and (not want or a["id"] in want):
            meeting_store.promote_action_item(a["id"], todos, scope)
    return meeting_store.action_items(id)


@app.post("/meetings/{id}/actions/{action_id}/dismiss")
def meeting_action_dismiss(id: str, action_id: str) -> dict[str, Any]:
    a = meeting_store.dismiss_action_item(action_id)
    if not a:
        raise HTTPException(404)
    return a


@app.post("/meetings/{id}/retranscribe")
async def retranscribe_meeting(id: str, limit: int = 20) -> dict[str, Any]:
    """Replay the failed segments whose wav is still on disk. One HTTP request per segment, so it
    runs in a thread; a segment past its attempt ceiling is left alone."""
    if not meeting_store.get(id):
        raise HTTPException(404)
    # `retranscribe` settles every meeting it touched itself, rebuilding `transcript`, the FTS row
    # and the error column clause by clause. This route used to redo that rebuild and compute
    # `error = "" if text and not failed_segments(id)`, which cleared the WHOLE column - throwing
    # away banners that are still true after a replay, like a dead loopback channel or a failed
    # enhance pass. Let the service own it.
    settled = await asyncio.to_thread(meeting_svc.retranscribe, id, limit)
    return {"settled": settled, "meeting": meeting_store.get(id)}


@app.delete("/meetings/{id}/audio")
def delete_meeting_audio(id: str) -> dict[str, Any]:
    """The wavs go; the segment rows stay, so the UI can still say why retranscribe is over."""
    m = meeting_store.delete_audio(id)
    if not m:
        raise HTTPException(404)
    return m


@app.on_event("startup")
async def _meetings_startup() -> None:
    """Close out whatever a quit interrupted, then run the 45s nudge/auto-stop/retranscribe tick."""
    try:
        meeting_svc.recover()
    except Exception as e:  # noqa: BLE001 - a failing probe must not stop the backend booting
        log.warning("meetings: could not recover interrupted meetings: %s", e)
    app.state.meetings_task = asyncio.create_task(meeting_svc.loop())


@app.on_event("shutdown")
async def _meetings_shutdown() -> None:
    task = getattr(app.state, "meetings_task", None)
    if task:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task
    # Synchronous, and up to ~5s per live meeting: every ffmpeg gets its graceful `q` so the last
    # segment is flushed rather than left as a 0-byte orphan.
    await asyncio.to_thread(meeting_svc.shutdown)


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


# ---------------- outbox: the delayed-send worker (outbox.py) ----------------
@app.on_event("startup")
async def _outbox_startup() -> None:
    """Run the hold timer. The loop's first act is resume(), which decides what a restart does with
    sends that were still waiting — see the outbox.py docstring."""
    app.state.outbox_task = asyncio.create_task(outbox.loop())


@app.on_event("shutdown")
async def _outbox_shutdown() -> None:
    task = getattr(app.state, "outbox_task", None)
    if task:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task


# ---------------- working memory: the plan, result handles, skills ----------------
class PlanIn(BaseModel):
    steps: list[dict[str, Any]] = Field(default_factory=list)


def _plan_payload(conv_id: str) -> dict[str, Any]:
    plan = work_plans.get(conv_id)
    return {"conversation_id": conv_id, "steps": (plan or {}).get("steps") or [], "updated_at": (plan or {}).get("updated_at")}


@app.get("/conversations/{id}/plan")
def get_plan(id: str) -> dict[str, Any]:
    if not convos.get(id, with_messages=False):
        raise HTTPException(404, "Conversation not found")
    return _plan_payload(id)


@app.put("/conversations/{id}/plan")
def put_plan(id: str, body: PlanIn) -> dict[str, Any]:
    """The user's half of the artifact: tick a step off, reorder, reword. The model sees the edit next round."""
    if not convos.get(id, with_messages=False):
        raise HTTPException(404, "Conversation not found")
    work_plans.set(id, body.steps)
    return _plan_payload(id)


@app.delete("/conversations/{id}/plan")
def delete_plan(id: str) -> dict[str, bool]:
    work_plans.clear(id)
    return {"ok": True}


@app.get("/conversations/{id}/tool-results")
def list_tool_results(id: str, limit: int = 20) -> list[dict[str, Any]]:
    """The handles this conversation produced: what was stored instead of inlined."""
    return tool_results.list(id, limit)


@app.get("/tool-results/{rid}")
def read_tool_result_blob(rid: str, offset: int = 0, limit: int = 20000) -> dict[str, Any]:
    row = tool_results.get(rid)
    if not row:
        raise HTTPException(404, "No such tool result")
    out = tool_results.read(row["conversation_id"], rid, offset, limit)
    return out or {}


class SkillPatch(BaseModel):
    name: str | None = None
    description: str | None = None
    procedure: str | None = None
    status: str | None = None
    project_id: str | None = None


class SkillIn(BaseModel):
    name: str
    description: str = ""
    procedure: str = ""
    project_id: str | None = None


class SkillDraftIn(BaseModel):
    name: str = ""
    description: str = ""
    procedure: str = ""
    skill_id: str | None = None


class SkillIntentIn(BaseModel):
    intent: str
    conversation_id: str | None = None
    project_id: str | None = None


@app.get("/skills")
def list_skills(status: str | None = None, project_id: str = "all") -> list[dict[str, Any]]:
    return skills.list(status=status, project_id="__all__" if project_id == "all" else sid(project_id))


@app.post("/skills")
def create_skill(body: SkillIn) -> dict[str, Any]:
    """A skill the user writes themselves. Still created as a candidate: approval is one explicit step, always."""
    return skills.propose(body.name, body.description, body.procedure, project_id=body.project_id, source="user")


@app.patch("/skills/{skill_id}")
def patch_skill(skill_id: str, body: SkillPatch) -> dict[str, Any]:
    """Rename, edit, approve ('approved') or reject ('rejected'). Approving here is the only way a skill
    ever reaches a system prompt — see learn.Skills.approved_block and context.build_context."""
    patch = body.model_dump(exclude_unset=True)
    if patch.get("status") and patch["status"] not in ("candidate", "approved", "rejected"):
        raise HTTPException(400, "status must be candidate, approved or rejected")
    before = skills.get(skill_id)
    if not before:
        raise HTTPException(404, "No such skill")
    # An approved skill is text in a later system prompt, so approving one -- or editing one that is
    # already live -- is checked on the same footing. Warnings never block: only the author weighs
    # those. Errors are authority claims, which is the one thing the fence cannot make safe.
    bad = skillbuild.approval_blockers(before, patch, known_tools=_known_tools(), existing=skills.list())
    if bad:
        raise HTTPException(422, "This procedure cannot be approved as written. " +
                            " ".join(f["message"] for f in bad))
    s = skills.update(skill_id, patch)
    if not s:
        raise HTTPException(404, "No such skill")
    return s


@app.delete("/skills/{skill_id}")
def delete_skill(skill_id: str) -> dict[str, bool]:
    skills.delete(skill_id)
    return {"ok": True}


def _known_tools() -> set[str]:
    """Every tool name the assistant could actually call, so the lint can catch an invented one."""
    return set(toolbox.specs)


def _lint_skill(name: str, description: str, procedure: str, skill_id: str | None = None) -> list[dict[str, Any]]:
    return skillbuild.lint_skill(name, description, procedure, known_tools=_known_tools(),
                                 existing=skills.list(), skill_id=skill_id)


@app.post("/skills/lint")
def lint_skill_draft(body: SkillDraftIn) -> dict[str, Any]:
    """Review a draft without saving it. The review surface calls this as the user types, and
    `skill_draft` runs the identical checks on what a model wrote."""
    findings = _lint_skill(body.name, body.description, body.procedure, skill_id=body.skill_id)
    return {"findings": findings, "blocking": skillbuild.blocking(findings)}


@app.post("/skills/draft")
async def draft_skill_from_intent(body: SkillIntentIn) -> dict[str, Any]:
    """Turn a line of intent into a draft procedure. Stores nothing: the user gets text to edit."""
    cfg = settings()
    context = ""
    if body.conversation_id:
        conv = convos.get(body.conversation_id)
        msgs = [m for m in ((conv or {}).get("messages") or [])
                if m["role"] in ("user", "assistant") and (m.get("content") or "").strip()]
        context = "\n\n".join(f"{m['role']}: {m['content']}" for m in msgs[-12:])
    return await skillbuild.draft_skill(settings=cfg, model=cfg["defaultModel"], intent=body.intent,
                                        context=context, known_tools=_known_tools(), existing=skills.list())


@app.get("/skills/preview")
def preview_skills(project_id: str | None = None) -> dict[str, Any]:
    """Exactly what the assistant will be shown, assembled by the same function the chat uses.

    Not a rendering of it: `skill_block` is the real injected text, so the preview cannot drift from
    what is actually sent, which is the only reason a preview of this is worth anything.
    """
    rows = [s for s in skills.list(status="approved", project_id=sid(project_id)) if (s["procedure"] or "").strip()]
    block = skill_block(rows) if rows else ""
    return {"block": block, "tokens_estimate": estimate_tokens(block),
            "included": [{"id": s["id"], "name": s["name"]} for s in rows[:MAX_INJECTED_SKILLS]],
            "omitted": [{"id": s["id"], "name": s["name"]} for s in rows[MAX_INJECTED_SKILLS:]]}


@app.post("/conversations/{id}/skills/induce")
async def induce_conversation_skill(id: str) -> dict[str, Any]:
    """Distil this conversation into a candidate procedure for review. Never enables anything."""
    conv = convos.get(id)
    if not conv:
        raise HTTPException(404, "Conversation not found")
    msgs = [m for m in conv["messages"] if m["role"] in ("user", "assistant") and (m["content"] or "").strip()]
    if len(msgs) < 2:
        return {"candidate": None, "reason": "Not enough of a conversation to learn a procedure from."}
    transcript = "\n\n".join(f"{m['role'].upper()}: {m['content'][:2000]}" for m in msgs[-24:])
    cfg = settings()
    cand = await induce_skill(settings=cfg, skills=skills, project_id=conv["project_id"], conversation_id=id,
                              transcript=transcript, model=conv["model"] or cfg["defaultModel"])
    return {"candidate": cand, "reason": None if cand else "Nothing reusable enough to propose."}


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
    plan = plans.get(desk["plan_id"]) if desk.get("plan_id") else plans.for_desk(id)
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
            # call_once answers (result, replayed); a replayed promotion is the point of taping it,
            # since accepting the same deliverable twice would create the doc twice.
            booked = (await run_store.call_once(rid, PROMOTE_STEP, "promote", args, _fn))[0] if rid else await _fn()
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
    plan = plans.get(plan_id)
    if not plan:
        raise HTTPException(404, "No such plan")
    return plan


@app.get("/conversations/{id}/plan")
def conversation_plan(id: str) -> dict[str, Any] | None:
    """The newest plan of this conversation, so a reloaded chat still shows its card."""
    rows = plans.for_conversation(id, limit=1)
    return rows[0] if rows else None


# A plan is decided through POST /approvals/{call_id} like every other card, not through a route of
# its own. The branch this came from had a second decision path keyed by plan_id; keeping one means
# a plan cannot be approved by a route that skips the approval row, the edited-digest re-derivation
# or the single-use claim. `_wake_desk` is called from there once the decision is recorded.


@app.on_event("startup")
async def _cowork_startup() -> None:
    """Recovery must never stop the backend from starting. Nothing is resumed here: every active
    run becomes `interrupted`, every in-flight tool call becomes `unknown`, every live desk lands
    in Needs you, and the user presses Resume."""
    try:
        # Runs are recovered by _recover_runs above, which owns run_store.recover(). This only has
        # to sweep the desks it left behind: LIVE -> interrupted, plus a needs_you event each.
        woken = desks.recover()
        if woken:
            log.info("cowork recovery: %s desks need you", woken)
    except Exception:  # noqa: BLE001
        log.warning("cowork recovery failed", exc_info=True)
