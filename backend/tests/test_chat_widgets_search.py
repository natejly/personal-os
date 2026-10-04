"""Chat-placed widgets, web_search domain lists, the fetch_url header, and the HTML widget repair pass. Offline."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="chatwidgets-"))
os.environ.setdefault("PERSONAL_OS_AUTH_TOKEN", "test-token")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from personal_os import dashboards as dash, llm, tools, websearch, widget_spec, widget_tools  # noqa: E402
from personal_os.canvas import Canvases  # noqa: E402
from personal_os.dashboards import Dashboards  # noqa: E402
from personal_os.db import Database  # noqa: E402
from personal_os.tools import Toolbox  # noqa: E402

SECRET = "s3cr3t-KEY-9f8e7d"
DATA = {"items": [{"name": "a", "value": 5}, {"name": "b", "value": 9}]}
SPEC = '{"kind":"chart","path":"$.items","select":{"x":"name","y":["value"]},"chart":{"type":"bar"}}'


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def test_widget_create_and_place_never_leak_the_secret(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    db = Database(str(tmp_path / "w.db"))
    store, canvases = Dashboards(db), Canvases(db)
    src = store.create_source("Sales API", "http", {"url": "https://api.example/sales"}, secret=SECRET)
    llm_calls: list[int] = []

    async def fake_complete(*a: Any, **k: Any) -> str:
        llm_calls.append(1)
        return SPEC

    async def fetch(sid: str) -> Any:
        return DATA
    monkeypatch.setattr(llm, "complete", fake_complete)
    box = SimpleNamespace(specs={}, settings=lambda: {"defaultModel": "m"})
    widget_tools.register(box, store, canvases, fetch)
    assert box.specs["widget_create"].danger == "writes" and box.specs["widget_place"].danger == "writes"

    out = run(box.specs["widget_create"].fn({}, kind="chart", source=src["id"], prompt="sales by name"))
    w = store.widget(out["widget_id"])
    assert w["kind"] == "chart" and w["spec"]["select"]["x"] == "name" and not w["data_error"]  # generate_spec accepted it
    assert len(llm_calls) == 1
    placed = run(box.specs["widget_place"].fn({}, widget_id=w["id"]))
    win = canvases.window(placed["window_id"])
    assert win["kind"] == "dashboard-widget" and win["ref_id"] == w["id"]
    first = canvases.list()[0]
    canvases.update(first["id"], {"locked": True})
    other = canvases.create("Open")
    assert run(box.specs["widget_place"].fn({}, widget_id=w["id"], space=first["name"]))["field"] == "space"  # a locked space refuses
    assert run(box.specs["widget_place"].fn({}, widget_id=w["id"]))["space"] == other["name"]  # the default skips it

    n = len(llm_calls)
    fresh = run(widget_spec.run_widget(store, store.widget(w["id"]), {}, "m", fetch))  # a plain refresh is a re-bind
    assert len(llm_calls) == n and fresh["data"]["rows"]
    blob = json.dumps([out, store.widget(w["id"]), placed, win, fresh, f"/widgets/{w['id']}/render"], default=str)
    assert SECRET not in blob

    internal = run(box.specs["widget_create"].fn({}, kind="chart", source="todos", prompt="count"))
    iw = store.widget(internal["widget_id"])
    assert (store.source(iw["source_ids"][0]) or {})["kind"] == "internal" and iw["spec"]["kind"] == "chart"
    bad = run(box.specs["widget_create"].fn({}, kind="chart", source="nope", prompt="x"))
    assert bad["field"] == "source"


ROWS = [{"title": "Docs", "url": "https://docs.sqlite.org/wal", "snippet": ""},
        {"title": "Blog", "url": "https://blog.example.com/p", "snippet": ""},
        {"title": "Main", "url": "https://sqlite.org/", "snippet": ""}]


def _stub(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    called: list[str] = []

    async def exa(cfg: Any, q: str, n: int, tr: str) -> list[dict[str, Any]]:
        called.append("exa")
        return list(ROWS)
    monkeypatch.setattr(websearch, "exa", exa)
    return called


def test_domain_lists(monkeypatch: pytest.MonkeyPatch) -> None:
    called = _stub(monkeypatch)
    rows, _ = run(websearch.search({}, "q", 6, allowed_domains=["sqlite.org"]))
    assert [r["title"] for r in rows] == ["Docs", "Main"]
    rows, _ = run(websearch.search({}, "q", 6, blocked_domains=["https://www.sqlite.org/x"]))
    assert [r["title"] for r in rows] == ["Blog"]
    called.clear()
    with pytest.raises(ValueError, match="not both"):
        run(websearch.search({}, "q", 6, allowed_domains=["a.com"], blocked_domains=["b.com"]))
    assert called == []  # no engine was touched
    box = Toolbox(None, None, None, lambda: {})  # type: ignore[arg-type]
    out = run(box.specs["web_search"].fn({}, query="q", allowed_domains=["a.com"], blocked_domains=["b.com"]))
    assert out["field"] == "domains" and called == []


PAGE = ("<html><head><title>  Fixture &amp; Page </title></head><body><article><h1>Heading</h1>"
        + "".join(f"<p>Paragraph {i} about write ahead logging and checkpoints in detail.</p>" for i in range(30))
        + '<p>See <a href="https://evil.example/x">this link</a></p></article></body></html>')


def test_fetch_url_header(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_resolve(host: str) -> list[str]:
        return ["93.184.216.34"]
    monkeypatch.setattr(tools, "_resolve", fake_resolve)
    real = httpx.AsyncClient

    def client(*a: Any, **k: Any) -> httpx.AsyncClient:
        k.pop("transport", None)
        return real(*a, transport=httpx.MockTransport(lambda r: httpx.Response(200, headers={"content-type": "text/html"}, text=PAGE)), **k)
    monkeypatch.setattr(tools.httpx, "AsyncClient", client)
    box = Toolbox(None, None, None, lambda: {"readerFallback": False})  # type: ignore[arg-type]
    ctx: dict[str, Any] = {}
    out = run(box.specs["fetch_url"].fn(ctx, url="https://page.example/a"))
    assert out["url"] == "https://page.example/a" and out["title"] == "Fixture & Page"
    assert 0 < len(out["excerpt"]) <= 300 and "Paragraph 0" in out["excerpt"]
    assert "evil.example" not in json.dumps(ctx.get("allowed_urls") or [], default=list)  # in-page links are not allowlisted


def _html_run(monkeypatch: pytest.MonkeyPatch, replies: list[str], secrets: list[str]) -> tuple[Any, list[int]]:
    calls: list[int] = []

    async def fake(*a: Any, **k: Any) -> str:
        calls.append(1)
        return replies.pop(0)
    monkeypatch.setattr(llm, "complete", fake)
    try:
        return run(dash.generate_widget_code({}, "m", "p", [], "http://x", 2, 2, {}, secrets)), calls
    except dash.WidgetCodeRejected as e:
        return e, calls


def test_html_repair(monkeypatch: pytest.MonkeyPatch) -> None:
    out, calls = _html_run(monkeypatch, ['<html><script src="https://cdn.example/x.js"></script></html>', "<html><body>ok</body></html>"], [SECRET])
    assert out == "<html><body>ok</body></html>" and len(calls) == 2
    out, calls = _html_run(monkeypatch, ["<html><body>clean</body></html>"], [SECRET])
    assert len(calls) == 1
    out, calls = _html_run(monkeypatch, ['<html><script src="//cdn.example/x.js"></script></html>'] * 3, [])
    assert isinstance(out, dash.WidgetCodeRejected) and len(calls) == 2
    out, calls = _html_run(monkeypatch, [f"<html>{SECRET}</html>", f"<html><i>{SECRET}</i></html>"], [SECRET])
    assert isinstance(out, dash.WidgetCodeRejected) and len(calls) == 2


def test_html_widget_storage(monkeypatch: pytest.MonkeyPatch) -> None:
    """The repair is what gets saved; a rejected one is stored as an error with no code, so nothing renders."""
    from personal_os import app as appmod
    d = appmod.dashboards.create("repair-store")
    src = appmod.dashboards.create_source("Secret API", "http", {"url": "https://api.example/x"}, secret=SECRET)
    replies = ['<html><script src="https://cdn.example/x.js"></script></html>', "<html><body>repaired</body></html>"]

    async def fake(*a: Any, **k: Any) -> str:
        return replies.pop(0)

    async def no_samples(ids: Any) -> dict[str, Any]:
        return {}
    monkeypatch.setattr(llm, "complete", fake)
    monkeypatch.setattr(appmod, "_samples", no_samples)
    req = SimpleNamespace(base_url="http://x/")
    w = appmod.dashboards.create_widget(d["id"], "t", "html", "p", [src["id"]])
    got = run(appmod._run_widget(w, req))
    assert got["code"] == "<html><body>repaired</body></html>" and not got["data_error"]
    assert appmod.dashboards.widget(w["id"])["code"] == got["code"]

    replies[:] = [f"<html>{SECRET}</html>", '<html><link href="https://cdn.example/s.css"></html>']
    got = run(appmod._run_widget(appmod.dashboards.widget(w["id"]), req, regenerate_code=True))
    row = appmod.dashboards.widget(w["id"])
    assert row["code"] == "" and row["data_error"] and row["output"] == row["data_error"] == got["data_error"]
    assert "rejected" in row["data_error"] and SECRET not in json.dumps(row, default=str)
