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
from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, AsyncIterator

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from pydantic import AfterValidator, BaseModel, Field

from . import activity, approval_edits, assist, backups, llm, mac, mcp_drift, mcp_eval, mcp_search, tools
from . import compaction, otel_export, titles
from .context import build_context, estimate_tokens, layout_messages
from .db import SECRET_SETTINGS, Database, data_dir_from_env, new_id
from .extract_text import MAX_UPLOAD_BYTES, extract_structured, extract_text, for_index, has_readable_text, safe_upload_name
from .consolidate import Consolidator
from .learn import MAX_INJECTED_SKILLS, LearnJob, LearnWorker, Skills, induce_skill, run_transcript, skill_block
from .embed import Embedder
from .memory_index import MemoryIndex
from .retrieval import Retriever
from .repos import ALL, Conversations, Documents, Graph, Memories, Projects
from .artifact_routes import is_render_path as _is_artifact_render, make_router as artifact_router
from .artifacts import Artifacts
from .boards import Boards
from .canvas import FALLBACK_NAME, SNAP_MODES, WIDGET_KINDS, WINDOW_STATES, Canvases
from .dashboards import Dashboards, generate_recap, generate_summary, generate_widget_code
from .docs import Docs, unified_diff
from . import widget_spec
from . import cache as google_cache
from .google import Google, GoogleNotConnected, json_safe
from . import job_history, job_tools
from .jobs_policy import JobPolicy
from .jobs import (KINDS, PROPOSAL_STATUSES, Jobs, Proposals, Scheduler, local_tz_name, spent, valid_cron,
                   valid_tz)
from . import meeting_import, skillbuild, skillmd
from . import mail_edits  # noqa: F401 - mail_edits registers the gmail validators
from .mcp_client import MCP_DANGER, McpClient, McpError
from .mcp_oauth import CALLBACK_PATH as MCP_OAUTH_CALLBACK, OAuthFlows, OAuthStore
from .mcp_servers import MODES as MCP_MODES, RESERVED_PREFIX as MCP_PREFIX, SCOPES as MCP_SCOPES, McpServers
from .meeting_recorder import RecorderBusy
from .meetings import MeetingBlocked, Meetings, MeetingService
from .cowork import (AUTONOMY, DESK_HINT, DESK_NUDGE, DESK_PLAN_HINT, LIVE as DESK_LIVE, continue_message, desk_manual, read_notes,
                     STATUSES as DESK_STATUSES, UNDECIDED_OUTPUTS, DeskRuntime, Desks,
                     parked_report)
from .workspace import MAX_PREVIEW, Workspace, WorkspaceError
from .envs import WorkEnv
from .microvm import Sandboxes
from .notes import Notes
from .plans import (MUTATING, PLAN_BLOCKED, PLAN_SAFE_DANGER, PLAN_TOOL, PROPOSE_ONLY, Plans,
                    normalize_plan, parse_plan_edits, plan_voided_by_taint, taint_expected)
from .filesnap import FileSnapshots, router as filesnap_router
from .snapshots import Snapshots, router as snapshots_router
from .outbox import Outbox, router as outbox_router
from .setup import router as setup_router
from .reliability import router as reliability_router, secret_values
from .retention import RetentionWorker
from .presets import CanvasPresets
from . import resume
from . import permrules
from . import shell as shell_tool
from .subagents import AgentDefs, Subagents
from .commands import Commands
from .workflows import ApprovalError as WorkflowApprovalError, Engine as WorkflowEngine, Workflows
from .runs import ACTIVE, PROMOTE_STEP, STATUSES, Run, RunBus, RunStore, Topic, args_digest
from .toolcalls import ensure_unique_call_ids, parse_arguments, resolve_name
from .stuck import STUCK_NUDGE, STUCK_STOP, StuckDetector
from .style import WritingStyle, learn_style_from_exchange, looks_like_prose
from .modules import Module, ModuleContext, build_modules, get as module_get
from .modules.todos import TodosModule
from .tools import Toolbox, summarize_result
from .webread import WebCache
from .trash import Trash, router as trash_router
from .trace import Tracer, now_ms
from .usage import Pricing, Usage
from .working import Plans as WorkPlans, ToolResults

log = logging.getLogger("personal_os")

backups.apply_pending_restore(data_dir_from_env())  # a restore staged in Settings → Data swaps in before the file is opened
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
PUBLIC_PATHS = ("/health", "/integrations/google/callback", "/mcp/oauth/callback")


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


ARTIFACT_TOKEN_TTL = 24 * 3600


def _artifact_render_token(aid: str, exp: int) -> str:
    """Capability for one artifact's sandboxed iframe, which cannot send the app token. Binds id + expiry, signed
    with the app secret, so a render URL cannot be edited to point at another artifact or outlive its TTL."""
    return hmac.new(AUTH_TOKEN.encode(), f"artifact:{aid}:{exp}".encode(), "sha256").hexdigest()[:32]


def _artifact_render_ok(aid: str, rt: str, re_: str) -> bool:
    try:
        exp = int(re_)
    except (TypeError, ValueError):
        return False
    return bool(rt) and exp >= time.time() and _token_eq(rt, _artifact_render_token(aid, exp))


def _artifact_render_path(aid: str) -> str:
    exp = int(time.time()) + ARTIFACT_TOKEN_TTL
    return f"/artifacts/{aid}/render?re={exp}&rt={_artifact_render_token(aid, exp)}"


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
    if request.method == "OPTIONS" or p in PUBLIC_PATHS or _is_artifact_render(request.method, p):
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
bus.on_change = lambda run: events.publish("run_state", run.info())  # `events` is bound below; read at call time
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
consolidator = Consolidator(db, memories, graph)
title_jobs = titles.TitleJobs(convos.get, convos.update, events.publish)
learner = LearnWorker(memories=memories, graph=graph, set_trace=convos.set_trace, publish=events.publish, consolidator=consolidator,
                     alive=lambda cid: convos.get(cid, with_messages=False) is not None)


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

_MODULES_DEFAULT = 3
_DEFAULT_OFF_VIEWS = ("library", "cowork", "meetings", "activity")
_DEFAULT_OFF_HOME = ("cowork", "meetings")


def _seed_hidden_modules() -> None:
    """Hide the views that ship off. A later stamp only adds the views new in that stamp, so one the user turned back on stays on."""
    stored = db.get_settings()
    current = stored.get("modulesDefault") or 0
    if current == _MODULES_DEFAULT:
        return
    hidden = list(stored["hiddenViews"]) if isinstance(stored.get("hiddenViews"), list) else list(
        llm.DEFAULT_SETTINGS["hiddenViews"]
    )
    # Stamp 1 applied Library, Cowork and Meetings. Stamp 2 added Activity.
    # A later stamp must not put a view back that the user has since shown.
    add = ("activity",) if current == 1 else (() if current >= 2 else _DEFAULT_OFF_VIEWS)
    for v in add:
        if v not in hidden:
            hidden.append(v)
    widgets = dict(stored["homeWidgets"]) if isinstance(stored.get("homeWidgets"), dict) else {}
    if not current:
        for k in _DEFAULT_OFF_HOME:
            widgets.setdefault(k, False)
    # Stamp 3 turns the Today cowork card off once, even if an older install had it on.
    # The Today slider and Settings → Modules can turn it back on.
    if current < 3:
        widgets["cowork"] = False
    db.set_settings({"hiddenViews": hidden, "homeWidgets": widgets, "modulesDefault": _MODULES_DEFAULT})


_seed_hidden_modules()


def settings() -> dict[str, Any]:
    return {**llm.DEFAULT_SETTINGS, **db.get_settings()}


jobs = Jobs(db)
proposals = Proposals(db)
boards = Boards(db)
dashboards = Dashboards(db)
artifacts = Artifacts(db)
google = Google(settings, db.set_settings, cache_dir=db.data_dir)
app.include_router(setup_router(settings, db.set_settings, lambda: google.status()["connected"]))
# sid/wsid are defined further down, so the module context looks them up late.
modules: list[Module] = build_modules(ModuleContext(
    db=db, settings=settings, set_settings=db.set_settings, google=google,
    sid=lambda p: sid(p), wsid=lambda p: wsid(p), mcp=lambda: mcp))
_todos_module = module_get(modules, "todos", TodosModule)
todos, tasks_sync, todo_calendar = _todos_module.store, _todos_module.tasks_sync, _todos_module.calendar_mirror
for _m in modules:
    if (_r := _m.router()) is not None:
        app.include_router(_r)
# Soft delete: the DELETE routes below move things here, and /trash restores or erases them (trash.py).
trash = Trash(db, todos, docs)
app.include_router(trash_router(trash))
usage = Usage(db)
pricing = Pricing()
llm.caps_lookup = pricing.caps
app.include_router(otel_export.router(db, lambda: settings()))
compactor = compaction.Compactor(db)
app.include_router(compaction.router(compactor, convos, lambda: settings(),
                                     window_fn=lambda cfg, m: compaction.window_for(cfg, m, pricing.caps(m).get("max_input_tokens"))))


def _int_setting(cfg: dict[str, Any], key: str, default: int) -> int:
    try:
        return int(cfg.get(key, default))
    except (TypeError, ValueError):
        return default


# One message may fill at most this share of the context window: past it the row would be replayed
# on every later turn (the first user message always survives compaction) and the chat is unusable.
MESSAGE_WINDOW_FRACTION = 0.5


def _message_too_long(text: str, cfg: dict[str, Any]) -> str | None:
    """A plain sentence when `text` is over the per-message bound, else None. Mirrors lib/messageLimit.ts."""
    limit = int(_int_setting(cfg, "contextWindow", 128000) * 4 * MESSAGE_WINDOW_FRACTION)
    if len(text) <= limit:
        return None
    return (f"That message is about {len(text):,} characters. One message can hold {limit:,} with the current "
            "context window. Attach it as a file instead.")


def _record_usage(ev: dict[str, Any]) -> None:
    """llm.on_usage listener: persist one row per model call with a best-effort cost."""
    try:
        cfg = settings()
        pt, ct = int(ev.get("prompt_tokens") or 0), int(ev.get("completion_tokens") or 0)
        cached, cwrite, reasoning = int(ev.get("cached_tokens") or 0), int(ev.get("cache_write_tokens") or 0), int(ev.get("reasoning_tokens") or 0)
        usage.record(model=ev.get("model", ""), kind=ev.get("kind", "chat"), prompt_tokens=pt, completion_tokens=ct,
                     duration_ms=int(ev.get("duration_ms") or 0), cost=pricing.cost(cfg, ev.get("model", ""), pt, ct, cached, cwrite),
                     estimated=bool(ev.get("estimated")), conversation_id=ev.get("conversation_id"), project_id=ev.get("project_id"),
                     cached_tokens=cached, cache_write_tokens=cwrite, reasoning_tokens=reasoning)
    except Exception:  # noqa: BLE001 - accounting must never break a reply
        pass


if not any(getattr(f, "__name__", "") == "_record_usage" for f in llm._usage_listeners):
    llm.on_usage(_record_usage)


def _save_model_caps(caps: dict[str, Any]) -> None:
    """llm.on_caps listener: keep what a provider rejected for a model across restarts."""
    try:
        db.set_settings({"modelCaps": caps})
        events.publish("model_caps", {})
    except Exception:  # noqa: BLE001 - a lost hint is relearned from the next rejection
        pass


llm.load_caps(db.get_settings().get("modelCaps") or {})
if not any(getattr(f, "__name__", "") == "_save_model_caps" for f in llm._caps_listeners):
    llm.on_caps(_save_model_caps)
# A desk is one conversation plus one workspace plus one approved plan. The workspace is a plain
# directory per desk under the data dir, containment-checked after symlink resolution; Desks is the
# state machine and the timeline over it.
workspace = Workspace(db.data_dir)
work_env = WorkEnv(db.data_dir, lambda: settings())  # the shared work venv (envs.py); created on demand
desks = Desks(db, workspace)
_loop: asyncio.AbstractEventLoop | None = None


def _desk_changed(row: dict[str, Any]) -> None:
    """Every desk write lands on the app topic as `desk_status`, so the rail, the badge and the Today
    card stay live for desks nobody is watching. Topic queues belong to the event loop and sync routes
    run in a threadpool, so a call from another thread is handed to the loop."""
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if running is not None:
        events.publish("desk_status", row)
    elif _loop is not None and not _loop.is_closed():
        _loop.call_soon_threadsafe(events.publish, "desk_status", row)


desks.on_change = _desk_changed
# The supervisor task per live desk: it owns the CHAIN, not the turn in flight. Cancelling one ends
# the chain and leaves the running turn to settle cooperatively.
_desk_tasks: dict[str, asyncio.Task[None]] = {}
sandboxes = Sandboxes(settings, import_dir=db.data_dir / "sandbox-imports")
# A desk's container sees the desk's workspace at /workspace/desk (microvm.DESK_MOUNT); other chats mount nothing.
sandboxes.desk_workspace = lambda conv_id: (str(workspace.ensure(d["id"])) if (d := desks.by_conversation(conv_id)) else None)
monitor = activity.Monitor(db, settings, llm.complete)
# Every Gmail send is held here first so it can be undone (outbox.py); its own routes are included below.
outbox = Outbox(db, google, settings)
app.include_router(outbox_router(outbox))
# Pre-images of local files the agent overwrites or moves; the restore route is the user's, never a tool (filesnap.py).
filesnap = FileSnapshots(db, db.data_dir / "snapshots", settings)
app.include_router(filesnap_router(filesnap))
# Whole-folder snapshots per reply, so Undo can take back shell effects too (snapshots.py); user-only routes.
snaps = Snapshots(db, db.data_dir / "snapshots", settings, workspace.desk_root)
app.include_router(snapshots_router(snaps, lambda rid: run_store.get(rid) is not None))


async def _snapshot_after(run: Run) -> None:
    await asyncio.to_thread(snaps.finish, run.run_id)


bus.after_hooks.append(_snapshot_after)
app.include_router(backups.router(db.data_dir))
# Supportability: GET /diagnostics, POST /maintenance/sweep, and the daily retention sweep (retention.py).
retention = RetentionWorker(db, settings)
app.include_router(reliability_router(db, settings, retention, lambda: activity.permissions()))


@app.on_event("startup")
async def _reliability_startup() -> None:
    from . import logs
    for v in (AUTH_TOKEN, *secret_values(settings())):
        logs.register_secret(v)
    retention.start()


@app.on_event("shutdown")
async def _reliability_shutdown() -> None:
    await retention.stop()
# Working memory that is not the chat: the per-conversation plan, the full tool-result blobs behind
# their handles (working.py), and procedural memory awaiting review (learn.Skills). `work_plans` is the
# todo_write artifact and is a different thing from `plans`, the propose_plan approval record.
work_plans = WorkPlans(db)
tool_results = ToolResults(db)
skills = Skills(db)
# The repo first, then the service around it: both routes and the 45s tick read through one
# instance, so a meeting's rows are never written by two Meetings objects at once.
meeting_store = Meetings(db)
meeting_svc = MeetingService(db, settings, llm.complete, meeting_store, google=google, todos=todos, docs=docs)
# A doc that is purged (not trashed) takes its recordings with it: row, FTS entry and audio directory.
docs.on_delete = meeting_store.purge_doc
# A doc that changes project takes its recordings along, or project-scoped meeting search shows them under the old one.
docs.on_move = meeting_store.move_doc
toolbox = Toolbox(memories, graph, documents, settings, modules=modules, google=google, boards=boards, sandboxes=sandboxes, docs=docs, activity=monitor,
                  outbox=outbox, work_plans=work_plans, results=tool_results, skills=skills, jobs=jobs,
                  style=style, meetings=meeting_svc, desks=desks, workspace=workspace, filesnap=filesnap, artifacts=artifacts,
                  conversations=convos)
# Hybrid retrieval over uploaded documents. Uploads embed in the background; with no embedding route
# every search is the old BM25 one.
embedder = Embedder()
retriever = Retriever(db, documents, embedder, docs=docs)
memory_index = MemoryIndex(db, memories, graph, embedder)
learner.index = memory_index
documents.on_chunks = lambda did, _rows: retriever.schedule(settings, did)
docs.on_chunks = lambda _did: retriever.schedule_docs(settings)


@app.on_event("startup")
async def _start_retrieval() -> None:
    retriever.bind_loop(asyncio.get_running_loop())
    retriever.schedule(settings)  # embed whatever is still waiting; silent when the route is down
    retriever.schedule_docs(settings)
toolbox.retriever = retriever
toolbox.memory_index = memory_index
toolbox.plans = plans  # desk_done's gate reads the approved plan's unconsumed steps
toolbox.work_env = work_env  # python_install and run_python find the shared work venv here
toolbox.web_cache = WebCache(db)  # fetch_url's response cache
# Subagents: child runs the agent_spawn tools start. Approval cards a child raises resolve through the
# same _approvals futures a chat's do.
agent_defs = AgentDefs(db)
subagent_mgr = Subagents(run_store, toolbox, settings, defs=agent_defs, results=tool_results, pricing=pricing, memories=memories,
                         projects=projects, workspace=workspace, approvals=_approvals)
toolbox.subagents = subagent_mgr
subagent_mgr.snaps = snaps
# Workflows (workflows.py) and commands (commands.py): saved definitions, approved by hash before a run starts.
workflow_store = Workflows(db, lambda: set(toolbox.specs), lambda n: subagent_mgr.role_for(n) is not None)
workflow_engine = WorkflowEngine(workflow_store, toolbox, subagent_mgr, run_store, settings, projects)
command_store = Commands(db)
toolbox.workflows, toolbox.workflow_engine, toolbox.commands = workflow_store, workflow_engine, command_store
# The insights pass proposes automations, so it is told which tools this install actually has - an
# unwired integration must not turn into a suggestion that cannot be carried out.
monitor.insights.tools_fn = lambda: [t["name"] for t in toolbox.list() if t.get("available")]
mcp_store = McpServers(db)
# Third-party servers are supervised, not owned by the chat loop: a wedged server must not be able
# to hold a reply, so everything it offers goes through McpClient's bounded calls.
# Remote servers sign in with OAuth; tokens live in their own table, never in a server's secrets.
mcp_oauth = OAuthFlows(OAuthStore(db))
mcp = McpClient(mcp_store, oauth=mcp_oauth)


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
    for tool in mcp_drift.offerable(mcp_store.tools()):  # a quarantined (drifted) tool is not offered
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


def _gate(name: str, mode: str, ctx: dict[str, Any], args: dict[str, Any] | None = None) -> str:
    """Effective mode for one call. Untrusted content in the run forces every external tool to ask.

    Every MCP tool is `external` by construction (mcp_client.MCP_DANGER), so the taint rule the
    Toolbox applies to built-ins has to apply to them too - they are not in Toolbox.specs.
    """
    if mcp_is(name):
        return "ask" if mode == "on" and ctx.get("tainted") else mode
    return toolbox.gate(name, mode, ctx, args)


# The note a user typed with a denial, handed from POST /approvals to the run waiting on that call.
_approval_notes: dict[str, str] = {}
# Run kinds that have nobody at the keyboard; with unattendedApprovals = "deny" a call that would ask is refused.
UNATTENDED_KINDS = ("job", "scheduled")


def _perm_roots(cfg: dict[str, Any], desk_id: str | None) -> list[str]:
    """Folders a shell or file call counts as inside: the granted roots plus the active desk's workspace."""
    roots = [r for r in (cfg.get("workspaceRoots") or []) if isinstance(r, str) and r]
    if desk_id:
        roots.append(str(workspace.desk_root(desk_id)))
    return roots


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
    every = mcp_store.tools()
    return {**row,
            "live": {"status": live.get("status", row["status"]), "detail": live.get("detail", row["status_detail"]),
                     "running": bool(live.get("running")), "ready": bool(live.get("ready")),
                     "attempts": live.get("attempts", 0), "server_info": live.get("server_info") or {}},
            "tools": [{**t, "effective": mcp_store.effective_mode(t["slug"]), "drift": mcp_drift.view(mcp_store, t, every)}
                      for t in mcp_store.tools(row["id"], include_missing=True)],
            "eval": mcp_store.latest_eval(row["id"]),
            "signed_in": mcp_oauth.store.signed_in(row["id"]) if row.get("transport") == "http" else None}


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


def _clamp(limit: int, hi: int = 500) -> int:
    """A list route's LIMIT. SQLite reads a negative one as 'no limit', so it is bounded here, not trusted."""
    return max(1, min(int(limit), hi))


def sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


# ---------------- health / settings / models ----------------
@app.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True}


# Google OAuth material lives in settings but never leaves the backend.
PRIVATE_SETTINGS = {"googleToken", "googleAuthPending", "modelCaps"}
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
    "subagentMaxConcurrent": (1, 20),
    "subagentMaxDepth": (0, 3),
    "subagentMaxRounds": (1, 60),
    "subagentMaxCost": (0, 100),
    "subagentStaleSeconds": (0, 86_400),
    "subagentToolSeconds": (0, 86_400),
    "fileSnapshotMaxBytes": (0, 100_000_000),
    "fileSnapshotRetainDays": (1, 365),
    "fileSnapshotBudgetMB": (1, 20_000),
    "llmRetries": (0, 10),
    "llmIdleSeconds": (10, 3_600),
    "retainUsageDays": (7, 3_650),
    "contextWindow": (1000, 4_000_000),
    "compactAt": (0.1, 0.95),
    "microAt": (0.05, 0.95),
    "compactKeepRecent": (2, 200),
    "microKeep": (0, 50),
    "retainTraceDays": (1, 3_650),
    "retainToolResultDays": (1, 3_650),
    "retainApprovalDays": (1, 3_650),
    "browserMaxTabs": (1, 12),
    "browserIdleSeconds": (30, 86_400),
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


def _check_permission_rules(v: Any) -> dict[str, list[str]]:
    """The permissionRules setting: three lists of well-formed rule strings, nothing else."""
    if not isinstance(v, dict):
        raise HTTPException(422, "permissionRules must be {allow, ask, deny}")
    out: dict[str, list[str]] = {}
    for key in ("allow", "ask", "deny"):
        items = v.get(key) or []
        if not isinstance(items, list):
            raise HTTPException(422, f"permissionRules.{key} must be a list")
        try:
            out[key] = list(dict.fromkeys(permrules.parse_rule(str(t)).text for t in items))
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
    return out


@app.put("/settings")
def put_settings(patch: dict[str, Any]) -> dict[str, Any]:
    clean = {k: v for k, v in patch.items()
             if k in llm.DEFAULT_SETTINGS and k not in PRIVATE_SETTINGS and k not in SETTINGS_READ_ONLY}
    for k, v in clean.items():
        d = llm.DEFAULT_SETTINGS[k]
        if isinstance(d, (int, float)) and not isinstance(d, bool):
            clean[k] = _check_numeric_setting(k, v)
        elif k not in SECRET_SETTINGS and isinstance(d, (dict, list, str, bool)) and not isinstance(v, type(d)):
            # (A secret takes null to clear it; its own check below.)
            # Stored as given, a wrong-typed value (tools: "x", systemPrompt: null) 500s every route that reads it.
            raise HTTPException(422, f"{k} must be a {type(d).__name__}")
        elif k == "permissionRules":
            clean[k] = _check_permission_rules(v)
        elif k == "unattendedApprovals" and v not in ("ask", "deny"):
            raise HTTPException(422, "unattendedApprovals must be 'ask' or 'deny'")
        elif k == "workspaceRoots":
            if not (isinstance(v, list) and all(isinstance(x, str) for x in v)):
                raise HTTPException(422, "workspaceRoots must be a list of folders")
            # The file tools only work inside the home folder and outside hidden folders and ~/Library. A root they
            # would refuse is rejected here rather than stored and then silently ignored.
            for root in v:
                try:
                    mac.allowed_path(root)
                except mac.LocalPathError as e:
                    raise HTTPException(422, f"{root} cannot be a workspace folder: {e}") from e
    for k in SECRET_SETTINGS:
        if k in clean and clean[k] == "":  # blank means "unchanged" (the form never holds the saved key); null clears
            del clean[k]
        elif k in clean and clean[k] is not None and not isinstance(clean[k], str):
            raise HTTPException(422, f"{k} must be a string or null")
    db.set_settings(clean)
    return public_settings()


