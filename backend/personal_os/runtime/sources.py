"""Tool sources (design doc section 10.7.2): where extra tools come from besides the built-in ones and the MCP connectors.

An extension's source is treated as a connector. Its tools are named `x__<extension id>__<tool>`, are `external` by
construction, default to `ask`, and every call goes through `dispatch_tool(connector=...)`, so the permission stack, the
journal and the hooks apply and the run is tainted. A tool whose declaration changes after the user first saw it is withheld
until the user accepts the change.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass, replace
from typing import Any, Callable

from ..db import Database
from .slots import Provider, Runtime, Scopes

log = logging.getLogger(__name__)

PREFIX = "x__"
SERVER = "extension"  # the group the deferred-tool index lists these tools under
HASHES = "toolSourceHashes"  # private setting: {slug: the declaration hash the user last saw}
MAX_NAME = 64  # what a model API accepts as a function name
CALL_TIMEOUT_S = 120.0


@dataclass(frozen=True)
class SourceTool:
    slug: str
    provider: Provider
    name: str
    description: str
    parameters: dict[str, Any]
    digest: str
    quarantined: bool


def is_source_tool(name: str) -> bool:
    return name.startswith(PREFIX)


def _digest(name: str, description: str, schema: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps([name, description, schema], sort_keys=True).encode()).hexdigest()[:16]


class ToolSources:
    def __init__(self, rt: Runtime, db: Database, taken: Callable[[], set[str]]) -> None:
        self.rt, self.db, self.taken = rt, db, taken  # `taken`: the tool names the app already offers
        self._last: dict[str, list[SourceTool]] = {}  # provider id -> its last accepted-shape listing

    def _list(self, provider: Provider) -> list[SourceTool]:
        """The provider's tools now. A failing or malformed `list()` keeps the last listing."""
        try:
            seen = self.db.get_settings().get(HASHES) or {}
            known, taken = dict(seen), self.taken()
            out: list[SourceTool] = []
            for d in provider.impl.list():
                slug = f"{PREFIX}{provider.extension}__{d['name']}"
                if len(slug) > MAX_NAME or slug in taken or any(t.slug == slug for t in out):
                    log.warning("tool source %s: %r is not a usable tool name here; skipped", provider.id, d["name"])
                elif d.get("danger", "external") != "external":
                    log.warning("tool source %s: %r declares danger %r, a source's tools are external; skipped", provider.id, d["name"], d["danger"])
                else:
                    digest = _digest(d["name"], d.get("description", ""), d["schema"])
                    out.append(SourceTool(slug, provider, d["name"], d.get("description") or d["name"], d["schema"], digest,
                                          seen.setdefault(slug, digest) != digest))  # a tool seen for the first time is the user's baseline
            if seen != known:
                self.db.set_settings({HASHES: seen})
        except Exception:  # noqa: BLE001 - a source that cannot list keeps what it last listed
            log.warning("tool source %s failed to list its tools", provider.id, exc_info=True)
            return self._last.get(provider.id, [])
        self._last[provider.id] = out
        return out

    def tools(self, scopes: Scopes) -> list[SourceTool]:
        """The tools to offer this turn: those of the sources enabled for the chat, without any withheld for review."""
        extra = self.rt.resolve("tools.source", scopes).extra
        return [t for i in extra if (p := self.rt.provider("tools.source", i)) is not None for t in self._list(p) if not t.quarantined]

    def pending(self) -> list[SourceTool]:
        """The tools withheld until the user accepts their changed declaration."""
        return [t for tools in self._last.values() for t in tools if t.quarantined]

    def accept(self, slug: str) -> bool:
        tool = next((t for tools in self._last.values() for t in tools if t.slug == slug), None)
        if tool is None:
            return False
        self.db.set_settings({HASHES: {**(self.db.get_settings().get(HASHES) or {}), slug: tool.digest}})
        self._last[tool.provider.id] = [replace(t, quarantined=False) if t.slug == slug else t for t in self._last[tool.provider.id]]
        return True

    def find(self, slug: str) -> SourceTool | None:
        return next((t for tools in self._last.values() for t in tools if t.slug == slug and not t.quarantined), None)

    async def call(self, slug: str, args: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
        """One call on the tool's source. A tool already offered in a running turn stays callable until the turn ends."""
        tool = self.find(slug)
        if tool is None or tool.provider.impl is None:
            raise LookupError(f"No such tool: {slug}")
        out = await asyncio.wait_for(tool.provider.impl.call(tool.name, args, ctx), CALL_TIMEOUT_S)
        if not isinstance(out, dict):
            raise TypeError("a tool source returns an object")
        return out
