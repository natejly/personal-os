"""Programmatic tool calling ("code mode"): a run_python script that drives app tools and hands back only its stdout.

Bulk work (grep forty files, edit a dozen sites, read a hundred messages) costs one model round per call. With
`run_python(tools=[...])` or `tools="offered"` the backend opens a Unix socket and the sandboxed script calls
`grain_tools.fs_grep(pattern=..., root=...)`, `grain_tools.gather(...)` for several calls at once, or
`grain_tools.call("fs_grep", ...)`. The socket is the only thing the Seatbelt profile lets the script connect to.
Inner results stay in the script; the model gets stdout, a summary and a trace of calls (name, ok, ms), never payloads.

Every call goes through the same gate the model's own calls do, never around it:
  * `bridgeable`: the tool exists, is available, and its mode for this chat is on or ask. Never bridged: tools whose call
    is itself a user card, spawns and workflow tools, schedules, shell and coding tools, run_python.
  * `Toolbox.precheck` (bad arguments, a repo edit the coding extension sends to a coding agent) refuses before any card
  * `Toolbox.gate` (taint upgrades on -> ask), then an `ask` parks the script on a real approval card (`approve`)
    and the script's clock stops while it waits; no card to answer (a background run) means refused, not run;
    a refusal or a no raises ToolError in the script, which may carry on
  * `Toolbox.call` (or the connector path for an MCP tool) with the run's own ctx, so taint, the ledger and every
    tool-side check apply
  * at most `codeModeMaxCalls` inner calls per run
"""
from __future__ import annotations

import asyncio
import functools
import json
import os
import re
import shutil
import sys
import tempfile
import time
from typing import Any, Awaitable, Callable

from . import autoreview, guards, limits, mcp_search, permrules
from .dispatch import Contact, dispatch_tool
from .extensions.hooks import hook_ctx

MAX_SECONDS = 300
STDOUT_KEEP = 50_000
STDOUT_HEAD = 0.4          # share of the kept stdout taken from the start; the rest is the tail, where a failure is
STDERR_KEEP = 10_000
MAX_LINE = 8_000_000
RESULT_KEEP = 2_000_000    # characters of one inner result handed to the script whole; more is cut and says so
DOC_DESC = 300             # characters of a tool's description in its stub's docstring
DOC_SCHEMA = 700           # characters of its JSON schema; grain_tools.describe(name) has the rest
LOG_KEEP = 500
DEFAULT_MAX_CALLS = 100

# Never callable from a script: the call is itself a card for the user, hands work to another agent, starts a workflow
# or a schedule, runs a shell or coding agent, or is this tool.
NEVER = frozenset({"agent_spawn", "delegate", "message_worker", "check_worker", "resume_worker", "stop_worker", "run_python",
                   "tool_search", "mcp_tool_search", "ask_user", "schedule_task", "scheduled_tasks"})
NEVER_PREFIXES = ("agent_", "workflow_", "schedule")