@app.get("/models")
async def models() -> list[dict[str, Any]]:
    try:
        cfg = settings()
        await pricing.refresh(cfg)
        return await llm.list_models(cfg)
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
    capabilities: list[str] | None = None  # ['page', 'browser']; omitted by an older main = the page loader only


@app.post("/bridge/page")
def register_page_bridge(body: PageBridgeIn) -> dict[str, Any]:
    """The Electron main process says where its offscreen page loader listens (open_page). Re-sent periodically."""
    try:
        mac.page_bridge.register(body.url, body.token, body.capabilities)
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
    mcp_oauth.store.forget(id)
    await mcp.sync()  # stops and reaps the child process; grants survive, keyed by slug
    return {"ok": True}


@app.post("/mcp/servers/{id}/restart")
async def mcp_restart_server(id: str) -> dict[str, Any]:
    if mcp_store.server(id) is None:
        raise HTTPException(404, "No such MCP server")
    await mcp.restart(id)
    return _mcp_server_view(mcp_store.server(id) or {})


@app.post("/mcp/servers/{id}/sign-in")
async def mcp_sign_in(id: str, request: Request) -> dict[str, Any]:
    """Start a browser sign-in for a remote server. The renderer opens `auth_url`, then polls GET."""
    if mcp_store.server(id) is None:
        raise HTTPException(404, "No such MCP server")
    # The registered redirect is this backend's own loopback port, so it must be the port we listen on.
    redirect = f"http://127.0.0.1:{request.url.port or 80}{MCP_OAUTH_CALLBACK}"
    try:
        return await mcp.sign_in(id, redirect)
    except McpError as e:
        raise HTTPException(400, str(e)) from e


@app.get("/mcp/servers/{id}/sign-in")
def mcp_sign_in_status(id: str) -> dict[str, Any]:
    if mcp_store.server(id) is None:
        raise HTTPException(404, "No such MCP server")
    return mcp_oauth.status(id)


@app.delete("/mcp/servers/{id}/sign-in")
async def mcp_sign_out(id: str) -> dict[str, Any]:
    if mcp_store.server(id) is None:
        raise HTTPException(404, "No such MCP server")
    mcp_oauth.store.forget(id)
    await mcp.restart(id)
    return mcp_oauth.status(id)


@app.get(MCP_OAUTH_CALLBACK, response_class=HTMLResponse)
def mcp_oauth_callback(state: str = "", code: str = "", error: str = "", error_description: str = "") -> str:
    """Public (the browser has no token): it can only complete a sign-in this app started, by its state."""
    if not mcp_oauth.complete(state, code or None, error_description or error or None):
        return _oauth_page("Sign-in link expired", "Start the connection again from Grain.")
    if error or not code:
        return _oauth_page("Sign-in was not completed", html.escape(error_description or error or "No authorization code was returned."))
    return _oauth_page("Connected ✓", "You can close this tab and return to Grain.", ok=True)


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
                       "effective": mcp_store.effective_mode(t["slug"], sid(project_id), conversation_id),
                       "drift": mcp_drift.view(mcp_store, t, rows)}
                      for t in rows],
            "grants": mcp_store.grants()}


@app.post("/mcp/tools/{slug}/accept")
def mcp_accept_change(slug: str) -> dict[str, Any]:
    """The user read the diff. Releases a quarantined tool; its grant is untouched, so an 'on' stays 'ask'."""
    if mcp_drift.accept(mcp_store, slug) is None:
        raise HTTPException(404, "No such MCP tool")
    return mcp_store.effective_mode(slug)


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
async def _trash_startup() -> None:
    app.state.trash_task = asyncio.create_task(trash.loop(), name="trash-purge")


@app.on_event("shutdown")
async def _trash_shutdown() -> None:
    t = getattr(app.state, "trash_task", None)
    if t:
        t.cancel()


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
async def delete_project(id: str) -> dict[str, Any]:
    # async so each run's stop Event is set on the loop that owns it. Stop is cooperative: the replies wind down and
    # persist what they wrote, and the chats are still there to restore.
    stopped = sum(1 for c in convos.list(id, include_jobs=True, include_desks=True) if bus.stop(c["id"]))
    trash.trash("project", id)  # its chats, memories and uploads go to the trash; docs and todos are demoted to personal
    canvases.delete_windows_for("project", id)  # ref_id has no foreign key: a deleted referent's windows are swept here
    return {"ok": True, "stopped": stopped}


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
    pinned: bool | None = None
    archived: bool | None = None
    # null means Personal, so a move is detected by the key being present (model_fields_set).
    project_id: str | None = None


@app.get("/conversations")
def list_conversations(project_id: str | None = None, include_jobs: bool = False,
                       include_desks: bool = False, archived: bool = False) -> list[dict[str, Any]]:
    """A scheduled job's own transcripts are left out unless asked for: the Agent Inbox is their
    index, and Cowork is a desk's. Both stay reachable through their own flag."""
    return convos.list(sid(project_id), include_jobs, include_desks, archived)


@app.post("/conversations")
def create_conversation(body: ConvIn) -> dict[str, Any]:
    return convos.create(wsid(body.project_id), body.title, body.model or settings()["defaultModel"])


@app.get("/conversations/search")
def search_conversations(q: str = "", limit: int = 20) -> list[dict[str, Any]]:
    """Full-text search over what was said. Declared before `/conversations/{id}` so "search" is not an id."""
    if len(q.strip()) < 2:
        return []
    return convos.search(q, max(1, min(limit, 50)))


@app.get("/conversations/{id}")
def get_conversation(id: str) -> dict[str, Any]:
    c = convos.get(id)
    if not c:
        raise HTTPException(404)
    return c


@app.patch("/conversations/{id}")
def patch_conversation(id: str, body: ConvPatch) -> dict[str, Any]:
    patch = body.model_dump(exclude_none=True)
    moving = "project_id" in body.model_fields_set
    patch.pop("project_id", None)
    if moving or body.archived:
        row = convos.get(id, with_messages=False)
        if not row:
            raise HTTPException(404)
        # live, not answering: the run reads the project for context and connector tooling, and its
        # learn tail writes with it; a run parked on an approval must not be hidden either.
        if bus.live(id):
            raise HTTPException(409, "Stop the running reply first")
        if moving:
            if row["settings"].get("deskId") or row["settings"].get("job_id"):
                raise HTTPException(409, "A desk or job transcript cannot be moved")
            patch["project_id"] = wsid(body.project_id)
    settings_patch = patch.get("settings") if isinstance(patch.get("settings"), dict) else {}
    # Clearing the banner has to drop library text that was copied into the sandbox, or the next
    # command can print it back as if the chat were trusted again.
    if settings_patch.get("tainted") is False and sandboxes.holds_import(id):
        sandboxes.reset(id)
    new_title = (patch.get("title") or "").strip()
    if new_title:
        cur = convos.get(id, with_messages=False)
        # Only a changed title is the user's: the header input sends its value on every blur, edited or not.
        if cur and new_title != cur["title"]:
            patch["settings"] = {**settings_patch, "titleSource": "user"}
            title_jobs.cancel(id)
    c = convos.update(id, patch)
    if not c:
        raise HTTPException(404)
    return c


@app.post("/conversations/{id}/title")
async def retitle_conversation(id: str) -> dict[str, Any]:
    """Regenerate the title on request, from the user's messages only. Replaces a typed title too: it was asked for."""
    c = convos.get(id)
    if not c:
        raise HTTPException(404)
    texts = [m["content"] for m in c["messages"] if m["role"] == "user"]
    cfg = settings()
    try:
        new = await titles.generate(cfg, c["model"] or cfg["defaultModel"], titles.pick_texts(texts))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"Could not generate a title: {e}") from None
    if not new:
        raise HTTPException(502, "The model returned no usable title.")
    title_jobs.cancel(id)
    out = convos.update(id, {"title": new, "settings": {"titleSource": "auto", "titleTurns": len(texts)}})
    if not out:
        raise HTTPException(404)
    events.publish("conversation_changed", {"id": id, "title": new})
    return out


@app.delete("/conversations/{id}")
async def delete_conversation(id: str) -> dict[str, Any]:
    # Stop the live reply first (async, so its Event is set on the owning loop): a trashed chat must not keep calling
    # tools, waiting on a card or feeding auto-learn. Idempotent: an already-trashed or idle chat answers ok.
    stopped = bus.stop(id)
    trash.trash("conversation", id)
    canvases.delete_windows_for("chat", id)
    return {"ok": True, "stopped": stopped}


@app.delete("/conversations/{id}/messages/{mid}")
async def delete_message(id: str, mid: str) -> dict[str, bool]:
    if not convos.get(id, with_messages=False):
        raise HTTPException(404, "No such conversation")
    if (run := bus.answering(id)) is not None:
        raise HTTPException(409, {"message": "That conversation has a running reply", "run_id": run.run_id, "seq": run.seq})
    # The path's conversation must own the message: this used to delete it from whichever conversation held it.
    if not convos.delete_message(mid, id):
        raise HTTPException(404, "No such message in this conversation")
    return {"ok": True}


@app.post("/conversations/{id}/messages/{mid}/activate")
def activate_message(id: str, mid: str) -> dict[str, Any]:
    """Switch the trailing answer to another regenerate variant. Flips flags only: no tool runs, no approval is touched."""
    if not convos.get(id, with_messages=False):
        raise HTTPException(404, "Conversation not found")
    if bus.answering(id):
        raise HTTPException(409, "A reply is in progress")
    if not convos.activate_message(id, mid):
        raise HTTPException(409, "That answer is not a variant of the latest reply")
    return convos.get(id)  # type: ignore[return-value]


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
    resume_of: str | None = None  # run_id of an interrupted run this reply continues (POST /runs/{id}/resume)
    replace_from: str | None = None  # id of an earlier user message this one replaces: it and everything after it are hidden


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
- ```html — a self-contained HTML document or fragment (inline CSS/JS, no network, no external files). It is shown as a sandboxed live preview with a Code/Preview toggle and a "Save as artifact" button. Use it for a mock-up, a small interactive demo or a formatted layout. ```svg renders as an image.
For anything larger or that the user will keep and revise (a calculator, a dashboard-like page, a game, a formatted report), call artifact_create with the full HTML instead; change it with artifact_edit (exact search/replace pairs) for small fixes, or artifact_update (full new HTML) when most of it changes; the chat shows it as a live card and keeps every version. Both run in a sandbox with no network, no external scripts/fonts/images (use data: URIs or inline SVG), no localStorage and no form submits.
Maths renders when written inline as `$...$` and as a display block with `$$` on its own lines; do not use `\\(` `\\)` or `\\[` `\\]`.
Only chart real values you have or computed; never invent data for decoration. Text before and after a block is shown as usual."""

TOOLS_HINT = "You have tools. Use them when they would make the answer more accurate or current; otherwise answer directly. After using tools, write the final answer for the user."
# Only added when todo_write is actually available in this chat (see _chat_stream).
PLAN_HINT = ("When a request needs more than a couple of tool calls, open with todo_write to lay out the steps, then update it "
             "as each one lands. Your current plan is re-sent to you at the end of every round, so it — not your memory of "
             "earlier rounds — is what keeps a long task on track.")
# Plan mode in an ordinary chat (conv.settings.planMode, else settings.planMode). 'always' starts every
# reply drafting; 'auto' starts it the first time the reply reaches for a consequential tool.
CHAT_PLAN_HINT = ("## Plan mode is on\nBefore anything that changes something (writes, sends, creates, deletes, runs code), "
                  "call propose_plan with the exact calls you intend to make and wait for the user's answer. Reading and "
                  "searching are fine without a plan. Once a plan is approved, make each approved call exactly once with "
                  "exactly its arguments.")
# Groups whose `writes` tools are the assistant's own bookkeeping rather than a change the user would
# want to approve, so they never trip 'auto' plan mode.
PLAN_AUTO_EXEMPT_GROUPS = ("plan", "memory", "graph", "style")


def _today_hint() -> str:
    """Local date, weekday and the week ahead. Day-granular on purpose: a clock here would change the system prompt
    every minute and defeat the provider's prompt cache. current_time is there for the time of day."""
    now = datetime.now().astimezone()
    week = ", ".join(f"{d:%a} {d:%Y-%m-%d}" for d in (now + timedelta(days=i) for i in range(1, 8)))
    return (f"## Today\nToday is {now:%A %Y-%m-%d}, time zone {now:%Z} (UTC{now:%z}). Next 7 days: {week}. "
            "Use this for dates; do not compute weekdays in code. Calendar times are local: YYYY-MM-DDTHH:MM, no offset.")


BUDGET_STOP = ("Out of budget ({axis}): this tool call was not executed and no further tool calls will run. "
               "Write the best final answer you can from what you already have, and say in one line what is still missing.")
TIME_STOP = ("Out of budget (time): no further tool calls will run. "
             "Write the best final answer you can from what you already have, and say in one line what is still missing.")
EMPTY_NUDGE = ("Your last turn ended without any text and without a tool call. Answer the user now in plain text, "
               "or say in one line what you did and what is still missing.")
SOFT_NUDGE = ("Budget check: about {pct}% of this reply's budget is used. "
              "Make at most one or two more tool calls, then write the final answer.")
LOOP_STOP = ("{name} has been called with identical arguments {n} times in a row, so this reply is stopping tool use. "
             "Answer with what you already have, and say in one line what you could not finish.")
CUT_CALL = ("the arguments were cut off at the model's output limit and the call was not run; "
            "send a smaller call or split the content")
REPEAT_LIMIT = 5
TOOL_ERROR_LIMIT = 3


# Run kinds that may not complete an outward-facing side effect. A scheduled job proposes; the user executes.
PROPOSAL_ONLY_KINDS = ("job",)
# Caps for an unattended run, applied on top of the user's settings and only downward (see _caps). Tighter than
# interactive on purpose: nobody is watching, and a longer leash makes the answer worse, not better.
# A model call that is still open after this long while writing the closing answer is abandoned.
EFFORT_DROPPED_NOTICE = "This model does not accept a reasoning effort; it was sent without one."
FINAL_ROUND_SECONDS = 90.0
# Hard ceiling on an unattended run end to end (model, tools, everything), a backstop for a hang the budget cannot see.
JOB_HARD_SECONDS = 1800.0
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

    def arm_deadline(self, floor: float = 5.0, cap: float | None = None) -> None:
        """Bound the next provider stream by what is left of the wall-clock budget (llm.stream_deadline), so a hung
        provider cannot outlive maxRunSeconds. Unlimited (0) leaves it unbounded unless `cap` says otherwise."""
        left = self.max_seconds - self.elapsed() if self.max_seconds > 0 else None
        if cap is not None:
            left = cap if left is None else min(left, cap)
        llm.stream_deadline.set(None if left is None else time.monotonic() + max(left, floor))

    def snapshot(self) -> dict[str, Any]:
        """What agent_runs.budget stores: the limits and how much of each the run has used."""
        return {"max_rounds": self.max_rounds, "max_tokens": self.max_tokens, "max_seconds": self.max_seconds,
                "max_cost": self.max_cost, "rounds": self.rounds, "tokens": self.tokens, "cost": round(self.cost, 6),
                "seconds": round(self.elapsed(), 3), "paused_seconds": round(self.paused, 3)}


def _invalid_call_error(c: dict[str, Any], spec: Any) -> dict[str, Any]:
    """The tool result for a call `_normalise_call` refused: the parse error, a sample of what was sent and the
    signature to follow, or the unknown-name text with the nearest tools. Capped, it rides in the transcript."""
    problem = str(c["_problem"])[:500]
    if c["_invalid"] == "name":
        return tools.tool_error(problem, alternative="call one of the tools listed in this request, by its exact name")
    asked = c["_asked"] or c["name"]
    if problem == CUT_CALL:
        return tools.tool_error(f"{asked}: {CUT_CALL}"[:500], alternative=tools.ALTERNATIVE.get(asked))
    err = tools.tool_error(f"{asked}: the arguments were not valid JSON ({problem})"[:500],
                           expected=("required: " + (", ".join(spec.parameters.get("required") or []) or "none")) if spec else None,
                           example=(spec.examples or [None])[0] if spec else None,
                           alternative=tools.ALTERNATIVE.get(asked))
    err["sent"] = str(c.get("_raw") or "")[:200]
    return err


def _normalise_call(c: dict[str, Any], known: set[str], advertised: list[str], cut: bool) -> None:
    """Settle one streamed call in place, once, before anything reads it. `name` becomes the resolved tool (or
    'invalid_tool', the original in `_asked`); `_args` is the parsed object ({} for a failed call); `_repaired` and
    `_problem` say what happened, and `_invalid` is 'name' or 'arguments' when the call may not run. `arguments` is
    rewritten only for a repaired call (its JSON) or a failed one ('{}'), so a valid call replays byte for byte."""
    raw = c.get("arguments") or ""
    c["_raw"] = raw
    args, repaired, problem = parse_arguments(raw)
    name, renamed, name_problem = resolve_name(str(c.get("name") or ""), known, advertised)
    c["_asked"] = c.get("name") or ""
    c["_repaired"] = repaired or renamed
    c["_problem"] = None
    c["_invalid"] = None
    if name_problem:
        c["name"], c["_problem"], c["_invalid"] = "invalid_tool", name_problem, "name"
    else:
        c["name"] = name
        if args is None:
            c["_problem"], c["_invalid"] = (CUT_CALL if cut else problem), "arguments"
    if c["_invalid"]:
        c["_args"], c["arguments"], c["_repaired"] = {}, "{}", False
    else:
        c["_args"] = args or {}
        if repaired:
            c["arguments"] = json.dumps(c["_args"], ensure_ascii=False)


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
    if run is not None and snaps.wants(name, args, run.desk_id):
        await asyncio.to_thread(snaps.before, run.run_id, snaps.roots_for_call(name, args, run.desk_id))
    if run is None or run.store is None or spec is None or spec.danger not in IDEMPOTENT_DANGER:
        return await toolbox.call(name, args, ctx)
    result, replayed = await run.store.call_once(run.run_id, step, name, args, lambda: toolbox.call(name, args, ctx), call_id=call_id,
                                                  inherit=(run.input or {}).get("resume_of"))
    if replayed:
        if isinstance(result, dict):
            result = {**result, "replayed": True}
        if spec.taints and not (isinstance(result, dict) and result.get("error")):
            ctx["tainted"] = True
    return result


# How long a write that Stop caught mid-call is given to report its real outcome before it is cancelled.
STOP_GRACE_SECONDS = 3.0


async def _await_tool(coro: Any, stop: asyncio.Event, *, grace: float) -> tuple[Any, bool]:
    """Await a tool call, but let Stop cut it short. Returns (result, interrupted).

    Stop gives the call `grace` seconds to finish on its own (a write milliseconds from done then reports what
    happened), then cancels it and waits at most 2 s for its cleanup (a foreground shell kills its process group on
    cancel). A thread-backed body cannot be killed: its await is abandoned and it runs out its own limit in the
    background. If the run itself is cancelled (shutdown) the call is cancelled and awaited the same way before the
    cancel propagates, so cleanup handlers still run. The call's exception, if any, is always retrieved."""
    if stop.is_set():
        coro.close()  # Stop already landed (during the approval wait, or while the card event went out): never start it
        return None, True
    task = asyncio.ensure_future(coro)
    waiter = asyncio.ensure_future(stop.wait())
    try:
        await asyncio.wait({task, waiter}, return_when=asyncio.FIRST_COMPLETED)
        if not task.done():  # Stop fired first
            if grace > 0:
                await asyncio.wait({task}, timeout=grace)
            if not task.done():
                task.cancel()
                await asyncio.wait({task}, timeout=2)
                if not task.done():  # abandoned (thread-backed): whatever it ends with later is still retrieved
                    task.add_done_callback(lambda t: t.cancelled() or t.exception())
                return None, True
        return task.result(), False
    except asyncio.CancelledError:
        task.cancel()
        await asyncio.wait({task}, timeout=2)
        if not task.done():
            task.add_done_callback(lambda t: t.cancelled() or t.exception())
        raise
    finally:
        waiter.cancel()
        if task.done() and not task.cancelled():
            task.exception()  # retrieved, so an abandoned failure is not logged as never awaited


def _urls(text: str) -> set[str]:
    """URLs the user typed this turn: still fetchable, whole, once the reply has read untrusted content."""
    return {u for u in (m.rstrip(".,;:!?") for m in re.findall(r"https?://[^\s<>\"')]+", text or "")) if u}


def _short(args: dict[str, Any], limit: int = 300) -> dict[str, Any]:
    """Tool arguments for the trace: long strings (code, content) truncated."""
    return {k: (v[:limit] + "…" if isinstance(v, str) and len(v) > limit else v) for k, v in args.items()}


def _title_from(text: str) -> str:
    return titles.placeholder(text)


