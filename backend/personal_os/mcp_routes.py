"""Routes for finding and adding MCP connectors: the catalog, the public registry, and config import.

None of these routes ever returns a secret value. Install and import hand the values straight to McpServers.create_server,
which keeps the secret ones in the secret store and answers with key names only.
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from . import mcp_catalog, mcp_import, mcp_path
from .mcp_client import McpClient
from .mcp_servers import McpServers

REGISTRY_URL = "https://registry.modelcontextprotocol.io/v0/servers"
REGISTRY_TIMEOUT = 8.0         # a slow registry is an error line in the UI, never a hung request
REGISTRY_TTL = 300.0           # the same search within five minutes is answered from memory
REGISTRY_CACHE_MAX = 64        # distinct (query, limit) pairs kept
REGISTRY_DEFAULT_LIMIT = 20
REGISTRY_MAX_LIMIT = 50
REGISTRY_TEXT_CHARS = 300      # registry text is third-party; the card gets a short slice
REGISTRY_REMOTE_TYPES = {"streamable-http": "http", "http": "http", "sse": "sse"}


class InstallIn(BaseModel):
    name: str | None = None
    values: dict[str, str] = Field(default_factory=dict)


class ImportIn(BaseModel):
    refs: list[str] = Field(default_factory=list)


class ImportJsonIn(BaseModel):
    text: str
    enabled: bool | None = None  # None keeps each server's own enabled flag


def _install_of(pkg: dict[str, Any]) -> tuple[str, list[str]] | None:
    """(command, args) that runs a registry package over stdio, or None for a package kind Grain cannot launch."""
    kind, ident, version = pkg.get("registryType"), str(pkg.get("identifier") or ""), str(pkg.get("version") or "")
    if not ident:
        return None
    env_flags = [x for e in pkg.get("environmentVariables") or [] if isinstance(e, dict) and e.get("name") for x in ("-e", str(e["name"]))]
    if kind == "npm":
        return "npx", ["-y", f"{ident}@{version}" if version else ident]
    if kind == "pypi":
        return "uvx", [f"{ident}=={version}" if version else ident]
    if kind == "oci":
        return "docker", ["run", "-i", "--rm", *env_flags, ident]
    return None


def map_registry_result(item: dict[str, Any]) -> dict[str, Any] | None:
    """One registry listing as the form pre-fill the UI wants. A server with no way Grain can start it is dropped."""
    srv = item.get("server") if isinstance(item.get("server"), dict) else item
    name = str(srv.get("name") or "")
    if not name:
        return None
    transport, command, args, url = "", "", [], ""
    env_decl: list[dict[str, Any]] = []
    header_decl: list[dict[str, Any]] = []
    for pkg in srv.get("packages") or []:
        launch = _install_of(pkg) if isinstance(pkg, dict) else None
        if launch:
            transport, (command, args) = "stdio", launch
            env_decl = [e for e in pkg.get("environmentVariables") or [] if isinstance(e, dict) and e.get("name")]
            break
    if not transport:
        for rem in srv.get("remotes") or []:
            kind = REGISTRY_REMOTE_TYPES.get(str(rem.get("type") or "")) if isinstance(rem, dict) else None
            if kind and str(rem.get("url") or "").startswith("https://"):
                transport, url = kind, rem["url"]
                header_decl = [h for h in rem.get("headers") or [] if isinstance(h, dict) and h.get("name")]
                break
    if not transport:
        return None
    repo = srv.get("repository") if isinstance(srv.get("repository"), dict) else {}
    repo_url = str(repo.get("url") or "") or None
    secret_keys = [str(x["name"]) for x in (*env_decl, *header_decl) if x.get("isSecret")]
    return {"id": name, "name": name.rsplit("/", 1)[-1], "description": str(srv.get("description") or "")[:REGISTRY_TEXT_CHARS],
            "version": str(srv.get("version") or ""), "repository": repo_url, "verified": False, "transport": transport,
            "install": {"command": command, "args": args, "url": url, "env": {str(e["name"]): "" for e in env_decl},
                        "headers": {str(h["name"]): "" for h in header_decl}},
            "secret_keys": secret_keys, "env_keys": [str(e["name"]) for e in env_decl],
            "docs": str(srv.get("websiteUrl") or "") or repo_url}


_cache: dict[tuple[str, int], tuple[float, list[dict[str, Any]]]] = {}


async def registry_search(q: str, limit: int) -> list[dict[str, Any]]:
    """Live registry search, TTL-cached. Raises httpx errors for the route to turn into a message."""
    key = (q.strip().lower(), limit)
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < REGISTRY_TTL:
        return hit[1]
    async with httpx.AsyncClient(timeout=REGISTRY_TIMEOUT) as http:
        r = await http.get(REGISTRY_URL, params={"search": q.strip(), "limit": limit})
        r.raise_for_status()
        body = r.json()
    seen: set[str] = set()
    results: list[dict[str, Any]] = []
    for item in body.get("servers") or []:
        if not isinstance(item, dict) or not (m := map_registry_result(item)) or m["id"] in seen:
            continue
        seen.add(m["id"])
        results.append(m)
    if len(_cache) >= REGISTRY_CACHE_MAX:
        _cache.pop(next(iter(_cache)))
    _cache[key] = (time.monotonic(), results)
    return results


def router(store: McpServers, client: McpClient, server_view: Callable[[dict[str, Any]], dict[str, Any]]) -> APIRouter:
    """store/client: the app's McpServers and McpClient. server_view: the app's server-row presenter (one shape everywhere)."""
    r = APIRouter()

    @r.get("/mcp/catalog")
    async def catalog() -> dict[str, Any]:
        servers = store.servers()
        entries = [{**e, "installed": [s["id"] for s in servers if s.get("catalog_id") == e["id"]],
                    "detected": await asyncio.to_thread(mcp_catalog.detect, e)} for e in mcp_catalog.load()["entries"]]
        return {"entries": entries, "categories": mcp_catalog.CATEGORIES, "runtimes": await asyncio.to_thread(mcp_path.runtimes)}

    @r.post("/mcp/catalog/{entry_id}/install")
    async def install(entry_id: str, body: InstallIn) -> dict[str, Any]:
        entry = mcp_catalog.get(entry_id)
        if entry is None:
            raise HTTPException(404, "No such catalog entry")
        found = await asyncio.to_thread(mcp_catalog.detect, entry)
        if found is not None and not found["found"]:
            raise HTTPException(409, found["hint"] or f"{entry['name']} is not installed on this Mac")
        try:
            kwargs = mcp_catalog.render_install(entry, body.values, found["path"] if found else "")
        except ValueError as e:  # names the field, never a value
            raise HTTPException(400, str(e)) from e
        if body.name and body.name.strip():
            kwargs["name"] = body.name.strip()
        row = store.create_server(**kwargs)
        await client.sync()
        return server_view(store.server(row["id"]) or row)

    @r.get("/mcp/registry/search")
    async def registry(q: str = "", limit: int = REGISTRY_DEFAULT_LIMIT) -> dict[str, Any]:
        try:
            return {"results": await registry_search(q, max(1, min(limit, REGISTRY_MAX_LIMIT))), "error": None}
        except (httpx.HTTPError, ValueError) as e:
            return {"results": [], "error": f"The registry could not be reached ({type(e).__name__})"}

    @r.get("/mcp/import/sources")
    async def import_sources() -> dict[str, Any]:
        view, _configs = await asyncio.to_thread(mcp_import.scan, store.servers())
        return {"sources": view}

    @r.post("/mcp/import")
    async def import_servers(body: ImportIn) -> dict[str, Any]:
        _view, configs = await asyncio.to_thread(mcp_import.scan, store.servers())
        installed = {s["ref"] for src in _view for s in src["servers"] if s["installed"]}
        created: list[dict[str, Any]] = []
        skipped: list[dict[str, str]] = []
        for ref in dict.fromkeys(body.refs):  # a ref listed twice imports once
            if ref not in configs:
                skipped.append({"ref": ref, "reason": "not found in the config files"})
            elif ref in installed:
                skipped.append({"ref": ref, "reason": "already added"})
            else:
                created.append(store.create_server(**mcp_import.create_kwargs(configs[ref])))
        if created:
            await client.sync()
        return {"created": [server_view(store.server(c["id"]) or c) for c in created], "skipped": skipped}

    @r.post("/mcp/import/json")
    async def import_json(body: ImportJsonIn) -> dict[str, Any]:
        try:
            cfgs = mcp_import.parse_pasted(body.text)
        except ValueError as e:  # a fixed message: pasted values are never echoed
            raise HTTPException(400, str(e)) from e
        have = list(store.servers())
        created: list[dict[str, Any]] = []
        skipped: list[dict[str, str]] = []
        for cfg in cfgs:
            if any(mcp_import.same(cfg, s) for s in have):
                skipped.append({"name": cfg["key"], "reason": "already added"})
                continue
            kw = mcp_import.create_kwargs(cfg)
            if body.enabled is not None:
                kw["enabled"] = body.enabled
            row = store.create_server(**kw)
            have.append(row)
            created.append(row)
        if created:
            await client.sync()
        return {"created": [server_view(store.server(c["id"]) or c) for c in created], "skipped": skipped}

    return r