# What the sandboxed script imports. Stdlib only: nothing here can be installed inside the sandbox. The tool stubs are
# generated per run from the offered tools (Bridge.client_source); __TOOLS_JSON__ is replaced by their JSON.
CLIENT_SOURCE = r'''"""App tools for a run_python script.

    grain_tools.fs_grep(pattern="TODO", root=".")            one call; help(grain_tools.fs_grep) shows its arguments
    grain_tools.gather(("fs_grep", {...}), grain_tools.call_later("fs_glob", pattern="*.py", root="."))
                                                             several calls at once, results in order; a failed call is a ToolError in the list
    grain_tools.call("fs_grep", pattern="TODO", root=".")   the same by name
    grain_tools.describe("fs_grep")                          full description and JSON schema
    grain_tools.search("send an email")                      [{name, description}] of tools you can call but were not given a stub
Results stay here: print only what the user needs. A refused or declined call raises ToolError; catch it to carry on.
"""
import inspect
import json
import os
import re
import socket


class ToolError(Exception):
    """The bridge refused the call (tool not offered, off, declined, over the call cap). A tool's own error comes back as data."""


def _rpc(req):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        s.connect(os.environ["GRAIN_TOOLS_SOCK"])
        s.sendall(json.dumps(req).encode("utf-8") + b"\n")
        chunks = []
        while True:
            c = s.recv(1 << 16)
            if not c:
                break
            chunks.append(c)
            if c.endswith(b"\n"):
                break
    finally:
        s.close()
    return json.loads(b"".join(chunks).decode("utf-8"))


def _unwrap(resp):
    if not resp.get("ok"):
        raise ToolError(resp.get("error") or "refused")
    return resp["result"]


def call(tool, **args):
    """Call one app tool by name and return its result."""
    return _unwrap(_rpc({"op": "call", "tool": tool, "args": args}))


def call_later(tool, **args):
    """A call for gather() to run: grain_tools.gather(call_later("fs_grep", pattern="a", root="."), ...)."""
    return (tool, args)


def gather(*calls):
    """Run calls together (reads in parallel, anything else one after another) and return their results in order.
    Each call is (name, kwargs) or call_later(name, **kwargs). A call that fails puts a ToolError in its place."""
    if len(calls) == 1 and isinstance(calls[0], list):
        calls = tuple(calls[0])
    reqs = []
    for c in calls:
        if isinstance(c, str):
            c = (c, {})
        reqs.append({"tool": c[0], "args": dict(c[1] or {}) if len(c) > 1 else {}})
    resp = _rpc({"op": "batch", "calls": reqs})
    if not resp.get("ok"):
        raise ToolError(resp.get("error") or "refused")
    return [r["result"] if r.get("ok") else ToolError(r.get("error") or "refused") for r in resp["results"]]


def describe(tool):
    """The full description and JSON schema of a tool: {name, description, parameters}."""
    return _unwrap(_rpc({"op": "describe", "tool": tool}))


def search(query, limit=8):
    """Find callable tools by keyword: [{name, description}]. A tool found here can be used with call() / gather()."""
    return _unwrap(_rpc({"op": "search", "query": query, "limit": limit}))


_TOOLS = json.loads(__TOOLS_JSON__)
_TYPES = {"string": str, "integer": int, "number": float, "boolean": bool, "array": list, "object": dict}


def _make(name, doc, params):
    def stub(**kwargs):
        return call(name, **{k: v for k, v in kwargs.items() if v is not None})
    stub.__doc__ = doc
    try:
        ps = [inspect.Parameter(p, inspect.Parameter.KEYWORD_ONLY, default=inspect.Parameter.empty if req else None,
                                annotation=_TYPES.get(t, object)) for p, t, req in params if p.isidentifier()]
        stub.__signature__ = inspect.Signature(ps + [inspect.Parameter("extra", inspect.Parameter.VAR_KEYWORD)])
    except (ValueError, TypeError):
        pass
    return stub


for _name, _t in _TOOLS.items():
    _id = re.sub(r"\W", "_", _name)
    if _id and not _id[0].isdigit() and _id not in globals():
        _f = _make(_name, _t["doc"], _t["params"])
        _f.__name__ = _f.__qualname__ = _id
        globals()[_id] = _f
'''

Approve = Callable[[str, dict[str, Any], bool], Awaitable[bool]]


def _cfg(tb: Any, ctx: dict[str, Any]) -> dict[str, Any]:
    return ctx.get("settings") or tb.settings()


def _is_connector(ctx: dict[str, Any], name: str) -> bool:
    """An MCP or extension-source tool: not a built-in spec, known to this chat's connector tables."""
    return name in (ctx.get("mcp_modes") or {}) or name in (ctx.get("mcp_schemas") or {})


def raw_mode(tb: Any, ctx: dict[str, Any], name: str, modes: dict[str, str] | None = None) -> str:
    """This chat's effective mode for a tool: the live map, else the connector table, else the tool's own default."""
    if modes is None:
        modes = ctx.get("modes")
    if modes and modes.get(name):
        return str(modes[name])
    if name not in tb.specs:
        return str((ctx.get("mcp_modes") or {}).get(name) or "off")
    return tb.default_mode(tb.specs[name])