def _replay_args(raw: str | None) -> str:
    """Arguments to echo back in the assistant tool_calls turn. A provider validates this string as JSON, so a
    truncated one (finish_reason=length) must not be replayed or the next round dies with a 400."""
    try:
        args = json.loads(raw or "{}")
    except ValueError:
        return "{}"
    return raw or "{}" if isinstance(args, dict) else "{}"


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
    regen_am: dict[str, Any] | None = None  # set when a regenerate superseded the trailing answer
    placeholder_title: str | None = None  # set when this turn wrote the instant title; the model title replaces it
    carried_root: str | None = None

    if body.content is not None:
        user_text = body.content.strip()
        if not user_text:
            yield "error", {"message": "Empty message"}
            return
        edited_from: str | None = None
        had_writes = False
        if body.replace_from:
            # Edit and resend: the cut happens here, inside the run, so a refused request (409) can never leave
            # a half-applied cut. Rows are hidden, not deleted; state derived from them is invalidated with them.
            cut = convos.supersede_from(conv_id, body.replace_from)
            if not cut:
                yield "error", {"message": "That message can no longer be edited"}
                return
            edited_from = body.replace_from
            hidden = {r["id"] for r in cut}
            for r in cut:
                yield "removed_message", {"id": r["id"]}
            cut_at = cut[0]["created_at"]
            summary = compactor.get(conv_id)
            if summary and (summary["upto_message_id"] in hidden or summary["upto_created"] >= cut_at):
                compactor.clear(conv_id)
            wp = work_plans.get(conv_id)
            if wp and wp["updated_at"] >= cut_at:
                work_plans.clear(conv_id)
                yield "plan", {"conversation_id": conv_id, "steps": []}
            for a in run_store.approvals("pending", limit=500):
                if a["message_id"] in hidden:
                    if a["tool"] == PLAN_TOOL:
                        plans.decide(a["call_id"], "deny", by="stop", note="The message this plan was proposed for was edited.")
                    run_store.decide(a["call_id"], "deny", by="superseded", note="The message this call belonged to was edited.")
            # Connector tools are not in toolbox.specs but are external by construction (as the tool loop treats them).
            ran = [str(te.get("name") or "") for r in cut for te in r["tool_events"] if not te.get("pending")]
            had_writes = any((toolbox.specs[n].danger if n in toolbox.specs else (MCP_DANGER if mcp_is(n) else "safe")) in MUTATING
                             for n in ran)
            first_user = next((m for m in conv["messages"] if m["role"] == "user"), None)
            conv = {**conv, "messages": [m for m in conv["messages"] if m["id"] not in hidden]}
            # Cutting the first message re-titles the chat below, unless the user renamed it (an auto title is derived).
            if first_user and first_user["id"] in hidden and conv["title"] == _title_from(first_user["content"]):
                convos.update(conv_id, {"title": "New chat"})
                conv = {**conv, "title": "New chat"}
        um = convos.add_message(conv_id, "user", user_text)
        yield "user_message", {**um, **({"edited_from": edited_from, "had_writes": had_writes} if edited_from else {})}
        if conv["title"] == "New chat" and not [m for m in conv["messages"] if m["role"] == "user"]:
            title = _title_from(user_text)
            convos.update(conv_id, {"title": title})
            placeholder_title = title
            yield "title", {"id": conv_id, "title": title}
    elif body.resume_of:
        # resume: the salvaged reply of the dead run stays visible as history; this reply continues after it
        users = [m for m in conv["messages"] if m["role"] == "user"]
        if not users:
            yield "error", {"message": "Nothing to resume"}
            return
        user_text = users[-1]["content"]
    else:
        # regenerate: the trailing answer is superseded, not deleted, so it survives a failed or stopped replacement
        msgs = conv["messages"]
        users = [m for m in msgs if m["role"] == "user"]
        if not users:
            yield "error", {"message": "Nothing to regenerate"}
            return
        if msgs and msgs[-1]["role"] == "assistant":
            last = msgs[-1]
            if last.get("content") or last.get("tool_events"):
                regen_am = convos.begin_variant(last["id"], model)
            else:
                # An empty row is a failed earlier attempt: drop just that row and keep its group for the new one.
                with db.tx() as c:
                    c.execute("DELETE FROM messages WHERE id=?", (last["id"],))
                carried_root = last.get("variant_of")
            yield "removed_message", {"id": last["id"]}
        user_text = users[-1]["content"]

    tracer = Tracer()
    llm.usage_context.set({"conversation_id": conv_id, "project_id": conv["project_id"]})
    await pricing.refresh(cfg)
    project = projects.get(conv["project_id"]) if conv["project_id"] else None
    cspan = tracer.start("context", "Assemble context", {"model": model})
    doc_hits = await _doc_hits(conv["project_id"], user_text, cfg, conv["settings"])
    system, used = build_context(
        memories=memories, graph=graph, documents=documents, doc_hits=doc_hits,
        memory_hits=await _memory_hits(conv["project_id"], user_text, cfg, conv["settings"]),
        project=project, project_id=conv["project_id"], query=user_text,
        settings=cfg, conv_settings=conv["settings"], global_system_prompt=cfg["systemPrompt"],
        activity=monitor, skills=skills, style=style, meetings=meeting_svc,
        page=body.page_context.model_dump() if body.page_context else None,
    )
    # Older messages are folded into a rolling summary when the replay outgrows the window (compaction.py).
    # The window is this model's: the global setting, what the proxy reports, and what an overflow taught us.
    win = compaction.window_for(cfg, model, pricing.caps(model).get("max_input_tokens"))
    try:
        history, cinfo = await compaction.prepare_history(compactor, convos, cfg, str(cfg.get("extractionModel") or model), conv_id,
                                                          used["tokens_estimate"], window=win, cancel=stop)
    except llm.LLMError:
        if not stop.is_set():
            raise
        # Stopped while the history was being summarized: no reply row exists yet, so there is nothing to persist.
        yield "done", {"id": None, "error": None, "context_used": None, "tool_events": [], "trace": tracer.spans, "stopped": True,
                       "partial": None, "segment": False, "tainted": bool(conv["settings"].get("tainted")), "taint_sources": [],
                       "reasoning": None, "outcome": "stopped", "error_kind": None}
        return
    tracer.end(cspan, {"memories": len(used["memories"]), "entities": len(used["nodes"]), "excerpts": len(used["chunks"]),
                       "history_messages": len(history)})
    compact_span: dict[str, Any] | None = None
    if cinfo.get("compacted"):
        compact_span = tracer.start("compact", "Compact history", {"kind": "history"}, parent=cspan)
        tracer.end(compact_span, {k: cinfo[k] for k in ("tokens_before", "tokens_after", "summarized")})

    # Everything between creating the assistant row and the reply loop can raise (connector lookup, schemas, plans,
    # budget). Without this the row stays blank with no error and its _active entry leaks.
    am: dict[str, Any] = {}
    try:
        am = regen_am or convos.add_message(conv_id, "assistant", "", model=model, variant_of=carried_root)

        _bind_stop(am["id"], stop, run)
        buf: list[str] = []
        # Chain-of-thought from reasoning models. Kept out of `buf` so it never becomes the reply, and
        # never goes back to the model: history() reads content only.
        rbuf: list[str] = []
        error: str | None = None
        tool_events: list[dict[str, Any]] = []
        # Meeting titles, activity window titles, and uploaded-file excerpts are text the user did not
        # write as an instruction. Taint the turn when any of them is in the prompt, or a standing
        # grant would send mail with no card.
        ctx_taints = [k for k in ("meetings", "activity", "chunks") if used.get(k)]
        page = used.get("page") or {}
        if isinstance(page, dict) and (page.get("detail") or page.get("selection")):
            ctx_taints.append("page")
        tool_ctx: dict[str, Any] = {
            "project_id": conv["project_id"], "conversation_id": conv_id,
            # Taint is sticky for the whole conversation: the injected instructions live on in the replayed history, so
            # waiting one turn must not re-arm a standing 'always' grant. Only the user clears it (Context -> this chat).
            "tainted": bool(conv["settings"].get("tainted")) or bool(ctx_taints) or sandboxes.holds_import(conv_id),
            "taint_sources": list(conv["settings"].get("taint_sources") or []) + [f"context:{k}" for k in ctx_taints]
                + (["sandbox_import"] if sandboxes.holds_import(conv_id) else []),
            "allowed_urls": _urls(user_text), "settings": cfg,
            # Set for a scheduled job: Toolbox.call refuses every outward-facing tool outright, and _call_tool has
            # already turned the call into a proposals row before it got that far.
            "proposal_only": proposal_only(run), "message_id": am["id"],
            # What desk_deliver/desk_done record an output or a note against, so Accept can name the run
            # that wrote a file instead of guessing with the latest one.
            # Where artifact_create files what it makes (artifacts.run_id), so it can be found again from the run.
            "run_id": run.run_id if run else None,
        }
        use_tools = conv["settings"].get("useTools", True)
        modes = toolbox.effective(cfg.get("tools") or {}, (project or {}).get("tools"), conv["settings"].get("tools")) if use_tools else {}
        # MCP slugs all carry a reserved prefix no built-in may use, so the two mode maps cannot collide.
        mcp_modes, mcp_schemas = _mcp_tooling(conv["project_id"], conv_id) if use_tools else ({}, [])
        # A job's allowlist (job_tools) writes 'off' for tools outside it into the chat's tool map; MCP modes come from
        # mcp_grants, not that map, so the narrowing is applied to them here.
        chat_off = {n for n, v in (conv["settings"].get("tools") or {}).items() if v == "off"}
        if chat_off and mcp_modes:
            mcp_modes = {k: v for k, v in mcp_modes.items() if k not in chat_off}
            mcp_schemas = [s for s in mcp_schemas if s["function"]["name"] not in chat_off]
        # Past mcpDeferAbove ready tools the model gets mcp_tool_search instead of every schema. Only loaded
        # slugs are in `modes`, so a call to an unloaded one is off -> denied, never run.
        mcp_defer = mcp_search.should_defer(len(mcp_schemas), int(cfg.get("mcpDeferAbove", 12) or 0))
        tool_ctx["mcp_loaded"] = set()
        tool_ctx["modes"] = modes  # the live map: run_python's tool bridge resolves a script's calls against it
        bridge_n = 0

        async def _bridge_approve(name: str, args: dict[str, Any], forced: bool) -> bool:
            """A card for one call a run_python script made through the tool bridge. The script waits; the reply does not
            end. One-shot only: an 'always' answer is treated as 'allow' here, never as a standing grant."""
            nonlocal bridge_n
            if run is None or run.store is None:
                return False
            bridge_n += 1
            uid = f"{am['id']}:bridge{bridge_n}"
            fut: asyncio.Future = asyncio.get_event_loop().create_future()
            _approvals[uid] = fut
            spec = toolbox.specs.get(name)
            run.store.open_approval(uid, run.run_id, name, args, conversation_id=conv_id, message_id=am["id"], forced=forced,
                                    desk_id=run.desk_id, danger=spec.danger if spec else "external")
            run.publish("tool_call", {"message_id": am["id"], "id": uid, "name": name, "arguments": args,
                                      "needs_approval": True, "forced": forced, "proposal": None, "plan": None})
            decision = "deny"
            try:
                while not fut.done():
                    if stop.is_set():
                        run.store.decide(uid, "deny", by="stop")
                        fut.set_result("deny")
                        break
                    try:
                        await asyncio.wait_for(asyncio.shield(fut), timeout=2)
                    except asyncio.TimeoutError:
                        row = run.store.approval(uid)
                        if row and row["status"] != "pending" and not fut.done():
                            fut.set_result(row["decision"])
                decision = fut.result() if fut.done() else "deny"
            finally:
                _approvals.pop(uid, None)
            allowed = decision in ("allow", "always_chat", "always_global")
            run.publish("tool_result", {"message_id": am["id"], "id": uid, "name": name, "arguments": args,
                                        "result_preview": "", "duration_ms": 0,
                                        "error": None if allowed else "Declined by the user", "approval": "allow" if allowed else "deny",
                                        "forced": forced})
            return allowed

        tool_ctx["bridge_approve"] = _bridge_approve
        # The same one-shot card, for a tool that has to ask about part of what it was called to do (a browser form
        # submit, a host the shell may not reach). Absent in lanes with nobody to ask: a tool treats that as "no".
        tool_ctx["approve"] = _bridge_approve
        if mcp_defer:
            _mcp_names = {s["id"]: s["name"] for s in mcp_store.servers()}
            tool_ctx["mcp_catalog"] = lambda: [{**t, "server": _mcp_names.get(t["server_id"], "MCP")}
                                               for t in mcp_store.tools() if t["slug"] in mcp_modes]
            if use_tools:
                modes["mcp_tool_search"] = "on"
        else:
            modes.pop("mcp_tool_search", None)
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
        # Plan mode in an ordinary chat: the toggle's setting, the chat's own over the global default.
        # 'always' drafts from the first round; 'auto' flips `planning` on at the first consequential call
        # (see the gate). Either way a plan approved in this reply ends it, as it does for a desk.
        chat_plan_mode = "" if desk else str(conv["settings"].get("planMode") or cfg.get("planMode") or "off")
        if chat_plan_mode == "always":
            planning = True
        # Cards this desk let go of and the user has since answered: told to this turn once, then marked.
        parked_note = ""
        if desk_id:
            answered = run_store.unreported(desk_id)
            if answered:
                parked_note = parked_report(answered, plans.by_call)
                run_store.mark_reported(a["call_id"] for a in answered)
        # The desk tools derive their workspace root from this and never take one as an argument, so
        # desk A cannot address desk B's files.
        tool_ctx["desk_id"] = desk_id
        # What the user set for tools at project/chat level: shell.auto_ok must not override an explicit choice.
        tool_ctx["tool_overrides"] = {**((project or {}).get("tools") or {}), **(conv["settings"].get("tools") or {})}
        # What agent_spawn needs to start a child under this reply: its live tool modes (a child never has more),
        # the run its cards and status ride on, its stop flag, and where it sits in the spawn tree.
        tool_ctx.update(modes=modes, run=run, stop=stop, depth=0, agent_run_id=run.run_id if run else "", model=model,
                        effort=str(conv["settings"].get("effort") or "default"))

        def _schemas(withheld: bool = False) -> list[dict[str, Any]]:
            """One function, because the always_chat/always_global grant path recomputes the schemas; a
            filter applied at only one of the two sites lets a granted write tool reappear mid-plan.
            `modes` is mutated in place by that grant path, so this filters a copy and reads it fresh
            each time rather than rebuilding from a stale snapshot - otherwise the user's 'Always' click
            is silently discarded on the next recompute.
            """
            # Connectors are external, not reading tools: not offered while a plan is being drafted.
            offer = [] if planning and not withheld else mcp_search.select_schemas(mcp_schemas, tool_ctx["mcp_loaded"], mcp_defer)
            if not modes:
                return offer
            m = dict(modes)
            # A desk's workspace tools exist only inside a desk: elsewhere there is no root to resolve.
            if not desk_id:
                # MCP slugs live in this map and are not built-in specs. Indexing specs[n] crashed every
                # reply the moment a connector was connected.
                m = {n: v for n, v in m.items() if (spec := toolbox.specs.get(n)) is None or spec.group != "desk"}
            if planning and not withheld:
                # While a plan is being drafted the model is offered reading and the plan tool, nothing
                # else. Withholding them is kinder than denying them: a tool that is not offered costs no
                # round, where one that is offered and refused costs one every time. `withheld=True` asks
                # for the full set anyway, which is what a plan has to be judged against: its steps name
                # the tools it will use *after* approval. Connectors are not reading tools, so they stay out.
                m = {n: v for n, v in m.items() if n == PLAN_TOOL or ((spec := toolbox.specs.get(n)) is not None and spec.danger in PLAN_SAFE_DANGER)}
                m[PLAN_TOOL] = "ask"
                if desk_id:
                    m.pop("desk_done", None)  # `safe`, so it slips through the tier filter; finishing is for after approval
            if desk_id:
                m.pop("desk_start", None)  # a desk starting another desk is never offered (it would plan under its own budget)
            return toolbox.schemas(m) + offer

        tool_schemas = _schemas()

        def _desk_manual_text() -> str:
            # Only the tools actually sent this turn are described, so the manual never promises one the model lacks.
            net = ("open" if cfg.get("shellNetwork") else
                   "allowlist" if cfg.get("shellRegistryAccess", True) or cfg.get("shellAllowedDomains") else "off")
            text = desk_manual({s["function"]["name"] for s in tool_schemas},
                               {"shell_network": net, "sandbox_mount": bool(cfg.get("sandboxMountDesk", True))})
            return "\n\n" + text if text else ""

        tools_hint = (TOOLS_HINT + ("\n" + PLAN_HINT if any(s["function"]["name"] == "todo_write" for s in tool_schemas) else "")) if tool_schemas else ""
        if mcp_defer:
            _counts: dict[str, int] = {}
            for t in mcp_store.tools():
                if t["slug"] in mcp_modes:
                    _n = _mcp_names.get(t["server_id"], "MCP")
                    _counts[_n] = _counts.get(_n, 0) + 1
            tools_hint = "\n".join(p for p in (tools_hint, mcp_search.catalog_hint(_counts.items())) if p)
        hints = (RENDER_HINT, tools_hint, JOB_HINT if proposal_only(run) else "",
                 DESK_HINT + _desk_manual_text() if desk else "", DESK_PLAN_HINT if planning and desk else "",
                 CHAT_PLAN_HINT if chat_plan_mode in ("auto", "always") and tool_schemas else "", _today_hint())
        if cfg.get("cacheLayout", True):
            # Stable prefix first, per-turn retrieval just before the newest user message (see context.layout_messages).
            stable = "\n\n".join(p for p in (used["stable_system"], *hints) if p)
            system = "\n\n".join(p for p in (stable, *used["volatile_blocks"]) if p)
            used["stable_hash"] = hashlib.sha256(stable.encode()).hexdigest()[:12]
            cspan["meta"]["stable_hash"] = used["stable_hash"]
        else:
            stable = None
            system = "\n\n".join(p for p in (system, *hints) if p)
        used["system_prompt"] = system
        used["tokens_estimate"] = estimate_tokens(system)
        run_notes: list[dict[str, Any]] = []  # system notes that follow the history, whichever history it is
        if parked_note:
            run_notes.append({"role": "system", "content": parked_note})
        if body.resume_of:
            old = run_store.get(body.resume_of) or {}
            old_events = run_store.events(body.resume_of)
            run_notes.append({"role": "system", "content": resume.build_resume_note(
                old, old_events, run_store.executed(body.resume_of), run_store.approvals(None, run_id=body.resume_of),
                reason=resume.reason_tag(old, _run_message(old)))})
            # Taint is only ever added to: a resume cannot launder what the dead run read.
            for src in resume.taint_from_tape(old_events):
                tool_ctx["tainted"] = True
                if src not in tool_ctx["taint_sources"]:
                    tool_ctx["taint_sources"].append(src)

        def _assemble(hist: list[dict[str, str]]) -> list[dict[str, Any]]:
            """Everything before this run's own messages: the layout around `hist`, then the parked / resume notes."""
            head = layout_messages(stable, used["volatile_blocks"], hist) if stable is not None \
                else [{"role": "system", "content": system}] + hist
            return head + [dict(n) for n in run_notes]
        messages = _assemble(history)
        base_len = len(messages)  # what follows is this run's own steers, tool turns and notes
        yield "assistant_message", {**am, "context_used": used}
        yield "span", {"message_id": am["id"], "span": cspan}
        if compact_span:
            yield "span", {"message_id": am["id"], "span": compact_span}

        budget = Budget(_caps(cfg, JOB_BUDGET) if proposal_only(run) else cfg)
        tool_ctx["budget"] = budget  # children are charged to it
        partial: str | None = None
        last_sig: str | None = None
        repeats = 0
        tool_errors: dict[str, int] = {}
        detector = StuckDetector() if cfg.get("stuckDetection", True) else None  # loop shapes REPEAT_LIMIT cannot see
        perm_rules = permrules.load_rules(cfg.get("permissionRules"))
        denials = permrules.DenialStreak()  # refused calls in a row; at three the next result says to stop varying them
        stuck_hits = 0
        stop_text: str | None = None
        blocked: set[str] = set()
        _round = 0
        seen_call_ids: set[str] = set()  # a provider restarts its call numbering every round; ids stay unique per reply
        quiet_retries = 0  # silent retries of an incomplete or empty round: at most one per reply
        cut_recoveries = 0  # rounds whose tool calls were cut off at the output limit
        seen_ids = set(cinfo.get("row_ids") or [])  # rows the context was built from; a steer in it is not sent twice
        inflight: dict[str, Any] | None = None  # the call whose tool is executing, for a cancelled run to record
        error_kind: str | None = None
        notice: str | None = None  # one line for the user on the final done, e.g. the model took no reasoning effort
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
            block = work_plans.block(conv_id, desk=bool(desk_id))
            if desk_id and active_plan is not None:
                # A desk's approved plan, re-read every round so each step's status is current: it is
                # what a chained or woken turn picks up from, and what tells it a step already ran.
                approved = plans.block(plans.get(active_plan["plan_id"]) or active_plan)
                block = "\n\n".join(b for b in (approved, block) if b)
            if block:
                plan_msg = {"role": "system", "content": block}
                messages.append(plan_msg)

        overflow_retried = False
        run_started = am["created_at"]

        async def _recover_overflow(e: llm.ContextOverflowError) -> dict[str, Any] | None:
            """The provider said this request does not fit. Learn the model's limit, stub old tool output, fold the
            history from before this run into the summary, and rebuild the head of `messages` in place. None when
            neither step freed anything."""
            nonlocal win, base_len
            before = compaction.estimate_messages(messages)
            compaction.note_overflow(model, e.limit, before)
            win = compaction.window_for(cfg, model, pricing.caps(model).get("max_input_tokens"))
            cleared, saved = compaction.microcompact(messages, 1, win, 0.0)
            summarized = 0
            rows = [r for r in convos.history_rows(conv_id) if r["created_at"] < run_started]
            untrusted = bool(tool_ctx.get("tainted"))
            try:
                res = await compactor.compact({**cfg, "compactKeepRecent": min(_int_setting(cfg, "compactKeepRecent", 8), 2)},
                                              str(cfg.get("extractionModel") or model), conv_id, rows, include_untrusted=untrusted,
                                              complete=compaction.bind_supported(llm.complete, cancel=stop))
            except Exception:  # noqa: BLE001 - a summarizer failure leaves the stubs as the only relief
                log.warning("overflow compaction failed for %s", conv_id, exc_info=True)
                res = None
            if res:
                head = _assemble(compactor.build_history(rows, compactor.get(conv_id), untrusted))
                messages[:] = head + messages[base_len:]
                base_len = len(head)
                summarized = res["summarized"]
                saved += max(0, res["tokens_before"] - res["tokens_after"])
            if not cleared and not res:
                return None
            span = tracer.start("compact", "Compact after overflow", {"kind": "overflow"}, parent=cspan)
            tracer.end(span, {"cleared": cleared, "summarized": summarized, "tokens_saved": saved})
            return {"span": span}

        async def _stream_round() -> AsyncIterator[dict[str, Any]]:
            """The round's model stream. A provider overflow before anything streamed is recovered once per reply:
            the stream is re-issued over the compacted context and a synthetic `overflow_recovered` event says so.
            Anything else (a second overflow, one after text arrived) propagates to the reply's error handling."""
            nonlocal overflow_retried
            while True:
                streamed = False
                stream = llm.stream_chat(cfg, model, messages, tool_schemas or None,
                                         effort=str(conv["settings"].get("effort") or "default"),
                                         fast=bool(conv["settings"].get("fast")),
                                         cancel=_stream_cancel(stop, steers, run))
                try:
                    async for ev in stream:
                        streamed = streamed or ev["type"] in ("reasoning", "delta")
                        yield ev
                    return
                except llm.ContextOverflowError as e:
                    if streamed or overflow_retried or stop.is_set():
                        raise
                    overflow_retried = True
                    recovered = await _recover_overflow(e)
                    if stop.is_set():
                        return  # Stop arrived while the summarizer ran: the round ends with nothing, as stopped
                    if recovered is None:
                        raise
                finally:
                    await stream.aclose()
                yield {"type": "overflow_recovered", **recovered}

        async def _end_jobs() -> None:
            """A chat's background shell jobs end with its reply; a desk's outlive a turn (they wake it). Called at
            the very end of the reply, after its `done`, and on cancellation: no await sits between the last
            steer check and `done`, so a steer is either folded in or already answered with a 409."""
            if not desk_id:
                await toolbox.shell.kill_conversation(conv_id)

        async def _final_round() -> AsyncIterator[tuple[str, Any]]:
            """Closing answer after a budget or breaker stop: one tool-free call, itself exempt from the budget."""
            nonlocal notice
            _reinject_plan()
            # One newline, not a blank line: the transcript renders as markdown, where a blank line opens a
            # new paragraph and reads as an empty line dropped into the middle of the reply.
            if buf and buf[-1] and not buf[-1].endswith("\n"):
                buf.append("\n")
                yield "delta", {"id": am["id"], "text": "\n"}
            span = tracer.start("llm", model, {"round": _round, "final": True, "messages": len(messages), "tools": len(tool_schemas)})
            yield "span", {"message_id": am["id"], "span": span}
            start, fin = len(buf), {}
            budget.arm_deadline(cap=FINAL_ROUND_SECONDS)  # the closing answer is exempt from the budget, not from a hang
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
                    if ev.get("effort_dropped"):
                        notice = EFFORT_DROPPED_NOTICE
                if steers:
                    break
            tracer.end(span, {"finish_reason": fin.get("finish_reason"), "usage": fin.get("usage"),
                              "output_chars": len("".join(buf[start:]))},
                       error="Stopped by user" if stop.is_set() else None)
            yield "span", {"message_id": am["id"], "span": span}
    except asyncio.CancelledError:
        if am:
            _active.pop(am["id"], None)
            convos.finish_message(am["id"], "", "Cancelled", used, [], tracer.spans, None)
            convos.touch(conv_id)
        raise
    except Exception as e:  # noqa: BLE001
        log.exception("chat setup failed for %s", conv_id)
        if am:
            _active.pop(am["id"], None)
            convos.finish_message(am["id"], "", str(e), used, [], tracer.spans, None, error_kind=getattr(e, "kind", None))
            convos.touch(conv_id)
        yield "done", {"id": am.get("id"), "error": str(e), "context_used": used, "tool_events": [], "trace": tracer.spans,
                       "stopped": False, "partial": None, "segment": False, "tainted": False, "taint_sources": [],
                       "reasoning": None, "outcome": None, "error_kind": getattr(e, "kind", None)}
        return

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
                    convos.finish_message(am["id"], "".join(buf).strip(), None, used, tool_events, tracer.spans, reasoning,
                                          outcome=partial)
                    _active.pop(am["id"], None)
                    # A steer closes the current segment and the reply carries on in a fresh assistant
                    # message, so this `done` ends a segment, not the run. Anything supervising the run
                    # (a desk turn) must not read it as the end of the turn and charge for it.
                    yield "done", {"id": am["id"], "error": None, "context_used": used, "tool_events": tool_events,
                                   "trace": tracer.spans, "stopped": False, "partial": partial, "segment": True,
                                   "tainted": tool_ctx["tainted"], "taint_sources": tool_ctx["taint_sources"],
                                   "reasoning": reasoning, "outcome": partial}
                    am = convos.add_message(conv_id, "assistant", "", model=model)
                    _bind_stop(am["id"], stop, run)
                    tool_ctx["message_id"] = am["id"]
                    buf = []
                    rbuf = []
                    tool_events = []
                    tracer = Tracer()
                    yield "assistant_message", {**am, "context_used": used}
                elif any(um["created_at"] >= am["created_at"] for um in steered):
                    # Nothing was written, and the steer landed after this row: swap it for a fresh one so the
                    # transcript reads user message, then answer. No segment closed, so no `done` and the
                    # tracer keeps its spans (the context span belongs to the reply, not to the row).
                    _active.pop(am["id"], None)
                    # Just this row: on a regenerate it is the open sibling of a group, and delete_message would take
                    # the whole group (the answer being replaced) with it. The fresh row keeps the row's place in it.
                    with db.tx() as c:
                        c.execute("DELETE FROM messages WHERE id=?", (am["id"],))
                    yield "removed_message", {"id": am["id"]}
                    am = convos.add_message(conv_id, "assistant", "", model=model, variant_of=am.get("variant_of"))
                    _bind_stop(am["id"], stop, run)
                    tool_ctx["message_id"] = am["id"]
                    yield "assistant_message", {**am, "context_used": used, "trace": tracer.spans}
                for um in steered:
                    if um["id"] not in seen_ids:  # a steer that landed during context assembly is already in the history
                        messages.append({"role": "user", "content": um["content"]})
                    user_text = um["content"]
                    tool_ctx["allowed_urls"] |= _urls(um["content"])
                # The new message gets a clean slate: breakers that tripped on the work before it must not cut
                # the work it asks for. Budget and round count are the run's and stay.
                if partial == "loop":
                    partial = None
                repeats, last_sig, stuck_hits, stop_text = 0, None, 0, None
                blocked.clear()
                tool_errors.clear()
                if detector is not None:
                    detector.obs.clear()

            _round += 1
            budget.rounds = _round - 1  # rounds already completed: the Nth round's tool calls must still be allowed to run
            round_start = len(buf)
            end: dict[str, Any] = {}
            # Old tool results shrink to stubs once the run has filled half the window; read_tool_result still serves them.
            n_cleared, n_saved = compaction.microcompact(messages, _int_setting(cfg, "microKeep", 3), win,
                                                         float(cfg.get("microAt", 0.5)))
            if n_cleared:
                mspan = tracer.start("compact", "Clear old tool results", {"kind": "micro"}, parent=cspan)
                tracer.end(mspan, {"cleared": n_cleared, "tokens_saved": n_saved})
                yield "span", {"message_id": am["id"], "span": mspan}
            for _note in toolbox.shell.drain_notes(conv_id):  # a background shell job finished since the last round
                messages.append({"role": "system", "content": _note})
            _reinject_plan()  # last message in the context, after the previous round's tool results
            lspan = tracer.start("llm", model, {"round": _round, "messages": len(messages), "tools": len(tool_schemas)})
            round_span = lspan  # the tool calls below nest under it
            yield "span", {"message_id": am["id"], "span": lspan}
            first_token: int | None = None
            budget.arm_deadline()
            async for ev in _stream_round():
                if stop.is_set():
                    break
                if ev["type"] == "overflow_recovered":
                    # The request did not fit; the context was compacted and the same round is being re-issued.
                    tracer.end(lspan, {"finish_reason": "overflow"}, error="Context overflow; compacted and retrying")
                    yield "span", {"message_id": am["id"], "span": lspan}
                    yield "span", {"message_id": am["id"], "span": ev["span"]}
                    lspan = tracer.start("llm", model, {"round": _round, "messages": len(messages), "tools": len(tool_schemas)})
                    round_span = lspan
                    yield "span", {"message_id": am["id"], "span": lspan}
                elif ev["type"] == "reasoning":
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
                    if ev.get("effort_dropped"):
                        notice = EFFORT_DROPPED_NOTICE
                if steers:
                    break
            calls = [] if end.get("finish_reason") == "cancelled" else (end.get("tool_calls") or [])
            ensure_unique_call_ids(calls, seen_call_ids)
            if end.get("finish_reason") == "timeout":
                # The provider outran maxRunSeconds mid-stream. Keep what arrived and mark the reply partial.
                # A reply that already ran tools has something to close out with (see below), so it does not raise.
                if not "".join(buf).strip() and not any(m.get("role") == "tool" for m in messages):
                    raise llm.LLMError(f"This reply hit its {int(budget.max_seconds)}s time limit before the model produced anything. Try again, or raise maxRunSeconds in Settings.")
                partial = "time"
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
            fr, inc = end.get("finish_reason"), bool(end.get("incomplete"))
            round_text = "".join(buf[round_start:]).strip()
            # How this round ended, when it did not end well. A steer overrides all of it: the new message is the
            # next thing to answer, and the segment closes as it always did.
            if not steers:
                if fr == "content_filter":
                    calls = []
                    error, error_kind = "The provider filtered this reply. Try rephrasing.", "content_filter"
                    break
                if fr == "timeout":
                    calls = []
                    if not round_text and any(m.get("role") == "tool" for m in messages):
                        messages.append({"role": "system", "content": TIME_STOP})
                        async for chunk in _final_round():
                            yield chunk
                        if steers and not stop.is_set():
                            continue
                    break  # partial is already "time"; text that was written stays as it is
                if fr == "length" and not calls:
                    if "".join(buf).strip():
                        partial = "length"
                    else:
                        error = ("The model reached its output limit before writing an answer. "
                                 "Try a lower effort or a shorter request.")
                    break
                if inc:
                    calls = []
                    if not round_text and quiet_retries == 0 and not budget.exceeded():
                        quiet_retries = 1  # a counted round: the usage above was already charged
                        continue
                    partial = "incomplete"
                    break
                if not calls and not desk_id and not "".join(buf).strip():
                    if quiet_retries == 0 and not budget.exceeded():
                        quiet_retries = 1
                        messages.append({"role": "system", "content": EMPTY_NUDGE})
                        continue
                    # Still silent. A reply that ran tools is its cards, and ends as one; one that did nothing is an error.
                    if not tool_events:
                        error = "The model returned an empty reply. Try again or pick another model."
                    break
            if not calls:
                # A steer that arrived while this answer streamed: keep the model's own turn in its
                # context, then loop back so the top of the loop closes this segment and a new one
                # replies to it.
                if steers:
                    if round_text:
                        messages.append({"role": "assistant", "content": round_text})
                    continue
                break
            known_names = set(toolbox.specs) | set(modes) | set(mcp_modes)
            advertised = [s["function"]["name"] for s in tool_schemas]
            for i, c in enumerate(calls):
                _normalise_call(c, known_names, advertised, fr == "length" and i == len(calls) - 1)
            turn = {"role": "assistant", "content": "".join(buf[round_start:]).strip() or None,
                    "tool_calls": [{"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": _replay_args(c["arguments"])}} for c in calls]}
            if fr == "length" and not steers:
                # The output limit cut this round's tool calls short. They are never replayed as written (see
                # _replay_args), so the model can try again smaller; twice in one reply is a loop of its own.
                cut_recoveries += 1
                if cut_recoveries > 2:
                    partial = "length"
                    messages.append(turn)
                    for c in calls:
                        messages.append({"role": "tool", "tool_call_id": c["id"],
                                         "content": BUDGET_STOP.format(axis="the model's output limit")})
                    async for chunk in _final_round():
                        yield chunk
                    if steers and not stop.is_set():
                        continue
                    break
            over = budget.exceeded()
            if (over and run is not None and run.desk_id is None and (plan_seen or active_plan)
                    and plans.covers(run.run_id, [(c["name"], c["_args"]) for c in calls], desk_id=run.desk_id)):
                # Every call here is a step the user approved. The budget that ran out was spent drafting that
                # plan, so refusing now would turn the approval into a dead end. The next round is still checked.
                # Never a desk: a desk carries its remaining steps into a chained turn instead (_should_chain).
                over = None
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
            # Read-only agent_spawn calls of this round start together; never in plan mode or when every change cards.
            subagent_mgr.prestart(calls, tool_ctx, start=not planning and autonomy != "ask" and not stop.is_set())
            if buf and buf[-1] and not buf[-1].endswith("\n"):
                buf.append("\n")
                yield "delta", {"id": am["id"], "text": "\n"}
            for c in calls:
                if stop.is_set():
                    # Stop means the rest of this round's calls do not run either. The provider still needs a tool
                    # message per call, and nothing was journaled as started, so nothing replays.
                    messages.append({"role": "tool", "tool_call_id": c["id"], "content": json.dumps({"error": "Stopped by the user before this call ran; it was not executed."})})
                    continue
                args = c["_args"] or {}
                sig = tools.call_key(c["name"], args)
                repeats = repeats + 1 if sig == last_sig else 1
                last_sig = sig
                if repeats >= REPEAT_LIMIT:  # before the approval gate: denying the same call forever is still a loop
                    partial = "loop"
                if partial == "loop":  # every pending call still needs a tool message, executed or not
                    messages.append({"role": "tool", "tool_call_id": c["id"], "content": stop_text or LOOP_STOP.format(name=c["name"], n=REPEAT_LIMIT)})
                    continue
                # A call that cannot run as sent (broken JSON, an unknown name, a cut-off call) is answered here with
                # what was wrong. It never reaches the gate, a rule, a plan claim, an approval card or the tool:
                # nothing the user could approve would ever work. Arguments the signature cannot take are refused
                # below instead, once the gate has spoken: a plan-mode or turned-off refusal comes first, as it did.
                invalid = c["_invalid"]
                if c["_problem"]:
                    bad = _invalid_call_error(c, toolbox.specs.get(c["name"]))
                    shown = (c["_asked"] or c["name"])[:80] if invalid == "name" else c["name"]
                    uid = f"{am['id']}:{c['id']}"
                    if c["name"] in blocked:
                        bad = tools.denied(c["name"], f"failing {TOOL_ERROR_LIMIT} times in a row and disabled for the rest of this reply")
                    yield "tool_call", {"message_id": am["id"], "id": uid, "name": shown, "arguments": {},
                                        "needs_approval": False, "forced": False, "proposal": None, "permission": None, "plan": None}
                    tspan = tracer.start("tool", shown, {"round": _round, "arguments": {}, "invalid": invalid}, parent=round_span)
                    yield "span", {"message_id": am["id"], "span": tspan}
                    preview, err = summarize_result(bad), bad.get("error")
                    tool_errors[c["name"]] = tool_errors.get(c["name"], 0) + 1
                    if tool_errors[c["name"]] >= TOOL_ERROR_LIMIT:
                        blocked.add(c["name"])
                    tracer.end(tspan, {"result_chars": len(preview), "invalid": invalid}, error=err)
                    event = {"id": uid, "name": shown, "arguments": {}, "result_preview": preview, "duration_ms": 0,
                             "error": err, "images": None, "undo": None, "approval": None, "plan": None, "forced": False,
                             "tainted": False, "blocked": None, "breaker": partial, "proposal": None, "artifact": None,
                             "invalid": invalid}
                    tool_events.append(event)
                    yield "tool_result", {"message_id": am["id"], **event}
                    yield "span", {"message_id": am["id"], "span": tspan}
                    messages.append({"role": "tool", "tool_call_id": c["id"],
                                     "content": tool_results.for_model(conv_id, am["id"], c["name"], bad, untrusted=False)})
                    continue
                raw_mode = modes.get(c["name"], "off")
                spec = toolbox.specs.get(c["name"])
                # Connector tools are not in toolbox.specs but are external by construction; "safe" here would
                # let them through plan mode, propose-only desks and the unexpected-taint rule.
                danger = spec.danger if spec else (MCP_DANGER if mcp_is(c["name"]) else "safe")
                mode = _gate(c["name"], raw_mode, tool_ctx, args)
                # A file write outside the granted folders (or in one, once the reply read untrusted content) asks.
                fs_ask = mode != "off" and toolbox.fs_needs_ask(c["name"], args, tool_ctx)
                if fs_ask and mode == "on":
                    mode = "ask"
                # untrusted content in this reply upgraded on -> ask; so does a call that may never run unasked
                # (shell_run outside its sandbox), which no standing grant can then buy off
                forced = mode != raw_mode or (mode == "ask" and toolbox.forces_ask(c["name"], args))
                # A sandboxed shell_run inside this desk's own workspace needs no card when the tool is still on its default
                # `ask` (shell.auto_ok). Everything below (plan mode, desk autonomy, permission rules, doom-loop) can still ask.
                if (c["name"] == "shell_run" and mode == "ask" and raw_mode == "ask" and not forced and desk_id
                        and shell_tool.auto_ok(args, tool_ctx, cfg, [workspace.desk_root(desk_id)])):
                    mode = "on"
                blocked_reason: str | None = None
                # ---- plan mode, in priority order. Each rule can only ever make a call ask or stop;
                # none of them can turn a card off, so this is a narrowing of the gate above.
                if c["name"] != PLAN_TOOL and raw_mode != "off":
                    if (chat_plan_mode == "auto" and not planning and active_plan is None and danger in MUTATING
                            and (spec.group if spec else "") not in PLAN_AUTO_EXEMPT_GROUPS):
                        # 'auto' plan mode: the first consequential call of a reply turns drafting
                        # on. This call is refused with the planning message below and the model is
                        # offered the reduced set from the next round, exactly as in 'always'.
                        planning = True
                        tool_schemas = _schemas()
                    if planning and danger not in PLAN_SAFE_DANGER:
                        # Nothing consequential runs while a plan is being drafted. `forced` rides out
                        # on the tool_call event so the UI can say "blocked while planning" rather than
                        # "turned off".
                        mode, forced, blocked_reason = "off", True, PLAN_BLOCKED
                    elif autonomy == "propose" and danger == "external":
                        # A propose-only desk may plan an external action, never perform one.
                        mode, forced, blocked_reason = "off", True, PROPOSE_ONLY
                    elif plan_voided_by_taint(danger, bool(tool_ctx["tainted"]),
                                              taint_expected(active_plan, tool_ctx["taint_sources"])):
                        # Taint the approved plan did not predict voids the pre-approval: ask, and
                        # consume nothing. A web fetch is included: its address can carry what was read.
                        # Checked before any claim, because a step burnt on a call the
                        # user then denies can never be reclaimed.
                        mode, forced = "ask", True
                    elif autonomy == "ask" and danger in MUTATING:
                        # 'Ask as it goes': a desk that does not plan first cards every change instead,
                        # one at a time. Forced, so the card cannot buy a standing grant that would
                        # quietly switch the mode back off.
                        mode, forced = "ask", True
                # Argument-pattern rules, session grants and the doom-loop card (permrules.py). A deny refuses; a
                # forced approval (taint, plan mode) is never downgraded; MCP tools keep their schema-bound grants.
                perm = permrules.Resolution(mode, forced)
                if c["name"] != PLAN_TOOL and mode != "off" and not mcp_is(c["name"]):
                    perm = permrules.resolve(
                        c["name"], args, mode, forced or (danger == "external" and bool(tool_ctx["tainted"])),
                        rules=perm_rules, roots=_perm_roots(cfg, desk_id), conv=conv_id,
                        doom=detector is not None and detector.repeat_count(c["name"], args) >= permrules.DOOM_LIMIT - 1)
                    mode = perm.mode
                    if perm.kind == "doom_loop":
                        forced = True
                # A background run never waits on an approval: there is nobody at the keyboard, and the call is not
                # going to happen either way. It becomes a proposal in _call_tool and the run carries on.
                # MCP tools are external by construction but are not in Toolbox.specs, so proposes()
                # cannot see them. A scheduled run must still record them instead of calling them.
                proposing = proposal_only(run) and mode != "off" and (toolbox.proposes(c["name"]) or mcp_is(c["name"]))
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
                if c["name"] != PLAN_TOOL and mode != "off" and (bad := toolbox.precheck(c["name"], args)) is not None:
                    # Arguments the tool's signature cannot take: nothing the user could approve would ever run, so
                    # no card opens and no plan step is spent on it. Not the plan tool: its placeholder function is
                    # narrower than its schema, and normalize_plan below is its check.
                    pre, invalid = bad, "schema"
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
                                               specs={**{n: sp.danger for n, sp in toolbox.specs.items()},
                                                      **{n: MCP_DANGER for n in modes if mcp_is(n)}},
                                               taints={n for n in modes if toolbox.taints(n) or mcp_is(n)})
                    if pre is None:
                        plan = plans.open(uid, args, run_id=run.run_id if run else None, conversation_id=conv_id,
                                          message_id=am["id"], tainted=bool(tool_ctx["tainted"]),
                                          desk_id=run.desk_id if run else None)
                        plan_seen = True
                        # Its own event, because the card needs the whole plan with its steps and
                        # their digests; the tool_call event carries only the raw arguments.
                        yield "plan_card", {"message_id": am["id"], "call_id": uid, "plan": plan}
                elif (plan_seen or active_plan) and c["name"] != PLAN_TOOL and pre is None:
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
                if perm.refusal and pre is None:
                    pre = tools.denied(c["name"], perm.refusal)
                unattended = False
                if (mode == "ask" and claimed is None and pre is None and not proposing and run is not None
                        and run.kind in UNATTENDED_KINDS and cfg.get("unattendedApprovals") == "deny"):
                    # Nobody is there to answer: refuse with a recorded reason rather than park a card for later.
                    unattended = True
                    why = "no one is available to approve it and unattendedApprovals is set to deny"
                    pre = tools.denied(c["name"], f"refused: {why}")
                    if run.store is not None:
                        run.store.open_approval(uid, run.run_id, c["name"], args, conversation_id=conv_id, message_id=am["id"],
                                                forced=forced, desk_id=run.desk_id, danger=danger)
                        run.store.decide(uid, "deny", by="unattended", note=why)
                if proposal_only(run) and mode == "ask" and claimed is None and pre is None:
                    # A background run has nobody to answer a card, and only external calls can become proposals.
                    # Opening one here would park the run forever (and every later fire behind it).
                    pre = tools.denied(c["name"], "not available in a background run: it needs an approval and nobody is watching")
                asks = mode == "ask" and claimed is None and pre is None
                if asks and desk_id and c["name"] != PLAN_TOOL and run_store.claim_parked(desk_id, c["name"], args, uid):
                    # The user already said yes to exactly this call on a card an earlier turn let go
                    # of (see parked_report). Spent here, once; any other arguments still ask.
                    asks = False
                yield "tool_call", {"message_id": am["id"], "id": uid, "name": c["name"], "arguments": args,
                                    "needs_approval": asks, "forced": forced, "proposal": proposing or None,
                                    "permission": perm.card() if asks else None,
                                    "plan": {"plan_id": claimed["plan_id"], "idx": claimed["idx"], "title": claimed["title"]} if claimed else None}
                tspan = tracer.start("tool", c["name"], {"round": _round, "arguments": _short(args), "mode": mode, "forced": forced,
                                                         "plan_step": f"{claimed['plan_id']}#{claimed['idx']}" if claimed else None,
                                                         **({"repaired": True} if c["_repaired"] else {}),
                                                         **({"invalid": invalid} if invalid else {})},
                                 parent=round_span)
                yield "span", {"message_id": am["id"], "span": tspan}
                t0 = time.time()
                decision = "allow"
                deny_note: str | None = None
                edit_info: dict[str, Any] = {}
                interrupted = False  # Stop cut this call short (see _await_tool)
                if asks:
                    # Pause the reply until the user approves or denies this call (POST /approvals/{call_id}).
                    # The approval is a row, and it waits as long as it takes: there is no auto-deny.
                    approval_t0 = time.time()
                    fut: asyncio.Future = asyncio.get_event_loop().create_future()
                    _approvals[uid] = fut
                    store = run.store if run is not None else None
                    mine = False  # whether the row on file is the one this wait opened (a reused id returns the old row)
                    if store is not None:
                        opened = store.open_approval(uid, run.run_id, c["name"], args, conversation_id=conv_id, message_id=am["id"],
                                                     forced=forced, desk_id=run.desk_id, danger=danger)
                        mine = bool(opened and opened.get("status") == "pending" and opened.get("run_id") == run.run_id
                                    and opened.get("tool") == c["name"] and opened.get("args_digest") == args_digest(args))
                        if not mine:
                            log.warning("approval %s was already on file for another call; only a live answer will count", uid)
                        run.budget = budget.snapshot()
                        run.set_status("awaiting_approval")
                    awaiting = {"id": uid, "name": c["name"], "arguments": args, "result_preview": "", "duration_ms": 0,
                                "error": None, "pending": True, "needs_approval": True, "forced": forced}
                    if desk_id:
                        # The desk leaves the rail's "working" label and says what it is waiting for.
                        # These are the *live* waiting states: a run is still holding the card open.
                        # desk_ask's card IS the question, so the desk carries it from the moment the
                        # card opens: the rail and the banner show it, and the banner's answer can
                        # settle this card (message_desk) as well as the card's own box can.
                        q = str(args.get("question") or "").strip() if c["name"] == "desk_ask" else None
                        desks.set_status(desk_id, "awaiting_plan" if plan is not None else "needs_approval",
                                         run_id=run.run_id if run else None, question=q)
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
                            if steers and not desk_id:
                                # The user wrote instead of answering the card: that is a no, with their words as the
                                # reason. `by="steer"` keeps a plan re-proposable (only a user's own no blocks it), and
                                # the loop top folds the message in once this call has its result.
                                note = str(steers[-1]["content"]).strip()[:500]
                                fut.set_result("deny")
                                _approval_notes[uid] = note
                                if store is not None:
                                    store.decide(uid, "deny", by="steer", note=note)
                                if plan is not None:
                                    plans.decide(uid, "deny", by="steer", note=note)
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
                                if mine and row and row["status"] != "pending" and not fut.done():  # decided on the row alone
                                    fut.set_result(row["decision"])
                        decision = fut.result() if fut.done() else "deny"
                    finally:
                        _approvals.pop(uid, None)
                    deny_note = _approval_notes.pop(uid, None)
                    pending_card, awaiting = awaiting, None
                    if decision != "deny" and mine and store is not None and (arow := store.approval(uid)) and arow.get("edited_args"):
                        # The user rewrote this call on its card. approval_edits validated it in the route and the row's
                        # digest was re-bound to it; from here on the edited arguments are THE call: they run, are
                        # journaled, verified and queued (outbox) in place of the model's. Never read from the model's call.
                        original_args, args = args, arow["edited_args"]
                        edit_info = {"original_arguments": original_args, "edited_arguments": args, "edited_by": "user"}
                    if parked:
                        # The reply ends here, still owing this call an answer. The row stays pending
                        # and decidable; the desk moves from a live waiting state to `blocked`, which
                        # is the one that means "no run is coming back for this on its own".
                        if desk_id:
                            desks.set_status(desk_id, "blocked",
                                             reason="plan" if plan is not None else
                                             ("question" if c["name"] == "desk_ask" else "approval"),
                                             run_id=None)
                        # The reply ends here, but what it streamed so far is the user's to keep: finish the row
                        # (the card stays on it as pending) and record what this turn spent, as the normal end does.
                        kept = tool_events + ([pending_card] if pending_card else [])
                        convos.finish_message(am["id"], "".join(buf).strip(), None, used, kept, tracer.spans,
                                              "".join(rbuf).strip() or None)
                        convos.touch(conv_id)
                        if run is not None:
                            run.partial, run.cost, run.rounds = partial, budget.cost, budget.rounds
                        yield "parked", {"message_id": am["id"], "call_id": uid, "name": c["name"]}
                        # Persist the reply with its card still pending, so the transcript keeps a
                        # card the user can answer after a reload; then end like any other reply,
                        # so a watching window stops showing it as streaming.
                        card = {"id": uid, "name": c["name"], "arguments": args, "result_preview": "", "duration_ms": 0,
                                "error": None, "pending": True, "needs_approval": True, "forced": forced, "parked": True}
                        tool_events.append(card)
                        tracer.end(tspan, {"parked": True})
                        text = "".join(buf).strip()
                        reasoning = "".join(rbuf).strip() or None
                        convos.finish_message(am["id"], text, None, used, tool_events, tracer.spans, reasoning)
                        convos.touch(conv_id)
                        if run is not None:
                            run.partial, run.cost, run.rounds = None, budget.cost, budget.rounds
                        yield "done", {"id": am["id"], "error": None, "context_used": used, "tool_events": tool_events,
                                       "trace": tracer.spans, "stopped": False, "partial": None, "segment": False,
                                       "tainted": tool_ctx["tainted"], "taint_sources": tool_ctx["taint_sources"],
                                       "reasoning": reasoning, "outcome": None, "error_kind": None, "parked": uid}
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
                                         if (approved_plan or {}).get("status") == "approved" else None,
                                         question="" if c["name"] == "desk_ask" else None)
                    budget.paused += time.time() - approval_t0  # a slow approval must not blow the wall clock
                    t0 = time.time()  # don't count waiting time as tool time
                    if decision == "always_session":
                        # Scoped to this chat and to exactly what the card named; a forced card is answered once.
                        if not forced:
                            permrules.SESSION.add(conv_id, perm.keys)
                        decision = "allow"
                    elif decision == "always_rule":
                        decision = "allow"  # POST /approvals already saved the rules, or refused to for a forced card
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
                            # Re-read: the approval may have waited for hours, and a tool switched off in the meantime
                            # must stay off. Merge into the current map, never the one read at the start of the reply.
                            fresh = (convos.get(conv_id) or conv)["settings"].get("tools") or {}
                            convos.update(conv_id, {"settings": {"tools": {**fresh, c["name"]: "on"}}})
                            conv["settings"].setdefault("tools", {})[c["name"]] = "on"
                        modes[c["name"]] = "on"
                        decision = "allow"
                    elif decision == "always_global":
                        if mcp_is(c["name"]):
                            mcp_store.set_grant(c["name"], "on", "global")
                        else:
                            # Re-read for the same reason: the Settings page may have changed the map during the wait.
                            db.set_settings({"tools": {**(settings().get("tools") or {}), c["name"]: "on"}})
                        modes[c["name"]] = "on"
                        decision = "allow"
                    if standing:
                        # the grant changed modes; keep the schemas in step
                        tool_schemas = _schemas()
                    if plan is not None:
                        # The decision was recorded on the plan by whoever answered it (the route, or the stop
                        # above), including any step the user edited: re-read it rather than trust `args`.
                        plan = plans.by_call(uid) or plan
                        if (desk_id or chat_plan_mode in ("auto", "always")) and plan.get("status") == "approved":
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
                ran = False  # only a call that really executed says anything about being stuck
                mcp_ran = False  # the connector was actually contacted: whatever it said, error or not, is third-party text
                if was_blocked:
                    result: Any = tools.denied(c["name"], f"failing {TOOL_ERROR_LIMIT} times in a row and disabled for the rest of this reply")
                elif pre is not None:
                    # An unusable plan, a step of a plan the user rejected, or a plan-mode refusal.
                    # Ahead of the `off` branch: plan mode turns the mode off to stop the call, and
                    # the model must hear "put it in a plan", not "turned off, do not retry".
                    result = pre
                elif mode == "off":
                    result = tools.denied(c["name"], "not loaded; call mcp_tool_search first"
                                          if mcp_defer and mcp_is(c["name"]) and c["name"] in mcp_modes
                                          else "turned off for this chat")
                elif plan is not None:
                    result = plans.model_result(plan)  # the decision, and the arguments the user actually authorised
                elif decision != "allow":
                    # A note typed with the denial goes back as the result and the reply carries on.
                    result = (tools.tool_error(f"{c['name']} was declined by the user, who said: {deny_note}",
                                               alternative="follow what the user said, or ask them what they would like instead")
                              if deny_note else tools.denied(c["name"], "just declined by the user"))
                elif asks and c["name"] == "desk_ask" and (answer := ((run_store.approval(uid) or {}).get("note") or "").strip()):
                    # The card was answered while this reply was still holding it, so the answer goes
                    # straight back as the result and the turn carries on. Running the tool body here
                    # would block the desk on a question the user has just answered.
                    result = {"status": "answered", "answer": answer,
                              "note": "The user answered your question. Carry on with it; do not ask it again."}
                    if answer in [str(o).strip() for o in (args.get("options") or []) if isinstance(o, str)]:
                        result["choice"] = answer  # they picked one of the choices the question offered
                elif mcp_is(c["name"]):
                    # The branch above only reaches here when the call was allowed. A job has nobody
                    # to allow it, so the connector call is a proposal and the server is not contacted.
                    if proposal_only(run) and run is not None:
                        result = _propose(run, c["name"], args, uid, tool_ctx)
                    else:
                        inflight = {"id": uid, "name": c["name"], "arguments": args}
                        result, interrupted = await _await_tool(_mcp_call(c["name"], args), stop,
                                                                grace=STOP_GRACE_SECONDS if danger in IDEMPOTENT_DANGER else 0.0)
                        mcp_ran = not interrupted
                    ran = not interrupted
                else:
                    tool_ctx["fs_outside_ok"] = fs_ask  # the user approved this write (or granted the folder)
                    inflight = {"id": uid, "name": c["name"], "arguments": args}
                    try:
                        result, interrupted = await _await_tool(
                            _call_tool(run, _round, c["name"], args, tool_ctx, uid), stop,
                            grace=STOP_GRACE_SECONDS if danger in IDEMPOTENT_DANGER else 0.0)
                    finally:
                        tool_ctx["fs_outside_ok"] = False
                    ran = not interrupted
                if interrupted:
                    # The journal row stays `started`, so a later identical call reports unknown_outcome rather than repeating it.
                    result = tools.tool_error("Stopped by the user before it finished; whether it took effect is unknown."
                                              if danger in IDEMPOTENT_DANGER else "Stopped by the user.")
                hint = denials.note()
                if hint and isinstance(result, dict):
                    result["permission_note"] = hint
                denials.record(bool(perm.refusal) or unattended or (asks and decision != "allow"))
                ms = int((time.time() - t0) * 1000)
                made = tool_ctx.pop("artifact", None) if decision == "allow" else None
                if made is None and isinstance(result, dict) and result.get("artifact_id") and c["name"] in ("artifact_create", "artifact_update", "artifact_edit"):
                    # A journal replay returns the recorded result without running the tool, so ctx carries no note.
                    made = {"id": result["artifact_id"], "title": result.get("title", ""), "version": result.get("version"),
                            "action": "created" if result.get("created") else "updated"}
                # images (e.g. matplotlib figures from run_python) go to the UI, not to the model
                images = result.pop("images", None) if isinstance(result, dict) else None
                preview = summarize_result(result)
                err = result.get("error") if isinstance(result, dict) else None
                tool_errors[c["name"]] = tool_errors.get(c["name"], 0) + 1 if err else 0  # reset on success = consecutive
                if ran and not err and run is not None:
                    run.tool_ok += 1  # a desk turn's progress, beside steps_consumed (_chain_kind)
                if tool_errors[c["name"]] >= TOOL_ERROR_LIMIT:
                    blocked.add(c["name"])
                # An MCP result is third-party text by definition, so it taints like a web fetch does.
                # A connector's error text is as untrusted as its output, so it taints either way.
                tainted = decision == "allow" and (mcp_ran or (not err and toolbox.taints(c["name"])))
                if tainted:
                    tool_ctx["tainted"] = True  # Toolbox.call sets this for built-ins; _mcp_call cannot reach ctx
                    if not was_tainted:
                        yield "taint", {"message_id": am["id"], "source": c["name"]}
                    tool_ctx["taint_sources"].append(c["name"])
                event = {"id": uid, "name": c["name"], "arguments": args, "result_preview": preview, "duration_ms": ms,
                         "error": err, "images": images or None, **edit_info,
                         "undo": result.get("undo") if isinstance(result, dict) and isinstance(result.get("undo"), dict) else None,
                         "approval": (("plan" if claimed else decision) if mode == "ask" and not invalid else None),
                         "plan": {"plan_id": claimed["plan_id"], "idx": claimed["idx"], "title": claimed["title"]} if claimed else None,
                         "forced": forced, "tainted": tainted, "blocked": c["name"] if was_blocked else None, "breaker": partial,
                         **({"interrupted": True, "pending": False} if interrupted else {}),
                         **({"repaired": True} if c["_repaired"] else {}),
                         **({"invalid": invalid} if invalid else {}),
                         "blocked_by": "plan_mode" if blocked_reason == PLAN_BLOCKED else None,
                         "proposal": (result.get("proposal_id") if proposing and isinstance(result, dict) else None),
                         # Persisted with the tool event, so the card finds its artifact again after a reload.
                         "artifact": made}
                stuck = None
                if detector is not None and ran:
                    detector.observe(c["name"], args, result)
                    stuck = detector.check()
                    if stuck and stuck_hits == 0:
                        stuck_hits = 1
                        detector.obs.clear()  # the model gets a fresh run at it; the same shape again ends tool use
                        event["breaker"] = "stuck_nudge"
                    elif stuck:
                        stuck_hits += 1
                        partial, stop_text = "loop", STUCK_STOP.format(detail=stuck.detail)
                        event["breaker"] = "stuck"
                tracer.end(tspan, {"result_chars": len(preview), "images": len(images or []), **({"stuck": stuck.pattern} if stuck else {})}, error=err)
                if claimed is not None:
                    # The step was spent at the gate; this records whether the call it authorised
                    # actually worked, so the plan can be read after the fact.
                    plans.finish(uid, not err, err)
                tool_events.append(event)
                inflight = None
                yield "tool_result", {"message_id": am["id"], **event}
                if made and not err:
                    yield "artifact", {"message_id": am["id"], "call_id": uid, "conversation_id": conv_id, **made}
                yield "span", {"message_id": am["id"], "span": tspan}
                for_model = {**result, "images_shown_to_user": [i["name"] for i in images]} if images and isinstance(result, dict) else result
                if edit_info and isinstance(for_model, dict):
                    # The model proposed one thing and the user ran another: say so, or it reports the wrong call as done.
                    for_model = {**for_model, "note": "The user edited these arguments before approving; this ran with their version: "
                                 + json.dumps(args, default=str)[:1500]}
                # Small results go in whole; a big one is stored and replaced by a handle the model can
                # page with read_tool_result, so nothing is silently truncated away. See working.py.
                # Anything saved while this run is tainted can carry that text, including a script
                # that prints it. Paging the handle later has to taint again, even after the banner is cleared.
                brought_untrusted = bool(tool_ctx.get("tainted"))
                content, rid = tool_results.render(conv_id, am["id"], c["name"], for_model, untrusted=brought_untrusted)
                if rid:
                    event["result_id"] = rid  # persisted with the tool event, so later turns can name the handle
                if stuck and event["breaker"] == "stuck_nudge":
                    content = f"{content}\n\n[stuck_notice] {STUCK_NUDGE.format(detail=stuck.detail)}"
                messages.append({"role": "tool", "tool_call_id": c["id"], "content": content})
                if c["name"] == "mcp_tool_search":
                    # A search loads schemas: bring their modes in (grants and ask are already resolved
                    # in mcp_modes) and offer them from the next round.
                    for _slug in tool_ctx["mcp_loaded"]:
                        if _slug in mcp_modes:
                            modes.setdefault(_slug, mcp_modes[_slug])
                    tool_schemas = _schemas()
                if tool_ctx.pop("plan_changed", None):
                    yield "plan", {"conversation_id": conv_id, "steps": (work_plans.get(conv_id) or {}).get("steps") or []}
            if stop.is_set():  # no final round for a Stop: the existing stop path persists the reply
                break
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
        if inflight:
            # The call was executing when the task was cut: whether it took effect is unknown, and its journal row
            # stays `started`, so a resume does not repeat it either.
            kept = kept + [{**inflight, "result_preview": "", "duration_ms": 0, "pending": False, "interrupted": True,
                            "error": "The app closed while this was running; it may or may not have completed."}]
        gone = None
        if run is not None and run.kind == "chat" and not desk_id:
            gone = "Interrupted: the backend shut down while this reply was running."
        convos.finish_message(am["id"], text, gone or (None if text else "Cancelled"), used, kept, tracer.spans,
                              "".join(rbuf).strip() or None, outcome="interrupted")
        convos.touch(conv_id)
        await _end_jobs()
        raise
    except Exception as e:  # noqa: BLE001
        error = str(e)
        error_kind = getattr(e, "kind", None)
        for s in tracer.fail_open(error):
            yield "span", {"message_id": am["id"], "span": s}
    finally:
        _active.pop(am["id"], None)

    text = "".join(buf).strip()
    if not text and not error and not stop.is_set() and not tool_events and not desk_id:
        error = "The model returned an empty reply. Try again."
    reasoning = "".join(rbuf).strip() or None
    outcome = None if error else ("stopped" if stop.is_set() else partial)
    convos.finish_message(am["id"], text, error, used, tool_events, tracer.spans, reasoning,
                          outcome=outcome, error_kind=error_kind)
    convos.touch(conv_id)
    otel_export.export_in_background(cfg, conv_id, am["id"], model, project["name"] if project else None, tracer.spans, used, text)
    if tool_ctx["tainted"]:
        # Asking about the screen taints this turn only. Storing it would make Clear come back
        # on the next question, because the screen is sent again.
        srcs = sorted(s for s in set(tool_ctx["taint_sources"]) if s != "context:page")
        if srcs and (not conv["settings"].get("tainted") or srcs != sorted(set(conv["settings"].get("taint_sources") or []))):
            convos.update(conv_id, {"settings": {"tainted": True, "taint_sources": srcs}})
    if run is not None:
        # What this reply spent, for whoever is supervising it. A desk turn chains on these; an
        # ordinary chat never reads them back.
        run.partial, run.cost, run.rounds = partial, budget.cost, budget.rounds
    yield "done", {"id": am["id"], "error": error, "context_used": used, "tool_events": tool_events,
                   "trace": tracer.spans, "stopped": stop.is_set(), "partial": partial, "segment": False,
                   "tainted": tool_ctx["tainted"], "taint_sources": tool_ctx["taint_sources"],
                   "reasoning": reasoning, "outcome": outcome, "error_kind": error_kind, "notice": notice}
    await _end_jobs()  # after the done: _run_chat has marked the run replied, so a steer already gets its 409
    if tool_ctx.get("learned"):
        yield "learned", tool_ctx["learned"]

    # Auto-learn is another LLM call, and the run owns the conversation for as long as this
    # generator lives — a second message is a 409 until it returns. So the exchange is handed to the
    # worker and the run ends here; what the worker learns arrives on the app topic (GET /events).
    # A scheduled run never writes to long-term memory either way: it is one more model call nobody
    # asked for, on text the user has not read yet. What it found belongs in its report and the inbox.
    # A tainted reply has read someone else's page or transcript. Mining it into memory would
    # plant that text in later chats. The user can still save a memory by approving the tool.
    # A chat deleted mid-reply is not mined: the exchange is the user's to discard (Undo restores it unlearned).
    gone = convos.get(conv_id, with_messages=False) is None
    if (not error and text and not gone and not proposal_only(run) and not tool_ctx["tainted"]
            and cfg.get("autoLearn", True) and conv["settings"].get("autoLearn", True)):
        learner.submit(LearnJob(
            conversation_id=conv_id, message_id=am["id"], project_id=conv["project_id"],
            user_text=user_text, assistant_text=text, model=model, settings=cfg,
            spans=list(tracer.spans),
        ))

    # The model title: after the reply, off the run, from the user's typed text only. It replaces the placeholder this
    # turn wrote, or (once, at RETITLE_AT user turns) an earlier auto title; a title the user typed is never touched.
    # No taint or autoLearn gate: nothing but the user's own messages reaches the call.
    if not error and not gone and not proposal_only(run) and cfg.get("autoTitle", True):
        user_texts = [m["content"] for m in conv["messages"] if m["role"] == "user"] + [user_text]
        fresh = convos.get(conv_id, with_messages=False)
        fs = (fresh or {}).get("settings") or {}
        if placeholder_title is not None:
            if fs.get("titleSource") != "user" and fresh and fresh["title"] == placeholder_title:
                title_jobs.spawn(conv_id, conv["project_id"], placeholder_title, user_texts, model, cfg, len(user_texts))
        elif (fresh and fs.get("titleSource") == "auto" and len(user_texts) >= titles.RETITLE_AT
              and int(fs.get("titleTurns") or 0) < titles.RETITLE_AT):
            title_jobs.spawn(conv_id, conv["project_id"], fresh["title"], user_texts, model, cfg, len(user_texts))

    # Writing style, from the user's half of the exchange only (style.py). Banking a sample is free;
    # the LLM re-reads the samples only on the message that crosses the threshold, so most turns add
    # a row and stop. A failure here is as quiet as a failed memory extraction.
    # The prose check runs before the span so an ordinary short instruction leaves no trace of a step
    # that did nothing — and never leaves a span open for the UI to show as still running.
    # Same bound as auto-learn: a message in a chat that has read someone else's page is not a
    # sample of how the user writes. The voice profile is injected into later chats.
    if (not error and not gone and not tool_ctx["tainted"] and cfg.get("learnStyle", True)
            and conv["settings"].get("autoLearn", True) and looks_like_prose(user_text)):
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
    failure: str | None = None
    try:
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
    except BaseException as e:  # noqa: BLE001 - noted for the settle hook below, then re-raised untouched
        failure = None if isinstance(e, asyncio.CancelledError) else (str(e) or type(e).__name__)
        raise
    finally:
        # A regenerate that ended with nothing (provider error, Stop before the first token, a setup crash, shutdown)
        # puts the answer it superseded back. Synchronous, so it also runs while the task is being cancelled.
        if body.content is None and not body.resume_of:
            _settle_regenerate(run, failure)


