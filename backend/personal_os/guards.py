"""The core guards: the stateless refusals every tool call meets before any extension sees it.

CORE_GUARDS is a module-level tuple of frozen Guard entries in a fixed order. A guard returns the reason a call is refused,
or None. They need no card and no decision from a person: asks, cards, the reviewer and the approval wait stay in each loop.
run_core_guards runs them first in dispatch_tool (before tool.pre) and again in RunStore.open_approval; the loops call the
same functions for their own refusals, so each rule has one implementation.

An extension cannot reach any of this: the tuple is never handed to a scope, a context, the hook bus or a service, and no
hook is named after a guard. `sealed` compares the tuple, each guard's function and its code against what was computed at
import; a mismatch means something in this process changed them, and every call is refused. The extension tiers are
installed software running in this process, so this detects a tamper and fails closed; it does not isolate.
"""
from __future__ import annotations

import copy
import hashlib
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from . import fsx, mac, permissions, permrules
from .mcp_servers import RESERVED_PREFIX
from .extensions.hooks import Ask, Deny, HookBus

if TYPE_CHECKING:
    from .tools import Toolbox

log = logging.getLogger(__name__)

TAMPERED = "core guards were modified"


@dataclass(frozen=True)
class Call:
    """One tool call as the guards see it. `roots`/`cwd` say where a relative shell path starts (None: derived from the
    run); `kind` is the destructive category of a call that is not in the toolbox (a connector tool the server calls destructive)."""
    toolbox: Toolbox
    ctx: dict[str, Any]
    name: str
    args: dict[str, Any]
    roots: tuple[str, ...] | None = None
    cwd: str | None = None
    kind: str | None = None


@dataclass(frozen=True)
class Guard:
    id: str
    fn: Callable[[Call], str | None]


@dataclass(frozen=True)
class Refusal:
    guard: str
    reason: str

    def result(self, name: str) -> dict[str, Any]:
        from .tools import denied
        return denied(name, self.reason)


def _settings(c: Call) -> dict[str, Any]:
    return c.ctx.get("settings") or c.toolbox.settings()


def _where(c: Call) -> tuple[list[str], str | None]:
    """(roots, cwd) for permrules: the desk workspace, and for a shell command where the last one in this chat ended."""
    if c.roots is not None:
        return list(c.roots), c.cwd
    from . import shell
    roots, cwd = shell.perm_where(c.toolbox, c.ctx)
    return roots, cwd if c.name == "shell_run" else None


def plan_refusal(danger: str, *, planning: bool, propose_without_run: bool) -> str | None:
    """Nothing consequential runs while a desk drafts its plan; a propose-only desk with no run to file under refuses external calls."""
    from .plans import PLAN_BLOCKED, PLAN_SAFE_DANGER, PROPOSE_ONLY
    if planning and danger not in PLAN_SAFE_DANGER:
        return PLAN_BLOCKED
    if propose_without_run and danger == "external":
        return PROPOSE_ONLY
    return None


def rule_refusal(name: str, args: dict[str, Any], rules: Any, *, roots: list[str], cwd: str | None, hardline: bool) -> str | None:
    """Why the hardline list (`hardline`) or a deny rule (not) refuses this call. A connector slug is judged by `mcp_denied`."""
    if name.startswith(RESERVED_PREFIX):
        return None if hardline else permrules.mcp_denied(name, rules)
    v = permrules.evaluate(name, args, rules, roots=roots, cwd=cwd)
    return v.refusal if v.action == "deny" and v.hardline == hardline else None


def _unknown_or_off(c: Call) -> str | None:
    if c.toolbox.specs.get(c.name) is None and not c.name.startswith(RESERVED_PREFIX):
        return "not a tool in this request"
    modes = c.ctx.get("modes")
    return "turned off for this run" if isinstance(modes, dict) and modes.get(c.name) == "off" else None


def _proposal_only(c: Call) -> str | None:
    if c.ctx.get("proposal_only") and c.toolbox.proposes(c.name):
        return ("not available in this unattended background run: it is recorded as a proposal the user accepts or rejects. "
                "Describe what you proposed and move on")
    return None


def _plan_block(c: Call) -> str | None:
    spec = c.toolbox.specs.get(c.name)
    return plan_refusal(spec.danger if spec else "safe", planning=bool(c.ctx.get("planning")),
                        propose_without_run=bool(c.ctx.get("propose_without_run")))


def _hardline(c: Call) -> str | None:
    roots, cwd = _where(c)
    return rule_refusal(c.name, c.args, permissions.get(_settings(c), "permissionRules"), roots=roots, cwd=cwd, hardline=True)


def _deny_rules(c: Call) -> str | None:
    roots, cwd = _where(c)
    return rule_refusal(c.name, c.args, permissions.get(_settings(c), "permissionRules"), roots=roots, cwd=cwd, hardline=False)