def bridgeable(tb: Any, ctx: dict[str, Any], name: str, modes: dict[str, str] | None = None) -> tuple[bool, str]:
    """(may a script call this tool in this chat, why not). The tool exists and is available, its effective mode is on
    or ask, and it is not one of the tools that never run from a script."""
    spec = tb.specs.get(name)
    if name in NEVER or name.startswith(NEVER_PREFIXES) or name in _shell_tools() or (spec is not None and not spec.bridge):
        return False, "cannot be called from a script"
    if spec is None:
        if not _is_connector(ctx, name):
            return False, "not available here"
    elif not tb.available(name):
        return False, "not available here"
    if raw_mode(tb, ctx, name, modes) not in ("on", "ask"):
        return False, "turned off for this chat"
    if spec is not None and name in _always_card(tb):
        return False, "cannot be called from a script"
    return True, ""


@functools.cache
def _shell_tools() -> frozenset[str]:
    from .subagents import SHELL_TOOLS
    return frozenset(SHELL_TOOLS)


def _always_card(tb: Any) -> frozenset[str]:
    from .tools import ALWAYS_CARD
    return ALWAYS_CARD


def offered(tb: Any, ctx: dict[str, Any], wanted: list[str] | str | None, modes: dict[str, str] | None = None
            ) -> tuple[list[str], dict[str, str]]:
    """(names the script gets, {refused name: why}). `wanted` is a list of names, or "offered": every bridgeable tool this
    chat is offered right now (a held-back tool is not, until it is loaded; a script may still name one)."""
    ok: list[str] = []
    why: dict[str, str] = {}
    if wanted == "offered":
        held = (ctx.get("deferred") or set()) - (ctx.get("tool_loaded") or set())
        for n in dict.fromkeys([*tb.specs, *(modes or ctx.get("modes") or {})]):
            if n not in held and bridgeable(tb, ctx, n, modes)[0]:
                ok.append(n)
        return ok, why
    for n in dict.fromkeys(str(x) for x in (wanted or [])):
        good, reason = bridgeable(tb, ctx, n, modes)
        if good:
            ok.append(n)
        else:
            why[n] = reason
    return ok, why


def _schema_of(tb: Any, ctx: dict[str, Any], name: str) -> dict[str, Any]:
    """{name, description, parameters} of a built-in or connector tool."""
    if (spec := tb.specs.get(name)) is not None:
        return {"name": name, "description": spec.description, "parameters": spec.parameters}
    fn = ((ctx.get("mcp_schemas") or {}).get(name) or {}).get("function") or {}
    return {"name": name, "description": str(fn.get("description") or ""), "parameters": fn.get("parameters") or {"type": "object", "properties": {}}}


def _stub_doc(info: dict[str, Any]) -> str:
    desc = " ".join(str(info["description"]).split())
    if len(desc) > DOC_DESC:
        desc = desc[: DOC_DESC - 1].rstrip() + "…"
    schema = json.dumps(info["parameters"], ensure_ascii=False, separators=(",", ":"))
    if len(schema) > DOC_SCHEMA:
        schema = schema[: DOC_SCHEMA - 1] + "…"
    return f"{desc}\n\nArguments (JSON schema): {schema}\nFull schema: grain_tools.describe({info['name']!r})"


def _stub_spec(info: dict[str, Any]) -> dict[str, Any]:
    props = (info["parameters"] or {}).get("properties") or {}
    req = set((info["parameters"] or {}).get("required") or [])
    params = [[str(k), str((v or {}).get("type") if isinstance(v, dict) else "") or "", k in req] for k, v in list(props.items())[:24]]
    return {"doc": _stub_doc(info), "params": params}