def _settle_regenerate(run: Run, failure: str | None) -> None:
    try:
        restored = convos.restore_if_empty(run.conversation_id)
    except Exception:  # noqa: BLE001 - settling must never mask the run's own outcome
        log.warning("could not restore the superseded answer of conversation %s", run.conversation_id, exc_info=True)
        return
    if not restored:
        return
    if run.message_id:
        run.publish("removed_message", {"id": run.message_id})
    run.publish("restored_message", {"message": restored, "reason": failure or run.error})


async def _run_job(run: Run, body: ChatIn) -> None:
    """An unattended run is _run_chat under a hard ceiling, so a hung tool or provider cannot hold it open forever."""
    try:
        await asyncio.wait_for(_run_chat(run, body), JOB_HARD_SECONDS)
    except asyncio.TimeoutError:
        raise RuntimeError(f"This scheduled run was stopped after {int(JOB_HARD_SECONDS // 60)} minutes without finishing.") from None


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
            if event == "parked":
                # A parked turn returns without a `done`, but it did spend: count it, or a desk that keeps
                # parking never runs out of budget.
                desks.charge(desk_id, run.cost, 1)
            if event == "done" and not data.get("segment"):
                # A steered reply closes its current segment with its own `done` and keeps going;
                # only the run's real final `done` ends the turn, so only it may charge one.
                partial, stopped = data.get("partial"), bool(data.get("stopped"))
                err = err or data.get("error")
                # Charge BEFORE deciding, so the row both _should_chain calls read — this one and
                # the supervisor's after settle — has already counted this turn. Deciding on two
                # different rows is how a desk ends up `working` with nothing driving it.
                desks.charge(desk_id, run.cost, 1)
                chain = _should_chain(desks.get(desk_id) or {}, run, err)
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
    if (settled or {}).get("status") in ("done", "failed", "stopped", "review") and mac.page_bridge.has("browser"):
        # The desk is over (or waiting on a review of its files): its browser window has nothing left to do, and
        # leaving it to the idle timer would hold one of the few sessions the app allows.
        try:
            await mac.page_bridge.browser("close", {"session": f"desk:{desk_id}"}, 5.0)
        except Exception:  # noqa: BLE001 - closing a window must never change how a desk ended
            pass
    return settled


