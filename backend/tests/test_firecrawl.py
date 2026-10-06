"""Firecrawl as the primary web_search / fetch_url provider (firecrawl.py + its wiring). No network: every call is faked."""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

import httpx
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("GRAIN_SECRETS_BACKEND", "file")
os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="firecrawl-"))

from fastapi.testclient import TestClient  # noqa: E402

from personal_os import firecrawl, reach, tools, webread, websearch  # noqa: E402
from personal_os.app import AUTH_TOKEN, app  # noqa: E402
from personal_os.db import Database  # noqa: E402
from personal_os.tools import Toolbox  # noqa: E402


def run(coro: Any) -> Any:
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def no_env_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)


# ---- key ----
def test_key_prefers_settings_then_env(monkeypatch: pytest.MonkeyPatch) -> None:
    assert firecrawl.key({}) == ""
    monkeypatch.setenv("FIRECRAWL_API_KEY", " env-key ")
    assert firecrawl.key({}) == "env-key" and firecrawl.key({"firecrawlApiKey": "  "}) == "env-key"
    assert firecrawl.key({"firecrawlApiKey": " set-key "}) == "set-key"


# ---- the API client ----
class FakeResp:
    def __init__(self, status: int, data: Any = None):
        self.status_code, self._d = status, data

    def json(self) -> Any:
        return self._d

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("x", request=None, response=None)  # type: ignore[arg-type]


class Net:
    seen: list[tuple[str, dict[str, Any], dict[str, str]]] = []
    resp: FakeResp = FakeResp(200, {})


class FakeClient:
    def __init__(self, *a: Any, **k: Any):
        pass

    async def __aenter__(self) -> "FakeClient":
        return self

    async def __aexit__(self, *a: Any) -> None:
        return None

    async def post(self, url: str, json: Any = None, headers: Any = None) -> FakeResp:
        Net.seen.append((url, dict(json or {}), dict(headers or {})))
        return Net.resp


@pytest.fixture()
def net(monkeypatch: pytest.MonkeyPatch) -> type[Net]:
    Net.seen = []
    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    return Net


def test_search_maps_rows_and_sends_tbs_and_auth(net: type[Net]) -> None:
    net.resp = FakeResp(200, {"success": True, "data": [{"title": "A", "url": "https://a.com", "description": "da"},
                                                        {"title": "B", "url": "https://b.com", "description": "db"}]})
    rows = run(firecrawl.search({"firecrawlApiKey": "fc-k"}, "q", 1, "week"))
    assert rows == [{"title": "A", "url": "https://a.com", "snippet": "da"}]
    url, body, headers = net.seen[-1]
    assert url == f"{firecrawl.BASE}/search" and body == {"query": "q", "limit": 1, "tbs": "qdr:w"}
    assert headers["Authorization"] == "Bearer fc-k"
    run(firecrawl.search({"firecrawlApiKey": "fc-k"}, "q", 5, ""))
    assert "tbs" not in net.seen[-1][1]


def test_search_failure_body_raises(net: type[Net]) -> None:
    net.resp = FakeResp(200, {"success": False, "error": "out of credits"})
    with pytest.raises(reach.ReachError, match="out of credits"):
        run(firecrawl.search({"firecrawlApiKey": "k"}, "q", 3, ""))
    net.resp = FakeResp(429)
    with pytest.raises(httpx.HTTPStatusError):  # websearch._retrying owns the retry
        run(firecrawl.search({"firecrawlApiKey": "k"}, "q", 3, ""))


def test_scrape_maps_title_and_markdown(net: type[Net]) -> None:
    net.resp = FakeResp(200, {"success": True, "data": {"markdown": "# Hi\n\nbody", "metadata": {"title": "Hi page"}}})
    out = run(firecrawl.scrape("https://a.com/x", "fc-k"))
    assert out == {"title": "Hi page", "text": "# Hi\n\nbody"}
    url, body, headers = net.seen[-1]
    assert url == f"{firecrawl.BASE}/scrape" and body["url"] == "https://a.com/x" and body["formats"] == ["markdown"]
    assert headers["Authorization"] == "Bearer fc-k"


def test_scrape_errors(net: type[Net]) -> None:
    net.resp = FakeResp(402, {})
    with pytest.raises(reach.ReachError, match="402"):
        run(firecrawl.scrape("https://a.com", "k"))
    net.resp = FakeResp(200, {"success": False, "error": "blocked"})
    with pytest.raises(reach.ReachError, match="blocked"):
        run(firecrawl.scrape("https://a.com", "k"))
    net.resp = FakeResp(200, {"success": True, "data": {"metadata": {}}})  # no markdown
    with pytest.raises(reach.ReachError):
        run(firecrawl.scrape("https://a.com", "k"))


