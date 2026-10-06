"""Firecrawl: web_search and page reads through its hosted API. Primary when a key is set; every failure falls back."""
from __future__ import annotations

import asyncio
import os
from typing import Any

import httpx

from . import reach

BASE = "https://api.firecrawl.dev/v1"
ENV = "FIRECRAWL_API_KEY"
TBS = {"day": "qdr:d", "week": "qdr:w", "month": "qdr:m", "year": "qdr:y"}


def key(cfg: dict[str, Any]) -> str:
    """The settings key, else the environment's: a key typed in Settings wins over a shell default."""
    return str(cfg.get("firecrawlApiKey") or "").strip() or os.environ.get(ENV, "").strip()


def _auth(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}"}


async def search(cfg: dict[str, Any], q: str, n: int, tr: str) -> list[dict[str, Any]]:
    # No scrapeOptions: web_search returns snippets, fetch_url reads the full page.
    body: dict[str, Any] = {"query": q, "limit": n}
    if tr:
        body["tbs"] = TBS[tr]
    async with httpx.AsyncClient(timeout=25) as c:
        r = await c.post(f"{BASE}/search", json=body, headers=_auth(key(cfg)))
        r.raise_for_status()  # 429/5xx are retried by websearch._retrying
        try:
            data = r.json()
        except ValueError:
            data = None
    if not isinstance(data, dict):
        raise reach.ReachError("Firecrawl did not return JSON")
    if data.get("success") is False:
        raise reach.ReachError(str(data.get("error") or "Firecrawl search failed"))
    return [{"title": w.get("title"), "url": w.get("url"), "snippet": w.get("description")} for w in (data.get("data") or [])[:n]]


async def scrape(url: str, api_key: str) -> dict[str, Any]:
    """Read one page as markdown: {"title", "text"}, the shape of reach.jina_read.

    `url` must already have passed tools._check_url and tools._resolve. Firecrawl fetches from its own servers, so it sees the URL
    (the same caveat as Jina Reader).
    """
    async def _post() -> httpx.Response:
        async with httpx.AsyncClient(timeout=25) as c:
            return await c.post(f"{BASE}/scrape", json={"url": url, "formats": ["markdown"], "onlyMainContent": True}, headers=_auth(api_key))
    try:  # short: a stall must leave most of fetch_url's 90s deadline for the plain fallback
        r = await asyncio.wait_for(_post(), 30)
    except asyncio.TimeoutError:
        raise reach.ReachError("Firecrawl took too long") from None
    if r.status_code != 200:
        raise reach.ReachError(f"Firecrawl answered {r.status_code}")
    try:
        data = r.json()
    except ValueError:
        raise reach.ReachError("Firecrawl did not return JSON") from None
    if not isinstance(data, dict):
        raise reach.ReachError("Firecrawl did not return JSON")
    d = data.get("data") or {}
    if data.get("success") is False or not d.get("markdown"):
        raise reach.ReachError(str(data.get("error") or "Firecrawl returned no content"))
    meta = d.get("metadata") or {}
    code = meta.get("statusCode")
    if isinstance(code, int) and code >= 400:  # an error page is not content: let the plain fetch report it
        raise reach.ReachError(f"Firecrawl: the page answered {code}")
    return {"title": str(meta.get("title") or ""), "text": d["markdown"][:reach.MAX_BODY]}
