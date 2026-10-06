"""web_search backends (websearch.py + the Toolbox wiring). No network: every provider is monkeypatched."""
from __future__ import annotations

import asyncio
import os
import sys
from typing import Any

import httpx
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from personal_os import llm, tools, websearch  # noqa: E402
from personal_os.tools import Toolbox  # noqa: E402


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def test_norm_key_collapses_variants() -> None:
    k = websearch.norm_key("https://www.Example.com/a/b/?utm_source=x&id=3&fbclid=z#frag")
    assert k == websearch.norm_key("http://example.com/a/b?id=3") == "example.com/a/b?id=3"
    assert websearch.norm_key("https://example.com") == websearch.norm_key("http://www.example.com/")


def test_rrf_merge_prefers_agreement_and_keeps_longest_snippet() -> None:
    lists = {"exa": [{"title": "Solo", "url": "https://solo.com/", "snippet": "s"},
                     {"title": "Both", "url": "https://both.com/p", "snippet": "short"}],
             "searxng": [{"title": "Other", "url": "https://other.com/", "snippet": "o"},
                         {"title": "Both!", "url": "http://www.both.com/p/?utm_medium=x", "snippet": "a much longer snippet"}]}
    hits = websearch.rrf_merge(lists)
    assert hits[0].url == "https://both.com/p" and hits[0].title == "Both"
    assert hits[0].snippet == "a much longer snippet" and hits[0].engines == ["exa", "searxng"]
    assert len(hits) == 3 and hits[1].score == hits[2].score and hits[1].url == "https://solo.com/"  # stable


def test_apply_site() -> None:
    assert websearch.apply_site("wal mode", "sqlite.org") == "wal mode site:sqlite.org"
    assert websearch.apply_site("q", "https://www.sqlite.org/x") == "q site:www.sqlite.org"
    assert websearch.apply_site("q", "") == "q"
    for bad in ("a b", 'x.com"', "nodot", "x.com OR evil.com"):
        with pytest.raises(ValueError):
            websearch.apply_site("q", bad)


def test_parse_searxng_tolerates_missing_content() -> None:
    rows = websearch.parse_searxng({"results": [{"title": "A", "url": "https://a.com", "content": "c", "engines": ["x"]},
                                                {"title": "B", "url": "https://b.com"}, {"title": "no url"}]})
    assert rows == [{"title": "A", "url": "https://a.com", "snippet": "c"}, {"title": "B", "url": "https://b.com", "snippet": ""}]


class FakeResp:
    def __init__(self, status: int, data: Any = None):
        self.status_code, self._d = status, data

    def json(self) -> Any:
        return self._d

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("x", request=None, response=None)  # type: ignore[arg-type]


class Recorder:
    seen: list[tuple[str, dict[str, Any]]] = []
    resp: FakeResp = FakeResp(200, {})


class FakeClient:
    def __init__(self, *a: Any, **k: Any):
        pass

    async def __aenter__(self) -> "FakeClient":
        return self

    async def __aexit__(self, *a: Any) -> None:
        return None

    async def get(self, url: str, params: dict[str, Any] | None = None, headers: Any = None) -> FakeResp:
        Recorder.seen.append((url, dict(params or {})))
        return Recorder.resp

    async def post(self, url: str, json: Any = None) -> FakeResp:
        Recorder.seen.append((url, dict(json or {})))
        return Recorder.resp


@pytest.fixture()
def net(monkeypatch: pytest.MonkeyPatch) -> type[Recorder]:
    Recorder.seen = []
    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    return Recorder


def test_time_range_reaches_each_provider_natively(net: type[Recorder]) -> None:
    net.resp = FakeResp(200, {"web": {"results": []}, "results": []})
    run(websearch.brave({"braveApiKey": "k"}, "q", 5, "week"))
    assert net.seen[-1][1]["freshness"] == "pw"
    run(websearch.tavily({"tavilyApiKey": "k"}, "q", 5, "month"))
    assert net.seen[-1][1]["time_range"] == "month"
    run(websearch.searxng({"searxngUrl": "http://localhost:8080/"}, "q", 5, "day"))
    assert net.seen[-1][0] == "http://localhost:8080/search" and net.seen[-1][1]["time_range"] == "day"
    assert net.seen[-1][1]["format"] == "json"
    run(websearch.searxng({"searxngUrl": "http://localhost:8080"}, "q", 5, "week"))
    assert net.seen[-1][1]["time_range"] == "month"
    run(websearch.searxng({"searxngUrl": "http://localhost:8080"}, "q", 5, ""))
    assert "time_range" not in net.seen[-1][1]


def test_searxng_403_maps_to_helpful_error(net: type[Recorder]) -> None:
    net.resp = FakeResp(403)
    with pytest.raises(websearch.ProviderError, match="format=json disabled"):
        run(websearch.searxng({"searxngUrl": "http://localhost:8080"}, "q", 5, ""))
    with pytest.raises(websearch.ProviderError, match="http"):
        run(websearch.searxng({"searxngUrl": "file:///etc"}, "q", 5, ""))


