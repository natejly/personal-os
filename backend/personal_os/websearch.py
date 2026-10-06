"""web_search backends: Firecrawl, Brave, Tavily, Exa, DuckDuckGo and a user-run SearXNG, merged with reciprocal rank fusion.

A search key answers alone (Firecrawl first when its key is set, then Brave, then Tavily), as it always has. Without one, SearXNG (when `searxngUrl` is set)
and Exa run side by side and their results are fused; DuckDuckGo is the fallback when both come back empty."""
from __future__ import annotations

import asyncio
import re
import urllib.parse
from dataclasses import dataclass, field
from typing import Any

import httpx

from . import firecrawl, reach

TIME_RANGES = ("day", "week", "month", "year")
BRAVE_FRESH = {"day": "pd", "week": "pw", "month": "pm", "year": "py"}
DDG_LIMIT = {"day": "d", "week": "w", "month": "m", "year": "y"}
_DOMAIN = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$", re.I)
_TRACKING = re.compile(r"^(utm_|fbclid$|gclid$|mc_eid$|ref$)", re.I)


BRAVE_MAX = 20  # Brave answers 422 for count > 20
RETRY_DELAYS = (0.5, 1.5)  # backoff before the 2nd and 3rd attempt on a keyed engine


def clamp_query(q: str) -> str:
    """Brave rejects (422) queries over 400 chars or 50 words: collapse whitespace, then cut to both limits."""
    return " ".join(q.split()[:50])[:400].rstrip()


def _why(e: BaseException) -> str:
    if isinstance(e, httpx.HTTPStatusError):
        return f"HTTP {e.response.status_code}"
    return (str(e).splitlines() or [""])[0] or type(e).__name__


async def _retrying(fn: Any, *a: Any) -> list[dict[str, Any]]:
    """Retry 429, 5xx and network errors with backoff; other statuses (400, 401, 422...) fail at once."""
    for i in range(len(RETRY_DELAYS) + 1):
        try:
            return await fn(*a)
        except httpx.HTTPError as e:
            code = e.response.status_code if isinstance(e, httpx.HTTPStatusError) else 0
            if (code and code != 429 and code < 500) or i == len(RETRY_DELAYS):
                raise
            await asyncio.sleep(RETRY_DELAYS[i])
    return []


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
    params: dict[str, Any] = {"q": q, "count": min(n, BRAVE_MAX)}
    n = min(n, BRAVE_MAX)
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
    # Exa (semantic, page highlights rather than one-line snippets). The keyless MCP has no date filter.
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


def _hosts(domains: Any) -> list[str]:
    out = []
    for d in domains or []:
        h = re.sub(r"^https?://", "", str(d).strip().lower()).split("/")[0].removeprefix("www.")
        if not _DOMAIN.match(h):
            raise ValueError(f"domains must be bare domains like sqlite.org, got {d!r}")
        out.append(h)
    return out


def filter_domains(rows: list[dict[str, Any]], allowed: list[str], blocked: list[str]) -> list[dict[str, Any]]:
    """Keep rows whose own host is (a subdomain of) an allowed domain / is not a blocked one. A page's links never extend the list."""
    def host(r: dict[str, Any]) -> str:
        return (urllib.parse.urlsplit(str(r.get("url") or "")).hostname or "").removeprefix("www.")
    def hit(h: str, ds: list[str]) -> bool:
        return any(h == d or h.endswith("." + d) for d in ds)
    return [r for r in rows if (not allowed or hit(host(r), allowed)) and not hit(host(r), blocked)]


async def search(cfg: dict[str, Any], query: str, want: int, time_range: str = "", site: str = "",
                 allowed_domains: list[str] | None = None, blocked_domains: list[str] | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """(rows, meta). meta: engines_used, failed{provider: first error line}, time_range_ignored / time_range_note.

    allowed_domains / blocked_domains filter the merged rows; setting both is an error raised before any engine is called."""
    if allowed_domains and blocked_domains:
        raise ValueError("set allowed_domains or blocked_domains, not both")
    allow, block = _hosts(allowed_domains), _hosts(blocked_domains)
    if allow or block:
        rows, meta = await search(cfg, query, want, time_range, site)
        return filter_domains(rows, allow, block), meta
    tr = (time_range or "").strip().lower()
    if tr and tr not in TIME_RANGES:
        raise ValueError(f"time_range must be one of {', '.join(TIME_RANGES)}")
    q = clamp_query(apply_site(query, site))
    meta: dict[str, Any] = {}
    failed: dict[str, str] = {}

    async def one(name: str, fn: Any) -> list[dict[str, Any]]:
        return await fn(cfg, q, want, tr)

    for nm, key, fn in (("firecrawl", firecrawl.key(cfg), firecrawl.search), ("brave", cfg.get("braveApiKey"), brave), ("tavily", cfg.get("tavilyApiKey"), tavily)):
        if key:
            try:
                meta["engines_used"] = [nm]
                rows = await _retrying(fn, cfg, q, want, tr)
                if failed:
                    meta["failed"] = dict(failed)
                return rows, meta
            except (httpx.HTTPError, reach.ReachError) as e:  # keyed engine down: note it and fall through as if no key were set
                failed[nm] = _why(e)
    meta.pop("engines_used", None)
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