BUDGET_STOPS = ("rounds", "tokens", "time", "cost")  # per-reply window stops; not "loop" (stuck) or "blocked" (a card)


def _chain_kind(desk: dict[str, Any], run: Run, error: str | None = None) -> str | None:
    """Which turn follows this one: "continue", "nudge", or None (settle). Both decision points -
    the run's final `done` and the supervisor after the run ends - call this on the same row, so they
    cannot disagree.

    continue: any per-reply budget stop (with or without a plan) in a turn that made progress - consumed
    a plan step or ran a tool without error. The progress guard is what stops a desk burning turns
    re-reading the same file. nudge: the reply simply ended (no stop, no desk_done/desk_ask); one more
    turn tells the model to finish or ask, never two in a row. Caps hold for both."""
    caps = _desk_caps(settings(), desk.get("budget"))
    turns, cost = caps["deskMaxTurns"], caps["deskMaxCost"]
    # claim_run puts a desk with no plan into `planning` whatever its autonomy, and only a plan-autonomy desk
    # is actually held in plan mode (see _chat_stream); an ask/propose desk works from its first turn.
    live = desk.get("status") == "working" or (desk.get("status") == "planning" and desk.get("autonomy") != "plan")
    if not (live and not run.stop.is_set() and not error and not run.error
            # 0 on either axis means unlimited, the same reading _caps gives it.
            and (turns <= 0 or int(desk.get("turn") or 0) + 1 < turns)
            and (cost <= 0 or float(desk.get("cost") or 0) < cost)):
        return None
    if run.partial in BUDGET_STOPS:
        return "continue" if (run.steps_consumed > 0 or run.tool_ok > 0) else None
    if run.partial is None and not str(run.input.get("content") or "").startswith(DESK_NUDGE):
        return "nudge"
    return None


def _should_chain(desk: dict[str, Any], run: Run, error: str | None = None) -> bool:
    return _chain_kind(desk, run, error) is not None


def _desk_message(desk_id: str, kind: str) -> str:
    """continue_message with the desk's own PROGRESS.md appended: a new turn replays assistant text, not tool results."""
    return continue_message(kind, read_notes(workspace.ensure(desk_id)))


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
            kind = _chain_kind(state, run) if state else None
            if not state or not kind:
                # The run has really ended now, so a wake that lost the race against it can be
                # retried here (§F5): _launch_desk refuses while a run is live on the conversation,
                # and a run that parked a card stays live for _final_round and the learn tail.
                state = _missed_wake(desk_id)
                if not state:
                    return
                kind = "continue"
            body = ChatIn(content=_desk_message(desk_id, kind))
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
# Pause holds a desk that is doing something; pausing one in review or done would make it resumable
# work it is not. Stop ends anything not already over.
PAUSE_FROM = (*DESK_LIVE, "awaiting_plan")
STOP_FROM = ("draft", *DESK_LIVE, "awaiting_plan", "blocked", "paused", "interrupted")


def _over_live_cap() -> bool:
    cap = int(settings().get("deskMaxLive") or 0)
    return cap > 0 and desks.live_count() >= cap


def _launch_desk(desk_id: str, content: str | None, from_statuses: tuple[str, ...]) -> Run | None:
    """Claim the desk, start its first turn now — so the route can hand back a run_id — and give
    the chain to a supervisor task. None means the claim was lost or a run is already live."""
    desk = desks.get(desk_id, with_outputs=False)
    if not desk or bus.live(desk["conversation_id"]):
        return None
    # Every way in counts against the cap - start, resume, a message, a wake from an approval - not
    # only the two routes that used to check it. The desk is not live yet, so it is not counted.
    if _over_live_cap():
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
    return _launch_desk(desk_id, _desk_message(desk_id, "resume" if desk["status"] == "interrupted" else "continue"), RESUME_FROM)


def _shell_wake(conversation_id: str | None) -> None:
    """A background shell job finished in a desk that has no run going: wake it so it reads the result."""
    desk = desks.by_conversation(conversation_id) if conversation_id else None
    if not desk or bus.live(desk["conversation_id"]) or desk["status"] not in (*RESUME_FROM, "done"):
        return
    notes = toolbox.shell.drain_notes(conversation_id)
    if notes:
        _launch_desk(desk["id"], "\n\n".join(notes), (*RESUME_FROM, "done"))


toolbox.shell.on_note = _shell_wake


