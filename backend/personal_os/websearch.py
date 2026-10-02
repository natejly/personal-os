"""web_search backends: Brave, Tavily, Exa, DuckDuckGo and a user-run SearXNG, merged with reciprocal rank fusion.

A search key (Brave, then Tavily) answers alone, as it always has. Without one, SearXNG (when `searxngUrl` is set)
and Exa run side by side and their results are fused; DuckDuckGo is the fallback when both come back empty."""
from __future__ import annotations

import asyncio
import re
import urllib.parse
from dataclasses import dataclass, field
from typing import Any

import httpx

from . import reach

TIME_RANGES = ("day", "week", "month", "year")
BRAVE_FRESH = {"day": "pd", "week": "pw", "month": "pm", "year": "py"}
DDG_LIMIT = {"day": "d", "week": "w", "month": "m", "year": "y"}
_DOMAIN = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$", re.I)
_TRACKING = re.compile(r"^(utm_|fbclid$|gclid$|mc_eid$|ref$)", re.I)


class ProviderError(Exception):
    pass


@dataclass
class Hit:
    title: str
    url: str
    snippet: str = ""
    engines: list[str] = field(default_factory=list)
    score: float = 0.0


def norm_key(url: str) -> str:
    """Identity of a result across engines: host without www, no fragment or tracking params, no trailing slash, http == https."""
    try:
        u = urllib.parse.urlsplit((url or "").strip())
    except ValueError:
        return (url or "").strip().lower()
    host = (u.hostname or "").lower()
    host = host[4:] if host.startswith("www.") else host
    q = [(k, v) for k, v in urllib.parse.parse_qsl(u.query, keep_blank_values=True) if not _TRACKING.match(k)]
    path = (u.path or "/").rstrip("/") or "/"
    return f"{host}{f':{u.port}' if u.port and u.port not in (80, 443) else ''}{path}" + (f"?{urllib.parse.urlencode(q)}" if q else "")


def rrf_merge(lists: dict[str, list[dict[str, Any]]], k: int = 60) -> list[Hit]:
    by: dict[str, Hit] = {}
    for engine, rows in lists.items():
        for rank, row in enumerate(rows, 1):
            url = row.get("url")
            if not url:
                continue
            h = by.get(key := norm_key(url))
            if h is None:
                h = by[key] = Hit(str(row.get("title") or ""), url, "")
            h.score += 1.0 / (k + rank)
            if engine not in h.engines:
                h.engines.append(engine)
            snip = str(row.get("snippet") or "")
            if len(snip) > len(h.snippet):
                h.snippet = snip
    return sorted(by.values(), key=lambda h: -h.score)


def apply_site(query: str, site: str) -> str:
    site = (site or "").strip().lower()
    if not site:
        return query
    site = re.sub(r"^https?://", "", site).split("/")[0]
    if not _DOMAIN.match(site):
        raise ValueError(f"site must be a bare domain like sqlite.org, got {site!r}")
    return f"{query} site:{site}"


# ---- providers: each returns [{title,url,snippet}] ----
async def brave(cfg: dict[str, Any], q: str, n: int, tr: str) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"q": q, "count": n}
    if tr:
        params["freshness"] = BRAVE_FRESH[tr]
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get("https://api.search.brave.com/res/v1/web/search", params=params,
                        headers={"X-Subscription-Token": cfg["braveApiKey"], "Accept": "application/json"})
        r.raise_for_status()
        return [{"title": w.get("title"), "url": w.get("url"), "snippet": w.get("description")} for w in r.json().get("web", {}).get("results", [])[:n]]


async def tavily(cfg: dict[str, Any], q: str, n: int, tr: str) -> list[dict[str, Any]]:
    body: dict[str, Any] = {"api_key": cfg["tavilyApiKey"], "query": q, "max_results": n}
    if tr:
        body["time_range"] = tr
    async with httpx.AsyncClient(timeout=25) as c:
        r = await c.post("https://api.tavily.com/search", json=body)
        r.raise_for_status()
        return [{"title": w.get("title"), "url": w.get("url"), "snippet": w.get("content")} for w in r.json().get("results", [])[:n]]


async def exa(cfg: dict[str, Any], q: str, n: int, tr: str) -> list[dict[str, Any]]:
    # Exa (Agent Reach's pick: semantic, page highlights rather than one-line snippets). The keyless MCP has no date filter.
    try:
        return await reach.exa_search(q, n, str(cfg.get("exaApiKey") or ""))
    except (reach.ReachError, httpx.HTTPError, ValueError) as e:
        raise ProviderError(str(e).splitlines()[0] if str(e) else type(e).__name__) from None