def stub_providers(monkeypatch: pytest.MonkeyPatch, **impl: Any) -> list[str]:
    called: list[str] = []
    for name in ("brave", "tavily", "exa", "searxng", "ddg"):
        def mk(nm: str) -> Any:
            async def f(cfg: dict[str, Any], q: str, n: int, tr: str) -> list[dict[str, Any]]:
                called.append(nm)
                r = impl.get(nm, [])
                if isinstance(r, Exception):
                    raise r
                return r
            return f
        monkeypatch.setattr(websearch, name, mk(name))
    return called


R1 = [{"title": "A", "url": "https://a.com/", "snippet": "a"}]
R2 = [{"title": "B", "url": "https://b.com/", "snippet": "b"}, {"title": "A", "url": "https://www.a.com", "snippet": "aaa"}]


def test_keyed_brave_answers_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    called = stub_providers(monkeypatch, brave=R1, exa=R2)
    rows, meta = run(websearch.search({"braveApiKey": "k", "searxngUrl": "http://x"}, "q", 6))
    assert called == ["brave"] and rows == R1 and meta["engines_used"] == ["brave"]


def test_keyless_merges_searxng_and_exa(monkeypatch: pytest.MonkeyPatch) -> None:
    called = stub_providers(monkeypatch, exa=R1, searxng=R2)
    rows, meta = run(websearch.search({"searxngUrl": "http://x"}, "q", 6, time_range="week"))
    assert sorted(called) == ["exa", "searxng"] and websearch.norm_key(rows[0]["url"]) == "a.com/" and rows[0]["engines"] == ["searxng", "exa"]
    assert sorted(meta["engines_used"]) == ["exa", "searxng"]
    assert "time_range_ignored" not in meta and "month" in meta["time_range_note"]


def test_default_is_exa_then_ddg_with_no_extra_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    called = stub_providers(monkeypatch, exa=R1)
    rows, meta = run(websearch.search({}, "q", 6))
    assert called == ["exa"] and rows == R1 and "engines" not in rows[0]
    called = stub_providers(monkeypatch, exa=websearch.ProviderError("Exa answered 429"), ddg=R2)
    rows, meta = run(websearch.search({}, "q", 6))
    assert called == ["exa", "ddg"] and rows == R2 and meta["failed"] == {"exa": "Exa answered 429"}


def test_exa_failure_falls_through_to_searxng_and_all_failing_to_ddg(monkeypatch: pytest.MonkeyPatch) -> None:
    called = stub_providers(monkeypatch, exa=websearch.ProviderError("down"), searxng=R2, ddg=R1)
    rows, meta = run(websearch.search({"searxngUrl": "http://x"}, "q", 6))
    assert rows == R2 and "ddg" not in called and meta["engines_used"] == ["searxng"]
    called = stub_providers(monkeypatch, exa=websearch.ProviderError("down"), searxng=websearch.ProviderError("403"), ddg=R1)
    rows, meta = run(websearch.search({"searxngUrl": "http://x"}, "q", 6))
    assert rows == R1 and called[-1] == "ddg" and set(meta["failed"]) == {"exa", "searxng"}


def test_exa_only_with_time_range_flags_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    stub_providers(monkeypatch, exa=R1)
    _, meta = run(websearch.search({}, "q", 6, time_range="day"))
    assert meta["time_range_ignored"] is True
    with pytest.raises(ValueError):
        run(websearch.search({}, "q", 6, time_range="decade"))


def test_site_is_appended_to_the_query(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    async def exa(cfg: dict[str, Any], q: str, n: int, tr: str) -> list[dict[str, Any]]:
        seen.append(q)
        return R1
    monkeypatch.setattr(websearch, "exa", exa)
    run(websearch.search({}, "wal", 6, site="sqlite.org"))
    assert seen == ["wal site:sqlite.org"]


# ---- through the Toolbox ----
def test_tool_registers_every_result_for_a_tainted_run(monkeypatch: pytest.MonkeyPatch) -> None:
    stub_providers(monkeypatch, exa=R1, searxng=R2)
    tb = Toolbox(None, None, None, lambda: {"searxngUrl": "http://x"})  # type: ignore[arg-type]
    ctx: dict[str, Any] = {"tainted": True}
    out = run(tb.specs["web_search"].fn(ctx, query="q", time_range="month"))
    assert [websearch.norm_key(r["url"]) for r in out["results"]] == ["a.com/", "b.com/"] and "engines_used" in out
    for u in ("https://www.a.com", "https://b.com/"):
        tools._check_url(u, ctx, {})  # a tainted run may now fetch them
    with pytest.raises(tools.UrlBlocked):
        tools._check_url("https://c.com/", ctx, {})
    bad = run(tb.specs["web_search"].fn(ctx, query="q", site="a b"))
    assert "error" in bad
    bad = run(tb.specs["web_search"].fn(ctx, query="q", time_range="decade"))
    assert "error" in bad


def test_setting_default_present() -> None:
    assert llm.DEFAULT_SETTINGS["searxngUrl"] == ""
