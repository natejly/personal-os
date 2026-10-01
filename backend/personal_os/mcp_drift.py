"""Tool-definition drift: what changed in a connector's tool, what is newly suspicious, and who decides.

mcp_servers pins a schema_hash and decays an 'on' grant to 'ask' when it changes, but that only says
*that* something changed. This module says *what* (a diff of the old and new shape), re-runs the
static eval on the new text, flags findings that are new relative to the old shape, checks for
cross-server shadowing, and withholds a tool whose change adds a fail-level finding until the user
clicks Accept. Accepting never raises a grant: the stale-hash decay to 'ask' stays.
"""
from __future__ import annotations

import difflib
import re
from typing import Any

from . import mcp_eval
from .mcp_servers import McpServers


def _sentences(text: str) -> list[str]:
    return [p for p in re.split(r"(?<=[.!?])\s+|\n", text or "") if p.strip()]


def _props(params: Any) -> dict[str, Any]:
    p = params.get("properties") if isinstance(params, dict) else None
    return p if isinstance(p, dict) else {}


def _required(params: Any) -> set[str]:
    r = params.get("required") if isinstance(params, dict) else None
    return {str(x) for x in r} if isinstance(r, list) else set()


def diff_shape(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """Description as unified-diff lines, and what moved in the parameters."""
    desc = list(difflib.unified_diff(_sentences(old.get("description", "")), _sentences(new.get("description", "")),
                                     "previous", "current", lineterm="", n=1))
    op, np_ = _props(old.get("parameters")), _props(new.get("parameters"))
    changed = [k for k in np_ if k in op and op[k] != np_[k]]
    return {"description": desc,
            "added_params": sorted(set(np_) - set(op)),
            "removed_params": sorted(set(op) - set(np_)),
            "changed_params": sorted(changed),
            "new_required": sorted(_required(new.get("parameters")) - _required(old.get("parameters")))}


def _with_server(store: McpServers, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    slugs = {s["id"]: s["slug"] for s in store.servers()}
    return [{**t, "server_slug": slugs.get(t["server_id"], "")} for t in tools]


def _key(f: dict[str, Any]) -> tuple[str, str]:
    return f["code"], f["where"]


def _findings(shape: dict[str, Any], tool: dict[str, Any], others: list[dict[str, Any]]) -> list[dict[str, Any]]:
    probe = {**tool, "description": shape.get("description", ""), "parameters": shape.get("parameters") or {}}
    out = mcp_eval.evaluate_tools([probe], {tool["name"]: tool["slug"]})["findings"]
    return out + mcp_eval.check_shadowing(probe, others, tool["slug"])


def review_change(store: McpServers, slug: str, other_tools: list[dict[str, Any]] | None = None) -> dict[str, Any] | None:
    """Compare a tool's two latest shapes. None when there is no prior shape to compare against."""
    tool = store.tool(slug)
    versions = store.versions(slug, 2)
    if not tool or len(versions) < 2:
        return None
    new, old = versions[0], versions[1]
    other_tools = _with_server(store, store.tools() if other_tools is None else other_tools)
    srv = next((s["slug"] for s in store.servers() if s["id"] == tool["server_id"]), "")
    tool = {**tool, "server_slug": srv}
    others = [t for t in other_tools if t.get("server_id") != tool["server_id"]]
    old_keys = {_key(f) for f in _findings(old, tool, others)}
    new_findings = [f for f in _findings(new, tool, others) if _key(f) not in old_keys]
    return {"previous": {"description": old["description"], "parameters": old["parameters"], "schema_hash": old["schema_hash"],
                         "seen_at": old["seen_at"]},
            "current": {"description": new["description"], "parameters": new["parameters"], "schema_hash": new["schema_hash"],
                        "seen_at": new["seen_at"]},
            "diff": diff_shape(old, new),
            "new_findings": new_findings,
            "quarantine": any(f["severity"] == "fail" for f in new_findings),
            "changed_at": new["seen_at"]}


def apply_review(store: McpServers, slug: str) -> dict[str, Any] | None:
    """Run after a sync reported `slug` as changed: quarantine on a new fail, and file the report."""
    review = review_change(store, slug)
    tool = store.tool(slug)
    if not review or not tool:
        return review
    if review["quarantine"]:
        store.set_quarantine(slug, True)
    findings = [{**f, "origin": "drift"} for f in review["new_findings"]]
    status = mcp_eval.status_for(findings)
    summary = ("drift: definition changed" + (" and withheld until you accept it" if review["quarantine"] else "")
               + (f"; {len(findings)} new finding{'s' if len(findings) != 1 else ''}" if findings else "; nothing new flagged"))
    store.record_eval(tool["server_id"], status, summary, findings, tool_slug=tool["slug"], model=mcp_eval.EVAL_MODEL)
    return review


def accept(store: McpServers, slug: str) -> dict[str, Any] | None:
    """The user has read the change. Releases the quarantine; does not touch any grant."""
    if store.tool(slug) is None:
        return None
    store.mark_reviewed(slug)
    return store.tool(slug)


def offerable(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The tools the model may be offered: a quarantined one is treated like a server that is down."""
    return [t for t in tools if not t.get("quarantined_at")]


def view(store: McpServers, tool: dict[str, Any], all_tools: list[dict[str, Any]] | None = None) -> dict[str, Any] | None:
    """What the UI shows for a tool whose shape changed since the user last saw it, else None."""
    if not tool.get("schema_changed_at") or tool.get("reviewed_hash") == tool.get("schema_hash"):
        return None
    review = review_change(store, tool["slug"], all_tools)
    if review is None:
        return None
    return {"previous": review["previous"], "current": review["current"], "diff": review["diff"],
            "new_findings": review["new_findings"], "quarantined": bool(tool.get("quarantined_at")),
            "changed_at": review["changed_at"]}