@app.post("/conversations/{id}/chat")
async def chat(id: str, body: ChatIn) -> dict[str, Any]:
    """Start the reply as a background task. Watch it on GET /conversations/{id}/stream?since=seq."""
    row = convos.get(id, with_messages=False)
    if not row:
        raise HTTPException(404, "Conversation not found")
    # Before anything is persisted or a run exists; a string detail, so the client toasts it as is.
    if body.content is not None and (too_long := _message_too_long(body.content, settings())):
        raise HTTPException(413, too_long)
    # A chat with a live run is never hidden: writing in an archived one brings it back.
    if row.get("archived_at"):
        convos.update(id, {"archived": False})
    # `answering`, not `live`: a run still auto-learning has finished its reply, and a new message
    # deserves a run of its own rather than a 409 the caller can only turn into a dropped steer.
    running = bus.answering(id)
    if running:
        raise HTTPException(409, {"message": "That conversation already has a running reply",
                                 "run_id": running.run_id, "seq": running.seq,
                                 "message_id": running.message_id, "message_seq": running.message_seq})
    if body.replace_from:
        conv = convos.get(id)
        if not (body.content or "").strip():
            raise HTTPException(400, "Empty message")
        if conv["settings"].get("deskId") or conv["settings"].get("job_id"):
            raise HTTPException(400, "A desk or job transcript cannot be edited")
        target = next((m for m in conv["messages"] if m["id"] == body.replace_from), None)
        if not target or target["role"] != "user":
            raise HTTPException(404, "No such message to edit")
    run = bus.start(id, lambda r: _run_chat(r, body), input=body.model_dump())
    return {"run_id": run.run_id, "seq": run.seq}


class SteerIn(BaseModel):
    content: str


@app.post("/conversations/{id}/steer")
async def steer_run(id: str, body: SteerIn) -> dict[str, Any]:
    """Inject a user message into a run that is still answering; it replies in a fresh segment.

    The message is persisted and published here, so a window sees it immediately. Nothing can slip
    in after the round loop ends: `run.replied` is set in the same synchronous step that publishes
    `done`, with no await between, so a handler that observes `answering` still has a round coming. The
    generator holds the other end of that: after its last steer check it awaits nothing before `done`
    (shell teardown runs after it), so a steer is either folded in or gets this 409 and becomes a new run.
    """
    if not convos.get(id):
        raise HTTPException(404, "Conversation not found")
    text = (body.content or "").strip()
    if not text:
        raise HTTPException(400, "Empty message")
    if too_long := _message_too_long(text, settings()):
        raise HTTPException(413, too_long)
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
    return bus.list(statuses, conversation_id, _clamp(limit), desk_id)


@app.get("/runs/{run_id}/children")
async def run_children(run_id: str) -> list[dict[str, Any]]:
    """Subagents started by this run (or by another subagent), with live state while they run."""
    if not run_store.get(run_id):
        raise HTTPException(404, "No such run")
    out = []
    for r in run_store.children(run_id):
        live = subagent_mgr.children.get(r["run_id"])
        out.append({**r, "agent": subagent_mgr.info(live) if live else None})
    return out


@app.get("/runs/{run_id}/events")
async def run_events(run_id: str, since: int = 0, limit: int = 500) -> list[dict[str, Any]]:
    """A run's recorded tape (a subagent's trace). The transcript event carries a whole history and is left out."""
    if not run_store.get(run_id):
        raise HTTPException(404, "No such run")
    evs = [{"seq": s, "event": e, "data": d} for s, e, d in run_store.events(run_id, since) if e != "transcript"]
    return evs[:max(1, min(limit, 2000))]


# ---------------- workflows and commands ----------------
class WorkflowIn(BaseModel):
    text: str


class WorkflowRunIn(BaseModel):
    params: dict[str, Any] = {}


class WorkflowApproveIn(BaseModel):
    plan_digest: str


@app.on_event("startup")
async def _recover_workflows() -> None:
    """A workflow run left running died with the last process: it becomes interrupted and Resume continues it."""
    workflow_store.recover()


@app.post("/workflows/validate")
def validate_workflow(body: WorkflowIn) -> dict[str, Any]:
    """Every problem with a definition, for the editor. Never saves."""
    try:
        defn = _wf_parse(body.text)
    except ValueError as e:
        return {"ok": False, "errors": [str(e)]}
    errs = workflow_store.check(defn)
    return {"ok": not errs, "errors": errs}


def _wf_parse(text: str) -> dict[str, Any]:
    from . import workflows as _w
    return _w.parse(text)


@app.get("/workflows")
def list_workflows() -> list[dict[str, Any]]:
    return workflow_store.list()


@app.post("/workflows")
def create_workflow(body: WorkflowIn) -> dict[str, Any]:
    try:
        return workflow_store.save(body.text)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.put("/workflows/{wf_id}")
def update_workflow(wf_id: str, body: WorkflowIn) -> dict[str, Any]:
    if not workflow_store.get(wf_id):
        raise HTTPException(404, "No such workflow")
    try:  # an edit changes the digest, which is what invalidates every run still waiting on the old one
        return workflow_store.save(body.text, wf_id)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.delete("/workflows/{wf_id}")
def delete_workflow(wf_id: str) -> dict[str, bool]:
    if not workflow_store.delete(wf_id):
        raise HTTPException(404, "No such workflow")
    return {"ok": True}