def protected_reason(toolbox: Toolbox, ctx: dict[str, Any], name: str, args: dict[str, Any]) -> str | None:
    """Why a file tool's path is Grain's own data folder or app (mac.protected_reason); a desk's own workspace sits in there and is fine."""
    grants = fsx.grants_for(toolbox, ctx)
    desk = grants.desk
    start = mac.home() if name in permrules.HOME_TOOLS or desk is None else desk
    for sub in permrules.subject_for(name, args):
        if sub.kind not in ("Read", "Edit") or not sub.value:
            continue
        p = Path(os.path.expanduser(sub.value))
        p = (p if p.is_absolute() else start / p)
        try:
            resolved = p.resolve()
        except (OSError, RuntimeError):
            resolved = p
        if grants.in_desk(resolved):
            continue
        if why := mac.protected_reason(p, resolved):
            return f"{sub.value!r}: {why}"
    return None


def _protected_paths(c: Call) -> str | None:
    return protected_reason(c.toolbox, c.ctx, c.name, c.args)


def floor_refusal(toolbox: Toolbox, ctx: dict[str, Any], name: str, args: dict[str, Any], kind: str | None = None) -> str | None:
    """The destructive call the user's destructiveDefaults blocks in Manual mode (Toolbox.floor_action)."""
    if toolbox.floor_action(name, args, ctx, kind) != "block":
        return None
    return permissions.blocked_reason(toolbox.floor_kind(name, args, ctx) or kind or "items")


def _floor_block(c: Call) -> str | None:
    return floor_refusal(c.toolbox, c.ctx, c.name, c.args, c.kind)


CORE_GUARDS: tuple[Guard, ...] = (
    Guard("unknown_or_off", _unknown_or_off),
    Guard("proposal_only", _proposal_only),
    Guard("plan_block", _plan_block),
    Guard("hardline", _hardline),
    Guard("deny_rules", _deny_rules),
    Guard("protected_paths", _protected_paths),
    Guard("floor_block", _floor_block),
)


def run_core_guards(toolbox: Toolbox, ctx: dict[str, Any], name: str, args: dict[str, Any], *, roots: list[str] | None = None,
                    cwd: str | None = None, kind: str | None = None) -> Refusal | None:
    """The first refusal, in order, or None. Fails closed when the guards were modified."""
    if not sealed():
        log.critical("%s: every tool call is refused", TAMPERED)
        return Refusal("sealed", TAMPERED)
    call = Call(toolbox, ctx, name, args if isinstance(args, dict) else {}, None if roots is None else tuple(roots), cwd, kind)
    for g in CORE_GUARDS:
        if reason := g.fn(call):
            return Refusal(g.id, reason)
    return None


async def extension_gate(bus: HookBus, name: str, args: dict[str, Any], *, danger: str, mode: str, tainted: bool) -> tuple[str | None, bool]:
    """Ask the `tool.permission` handlers about a call the core settled on running or carding: (the refusal text, whether the
    call now needs a card). Only Deny or Ask comes back from a handler, so an extension can narrow the verdict and never widen it."""
    if not bus.active("tool.permission"):
        return None, False
    verdict = await bus.gate("tool.permission", {"name": name, "args": copy.deepcopy(args), "danger": danger, "mode": mode,
                                                 "tainted": tainted})
    return (blocked_by_extension(verdict) if isinstance(verdict, Deny) else None), isinstance(verdict, Ask)


async def settle(toolbox: Toolbox, ctx: dict[str, Any], name: str, args: dict[str, Any], *, danger: str, mode: str, roots: list[str] | None = None,
                 cwd: str | None = None, kind: str | None = None) -> tuple[str | None, bool]:
    """What a loop does last, once its own chain has settled on run or card: the core guards, then the extension gate.
    -> (the refusal text, whether the call now needs a card). No extension handler runs for a call the core refuses."""
    if refusal := run_core_guards(toolbox, ctx, name, args, roots=roots, cwd=cwd, kind=kind):
        return refusal.reason, False
    return await extension_gate(toolbox.bus, name, args, danger=danger, mode=mode, tainted=bool(ctx.get("tainted")))


def blocked_by_extension(verdict: Deny) -> str:
    return f"blocked by an extension ({verdict.reason})"


def _fingerprint() -> tuple[Any, ...]:
    out: list[Any] = []
    for g in CORE_GUARDS:
        code = g.fn.__code__
        digest = hashlib.sha256(code.co_code + repr((code.co_names, [c for c in code.co_consts if not hasattr(c, "co_code")])).encode())
        out.append((g, g.id, g.fn, code, digest.hexdigest(), globals().get(g.fn.__name__) is g.fn))
    return (CORE_GUARDS, tuple(out))


_SEAL = _fingerprint()


def sealed() -> bool:
    """True while the guards are exactly the ones this module defined at import."""
    return _fingerprint() == _SEAL