async def ddg(cfg: dict[str, Any], q: str, n: int, tr: str) -> list[dict[str, Any]]:
    from ddgs import DDGS

    def _ddg() -> list[dict[str, Any]]:
        with DDGS() as d:
            kw = {"timelimit": DDG_LIMIT[tr]} if tr else {}
            return [{"title": r.get("title"), "url": r.get("href"), "snippet": r.get("body")} for r in d.text(q, max_results=n, **kw)]
    return await asyncio.to_thread(_ddg)


def searxng_base(cfg: dict[str, Any]) -> str:
    base = str(cfg.get("searxngUrl") or "").strip().rstrip("/")
    if base and urllib.parse.urlsplit(base).scheme not in ("http", "https"):
        raise ProviderError("searxngUrl must start with http:// or https://")
    return base


def parse_searxng(data: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for w in data.get("results") or []:
        if w.get("url"):
            out.append({"title": w.get("title") or "", "url": w["url"], "snippet": w.get("content") or ""})
    return out


async def searxng(cfg: dict[str, Any], q: str, n: int, tr: str) -> list[dict[str, Any]]:
    # The user's own instance, possibly on localhost: configured by the user, not model-chosen, so no SSRF guard.
    base = searxng_base(cfg)
    params: dict[str, Any] = {"q": q, "format": "json", "pageno": 1}
    if tr:
        params["time_range"] = "month" if tr == "week" else tr  # SearXNG has no week
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as c:
        r = await c.get(f"{base}/search", params=params, headers={"Accept": "application/json"})
    if r.status_code == 403:
        raise ProviderError("SearXNG has format=json disabled; add `json` under search.formats in settings.yml")
    if r.status_code != 200:
        raise ProviderError(f"SearXNG answered {r.status_code}")
    try:
        return parse_searxng(r.json())[:n]
    except ValueError:
        raise ProviderError("SearXNG did not return JSON") from None


async def search(cfg: dict[str, Any], query: str, want: int, time_range: str = "", site: str = "") -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """(rows, meta). meta: engines_used, failed{provider: first error line}, time_range_ignored / time_range_note."""
    tr = (time_range or "").strip().lower()
    if tr and tr not in TIME_RANGES:
        raise ValueError(f"time_range must be one of {', '.join(TIME_RANGES)}")
    q = apply_site(query, site)
    meta: dict[str, Any] = {}
    failed: dict[str, str] = {}

    async def one(name: str, fn: Any) -> list[dict[str, Any]]:
        return await fn(cfg, q, want, tr)

    if cfg.get("braveApiKey"):
        meta["engines_used"] = ["brave"]
        return await one("brave", brave), meta
    if cfg.get("tavilyApiKey"):
        meta["engines_used"] = ["tavily"]
        return await one("tavily", tavily), meta
    names = (["searxng"] if searxng_base_safe(cfg) else []) + ["exa"]
    fns = {"searxng": searxng, "exa": exa}
    results = await asyncio.gather(*(one(nm, fns[nm]) for nm in names), return_exceptions=True)
    good: dict[str, list[dict[str, Any]]] = {}
    for nm, res in zip(names, results):
        if isinstance(res, BaseException):
            if isinstance(res, asyncio.CancelledError):
                raise res
            failed[nm] = (str(res).splitlines() or [type(res).__name__])[0]
        elif res:
            good[nm] = res
    rows: list[dict[str, Any]]
    if len(good) >= 2:
        rows = [{"title": h.title, "url": h.url, "snippet": h.snippet, "engines": h.engines} for h in rrf_merge(good)]
        meta["engines_used"] = list(good)
    elif good:
        (nm, rows), = good.items()
        meta["engines_used"] = [nm]
    else:
        try:
            rows = await ddg(cfg, q, want, tr)
        except Exception as e:  # noqa: BLE001 -- DDG is the last resort; report, do not raise past the tool
            failed["ddg"] = (str(e).splitlines() or [type(e).__name__])[0]
            rows = []
        meta["engines_used"] = ["ddg"] if rows else []
    if tr and meta["engines_used"] and "exa" in meta["engines_used"] and "searxng" not in meta["engines_used"]:
        meta["time_range_ignored"] = True  # the keyless Exa MCP has no date filter
    if tr == "week" and "searxng" in meta["engines_used"]:
        meta["time_range_note"] = "SearXNG has no week range; it searched the past month"
    if failed:
        meta["failed"] = failed
    return rows, meta


def searxng_base_safe(cfg: dict[str, Any]) -> str:
    try:
        return searxng_base(cfg)
    except ProviderError:
        return ""