def test_scrape_of_an_error_page_raises(net: type[Net]) -> None:
    net.resp = FakeResp(200, {"success": True, "data": {"markdown": "Not found", "metadata": {"title": "404", "statusCode": 404}}})
    with pytest.raises(reach.ReachError, match="404"):
        run(firecrawl.scrape("https://a.com", "k"))


def test_non_json_body_is_a_reach_error(net: type[Net]) -> None:
    class Bad(FakeResp):
        def json(self) -> Any:
            raise ValueError("not json")
    net.resp = Bad(200)
    with pytest.raises(reach.ReachError, match="JSON"):
        run(firecrawl.search({"firecrawlApiKey": "k"}, "q", 3, ""))
    with pytest.raises(reach.ReachError, match="JSON"):
        run(firecrawl.scrape("https://a.com", "k"))
    net.resp = FakeResp(200, ["not", "a", "dict"])
    with pytest.raises(reach.ReachError, match="JSON"):
        run(firecrawl.search({"firecrawlApiKey": "k"}, "q", 3, ""))


# ---- websearch order ----
def test_firecrawl_answers_first_and_brave_is_not_called(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    async def fc(cfg: Any, q: str, n: int, tr: str) -> list[dict[str, Any]]:
        calls.append("firecrawl")
        return [{"title": "F", "url": "https://f.com", "snippet": ""}]

    async def brave(cfg: Any, q: str, n: int, tr: str) -> list[dict[str, Any]]:
        calls.append("brave")
        return []
    monkeypatch.setattr(firecrawl, "search", fc)
    monkeypatch.setattr(websearch, "brave", brave)
    rows, meta = run(websearch.search({"firecrawlApiKey": "k", "braveApiKey": "b"}, "q", 5))
    assert calls == ["firecrawl"] and rows[0]["title"] == "F" and meta["engines_used"] == ["firecrawl"]


def test_firecrawl_failure_falls_back_to_brave(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fc(cfg: Any, q: str, n: int, tr: str) -> list[dict[str, Any]]:
        raise reach.ReachError("out of credits\nmore")

    async def brave(cfg: Any, q: str, n: int, tr: str) -> list[dict[str, Any]]:
        return [{"title": "B", "url": "https://b.com", "snippet": ""}]
    monkeypatch.setattr(firecrawl, "search", fc)
    monkeypatch.setattr(websearch, "brave", brave)
    rows, meta = run(websearch.search({"firecrawlApiKey": "k", "braveApiKey": "b"}, "q", 5))
    assert rows[0]["title"] == "B" and meta["engines_used"] == ["brave"]
    assert meta["failed"] == {"firecrawl": "out of credits"}


def test_no_key_never_touches_firecrawl(monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(*a: Any) -> list[dict[str, Any]]:
        raise AssertionError("firecrawl must not run without a key")

    async def exa(cfg: Any, q: str, n: int, tr: str) -> list[dict[str, Any]]:
        return [{"title": "E", "url": "https://e.com", "snippet": ""}]
    monkeypatch.setattr(firecrawl, "search", boom)
    monkeypatch.setattr(websearch, "exa", exa)
    rows, meta = run(websearch.search({}, "q", 5))
    assert rows[0]["title"] == "E" and meta["engines_used"] == ["exa"]


# ---- fetch_url ----
class Plain:
    calls: list[str] = []


class PlainResp:
    def __init__(self, url: str):
        self.url, self.status_code = url, 200
        self.content = b"plain page text, no heading"
        self.headers = {"content-type": "text/plain"}


@pytest.fixture()
def fetch(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> Any:
    Plain.calls = []

    async def open_pinned(client: Any, method: str, url: str, host: str, **_: Any) -> PlainResp:
        Plain.calls.append(url)
        return PlainResp(url)
    monkeypatch.setattr(tools, "_open_pinned", open_pinned)

    async def resolve(host: str) -> list[str]:
        return ["93.184.216.34"]
    monkeypatch.setattr(tools, "_resolve", resolve)

    def make(settings: dict[str, Any], cached: bool = False) -> Any:
        tb = Toolbox(None, None, None, lambda: settings)  # type: ignore[arg-type]
        if cached:
            tb.web_cache = webread.WebCache(Database(tmp_path))
        return lambda ctx=None, **kw: run(tb.specs["fetch_url"].fn(ctx if ctx is not None else {}, **kw))
    return make


def fake_scrape(monkeypatch: pytest.MonkeyPatch, seen: list[str]) -> None:
    async def scrape(url: str, api_key: str) -> dict[str, Any]:
        seen.append(url)
        return {"title": "T", "text": "# T\n\nbody " * 100}
    monkeypatch.setattr(firecrawl, "scrape", scrape)


def test_fetch_url_reads_through_firecrawl_and_caches(monkeypatch: pytest.MonkeyPatch, fetch: Any) -> None:
    seen: list[str] = []
    fake_scrape(monkeypatch, seen)
    f = fetch({"firecrawlApiKey": "k"}, cached=True)
    out = f(url="https://example.com/post")
    assert out["via"] == "firecrawl" and out["title"] == "T" and out["cached"] is False and out["kind"] == "text"
    assert out["status"] == 200 and out["redirects"] == 0 and out["text"].startswith("# T")
    again = f(url="https://example.com/post")  # served from the cache: no second scrape, title from the heading
    assert again["cached"] is True and again["title"] == "T" and "via" not in again
    assert seen == ["https://example.com/post"] and Plain.calls == []


def test_fetch_url_falls_back_when_firecrawl_fails(monkeypatch: pytest.MonkeyPatch, fetch: Any) -> None:
    async def scrape(url: str, api_key: str) -> dict[str, Any]:
        raise reach.ReachError("Firecrawl answered 402")
    monkeypatch.setattr(firecrawl, "scrape", scrape)
    out = fetch({"firecrawlApiKey": "k"})(url="https://example.com/post")
    assert Plain.calls == ["https://example.com/post"]
    assert "via" not in out and out["text"].startswith("plain page") and "error" not in out


def test_fetch_url_without_a_key_is_unchanged(monkeypatch: pytest.MonkeyPatch, fetch: Any) -> None:
    seen: list[str] = []
    fake_scrape(monkeypatch, seen)
    out = fetch({})(url="https://example.com/post")
    assert seen == [] and Plain.calls == ["https://example.com/post"] and "via" not in out


def test_blocked_url_never_reaches_firecrawl(monkeypatch: pytest.MonkeyPatch, fetch: Any) -> None:
    seen: list[str] = []
    fake_scrape(monkeypatch, seen)
    out = fetch({"firecrawlApiKey": "k"})(url="http://127.0.0.1/")
    assert "refused" in out["error"] and seen == [] and Plain.calls == []


def test_private_resolution_never_reaches_firecrawl(monkeypatch: pytest.MonkeyPatch, fetch: Any) -> None:
    seen: list[str] = []
    fake_scrape(monkeypatch, seen)

    async def private(host: str) -> list[str]:
        raise tools.UrlBlocked("x resolves to 10.0.0.1, which is private")
    monkeypatch.setattr(tools, "_resolve", private)
    out = fetch({"firecrawlApiKey": "k"})(url="https://internal.example.com/")
    assert "refused" in out["error"] and seen == [] and Plain.calls == []


def test_links_request_skips_firecrawl(monkeypatch: pytest.MonkeyPatch, fetch: Any) -> None:
    seen: list[str] = []
    fake_scrape(monkeypatch, seen)
    out = fetch({"firecrawlApiKey": "k"})(url="https://example.com/post", links=True)
    assert seen == [] and Plain.calls == ["https://example.com/post"] and "via" not in out


# ---- settings route ----
client = TestClient(app, headers={"X-Personal-OS-Token": AUTH_TOKEN})


@pytest.fixture()
def clear_key() -> Any:
    yield
    client.put("/settings", json={"firecrawlApiKey": None})


def test_settings_route_keeps_the_key_secret(clear_key: Any) -> None:
    from personal_os.app import settings
    r = client.put("/settings", json={"firecrawlApiKey": "fc-secret-123"})
    assert settings()["firecrawlApiKey"] == "fc-secret-123"
    body = client.get("/settings")
    assert "fc-secret-123" not in body.text and "fc-secret-123" not in r.text
    j = body.json()
    assert j["firecrawlApiKeySet"] is True and j["firecrawlApiKey"] == "" and isinstance(j["firecrawlEnvKey"], bool)
    client.put("/settings", json={"firecrawlApiKey": "", "theme": "light"})  # blank = unchanged
    assert settings()["firecrawlApiKey"] == "fc-secret-123"
    r = client.put("/settings", json={"firecrawlApiKey": None})  # null clears
    assert r.json()["firecrawlApiKeySet"] is False and settings()["firecrawlApiKey"] == ""