def _fit(result: Any, keep: int = RESULT_KEEP) -> Any:
    """`result` as the script gets it: whole up to `keep` characters of JSON, else cut with a note that says so."""
    try:
        text = json.dumps(result, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(result)[:keep]
    if len(text) <= keep:
        return result
    note = f"result was {len(text)} characters; cut to fit {keep}. Narrow the call (a pattern, a limit) to get the rest."
    if isinstance(result, str):
        return result[:keep] + f"\n[... {note}]"
    if isinstance(result, list):
        n = len(result)
        lo, hi = 0, n
        while lo < hi:  # the longest prefix that fits
            mid = (lo + hi + 1) // 2
            if len(json.dumps(result[:mid], ensure_ascii=False, default=str)) <= keep // 2:
                lo = mid
            else:
                hi = mid - 1
        return {"items": result[:lo], "truncated": True, "total_items": n, "note": note}
    if isinstance(result, dict):
        out = dict(result)
        cut: list[str] = []
        for _ in range(20):
            sizes = {k: len(json.dumps(v, ensure_ascii=False, default=str)) for k, v in out.items()}
            if sum(sizes.values()) <= keep:
                break
            big = max(sizes, key=lambda k: sizes[k])
            share = max(1000, keep - (sum(sizes.values()) - sizes[big]))
            out[big] = _fit(out[big], max(1000, min(share, sizes[big] // 2)))
            cut.append(big)
        return {**out, "truncated": True, "truncated_fields": cut, "note": note}
    return str(text)[:keep]


class Bridge:
    def __init__(self, tb: Any, ctx: dict[str, Any], names: list[str], modes: dict[str, str] | None = None,
                 approve: Approve | None = None):
        self.tb, self.ctx, self.names, self.modes = tb, ctx, set(names), modes
        self.approve = approve
        self.calls = 0
        self.log: list[dict[str, Any]] = []
        self.refused = 0
        self.asked = 0
        self.found: set[str] = set()   # bridgeable tools a search turned up: callable without a stub
        self.stderr_cap = STDERR_KEEP
        self.full_stdout: str | None = None
        self._dir: str | None = None
        self._server: asyncio.AbstractServer | None = None
        self._paused = 0.0
        self._pending = 0
        self._since = 0.0
        self._t0 = time.monotonic()
        self.socket_path = ""

    # -- lifecycle --
    async def start(self) -> "Bridge":
        self._dir = os.path.realpath(tempfile.mkdtemp(prefix="pos-br-"))  # short: sun_path holds ~104 bytes
        self.socket_path = os.path.join(self._dir, "b.sock")
        self._server = await asyncio.start_unix_server(self._serve, path=self.socket_path, limit=MAX_LINE)
        os.chmod(self.socket_path, 0o600)
        self._t0 = time.monotonic()
        return self

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            try:
                await asyncio.wait_for(self._server.wait_closed(), 2)
            except (asyncio.TimeoutError, Exception):  # noqa: BLE001
                pass
            self._server = None
        if self._dir:
            shutil.rmtree(self._dir, ignore_errors=True)
            self._dir = None

    def client_source(self) -> str:
        """grain_tools.py for this run: the library plus one documented stub per offered tool."""
        stubs = {n: _stub_spec(_schema_of(self.tb, self.ctx, n)) for n in sorted(self.names)}
        return CLIENT_SOURCE.replace("__TOOLS_JSON__", repr(json.dumps(stubs, ensure_ascii=True)))

    # -- the script's clock --
    def paused_for(self) -> float:
        """Seconds the script has spent parked on an approval card, so its timeout can leave them out."""
        return self._paused + ((time.monotonic() - self._since) if self._pending else 0.0)

    def elapsed(self) -> float:
        return max(0.0, time.monotonic() - self._t0 - self.paused_for())

    def shape_stdout(self, text: str) -> str:
        """Within STDOUT_KEEP chars: 40% head, 60% tail, the middle named and counted. The whole text is kept."""
        if len(text) <= STDOUT_KEEP:
            return text
        self.full_stdout = text
        head, tail = int(STDOUT_KEEP * STDOUT_HEAD), STDOUT_KEEP - int(STDOUT_KEEP * STDOUT_HEAD)
        return f"{text[:head]}\n[... {len(text) - STDOUT_KEEP} characters omitted ...]\n{text[-tail:]}"

    def trace(self) -> list[dict[str, Any]]:
        """What the model is told about the inner calls: name, ok, ms, and `approved` when the call asked. No payloads."""
        return [{k: e[k] for k in ("name", "ok", "ms", "approved") if k in e} for e in self.log[-LOG_KEEP:]]

    def summary(self) -> str:
        n = len(self.log)
        parts = [f"{n} tool call{'' if n == 1 else 's'}"]
        if self.asked:
            parts.append(f"{self.asked} asked")
        if self.refused:
            parts.append(f"{self.refused} refused")
        parts.append(f"{self.elapsed():.1f} s")
        return ", ".join(parts)

    # -- requests --
    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            line = await reader.readline()
            try:
                req = json.loads(line.decode("utf-8")) if line else None
            except ValueError:
                req = None
            resp = await self.request(req)
            writer.write(json.dumps(resp, ensure_ascii=False, default=str).encode("utf-8") + b"\n")
            await writer.drain()
        except (ConnectionError, asyncio.CancelledError):
            pass
        finally:
            try:
                writer.close()
            except Exception:  # noqa: BLE001
                pass

    async def request(self, req: Any) -> dict[str, Any]:
        """One protocol line: {"op": call|batch|describe|search, ...}; the older {"tool", "args"} is a call."""
        if not isinstance(req, dict):
            return {"ok": False, "error": "bad request"}
        op = str(req.get("op") or "call")
        if op == "batch":
            calls = req.get("calls")
            if not isinstance(calls, list) or not all(isinstance(c, dict) and isinstance(c.get("args") or {}, dict) for c in calls):
                return {"ok": False, "error": "bad request"}
            return {"ok": True, "results": await self.batch([(str(c.get("tool") or ""), c.get("args") or {}) for c in calls])}
        if op == "describe":
            name = str(req.get("tool") or "")
            if name not in self.names and name not in self.found:
                return {"ok": False, "error": f"{name} was not offered to this script."}
            return {"ok": True, "result": _schema_of(self.tb, self.ctx, name)}
        if op == "search":
            return {"ok": True, "result": self.search(str(req.get("query") or ""), req.get("limit"))}
        if op != "call" or not isinstance(req.get("args", {}), dict):
            return {"ok": False, "error": "bad request"}
        return await self.handle(str(req.get("tool") or ""), req.get("args") or {})

    def search(self, query: str, limit: Any = None) -> list[dict[str, str]]:
        """The tool_search ranking over every tool this script could call; a hit becomes callable here."""
        catalog = []
        for n in dict.fromkeys([*self.tb.specs, *(self.modes or self.ctx.get("modes") or {}), *(self.ctx.get("mcp_modes") or {})]):
            if bridgeable(self.tb, self.ctx, n, self.modes)[0]:
                info = _schema_of(self.tb, self.ctx, n)
                catalog.append({"slug": n, "description": info["description"], "parameters": info["parameters"],
                                "server": (self.tb.specs[n].group if n in self.tb.specs else "")})
        try:
            n_hits = max(1, min(int(limit or 8), 20))
        except (TypeError, ValueError):
            n_hits = 8
        hits = mcp_search.bm25_search(mcp_search.build_docs(catalog), query, limit=n_hits)
        by = {c["slug"]: c for c in catalog}
        self.found.update(slug for slug, _ in hits)
        return [{"name": slug, "description": " ".join(str(by[slug]["description"]).split())[:200]} for slug, _ in hits]

    def _refuse(self, entry: dict[str, Any], why: str) -> dict[str, Any]:
        entry.update(ok=False, why=why, refused=True)
        return {"ok": False, "error": why}

    def _max_calls(self) -> int:
        try:
            return max(1, min(int(_cfg(self.tb, self.ctx).get("codeModeMaxCalls") or DEFAULT_MAX_CALLS), 500))
        except (TypeError, ValueError):
            return DEFAULT_MAX_CALLS

    def _parallel(self, name: str, args: dict[str, Any]) -> bool:
        """True when this call may run beside others: a plain read that would run without a card (the model loop's rule)."""
        from .subagents import parallel_safe
        spec = self.tb.specs.get(name)
        if spec is None or (name not in self.names and name not in self.found):
            return False
        raw = raw_mode(self.tb, self.ctx, name, self.modes)
        return (parallel_safe(spec, name, raw) and self.tb.gate(name, raw, self.ctx, args) == "on"
                and not self.tb.fs_needs_ask(name, args, self.ctx))

    async def batch(self, calls: list[tuple[str, dict[str, Any]]]) -> list[dict[str, Any]]:
        """Run calls in order, with each maximal run of parallel-safe reads together (at most parallelReads wide).
        Anything else is a barrier and runs alone; a tainting read ends its run so later calls are gated after it."""
        out: list[dict[str, Any] | None] = [None] * len(calls)
        width = limits.slots(_cfg(self.tb, self.ctx), "parallelReads")
        i = 0
        while i < len(calls):
            seg: list[int] = []
            j = i
            while width > 1 and j < len(calls) and self._parallel(*calls[j]):
                seg.append(j)
                j += 1
                if self.tb.taints(calls[j - 1][0]):
                    break
            if len(seg) < 2:
                out[i] = await self.handle(*calls[i])
                i += 1
                continue
            sem = asyncio.Semaphore(width)

            async def one(k: int) -> None:
                async with sem:
                    out[k] = await self.handle(*calls[k])
            await asyncio.gather(*(one(k) for k in seg))
            i = j
        return [r or {"ok": False, "error": "not run"} for r in out]

    async def handle(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        entry: dict[str, Any] = {"tool": name, "name": name, "ok": False}
        t0 = time.monotonic()
        try:
            resp = await self._handle(name, args, entry)
        finally:
            entry["ms"] = int((time.monotonic() - t0) * 1000)
            if entry.get("refused"):
                self.refused += 1
            if len(self.log) < LOG_KEEP * 2:
                self.log.append(entry)
        return resp

    async def _handle(self, name: str, args: dict[str, Any], entry: dict[str, Any]) -> dict[str, Any]:
        if name not in self.names and name not in self.found:
            return self._refuse(entry, f"{name} was not offered to this script. Offered: {', '.join(sorted(self.names)[:40]) or 'none'}"
                                       f"{' ...' if len(self.names) > 40 else ''}. grain_tools.search(query) finds others.")
        self.calls += 1
        if self.calls > self._max_calls():
            return self._refuse(entry, f"this script has made its limit of {self._max_calls()} tool calls (codeModeMaxCalls); "
                                       "stop here and print what you have")
        # A tool that was held back loads on its first bridged call, the way a direct call to it does.
        self.ctx.setdefault("tool_loaded", set()).add(name)
        raw = raw_mode(self.tb, self.ctx, name, self.modes)
        spec = self.tb.specs.get(name)
        connector = spec is None
        if raw == "off":
            return self._refuse(entry, f"{name} is turned off for this chat.")
        if connector:
            call_connector = self.ctx.get("connector_call") or getattr(sys.modules.get((__package__ or "personal_os") + ".app"), "_connector_call", None)
            if call_connector is None:
                return self._refuse(entry, f"{name} cannot be called from here.")
        if (bad := self.tb.precheck(name, args, self.ctx)) is not None:  # refused (e.g. a routed repo edit) before any card
            entry["ok"] = False
            return {"ok": True, "result": bad}
        danger = spec.danger if spec else _mcp_danger()
        # The same effective-mode rules the model's own calls get: taint upgrades on -> ask.
        # Args go too, so a cancel of a queued send is visible to the gate and a list is not.
        mode = self.tb.gate(name, raw, self.ctx, args)
        fs_ask = self.tb.fs_needs_ask(name, args, self.ctx)  # a credential store, or a write after untrusted content
        if fs_ask and mode == "on":
            mode = "ask"
        # Then the user's argument-pattern rules and this chat's session grants: a deny refuses, an ask rule cards.
        cfg = _cfg(self.tb, self.ctx)
        roots: list[str] = []
        if self.ctx.get("desk_id") and (ws := getattr(self.tb, "workspace", None)) is not None:
            roots.append(str(ws.desk_root(self.ctx["desk_id"])))
        perm = permrules.resolve(name, args, mode, mode != raw or fs_ask, rules=cfg.get("permissionRules"), roots=roots,
                                 conv=self.ctx.get("conversation_id"))
        if perm.refusal:
            return self._refuse(entry, f"{name} was refused: {perm.refusal}")
        gated, mode = mode, perm.mode
        # Auto mode reviews a call the reply loop would review (autoreview.route), not only the outer run_python: the script
        # may have computed these arguments from content the reviewer never saw. The approve callback's auto branch is that review.
        if (mode == "on" and self.approve is not None and not self.ctx.get("proposal_only")
                and (self.ctx.get("permission_mode") or autoreview.mode_of(cfg)) == "auto"
                and autoreview.route("auto", mode="on", danger=danger, covered=bool(perm.rule) or gated == "ask",
                                     explicit_on=(self.ctx.get("explicit_modes") or {}).get(name) == "on") != "run"):
            mode = "ask"
        why, narrowed = await guards.settle(self.tb, self.ctx, name, args, danger=danger, mode=mode)
        if why:
            return self._refuse(entry, f"{name} was refused: {why}")
        if narrowed and mode == "on":
            mode = "ask"
            perm.forced = True
        if mode == "ask":
            if self.approve is None or self.ctx.get("proposal_only"):
                return self._refuse(entry, f"{name} needs the user's approval and there is nobody to ask in this run.")
            # An ask rule's card is never lifted by skip-permissions, the same as in the reply loop.
            forced = perm.forced or perm.kind == "rule"
            self.asked += 1
            self._pending += 1
            if self._pending == 1:
                self._since = time.monotonic()
            try:
                allowed = await self.approve(name, args, forced)
            finally:
                self._pending -= 1
                if self._pending == 0:
                    self._paused += time.monotonic() - self._since
            entry["approved"] = bool(allowed)
            if not allowed:
                return self._refuse(entry, f"the user declined {name}.")
        call = self.ctx.get("bridge_call")
        self.ctx["fs_outside_ok"] = fs_ask  # the user said yes to it above
        try:
            if connector:
                contact = Contact(functools.partial(call_connector, name, args, hook_ctx(self.ctx)))
                result = await dispatch_tool(self.tb, self.ctx, name, args, call_id=f"bridgecall{self.calls}", connector=contact)
                if contact.reached:  # third-party text, whatever it said: the run is tainted from here
                    self.ctx["tainted"] = True
                    self.ctx.setdefault("taint_sources", []).append(name)
            else:
                # The run's own caller when the lane has one (undo snapshot, idempotency journal), else the seam directly.
                result = await (call(name, args, self.ctx) if call else dispatch_tool(self.tb, self.ctx, name, args, call_id=f"bridgecall{self.calls}"))
        finally:
            self.ctx["fs_outside_ok"] = False
        err = isinstance(result, dict) and result.get("error")
        if not connector and self.tb.taints(name) and not err:
            self.ctx.setdefault("taint_sources", []).append(name)  # appended every time: the fence reads growth
        entry["ok"] = not err
        return {"ok": True, "result": _fit(result)}


def _mcp_danger() -> str:
    from .mcp_client import MCP_DANGER
    return MCP_DANGER


async def run(tb: Any, ctx: dict[str, Any], code: str, timeout: int, wanted: list[str] | str,
              runner: Callable[..., dict[str, Any]]) -> dict[str, Any]:
    """The run_python tool's bridged path: open the socket, run the script off the loop, shape the answer."""
    from .tools import tool_error
    if not _cfg(tb, ctx).get("codeMode", True):
        return tool_error("Code mode is turned off in Settings, so run_python cannot take `tools`.",
                          alternative="call the tools directly, or run_python without `tools`")
    if isinstance(wanted, str) and wanted != "offered":
        wanted = [wanted]
    names, refused = offered(tb, ctx, wanted, ctx.get("modes"))
    if not names:
        return tool_error("None of the requested tools can be called from a script"
                          + (": " + "; ".join(f"{k} {v}" for k, v in refused.items()) if refused else " (none is on for this chat)"),
                          alternative="call the tools directly, or run_python without `tools`")
    # Named tools that were held back load now, the way a direct call to one does.
    if wanted != "offered":
        ctx.setdefault("tool_loaded", set()).update(names)
    bridge = Bridge(tb, ctx, names, ctx.get("modes"), ctx.get("bridge_approve"))
    await bridge.start()
    try:
        out = await asyncio.to_thread(runner, code, max(1, min(int(timeout), MAX_SECONDS)), None, bridge)
    finally:
        await bridge.stop()
    out["bridged_calls"] = bridge.calls
    out["bridge_log"] = [{"tool": e["name"], "ok": e["ok"], **({"why": e["why"]} if "why" in e else {})} for e in bridge.log[-20:]]
    out["calls"] = bridge.trace()
    out["summary"] = bridge.summary()
    if refused:
        out["tools_refused"] = refused
    if bridge.full_stdout is not None and getattr(tb, "results", None) is not None and ctx.get("conversation_id"):
        row = tb.results.store(ctx["conversation_id"], ctx.get("message_id"), "run_python", bridge.full_stdout,
                               {"type": "string", "chars": len(bridge.full_stdout),
                                # Paging it later has to taint again, even after the chat's banner is cleared.
                                **({"untrusted": True} if ctx.get("tainted") else {})})
        out["result_id"] = row["id"]
        out["note"] = (f"stdout was {len(bridge.full_stdout)} characters; the middle is cut. The whole text is behind "
                       f"result_id {row['id']} (read_tool_result with an offset).")
    return out