@app.post("/workflows/{wf_id}/runs")
def propose_workflow_run(wf_id: str, body: WorkflowRunIn) -> dict[str, Any]:
    """Record a run with its expanded plan. It starts only when the user approves its digest."""
    wf = workflow_store.get(wf_id)
    if not wf:
        raise HTTPException(404, "No such workflow")
    try:
        return workflow_store.create_run(wf, body.params, source="user")
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.get("/workflow-runs")
def list_workflow_runs(workflow_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    return workflow_store.list_runs(workflow_id, max(1, min(limit, 200)))


@app.get("/workflow-runs/{run_id}")
def get_workflow_run(run_id: str) -> dict[str, Any]:
    r = workflow_store.get_run(run_id)
    if not r:
        raise HTTPException(404, "No such run")
    return r


@app.post("/workflow-runs/{run_id}/approve")
async def approve_workflow_run(run_id: str, body: WorkflowApproveIn) -> dict[str, Any]:
    try:
        return workflow_engine.approve(run_id, body.plan_digest)
    except WorkflowApprovalError as e:
        raise HTTPException(409, str(e)) from e


@app.post("/workflow-runs/{run_id}/resume")
async def resume_workflow_run(run_id: str) -> dict[str, Any]:
    try:
        return workflow_engine.resume(run_id)
    except WorkflowApprovalError as e:
        raise HTTPException(409, str(e)) from e


@app.post("/workflow-runs/{run_id}/cancel")
async def cancel_workflow_run(run_id: str) -> dict[str, bool]:
    if not workflow_store.get_run(run_id):
        raise HTTPException(404, "No such run")
    return {"ok": workflow_engine.cancel(run_id)}


@app.get("/commands")
def list_commands() -> list[dict[str, Any]]:
    return command_store.list()


@app.post("/commands")
def create_command(body: WorkflowIn) -> dict[str, Any]:
    try:
        return command_store.save(body.text)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.put("/commands/{cmd_id}")
def update_command(cmd_id: str, body: WorkflowIn) -> dict[str, Any]:
    if not command_store.get(cmd_id):
        raise HTTPException(404, "No such command")
    try:
        return command_store.save(body.text, cmd_id)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.delete("/commands/{cmd_id}")
def delete_command(cmd_id: str) -> dict[str, bool]:
    if not command_store.delete(cmd_id):
        raise HTTPException(404, "No such command")
    return {"ok": True}


class AgentDefIn(BaseModel):
    text: str


@app.get("/agents/defs")
async def list_agent_defs() -> dict[str, Any]:
    """The built-in agent roles and the user's own definitions (inert until approved)."""
    from .subagents import BUILTIN_ROLES
    return {"builtin": [{"name": r.name, "description": r.description, "tools": list(r.tools)} for r in BUILTIN_ROLES.values()],
            "custom": agent_defs.list()}


@app.post("/agents/defs")
async def create_agent_def(body: AgentDefIn) -> dict[str, Any]:
    try:
        return agent_defs.save(body.text)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.put("/agents/defs/{def_id}")
async def update_agent_def(def_id: str, body: AgentDefIn) -> dict[str, Any]:
    if not agent_defs.get(def_id):
        raise HTTPException(404, "No such agent definition")
    try:
        return agent_defs.save(body.text, def_id)  # editing withdraws the approval
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.post("/agents/defs/{def_id}/approve")
async def approve_agent_def(def_id: str, approved: bool = True) -> dict[str, Any]:
    row = agent_defs.approve(def_id, approved)
    if not row:
        raise HTTPException(404, "No such agent definition")
    return row


@app.delete("/agents/defs/{def_id}")
async def delete_agent_def(def_id: str) -> dict[str, bool]:
    return {"ok": agent_defs.delete(def_id)}


@app.get("/runs/{run_id}")
async def get_run(run_id: str) -> dict[str, Any]:
    """One run row, with its approvals and its idempotency journal."""
    row = run_store.get(run_id)
    if not row:
        raise HTTPException(404, "No such run")
    mem = bus.get(row["conversation_id"]) if row["conversation_id"] else None
    over = mem.info() if mem is not None and mem.run_id == run_id else {"seq": row["last_seq"], "live": False}
    return {**row, **over, "approvals": run_store.approvals(None, run_id=run_id), "executed_calls": run_store.executed(run_id),
            "plans": plans.for_run(run_id), "resumable": (res := _resumable(row))[0], "resume_reason": res[1]}


def _resumable(row: dict[str, Any]) -> tuple[bool, str]:
    cid = row.get("conversation_id")
    if not cid:
        return False, "no conversation"
    return resume.resumable(row, run_store.latest(cid), bool(bus.answering(cid)), bool(run_store.resumed_by(row["run_id"])),
                            message=_run_message(row))


def _run_message(row: dict[str, Any]) -> dict[str, Any] | None:
    """The reply row a run wrote, for its outcome."""
    mid = row.get("message_id")
    if not mid:
        return None
    with db.tx() as c:
        r = c.execute("SELECT outcome, error FROM messages WHERE id=?", (mid,)).fetchone()
    return dict(r) if r else None


@app.get("/conversations/{id}/resumable")
async def conversation_resumable(id: str) -> dict[str, Any]:
    """Whether the conversation's latest reply stopped short and can be continued, in one query for the UI."""
    if not convos.get(id, with_messages=False):
        raise HTTPException(404, "No such conversation")
    row = run_store.latest(id)
    if not row:
        return {"run_id": None, "resumable": False, "reason": "no run", "message_id": None}
    ok, reason = _resumable(row)
    return {"run_id": row["run_id"], "resumable": ok, "reason": reason, "message_id": row.get("message_id")}


@app.post("/runs/{run_id}/resume")
async def resume_run(run_id: str) -> dict[str, Any]:
    """Continue an interrupted chat reply in a new run. Always the user's click: nothing calls this on a timer."""
    row = run_store.get(run_id)
    if not row:
        raise HTTPException(404, "No such run")
    ok, reason = _resumable(row)
    if not ok:
        raise HTTPException(409, {"reason": reason})
    run = bus.start(row["conversation_id"], lambda r: _run_chat(r, ChatIn(resume_of=run_id)), input={"resume_of": run_id})
    run_store.set_resumed_from(run.run_id, run_id)
    return {"run_id": run.run_id, "seq": run.seq}


# async, so the run's asyncio.Event is set on the loop that owns it rather than from a threadpool.
@app.post("/conversations/{id}/stop")
async def stop_run(id: str, run_id: str | None = None) -> dict[str, bool]:
    """Stop before the assistant message exists: detaching a stream would only drop a viewer."""
    return {"ok": bus.stop(id, run_id)}


class ApprovalIn(BaseModel):
    decision: str  # allow | deny | always_chat | always_global | always_session | always_rule
    # propose_plan only: the steps of the plan the user is authorising, as [{idx, arguments?}]. A step left out is
    # dropped (it asks again if the model calls it); replacement arguments re-derive that step's digest, so the
    # edited values are what gets authorised.
    steps: list[dict[str, Any]] | None = None
    note: str | None = None  # one line back to the model, e.g. why a plan was rejected or a call was denied
    rules: list[str] | None = None  # always_rule: the rules to save, as edited on the card (default: the suggestions)
    # An editable tool's approval only (approval_edits.EDITABLE_TOOLS): the arguments the user wants run instead of the
    # model's. Validated against the tool's schema; what executes, is journaled and is verified is this, not the original.
    arguments: dict[str, Any] | None = None


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


def _untrashed(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pending cards of a trashed chat are hidden, not decided: Undo brings the chat and its cards back."""
    return [a for a in rows if not a.get("conversation_id") or convos.get(a["conversation_id"], with_messages=False)]


@app.get("/approvals")
async def list_approvals(status: str | None = "pending", run_id: str | None = None, limit: int = 100,
                         desk_id: str | None = None) -> list[dict[str, Any]]:
    """Approval rows, pending by default -- including ones whose run was interrupted, so they can still be answered.
    `live` says whether a run in this process is waiting on it."""
    if status not in (None, "", "all", "pending", "approved", "denied"):
        raise HTTPException(400, "status must be pending, approved, denied or all")
    rows = run_store.approvals(None if status in (None, "", "all") else status, run_id, _clamp(limit), desk_id)
    if status == "pending":
        rows = _untrashed(rows)
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
    if body.decision not in ("allow", "deny", "always_chat", "always_global", "always_session", "always_rule"):
        raise HTTPException(400, "Bad decision")
    pending = run_store.approval(call_id)
    is_plan = bool(pending and pending["tool"] == PLAN_TOOL)
    if body.decision == "always_rule" and pending and not pending["forced"] and not is_plan and pending["status"] == "pending":
        _save_allow_rules(pending, body.rules)  # validated before anything is decided
    if body.steps is not None and not is_plan:
        raise HTTPException(400, "Only a propose_plan approval carries edited steps")
    try:  # shape-check the edit before anything is decided, so a bad payload leaves the row pending
        edits = parse_plan_edits(body.steps)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    edited: dict[str, Any] | None = None
    if body.arguments is not None:
        if body.decision == "deny":
            raise HTTPException(400, "A denial cannot carry edited arguments")
        if is_plan:
            raise HTTPException(400, "A plan is edited through its steps, not arguments")
        if pending is None or pending["status"] != "pending":
            raise HTTPException(404, "No pending approval for that call")
        if pending.get("decided_by") == "park":
            # No run is waiting on a parked card and nothing reads its edit back, so "approved with edits" would be a
            # lie: the resumed desk would not run these arguments. Decide it as it is, or deny it.
            raise HTTPException(400, "This approval is parked and cannot take edits; approve it as proposed or deny it")
        spec = toolbox.specs.get(pending["tool"])
        try:
            edited = approval_edits.validate(pending["tool"], body.arguments, spec.parameters if spec else None)
        except approval_edits.EditError as e:
            raise HTTPException(400, str(e)) from e
    fut = _approvals.get(call_id)
    if body.decision == "deny" and body.note and not is_plan and fut and not fut.done():
        _approval_notes[call_id] = body.note.strip()[:500]
    row = run_store.decide(call_id, body.decision, note=None if is_plan else body.note, edited_args=edited)
    live = bool(fut and not fut.done())
    # Read off the row as it was BEFORE this decision: decide() overwrites `decided_by` with 'user'.
    was_parked = bool(pending and pending.get("parked_at"))
    if row is None and not live:
        raise HTTPException(404, "No pending approval for that call")
    if row is not None and is_plan:
        # The plan is decided before the run is woken: what it reads back is the user's answer, edits included.
        plans.decide(call_id, body.decision, edits=edits, note=body.note)
    if live:
        fut.set_result(body.decision)  # type: ignore[union-attr]
    elif row is not None and row.get("desk_id") and was_parked:
        # A desk card its run let go of: the answer is carried to the desk's next turn
        # (parked_report), so the card settles as answered rather than as an interrupted call.
        _patch_tool_event(row["message_id"], call_id, {
            "pending": False, "needs_approval": False, "approval": body.decision, "parked": False,
            "result_preview": ("Answered after the desk paused; picked up on its next turn."
                               + (f" Answer: {body.note.strip()}" if (body.note or "").strip() else ""))})
    elif row is not None:
        _patch_tool_event(row["message_id"], call_id, {
            "pending": False, "needs_approval": False, "approval": body.decision,
            **({"arguments": edited, "original_arguments": row["args"], "edited_arguments": edited, "edited_by": "user"} if edited else {}),
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


def _save_allow_rules(row: dict[str, Any], texts: list[str] | None) -> list[str]:
    """Append the rules a card offered (possibly edited) to permissionRules.allow. Raises 400 on a bad rule."""
    cfg = settings()
    if texts is None:
        texts = permrules.evaluate(row["tool"], row["args"], permrules.load_rules(cfg.get("permissionRules")),
                                   roots=_perm_roots(cfg, row.get("desk_id"))).suggestions
    try:
        rules = permrules.validate_saved_rules(row["tool"], row["args"], texts)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    cur = {k: list((cfg.get("permissionRules") or {}).get(k) or []) for k in ("allow", "ask", "deny")}
    cur["allow"] += [r for r in rules if r not in cur["allow"]]
    db.set_settings({"permissionRules": cur})
    return rules


class PermissionEvalIn(BaseModel):
    tool: str = "shell_run"
    command: str | None = None            # shorthand for tool=shell_run
    args: dict[str, Any] = {}
    desk_id: str | None = None
    rule: str | None = None               # validate one rule string instead of evaluating a call


@app.post("/permissions/evaluate")
def evaluate_permission(body: PermissionEvalIn) -> dict[str, Any]:
    """What the saved rules say about one call, for the Settings test box. Nothing runs."""
    if body.rule is not None:
        try:
            r = permrules.parse_rule(body.rule)
            return {"ok": True, "rule": r.text, "tool": r.tool, "pattern": r.pattern}
        except ValueError as e:
            return {"ok": False, "error": str(e)}
    cfg = settings()
    args = {"command": body.command, **body.args} if body.command is not None else body.args
    v = permrules.evaluate(body.tool, args, permrules.load_rules(cfg.get("permissionRules")), roots=_perm_roots(cfg, body.desk_id))
    return {"action": v.action or "none", "hardline": v.hardline, "reason": v.refusal, "rule": v.rule, "kind": v.kind,
            "subjects": v.subjects, "suggestions": v.suggestions, "external": v.external}


@app.on_event("startup")
async def _recover_runs() -> None:
    """Runs left active by the last process died with it: mark them interrupted and salvage their reply from the tape."""
    recovered = run_store.recover(bus.live_ids())
    for r in recovered:
        mid = r.get("message_id")
        if not mid:
            continue
        try:
            with db.tx() as c:
                cur = c.execute("SELECT content, tool_events, context_used FROM messages WHERE id=?", (mid,)).fetchone()
            if cur is None or cur["content"] or cur["tool_events"]:
                continue  # already finished (the cancel path persisted it): the message is the better record
            text, tape = run_store.transcript(r["run_id"], mid)
            # Finished calls carry a duration; a bare tool_call with no result was running when the app died, so it
            # may or may not have completed. A call waiting on a card is one of the pending approvals, not that.
            tool_events = [e for e in tape if "duration_ms" in e]
            done = {e.get("id") for e in tool_events}
            asked = {a["call_id"] for a in r["pending_approvals"] if a["message_id"] == mid}
            for e in tape:
                if "duration_ms" not in e and e.get("id") not in asked:
                    tool_events.append({"id": e.get("id"), "name": e.get("name"), "arguments": e.get("arguments") or {},
                                        "result_preview": "", "duration_ms": 0, "pending": False, "interrupted": True,
                                        "error": "The app closed while this was running; it may or may not have completed."})
            for a in r["pending_approvals"]:
                if a["message_id"] == mid and a["call_id"] not in done:
                    tool_events.append({"id": a["call_id"], "name": a["tool"], "arguments": a["args"], "result_preview": "",
                                        "duration_ms": 0, "error": None, "pending": True, "needs_approval": True, "forced": a["forced"]})
            convos.finish_message(mid, text, r["error"], json.loads(cur["context_used"]) if cur["context_used"] else None, tool_events,
                                  outcome="interrupted")
        except Exception:  # noqa: BLE001 - recovery must never stop the backend from starting
            log.warning("could not salvage the reply of run %s", r["run_id"], exc_info=True)
    # A regenerate that died before any text is replaced by the answer it superseded.
    for cid in {r["conversation_id"] for r in recovered if r.get("kind", "chat") == "chat" and r.get("conversation_id")}:
        try:
            convos.restore_if_empty(cid)
        except Exception:  # noqa: BLE001
            log.warning("could not restore the superseded answer of conversation %s", cid, exc_info=True)


# ---------------- scheduled jobs, proposals, agent inbox ----------------
# A job fire is a chat run in its own conversation, with kind='job', so it gets the journal, the budget snapshot
# and the idempotency journal for free — and proposal_only() for free with them.
LATE_NOTICE = ("[This run was scheduled for {due}, and is only starting now, at {fired} — {late} late{skipped}. "
               "Say so in one line at the top of your report, and re-check anything time-sensitive rather than "
               "assuming it is still true.]")
# The journal step a proposal's execution is booked under. No chat round uses a negative one.
PROPOSAL_STEP = -1


def _all_tool_infos() -> list[dict[str, Any]]:
    """Every tool a job could be given: the built-ins, and the connected-or-not MCP slugs (all of them external)."""
    mcp_infos = [{"name": t["slug"], "group": "mcp", "danger": "external"} for t in mcp_store.tools()]
    return toolbox.list() + mcp_infos


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
    # The previous fire still running (a card nobody can answer, a long job) must not stack another run behind it.
    prev = job.get("last_run_id")
    if prev and prev in bus.live_ids() and not fire.get("manual"):
        log.warning("job %s skipped: its previous run %s is still live", job["name"], prev)
        return prev  # handed back so the scheduler's mark_launched keeps pointing at the live run, not at nothing
    cfg = settings()
    conv = convos.create(job["project_id"], f"{job['name']} · {_stamp(fire['due_at'])}", cfg.get("defaultModel") or "")
    # job_id keeps this transcript out of the sidebar's chat list; the Agent Inbox links to it instead.
    conv_settings: dict[str, Any] = {"useTools": True, "autoLearn": False, "job_id": job["id"]}
    # Narrowing only: tools outside the job's allowlist (and, for a preview, everything that is not read-only) are
    # switched off for this conversation. Tools left out of the map keep the user's own modes.
    if fire.get("dry_run"):
        conv_settings["tools"] = job_tools.dry_run_modes(job.get("allowed_tools"), _all_tool_infos())
    elif job.get("allowed_tools") is not None:
        conv_settings["tools"] = job_tools.tool_modes(job["allowed_tools"], _all_tool_infos())
    convos.update(conv["id"], {"settings": conv_settings})
    prompt = _job_prompt(job, fire)
    body = ChatIn(content=(job_tools.DRY_RUN_HINT + "\n\n" + prompt) if fire.get("dry_run") else prompt)
    run = bus.start(conv["id"], lambda r: _run_job(r, body), input={**fire, "conversation_id": conv["id"]}, kind="job")
    log.info("job %s fired for %s as run %s", job["name"], _stamp(fire["due_at"]), run.run_id)
    # Runs after _drive has ended the run, so the row the renderer then asks about is final. Ids only: the
    # app topic is a doorbell, and what to notify about is decided from rows by GET /inbox/notify.
    if run.task is not None:
        run.task.add_done_callback(lambda _t, rid=run.run_id, jid=job["id"]: events.publish("job_finished", {"run_id": rid, "job_id": jid}))
    return run.run_id


job_policy = JobPolicy(jobs, run_store, _launch_job, settings=settings)
scheduler = Scheduler(jobs, _launch_job, policy=job_policy)


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
    max_retries: int = Field(default=1, ge=0, le=5)
    # None = every tool, as before. A list narrows the run to exactly those tools (job_tools).
    allowed_tools: list[str] | None = None


class JobPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    prompt: str | None = Field(default=None, min_length=1, max_length=8000)
    kind: str | None = None
    cron: str | None = Field(default=None, max_length=120)
    run_at: float | None = None
    timezone: str | None = None
    enabled: bool | None = None
    project_id: str | None = None
    max_retries: int | None = Field(default=None, ge=0, le=5)
    allowed_tools: list[str] | None = None  # an explicit null resets to "every tool"


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


def _check_allowed_tools(allowed: list[str] | None) -> None:
    if allowed is None:
        return
    unknown, banned = job_tools.check(allowed, _all_tool_infos())
    if unknown:
        raise HTTPException(400, "Unknown tool" + ("s" if len(unknown) > 1 else "") + ": " + ", ".join(unknown))
    if banned:
        raise HTTPException(400, ", ".join(banned) + " books future unattended work, which a scheduled run may not do")


@app.get("/jobs")
def list_jobs() -> list[dict[str, Any]]:
    """Every scheduled job, with the slot it is waiting for. `timezone` defaults to this machine's on create."""
    return jobs.list()


@app.post("/jobs")
def create_job(body: JobIn) -> dict[str, Any]:
    _check_schedule(body.kind, body.cron, body.timezone, body.run_at, fresh_time=True)
    _check_allowed_tools(body.allowed_tools)
    return jobs.create(body.name, body.cron, body.prompt, kind=body.kind, run_at=body.run_at,
                       timezone=body.timezone, enabled=body.enabled, project_id=wsid(body.project_id),
                       max_retries=body.max_retries, allowed_tools=body.allowed_tools)


@app.patch("/jobs/{id}")
def update_job(id: str, body: JobPatch) -> dict[str, Any]:
    patch = body.model_dump(exclude_unset=True)
    if "project_id" in patch:
        patch["project_id"] = wsid(patch["project_id"])
    cur = jobs.get(id)
    if not cur:
        raise HTTPException(404, "No such job")
    merged = {**cur, **patch}
    _check_allowed_tools(patch.get("allowed_tools"))
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
    ok, why = await job_policy.admit(job, fire)
    if not ok:
        raise HTTPException(409, f"Not started: {why}")
    run_id = await _launch_job(job, fire)
    jobs.mark_launched(job["id"], run_id)
    row = run_store.get(run_id) if run_id else None
    return {"ok": bool(run_id), "run_id": run_id, "conversation_id": (row or {}).get("conversation_id")}


def _job_run_summaries(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts = proposals.counts([r["run_id"] for r in rows])
    out = []
    for r in rows:
        text = run_store.transcript(r["run_id"], r["message_id"])[0] if r.get("message_id") else ""
        out.append(job_history.summarize_run(r, run_store.event_counts(r["run_id"]), counts.get(r["run_id"], {}), text))
    return out


def _known_job(id: str) -> dict[str, Any]:
    job = jobs.get(id)
    if not job:
        raise HTTPException(404, "No such job")
    return job


@app.get("/jobs/{id}/runs")
def job_runs(id: str, limit: int = 50) -> list[dict[str, Any]]:
    """The job's last runs (50 by default, 200 at most): status incl. timed_out, duration, cost, tool and proposal counts."""
    _known_job(id)
    return _job_run_summaries(run_store.of_job(id, limit))


@app.get("/jobs/{id}/stats")
def job_stats(id: str, days: float = 30.0) -> dict[str, Any]:
    _known_job(id)
    since = time.time() - max(0.0, float(days)) * 86400
    rows = [job_history.summarize_run(r, None, None) for r in run_store.of_job(id, 200, since)]
    return job_history.stats(rows)


@app.get("/jobs/{id}/runs.csv")
def job_runs_csv(id: str, limit: int = 200) -> Response:
    """The run history as a CSV download. Local only: nothing is sent anywhere."""
    job = _known_job(id)
    body = job_history.to_csv(_job_run_summaries(run_store.of_job(id, limit)))
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", job["name"])[:40]
    return Response(body, media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="job-{slug}-runs.csv"'})


@app.post("/jobs/{id}/dry_run")
async def dry_run_job(id: str) -> dict[str, Any]:
    """Preview a job: the same prompt on the same budget with every tool that is not read-only switched off, and a
    line telling the model to describe rather than do. Still a job run, so proposal-only; with nothing outward
    available it makes no proposals. Hidden from the inbox's "while you were away" and never counted as a failure."""
    job = _known_job(id)
    t = time.time()
    fire = {"job_id": job["id"], "job": job["name"], "kind": job["kind"], "cron": job["cron"], "timezone": job["timezone"],
            "due_at": t, "fired_at": t, "late_seconds": 0.0, "missed_slots": 0, "late": False, "manual": True, "dry_run": True}
    run_id = await _launch_job(job, fire)
    row = run_store.get(run_id) if run_id else None
    return {"ok": bool(run_id), "run_id": run_id, "conversation_id": (row or {}).get("conversation_id")}


class ProposalIn(BaseModel):
    args: dict[str, Any] | None = None  # the user's edit, accept only


@app.get("/proposals")
def list_proposals(status: str | None = "pending", run_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    """Outward-facing calls a background run recorded instead of making. Pending by default."""
    if status not in (None, "", "all", *PROPOSAL_STATUSES):
        raise HTTPException(400, "status must be pending, accepted, rejected or all")
    return proposals.list(None if status in (None, "", "all") else status, run_id, _clamp(limit))


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

    async def _execute() -> Any:
        # A connector is not a built-in. toolbox.call would report it as unknown and the accept would die.
        if mcp_is(claimed["tool"]):
            return await _mcp_call(claimed["tool"], claimed["args"])
        return await toolbox.call(claimed["tool"], claimed["args"], ctx)

    # call_id is not part of the journal key, so two identical proposals from one run would share it and the second
    # accept would "replay" the first's result without running. A step derived from the proposal id keeps them apart.
    step = PROPOSAL_STEP - 1 - int(hashlib.sha256(pid.encode()).hexdigest()[:12], 16)
    result, replayed = await run_store.call_once(claimed["run_id"], step, claimed["tool"], claimed["args"],
                                                 _execute, call_id=pid)
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
def agent_inbox(hours: float = 72.0, limit: int = 20, include_dry: int = 0) -> dict[str, Any]:
    """The Agent Inbox, built from rows only: agent_runs + run_events + approvals + proposals.

    "Needs you" is the pending approvals and the pending proposals. "While you were away" is one entry per job run,
    whose late-fire notice, failure and counts all come from the journal — the reply text is shown as the body, but
    nothing about the entry is parsed out of it.
    """
    cutoff = time.time() - max(0.0, float(hours)) * 3600
    pending_approvals = []
    for a in _untrashed(run_store.approvals("pending", limit=100)):
        row = run_store.get(a["run_id"]) if a["run_id"] else None
        pending_approvals.append({**a, "live": a["call_id"] in _approvals, "run_kind": (row or {}).get("kind"),
                                  "job": ((row or {}).get("input") or {}).get("job")})
    pending_proposals = proposals.list("pending", limit=100)

    runs = run_store.of_kind("job", since=cutoff, limit=_clamp(limit))
    if not include_dry:  # a preview is not something that happened while the user was away
        runs = [r for r in runs if not (isinstance(r.get("input"), dict) and r["input"].get("dry_run"))]
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
            "attempt": int(fire.get("attempt") or 1), "retry_of": fire.get("retry_of"),
            "started_at": r["started_at"], "ended_at": r["ended_at"], "error": r["error"],
            "tool_calls": ev.get("tool_result", 0), "proposals": sum(mine.values()),
            "pending_proposals": mine.get("pending", 0),
            "summary": text[:INBOX_SUMMARY_CHARS] + ("…" if len(text) > INBOX_SUMMARY_CHARS else ""),
        })
    paused_jobs = [{"id": jb["id"], "name": jb["name"], "reason": jb["paused_reason"], "paused_at": jb["updated_at"],
                    "consecutive_failures": jb["consecutive_failures"]}
                   for jb in jobs.list() if not jb["enabled"] and jb.get("paused_reason")]
    return {"needs_you": {"approvals": pending_approvals, "proposals": pending_proposals, "paused_jobs": paused_jobs},
            "while_you_were_away": away,
            "counts": {"needs_you": len(pending_approvals) + len(pending_proposals) + len(paused_jobs),
                       "paused_jobs": len(paused_jobs),
                       "approvals": len(pending_approvals), "proposals": len(pending_proposals),
                       "runs": len(away), "late": sum(1 for a in away if a["late"]),
                       "failed": sum(1 for a in away if a["status"] in ("error", "interrupted"))},
            "scheduler": {"last_tick": scheduler.last_tick, "fires": scheduler.fires,
                          "next_due_at": jobs.earliest_due(), "timezone": local_tz_name()}}


@app.get("/inbox/notify")
def inbox_notify(since: float = 0.0) -> list[dict[str, Any]]:
    """Events worth an OS notification since `since` (unix seconds), at most 20. Names and counts only: never reply
    text, which can quote mail. The renderer decides whether to show them (app hidden, notifyJobs on)."""
    by_id = {j["id"]: j for j in jobs.list()}
    runs = run_store.of_kind("job", since=max(0.0, since - 86400), limit=100)
    counts = proposals.counts([r["run_id"] for r in runs])
    rows = [job_history.summarize_run(r, None, counts.get(r["run_id"], {})) for r in runs]
    return job_history.notify_events(rows, by_id, proposals.list("pending", limit=100), since)


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
    await job_policy.shutdown()
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


async def _doc_hits(project_id: str | None, query: str, cfg: dict[str, Any], conv_settings: dict[str, Any]) -> list[dict[str, Any]] | None:
    """Pre-computed excerpts for build_context (which is sync). None = let it fall back to BM25."""
    if not conv_settings.get("useDocuments", True):
        return None
    try:
        srcs = ("files", "docs") if cfg.get("useDocsInContext", True) else ("files",)
        return await retriever.search(project_id, query, cfg, sources=srcs)
    except Exception:  # noqa: BLE001 - retrieval must never break a reply
        log.exception("retrieval failed; falling back to keyword search")
        return None


async def _memory_hits(project_id: str | None, query: str, cfg: dict[str, Any], conv_settings: dict[str, Any]) -> list[dict[str, Any]] | None:
    """Fused memory hits for build_context. None = embeddings off or unavailable: the plain pinned/recent + BM25 path."""
    if not conv_settings.get("useMemory", True):
        return None
    try:
        memory_index.schedule(cfg)  # lazily embed rows that have no vector yet
        qvec = await memory_index.query_vec(cfg, query)
        if qvec is None:
            return None
        return memory_index.search(project_id, query, qvec, limit=40, settings=cfg)
    except Exception:  # noqa: BLE001
        log.exception("memory retrieval failed; falling back to keyword search")
        return None


@app.post("/context/preview")
async def context_preview(body: ContextPreviewIn) -> dict[str, Any]:
    cfg = settings()
    project = projects.get(body.project_id) if sid(body.project_id) else None
    conv_settings = {"useMemory": True, "useGraph": True, "useDocuments": True, "useActivity": True,
                     "useSkills": True, "useStyle": True, "useMeetings": True, **body.conv_settings}
    _, used = build_context(
        memories=memories, graph=graph, documents=documents, project=project,
        doc_hits=await _doc_hits(sid(body.project_id), body.query, cfg, conv_settings), project_id=sid(body.project_id),
        memory_hits=await _memory_hits(sid(body.project_id), body.query, cfg, conv_settings),
        query=body.query, settings=cfg, conv_settings=conv_settings,
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
def list_memories(project_id: str | None = None, q: str = "", include_global: bool = True, include_invalid: bool = False) -> list[dict[str, Any]]:
    return memories.list(sid(project_id), q, include_global, include_invalid)


class ConsolidateIn(BaseModel):
    project_id: str | None = None
    model: str | None = None


@app.post("/memories/consolidate")
async def consolidate_memories(body: ConsolidateIn) -> list[dict[str, Any]]:
    """Manual 'Tidy up': proposes only. Nothing changes until a proposal is applied by hand."""
    cfg = settings()
    return await consolidator.propose(cfg, sid(body.project_id), body.model or cfg["defaultModel"])


@app.post("/memories/reindex")
async def reindex_memories() -> dict[str, Any]:
    """Embed every live memory that has no current vector (200 per call; call again while `pending` > 0)."""
    cfg = settings()
    embedder.reset()  # an explicit retry, so an earlier back-off does not apply
    n = await memory_index.index(cfg)
    return {"indexed": n, "pending": memory_index.pending_count(embedder.model(cfg)) if memory_index.enabled(cfg) else 0}


@app.get("/memories/proposals")
def list_memory_proposals(status: str = "pending", project_id: str | None = "all") -> list[dict[str, Any]]:
    return consolidator.list(status or None, sid(project_id))


@app.post("/memories/proposals/{id}/apply")
def apply_memory_proposal(id: str) -> dict[str, Any]:
    p = consolidator.apply(id)
    if not p:
        raise HTTPException(404)
    return p


@app.post("/memories/proposals/{id}/dismiss")
def dismiss_memory_proposal(id: str) -> dict[str, Any]:
    p = consolidator.dismiss(id)
    if not p:
        raise HTTPException(404)
    return p


@app.post("/memories/{id}/restore")
def restore_memory(id: str) -> dict[str, Any]:
    m = memories.restore(id)
    if not m:
        raise HTTPException(404)
    return m


@app.get("/memories/{id}/history")
def memory_history(id: str) -> list[dict[str, Any]]:
    return memories.history(id)


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
    trash.trash("memory", id)
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
def get_graph(project_id: str | None = None, include_global: bool = True, include_invalid: bool = False) -> dict[str, Any]:
    return graph.get(sid(project_id), include_global, include_invalid)


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


@app.get("/documents/index-status")
def index_status() -> dict[str, Any]:
    return retriever.status(settings())


@app.get("/documents/{id}")
def get_document(id: str) -> dict[str, Any]:
    d = documents.get(id)
    if not d:
        raise HTTPException(404)
    return d


def _too_big(n: int) -> str | None:
    if n > MAX_UPLOAD_BYTES:
        return f"Files must be {MAX_UPLOAD_BYTES // (1024 * 1024)} MB or smaller"
    return None


async def _read_upload(file: UploadFile) -> bytes:
    """Stop once the body passes the cap, so a huge upload is not copied into the library."""
    if file.size is not None and (msg := _too_big(file.size)):
        raise HTTPException(413, msg)
    buf = bytearray()
    while True:
        block = await file.read(1 << 20)
        if not block:
            break
        if msg := _too_big(len(buf) + len(block)):
            raise HTTPException(413, msg)
        buf.extend(block)
    return bytes(buf)


def _store_upload(project_id: str | None, name: str, mime: str, data: bytes) -> dict[str, Any]:
    # The project is resolved first so an upload for a deleted project leaves no file behind, and the file is
    # removed if anything after the write fails. Runs in a worker thread (see upload_document): parsing a 20 MB
    # PDF on the event loop would stall every SSE stream.
    pid = wsid(project_id)
    safe = safe_upload_name(name)
    digest = hashlib.sha256(data).hexdigest()
    dup = documents.find_by_hash(pid, digest)
    if dup:
        return {**dup, "duplicate": True, "extracted": has_readable_text(dup.get("text") or "")}
    text = for_index(extract_text(safe, data, mime))
    try:
        blocks = extract_structured(safe, data, mime)
    except Exception:  # noqa: BLE001 - the chunker falls back to the plain text
        blocks = None
    dest = db.data_dir / "uploads" / f"{new_id()}-{safe}"
    dest.write_bytes(data)
    try:
        row = documents.create(pid, safe, mime, len(data), str(dest), text, blocks=blocks, content_hash=digest)
    except BaseException:
        dest.unlink(missing_ok=True)
        raise
    return {**row, "extracted": has_readable_text(text)}


UNREADABLE_UPLOAD = ("No readable text came out of this file. It is stored and can be referred to by name, "
                     "but the assistant cannot see what is in it.")


@app.post("/documents")
async def upload_document(file: UploadFile = File(...), project_id: str | None = Form(None)) -> dict[str, Any]:
    data = await _read_upload(file)
    try:
        row = await asyncio.to_thread(_store_upload, project_id, file.filename or "untitled", file.content_type or "", data)
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, str(e)) from e
    # Stored either way; the client must not call a file the model cannot read an upload that worked.
    if row.get("extracted"):
        return {**row, "readable": True}
    return {**row, "readable": False, "reason": UNREADABLE_UPLOAD}


class ReindexIn(BaseModel):
    id: str | None = None


@app.post("/documents/reindex")
def reindex_documents(body: ReindexIn | None = None) -> dict[str, Any]:
    """Re-chunk existing documents with the current chunker, no re-upload. Vectors are re-made by embed-backfill."""
    did = body.id if body else None
    if did and not documents.get(did):
        raise HTTPException(404)
    return {"chunks": documents.reindex(did)}


@app.post("/documents/embed-backfill")
async def embed_backfill() -> dict[str, Any]:
    """Chunk any never-indexed Docs, then embed every chunk (files and Docs) that has no vector for the
    current model. Idempotent."""
    docs.backfill_chunks()
    embedder.reset()  # the user asked for it now, so a back-off from an earlier failure does not apply
    return await retriever.embed_pending(settings())


@app.delete("/documents/{id}")
def delete_document(id: str) -> dict[str, bool]:
    trash.trash("document", id)  # the uploaded file stays on disk until the trash is purged
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
    await title_jobs.stop()
    await learner.stop()  # after the runs, so nothing is still queueing work at it
    await meeting_bus.shutdown()
    shutil.rmtree(db.data_dir / "tmp", ignore_errors=True)
    await asyncio.to_thread(sandboxes.shutdown)  # after the runs: a live sandbox_exec would just see its container vanish
    await toolbox.shell.shutdown()  # host shell jobs: SIGTERM then SIGKILL to each group


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
    dropped = google.forget(*([namespace] if namespace else []))
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


def _board_op(fn: Any, *a: Any) -> Any:
    """A board write with a bad id or an empty board is the caller's mistake (400/404), never a 500."""
    try:
        return fn(*a)
    except KeyError as e:
        raise HTTPException(404, str(e.args[0]) if e.args else "Not found") from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except sqlite3.IntegrityError as e:  # an id that matches no row, caught by the foreign key
        raise HTTPException(404, "No such board, column or card") from e


@app.delete("/boards/{id}")
def delete_board(id: str) -> dict[str, bool]:
    boards.delete(id)
    canvases.delete_windows_for("board", id)
    return {"ok": True}


@app.post("/boards/{id}/columns")
def add_column(id: str, body: ColumnIn) -> dict[str, Any]:
    return _board_op(boards.add_column, id, body.name)


@app.put("/boards/columns/{cid}")
def update_column(cid: str, body: ColumnPatch) -> dict[str, bool]:
    _board_op(boards.update_column, cid, body.model_dump(exclude_none=True))
    return {"ok": True}


@app.delete("/boards/columns/{cid}")
def delete_column(cid: str) -> dict[str, bool]:
    boards.delete_column(cid)
    return {"ok": True}


@app.post("/boards/{id}/cards")
def add_card(id: str, body: CardIn) -> dict[str, Any]:
    if not body.title.strip():
        raise HTTPException(400, "Empty title")
    return _board_op(boards.add_card, id, body.column_id, body.title, body.description, body.due, body.priority, body.labels)


@app.put("/boards/cards/{cid}")
def update_card(cid: str, body: CardPatch) -> dict[str, Any]:
    patch = body.model_dump(exclude_none=True, exclude={"clear_due"})
    if body.clear_due:
        patch["due"] = None
    c = _board_op(boards.update_card, cid, patch)
    if not c:
        raise HTTPException(404)
    return c


@app.post("/boards/cards/{cid}/move")
def move_card(cid: str, body: MoveIn) -> dict[str, Any]:
    c = _board_op(boards.move_card, cid, body.column_id, body.before_card_id)
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
    kind: str = "html"           # html | summary | markdown | chart | stat | table
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
    spec: dict[str, Any] | None = None


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
    for w in (dashboards.get(id) or {}).get("widgets") or []:
        canvases.delete_windows_for("dashboard-widget", w["id"])
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
    elif w["kind"] in widget_spec.KINDS:
        w = await widget_spec.run_widget(dashboards, w, cfg, model, _widget_fetch, regenerate=regenerate_code)
    return w


async def _widget_fetch(source_id: str) -> Any:
    internal = await _internal_data() if (dashboards.source(source_id) or {}).get("kind") == "internal" else None
    return await dashboards.fetch_source(source_id, internal)


@app.post("/dashboards/{id}/widgets")
async def create_widget(id: str, body: WidgetIn, request: Request) -> dict[str, Any]:
    if not dashboards.get(id):
        raise HTTPException(404)
    title = body.title.strip() or (body.prompt.strip()[:40] or "Widget")
    w = dashboards.create_widget(id, title, body.kind, body.prompt, body.source_ids, body.code, body.output, body.width, body.height, body.refresh_minutes)
    if body.kind in ("html", "summary") + widget_spec.KINDS:
        try:
            w = await _run_widget(w, request, regenerate_code=(body.kind == "html" and not body.code))
        except Exception as e:  # noqa: BLE001
            w = dashboards.update_widget(w["id"], {"output": f"Generation failed: {e}", "data_error": f"Generation failed: {e}"}) or w
    return w


@app.get("/widgets/{wid}")
def get_widget(wid: str) -> dict[str, Any]:
    w = dashboards.widget(wid)
    if not w:
        raise HTTPException(404)
    return w


@app.get("/widgets/{wid}/data")
async def widget_data(wid: str) -> dict[str, Any]:
    """A declarative widget's bound rows: cached inside its refresh_minutes TTL, otherwise re-bound. Never calls the model."""
    w = dashboards.widget(wid)
    if not w or w["kind"] not in widget_spec.KINDS:
        raise HTTPException(404)
    return await widget_spec.widget_data(dashboards, w, _widget_fetch)


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
    canvases.delete_windows_for("dashboard-widget", wid)
    return {"ok": True}


@app.get("/widgets/{wid}/render")
def render_widget(wid: str) -> HTMLResponse:
    w = dashboards.widget(wid)
    if not w:
        raise HTTPException(404)
    code = w["code"] or "<!doctype html><html><body style='font-family:system-ui;color:#9c9a94;padding:12px'>No code yet.</body></html>"
    # The iframe cannot set headers (sandbox, no same-origin). Electron attaches the app token to this
    # URL only; the page then gets a short-lived capability for its own sources, not the app token.
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
app.include_router(artifact_router(artifacts, settings, on_delete=lambda aid: canvases.delete_windows_for("artifact", aid),
                                   sign=_artifact_render_path, verify=_artifact_render_ok))
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
    name: str = FALLBACK_NAME
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
    return docs.create(body.title, body.content, wsid(body.project_id), body.folder)


class DailyIn(BaseModel):
    date: str | None = None  # YYYY-MM-DD; absent is today on this machine


# Declared above /docs/{id}, like /docs/pending: "daily" would otherwise be read as a doc id.
@app.post("/docs/daily")
def open_daily_doc(body: DailyIn) -> dict[str, Any]:
    try:
        doc, created = docs.daily(body.date)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"doc": doc, "created": created}


class DocRecordingIn(BaseModel):
    mode: str = "record"       # 'record' captures the room and proposes a summary; 'dictate' types what you say
    template: str = "general"
    title: str | None = None


@app.post("/docs/{doc_id}/recordings")
async def start_doc_recording(doc_id: str, body: DocRecordingIn) -> dict[str, Any]:
    """Create a recording linked to this doc and start it, through the same consent + preflight gate
    as any meeting. If the start is refused the row made for it is deleted, so nothing is left behind."""
    d = docs.get(doc_id)
    if not d:
        raise HTTPException(404)
    if body.mode not in ("record", "dictate"):
        raise HTTPException(400, "mode must be 'record' or 'dictate'")
    if not activity.IS_MAC:
        row = next((r for r in meeting_svc.capabilities() if r["id"] == "platform"), {})
        raise HTTPException(400, row.get("fix") or "Recording is macOS-only.")
    m = meeting_store.create(
        title=(body.title or "").strip() or d["title"], project_id=d["project_id"], template=body.template,
        status="scheduled", doc_id=doc_id, doc_mode=body.mode)
    try:
        started = await asyncio.to_thread(meeting_svc.start, m["id"])
    except BaseException as e:
        meeting_store.delete(m["id"])
        if isinstance(e, MeetingBlocked):
            raise HTTPException(409, {"blockers": e.blockers}) from e
        if isinstance(e, RecorderBusy):
            raise HTTPException(409, {"meeting_id": e.meeting_id, "blockers": [{
                "id": "busy", "label": "Already recording", "ok": False, "detail": str(e),
                "fix": "Stop the meeting that is recording before starting another."}]}) from e
        raise
    if not started:
        meeting_store.delete(m["id"])
        raise HTTPException(404)
    return started


@app.get("/docs/{doc_id}/recordings")
def doc_recordings(doc_id: str) -> list[dict[str, Any]]:
    if not docs.get(doc_id):
        raise HTTPException(404)
    return meeting_store.for_doc(doc_id)


@app.get("/docs/{doc_id}/backlinks")
def doc_backlinks(doc_id: str) -> list[dict[str, Any]]:
    out = docs.backlinks(doc_id)
    if out is None:
        raise HTTPException(404)
    return out


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
    # Not for a doc with a recording in it: its accepted summaries are other people's speech, not the user's voice.
    if settings().get("learnStyle", True) and not meeting_store.records_doc(id):
        style.add_sample(d["project_id"], d["content"], source="doc", ref=f"doc:{id}")
    return d


@app.patch("/docs/{id}")
def patch_doc(id: str, body: DocMetaPatch) -> dict[str, Any]:
    patch = body.model_dump(exclude_none=True, exclude={"clear_project", "scope"})
    if body.scope is not None:
        patch["project_id"] = (wsid(fscope(body.scope)) if fscope(body.scope) else None)
    elif body.clear_project:
        patch["project_id"] = None
    elif "project_id" in patch:
        patch["project_id"] = wsid(patch["project_id"])
    d = docs.update_meta(id, patch)
    if not d:
        raise HTTPException(404)
    return d


@app.delete("/docs/{id}")
def delete_doc(id: str) -> dict[str, bool]:
    trash.trash("doc", id)
    return {"ok": True}


@app.get("/docs/{id}/revisions")
def doc_revisions(id: str, limit: int = 100) -> list[dict[str, Any]]:
    if not docs.get(id):
        raise HTTPException(404)
    return docs.revisions(id, _clamp(limit))


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
    redactAllow: list[str] | None = None
    redactDeny: list[str] | None = None
    redactThreshold: float | None = None
    categories: list[dict[str, Any]] | None = None
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


@app.get("/activity/categories")
def activity_categories_get() -> dict[str, Any]:
    from . import activity_categories as cats
    return cats.effective(monitor.config())


class CategoriesIn(BaseModel):
    rules: list[dict[str, Any]] | None = None   # null resets to the default tree


@app.put("/activity/categories")
def activity_categories_put(body: CategoriesIn) -> dict[str, Any]:
    from . import activity_categories as cats
    try:
        return cats.save(monitor, body.rules)
    except ValueError as e:
        msg, idx = e.args
        raise HTTPException(400, {"error": msg, "index": idx})


@app.get("/activity/categories/report")
def activity_categories_report(days: int = 7) -> dict[str, Any]:
    from . import activity_categories as cats
    return cats.report_for(monitor, max(1, min(90, days)))


class RedactTestIn(BaseModel):
    text: str = ""


@app.post("/activity/redact/test")
def activity_redact_test(body: RedactTestIn) -> dict[str, Any]:
    """Run a string through the current redaction config. In memory only: not stored, not logged."""
    return activity.redact_preview(monitor.config(), body.text)


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


def _recording_changed(event: dict[str, Any]) -> None:
    """A recording moved (segment settled, status change, summary landed): tell every window.

    Called from the recorder's worker thread as well as the event loop, and a Topic's queues belong
    to the loop, so off-loop calls are handed to it (same shape as _desk_changed)."""
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if running is not None:
        events.publish("recording", event)
    elif _loop is not None and not _loop.is_closed():
        _loop.call_soon_threadsafe(events.publish, "recording", event)


meeting_svc.publish = _recording_changed


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
    # A recording made for a doc (audio import into a doc goes through here, then import-audio).
    doc_id: str | None = None
    doc_mode: str | None = None


class MeetingSummarizeIn(BaseModel):
    template: str | None = None
    focus: str = ""
    force: bool = False


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
    docSegmentSeconds: int | None = None
    dictationSegmentSeconds: int | None = None
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
    vadGate: bool | None = None
    vadMinSpeechRatio: float | None = None
    hallucinationFilter: bool | None = None
    whisperVadModelPath: str | None = None
    maxImportSeconds: int | None = None
    diarize: bool | None = None
    diarizeBackend: str | None = None
    diarizeSegmentationModel: str | None = None
    diarizeEmbeddingModel: str | None = None
    diarizeThreshold: float | None = None
    diarizeSpeakers: int | None = None


class MeetingSpeakersIn(BaseModel):
    """Display names for diarized speaker ids, e.g. {"S1": "Dana"}. A blank name clears one."""

    names: dict[str, str]


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
    return meeting_store.search(q, scope, _clamp(limit))


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
                  since_days: int = 0, limit: int = 100, doc_id: str | None = None,
                  include_docs: bool = False) -> list[dict[str, Any]]:
    """Preview rows: counts and the first 240 characters of the notes, never a body.

    Recordings made inside a doc are not meetings of their own: they are left out unless
    `include_docs` is set, and `doc_id` selects one doc's."""
    scope = "__all__" if project_id in (None, "all") else sid(project_id)
    return meeting_store.list(scope, q, status, since_days, _clamp(limit), doc_id=doc_id, include_docs=include_docs)


@app.post("/meetings")
def create_meeting(body: MeetingIn) -> dict[str, Any]:
    doc_project: str | None = None
    if body.doc_id:
        doc = docs.get(body.doc_id)  # None when missing or trashed
        if not doc:
            raise HTTPException(404, "That doc does not exist or is in the trash.")
        if body.doc_mode not in (None, "record", "dictate"):
            raise HTTPException(400, "doc_mode must be 'record' or 'dictate'")
        doc_project = doc["project_id"]
    return meeting_store.create(
        doc_id=body.doc_id, doc_mode=body.doc_mode if body.doc_id else None,
        title=body.title, project_id=doc_project if body.doc_id else wsid(body.project_id), template=body.template,
        calendar_event_id=body.calendar_event_id, calendar_id=body.calendar_id,
        calendar_link=body.calendar_link, conference_link=body.conference_link,
        attendees=body.attendees, scheduled_start=body.scheduled_start,
        scheduled_end=body.scheduled_end, status=body.status)


@app.get("/meetings/{id}")
def get_meeting(id: str) -> dict[str, Any]:
    m = meeting_store.get(id, include_hidden=False)
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


@app.post("/meetings/{id}/import-audio", status_code=202)
async def import_meeting_audio(id: str, file: UploadFile = File(...)) -> dict[str, Any]:
    """Transcribe an existing recording into this meeting. 202: the work runs in the background and
    the existing /meetings/{id}/segments poll carries progress."""
    try:
        meeting_import.check(meeting_svc, id)
    except LookupError as e:
        raise HTTPException(404) from e
    except MeetingBlocked as e:
        raise HTTPException(409, {"blockers": e.blockers}) from e
    except meeting_import.ImportRefused as e:
        raise HTTPException(409, str(e)) from e
    tmp = db.data_dir / "tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    dest = tmp / f"import-{new_id()}-{meeting_import.safe_name(file.filename or '')}"
    size, cap = 0, 1 << 30
    try:
        with dest.open("wb") as out:
            while chunk := await file.read(1 << 20):
                size += len(chunk)
                if size > cap:
                    raise HTTPException(413, "That file is over the 1 GiB import limit.")
                out.write(chunk)
        await asyncio.to_thread(meeting_import.probe, dest, meeting_svc.config())
    except ValueError as e:
        dest.unlink(missing_ok=True)
        raise HTTPException(400, str(e)) from e
    except BaseException:
        dest.unlink(missing_ok=True)
        raise

    async def _go() -> None:
        try:
            await meeting_import.run(meeting_svc, id, dest, cleanup_src=True)
        except Exception as e:  # noqa: BLE001 - the run already put the reason on the meeting
            logging.getLogger("personal_os").warning("meeting import %s: %s", id, e)

    asyncio.ensure_future(_go())
    return meeting_store.get(id, include_hidden=False) or {}


@app.post("/meetings/{id}/diarize")
async def diarize_meeting(id: str) -> dict[str, Any]:
    """(Re)run speaker separation on retained audio. With no backend it answers ok=false and a note,
    never an error: the transcript simply keeps its channel labels."""
    res = await meeting_svc.diarize(id)
    if res.get("note") == "no such meeting":
        raise HTTPException(404)
    return {**res, "meeting": meeting_store.get(id, include_hidden=False)}


@app.put("/meetings/{id}/speakers")
def rename_meeting_speakers(id: str, body: MeetingSpeakersIn) -> dict[str, Any]:
    try:
        m = meeting_svc.set_speakers(id, body.names)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
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
    if meeting_store.is_hidden(id):
        raise HTTPException(404)
    limit = _clamp(limit, 2000)
    if since >= 0:
        return meeting_store.since(id, since, limit)
    return meeting_store.segments(id, max(0, offset), limit, channel)


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
    m = meeting_store.get(id, include_hidden=False)
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
    m = meeting_store.get(id, include_hidden=False)
    if not m:
        raise HTTPException(404)
    if m.get("doc_id"):
        raise HTTPException(400, "This is a recording of a doc: use /meetings/{id}/summarize.")
    rev = await meeting_svc.enhance(id, force, template)
    if not rev:
        raise HTTPException(502, meeting_svc.last_error or "The enhance pass produced no revision")
    return rev


@app.post("/meetings/{id}/summarize")
async def summarize_meeting(id: str, body: MeetingSummarizeIn) -> dict[str, Any]:
    """Write a summary of a doc recording and PROPOSE it as a section at the end of its doc. Never
    applied here, whatever the doc edit mode is: the user accepts it in the doc. A model failure is a
    200 with `error` set and no revision, so the UI can offer it again."""
    m = meeting_store.get(id, include_hidden=False)
    if not m:
        raise HTTPException(404)
    if not m.get("doc_id"):
        raise HTTPException(400, "Only a recording made in a doc can be summarized into it.")
    return await meeting_svc.summarize_into_doc(id, template=body.template, focus=body.focus, force=body.force)


@app.get("/meetings/{id}/revisions")
def meeting_revisions(id: str, limit: int = 100) -> list[dict[str, Any]]:
    if not meeting_store.get(id, include_hidden=False):
        raise HTTPException(404)
    return meeting_store.revisions(id, _clamp(limit))


@app.get("/meetings/{id}/actions")
def meeting_actions(id: str) -> list[dict[str, Any]]:
    if not meeting_store.get(id, include_hidden=False):
        raise HTTPException(404)
    return meeting_store.action_items(id)


@app.post("/meetings/{id}/actions/add-todos")
def meeting_actions_add_todos(id: str, body: MeetingActionsIn) -> list[dict[str, Any]]:
    """Promote proposed items into real todos. Idempotent per item - one that already carries a
    todo_id is left alone - and todos.on_change pushes each new task to Google within ~2s."""
    if not meeting_store.get(id, include_hidden=False):
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
    if not meeting_store.get(id, include_hidden=False):
        raise HTTPException(404)
    # `retranscribe` settles every meeting it touched itself, rebuilding `transcript`, the FTS row
    # and the error column clause by clause. This route used to redo that rebuild and compute
    # `error = "" if text and not failed_segments(id)`, which cleared the WHOLE column - throwing
    # away banners that are still true after a replay, like a dead loopback channel or a failed
    # enhance pass. Let the service own it.
    settled = await asyncio.to_thread(meeting_svc.retranscribe, id, limit)
    return {"settled": settled, "meeting": meeting_store.get(id, include_hidden=False)}


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
    with contextlib.suppress(Exception):
        await asyncio.to_thread(filesnap.prune)  # snapshots past their age or byte budget
    with contextlib.suppress(Exception):
        await asyncio.to_thread(snaps.prune)  # folder snapshots: gc once a day, evict past the byte budget


@app.on_event("shutdown")
async def _outbox_shutdown() -> None:
    task = getattr(app.state, "outbox_task", None)
    if task:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task


# ---------------- backups: the daily snapshot timer (backups.py) ----------------
@app.on_event("startup")
async def _backups_startup() -> None:
    app.state.backups_task = asyncio.create_task(backups.Backups(db.data_dir).loop(), name="daily-backup")


@app.on_event("shutdown")
async def _backups_shutdown() -> None:
    task = getattr(app.state, "backups_task", None)
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
    return tool_results.list(id, _clamp(limit))


@app.get("/tool-results/{rid}")
def read_tool_result_blob(rid: str, offset: int = 0, limit: int = 20000) -> dict[str, Any]:
    row = tool_results.get(rid)
    if not row:
        raise HTTPException(404, "No such tool result")
    out = tool_results.read(row["conversation_id"], rid, max(0, offset), _clamp(limit, 200000))
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


class SkillImportIn(BaseModel):
    text: str
    project_id: str | None = None


@app.post("/skills/import")
def import_skill_md(body: SkillImportIn) -> dict[str, Any]:
    """Paste a SKILL.md. It becomes a candidate, never an approved skill: approval stays the PATCH above."""
    try:
        return skillmd.import_text(skills, _lint_skill, body.text, project_id=None if sid(body.project_id) == ALL else sid(body.project_id))
    except skillmd.ImportError_ as e:
        raise HTTPException(422, "; ".join(e.errors))


@app.get("/skills/{skill_id}/export")
def export_skill_md(skill_id: str) -> dict[str, str]:
    s = skills.get(skill_id)
    if not s:
        raise HTTPException(404, "No such skill")
    return {"filename": f"{skillmd.slug(s['name'])}/SKILL.md", "text": skillmd.render(s)}


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


class InduceIn(BaseModel):
    """`message_id` keeps one reply (and the user turn before it). Omit it to use the whole chat."""
    message_id: str | None = None


@app.post("/conversations/{id}/skills/induce")
async def induce_conversation_skill(id: str, body: InduceIn | None = None) -> dict[str, Any]:
    """Distil this conversation, or one reply, into a candidate procedure for review. Never enables anything."""
    conv = convos.get(id)
    if not conv:
        raise HTTPException(404, "Conversation not found")
    transcript, reason = run_transcript(conv["messages"], (body.message_id if body else None))
    if reason or not transcript:
        return {"candidate": None, "reason": reason or "Not enough of a conversation to learn a procedure from."}
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


@app.get("/cowork/env")
async def cowork_env_status() -> dict[str, Any]:
    return await asyncio.to_thread(work_env.status)


@app.post("/cowork/env/setup")
async def cowork_env_setup() -> dict[str, Any]:
    """Build the work venv (user-triggered from Settings); a no-op once it is complete."""
    return await asyncio.to_thread(work_env.ensure)


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


async def _desk_start_tool(ctx: dict[str, Any], title: str, brief: str, mode: str) -> dict[str, Any]:
    """desk_start: the same creation the REST route does, started at once, in plan (or tighter) autonomy."""
    try:
        out = await create_desk(DeskIn(brief=brief, title=title or None, project_id=ctx.get("project_id"), autonomy=mode, start=True))
    except HTTPException as e:
        detail = e.detail.get("message") if isinstance(e.detail, dict) else e.detail
        return tools.tool_error(f"The desk was not started: {detail}")
    return {"desk_id": out["desk"]["id"], "conversation_id": out["conversation_id"], "run_id": out.get("run_id"), "mode": mode,
            "note": "The desk is planning. It will wait for the user to approve its plan before it does anything."}


toolbox.desk_starter = _desk_start_tool


@app.get("/cowork/desks/{id}")
def get_desk(id: str) -> dict[str, Any]:
    desk = _desk_or_404(id)
    # The approved plan when there is one; otherwise the newest proposal, so a plan still waiting on
    # the user is visible (and answerable) in the Plan tab rather than "No plan yet".
    plan = (plans.get(desk["plan_id"]) if desk.get("plan_id") else None) or plans.for_desk(id) or plans.latest_for_desk(id)
    # Every card this desk is waiting on, live or parked: the desk pane answers them in place, so a
    # desk blocked on an approval does not have to be hunted down in its transcript.
    waiting = [{**a, "live": a["call_id"] in _approvals} for a in run_store.approvals("pending", desk_id=id)
               if a["tool"] != PLAN_TOOL]
    return {**desk, "plan": plan, "outputs": _outputs_view(id), "events": desks.events(id),
            "runs": run_store.list(desk_id=id, limit=20), "approvals": waiting}


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
        if _over_live_cap():
            raise HTTPException(409, {"message": "Too many desks are running at once; resume this one when another finishes"})
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
    # A desk_ask card still open - held by a live run or parked - is answered by this message, so the
    # banner's box and the card's own box do the same thing: the answer rides on the approval.
    asks = [a for a in run_store.approvals("pending", desk_id=id) if a["tool"] == "desk_ask"]
    if asks:
        out = await approve_tool_call(asks[-1]["call_id"], ApprovalIn(decision="allow", note=text))
        return {"ok": True, "steered": False, "answered": True, "resumed": out.get("resumed"), "live": out.get("live")}
    if desk["question"]:
        desks.set_status(id, desk["status"], reason=desk["status_reason"], question="", event=False)
    conv_id = desk["conversation_id"]
    run = bus.answering(conv_id)
    if run is not None:
        um = convos.add_message(conv_id, "user", text)
        run.publish("user_message", um)
        run.steers.append(um)
        run.poke()
        return {"ok": True, "steered": True, "run_id": run.run_id}
    # A run past its `done` (the learn/style tail) cannot take a steer and blocks a launch; it ends on
    # its own within seconds, so wait for it rather than drop the message with a 409.
    for _ in range(40):
        if not bus.live(conv_id):
            break
        await asyncio.sleep(0.25)
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
    if desk["status"] not in PAUSE_FROM:
        raise HTTPException(409, {"message": "Only a running desk can be paused", "status": desk["status"]})
    out = desks.set_status(id, "paused", reason="paused", headline="")
    bus.stop(desk["conversation_id"])
    return out or desk


@app.post("/cowork/desks/{id}/stop")
async def stop_desk(id: str) -> dict[str, Any]:
    """Stopped before the run is, for the same reason pause is: settle() and the cancellation
    handler both read the row back, and whichever of them runs last must find the decision the
    user made, not overwrite it."""
    desk = _desk_or_404(id, False)
    if desk["status"] not in STOP_FROM:
        raise HTTPException(409, {"message": "That desk has already finished", "status": desk["status"]})
    out = desks.set_status(id, "stopped", reason="stopped", headline="")
    bus.stop(desk["conversation_id"])
    task = _desk_tasks.pop(id, None)
    if task is not None:
        task.cancel()
    return out or desk


@app.get("/cowork/desks/{id}/events")
def desk_event_list(id: str, limit: int = 200) -> list[dict[str, Any]]:
    _desk_or_404(id, False)
    return desks.events(id, _clamp(limit))


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


PREVIEW_PAGE = 20_000                     # characters per page of a text or document preview
PREVIEW_MAX_BYTES = 25 * 1024 * 1024      # past this a file is named and sized, never opened
PREVIEW_IMAGE_EDGE = 1600                 # the long edge a previewed picture is scaled down to
PREVIEW_IMAGE_B64 = 1_500_000             # budget for the data URL's base64, so one picture cannot swamp the pane
_PREVIEW_IMAGES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
# Formats whose bytes are not text but whose words are readable: extract_text handles each.
_PREVIEW_DOCS = {".pdf", ".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt", ".odt", ".ods", ".odp", ".rtf", ".epub"}
_preview_doc_cache: dict[tuple[str, int, int], str] = {}   # (path, mtime_ns, size) -> extracted text; paging must not re-OCR


def _preview_image(data: bytes) -> dict[str, Any] | None:
    """Re-encode a picture so the pane always gets something small and safe to inline. A PNG that has
    transparency stays a PNG (JPEG would paint it black); everything else is JPEG, with the quality
    stepped down until the base64 fits the budget. None when Pillow cannot read it."""
    import base64
    import io

    from PIL import Image, ImageOps
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
        img = ImageOps.exif_transpose(img) or img
    except Exception:  # noqa: BLE001 - a damaged or exotic image is "no preview", not an error page
        return None
    img.thumbnail((PREVIEW_IMAGE_EDGE, PREVIEW_IMAGE_EDGE))
    alpha = img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info)
    for quality in (85, 70, 55, 40):
        buf = io.BytesIO()
        if alpha:
            img.convert("RGBA").save(buf, "PNG", optimize=True)
        else:
            img.convert("RGB").save(buf, "JPEG", quality=quality)
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        if len(b64) <= PREVIEW_IMAGE_B64:
            break
        if alpha:
            img.thumbnail((img.width * 3 // 4, img.height * 3 // 4))   # PNG has no quality knob: shrink instead
    else:
        return None
    mime = "image/png" if alpha else "image/jpeg"
    return {"kind": "image", "data_url": f"data:{mime};base64,{b64}", "width": img.width, "height": img.height, "bytes": len(data)}


@app.get("/cowork/desks/{id}/preview")
def desk_file_rich_preview(id: str, path: str, offset: int = 0) -> dict[str, Any]:
    """What the Files tab shows for ANY workspace file: a scaled picture, a page of text, or a page of
    the words extracted from a PDF/office file. It never 500s on an odd file: a file it cannot show
    is `{kind: "none", reason}`, which the pane renders as a sentence. SVG is returned as text, never
    rendered, because a desk's files are agent-written."""
    _desk_or_404(id, False)
    try:
        p = workspace.resolve_in(id, path)
    except WorkspaceError as e:
        raise HTTPException(400, str(e)) from e
    if not p.is_file():
        raise HTTPException(404, "No such file")
    ext = p.suffix.lower()
    try:
        st = p.stat()
        if st.st_size > PREVIEW_MAX_BYTES:
            return {"kind": "none", "reason": f"This file is {st.st_size // (1024 * 1024)} MB, too large to preview. Download it to open it."}
        data = p.read_bytes()
    except OSError as e:
        return {"kind": "none", "reason": f"Could not read this file: {e.strerror or e}"}
    if ext in _PREVIEW_IMAGES:
        shown = _preview_image(data)
        return shown or {"kind": "none", "reason": "This image could not be decoded."}
    kind, note, text = "text", "", ""
    if ext not in _PREVIEW_DOCS and b"\x00" not in data[:8192]:
        text = data.decode("utf-8", errors="replace")
    else:
        kind = "document"
        key = (str(p), st.st_mtime_ns, st.st_size)
        if key not in _preview_doc_cache:
            try:
                got = extract_text(p.name, data)
            except Exception:  # noqa: BLE001 - extraction failing is "no preview"
                got = ""
            if len(_preview_doc_cache) >= 4:
                _preview_doc_cache.pop(next(iter(_preview_doc_cache)))
            _preview_doc_cache[key] = got or ""
        text = _preview_doc_cache[key]
        if not text.strip():
            return {"kind": "none", "reason": "No readable text in this file. Download it to open it."}
        note = "Text extracted from the file; layout and images are not shown."
    total = len(text)
    off = max(0, min(offset, total))
    end = min(total, off + PREVIEW_PAGE)
    out: dict[str, Any] = {"kind": kind, "text": text[off:end], "offset": off, "next_offset": end if end < total else None, "total_chars": total}
    if kind == "document":
        out["note"] = note
    return out


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
    src = workspace.resolve_in(desk_id, rel)
    if msg := _too_big(src.stat().st_size):
        return {"ref": None, "verified": False, "error": msg}
    data = src.read_bytes()
    if msg := _too_big(len(data)):
        return {"ref": None, "verified": False, "error": msg}
    safe = safe_upload_name(Path(rel).name)
    # Resolve the project and extract before anything is written, in a thread (a big PDF must not stall the
    # loop), and drop the stored file if the row cannot be created, so a failure leaves no orphan upload.
    pid = wsid(item.project_id) if item.project_id else None
    try:
        text = for_index(await asyncio.to_thread(extract_text, safe, data, ""))
    except Exception as e:  # noqa: BLE001 - an unreadable file is a failed promotion, not a 500
        return {"ref": None, "verified": False, "error": str(e)}
    stored = db.data_dir / "uploads" / f"{new_id()}-{safe}"
    stored.parent.mkdir(parents=True, exist_ok=True)
    stored.write_bytes(data)
    try:
        doc = documents.create(pid, title, "", len(data), str(stored), text)
    except BaseException:
        stored.unlink(missing_ok=True)
        raise
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
    return desks.inbox(_clamp(limit))


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
    global _loop
    _loop = asyncio.get_running_loop()  # where _desk_changed hands writes made in the threadpool
    try:
        # Runs are recovered by _recover_runs above, which owns run_store.recover(). This only has
        # to sweep the desks it left behind: LIVE -> interrupted, plus a needs_you event each.
        woken = desks.recover()
        if woken:
            log.info("cowork recovery: %s desks need you", woken)
    except Exception:  # noqa: BLE001
        log.warning("cowork recovery failed", exc_info=True)
