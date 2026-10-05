"""fetch_url pipeline (webread.py + its Toolbox wiring). No network, no model: httpx and the resolver are stubbed."""
from __future__ import annotations

import asyncio
import os
import sys
from typing import Any

import httpx
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from personal_os import llm, tools, webread  # noqa: E402
from personal_os.db import Database  # noqa: E402
from personal_os.tools import Toolbox  # noqa: E402


def run(coro: Any) -> Any:
    return asyncio.run(coro)


# ---- pure functions ----
def test_classify() -> None:
    assert webread.classify("text/html; charset=utf-8", "https://x.com/", b"") == "html"
    assert webread.classify("application/pdf", "https://x.com/a", b"") == "pdf"
    assert webread.classify("", "https://x.com/a", b"%PDF-1.7 ...") == "pdf"
    assert webread.classify("application/octet-stream", "https://x.com/a.pdf", b"") == "pdf"
    assert webread.classify("application/json", "https://x.com/a", b"{}") == "json"
    assert webread.classify("application/vnd.api+json", "https://x.com/a", b"{}") == "json"
    assert webread.classify("image/png", "https://x.com/a.png", b"\x89PNG") == "binary"
    assert webread.classify("text/plain", "https://x.com/a.txt", b"hi") == "text"


def test_render_json_pdf_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    assert webread.render("json", b"", '{"a":[1,2]}', "https://x").text.startswith('{\n "a"')
    assert webread.render("json", b"", "not json", "https://x").text == "not json"
    monkeypatch.setattr(webread, "extract_text", lambda name, data, mime="": "page one text")
    assert webread.render("pdf", b"%PDF-", "", "https://x").text == "page one text"

    def boom(*a: Any, **k: Any) -> str:
        raise ValueError("bad")
    monkeypatch.setattr(webread, "extract_text", boom)
    with pytest.raises(webread.Unreadable, match="could not read PDF"):
        webread.render("pdf", b"junk", "", "https://x")
    with pytest.raises(webread.Unreadable, match="binary"):
        webread.render("binary", b"\x00", "", "https://x")


def test_numberize_links() -> None:
    md = ("See [docs](/docs/a) and [again](https://x.com/docs/a#top) plus [other](https://y.org/p), "
          "[mail](mailto:a@b.c), [js](javascript:alert(1)), ![img](/i.png).")
    out, links = webread.numberize_links(md, "https://x.com/start")
    assert [(l["n"], l["url"]) for l in links] == [(1, "https://x.com/docs/a"), (2, "https://y.org/p")]
    assert "[docs][1]" in out and "[again][1]" in out and "[other][2]" in out
    assert "mailto" not in out and "javascript" not in out and "mail," in out and "![img](/i.png)" in out


def test_bm25_focus_keeps_lede_and_relevant_blocks_in_order() -> None:
    blocks = ["# Title of the page and its lede paragraph here"]
    blocks += [f"Filler paragraph number {i} about weather and unrelated gardening topics." for i in range(30)]
    blocks[12] = "Our enterprise pricing starts at ten dollars per seat, with pricing tiers for teams."
    blocks[25] = "Pricing for startups is discounted; ask about pricing exceptions."
    text = "\n\n".join(blocks)
    out = webread.bm25_focus(text, "pricing", 600)
    assert out.startswith("# Title") and "enterprise pricing" in out and "startups" in out
    assert out.index("enterprise pricing") < out.index("startups") and "[...]" in out
    assert len(out) <= 700
    assert webread.bm25_focus(text, "the of", 600) == text  # all stopwords: no-op
    assert webread.bm25_focus(text, "", 600) == text


def test_page_window() -> None:
    assert webread.page_window("abcdefghij", 0, 4) == ("abcd", 10, 4)
    assert webread.page_window("abcdefghij", 8, 4) == ("ij", 10, None)


def test_webcache_ttl_fresh_and_cap(tmp_path: Any) -> None:
    now = [1000.0]
    cache = webread.WebCache(Database(tmp_path), clock=lambda: now[0])
    cache.put("a.com/", 200, "text/html", b"hello", "https://a.com/")
    assert cache.get("a.com/", 3600)["body"] == b"hello"
    assert cache.get("a.com/", 0) is None  # ttl 0 = disabled
    now[0] += 3601
    assert cache.get("a.com/", 3600) is None
    cache.put("big.com/", 200, "text/html", b"x" * (webread.MAX_CACHE_BODY + 1), "https://big.com/")
    assert cache.get("big.com/", 3600) is None
    for i in range(webread.MAX_CACHE_ROWS + 5):
        now[0] += 1
        cache.put(f"s{i}.com/", 200, "text/plain", b"x", f"https://s{i}.com/")
    with cache.db.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM web_cache").fetchone()[0] == webread.MAX_CACHE_ROWS
    assert cache.get("s0.com/", 10**9) is None and cache.get(f"s{webread.MAX_CACHE_ROWS + 4}.com/", 10**9) is not None


# ---- fetch_url through the Toolbox ----
class FakeResp:
    def __init__(self, url: str, body: bytes, ctype: str, status: int = 200):
        self.url, self.content, self.status_code = url, body, status
        self.headers = {"content-type": ctype}
        self.text = body.decode("utf-8", errors="replace")


class Net:
    calls: list[str] = []
    routes: dict[str, FakeResp] = {}


class FakeClient:
    def __init__(self, *a: Any, **k: Any):
        pass

    async def __aenter__(self) -> "FakeClient":
        return self

    async def __aexit__(self, *a: Any) -> None:
        return None

    async def get(self, url: str) -> FakeResp:
        Net.calls.append(url)
        return Net.routes[url]


@pytest.fixture()
def box(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> Toolbox:
    Net.calls, Net.routes = [], {}
    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)

    async def ok(host: str) -> list[str]:
        return ["93.184.216.34"]
    monkeypatch.setattr(tools, "_resolve", ok)

    async def by_name(client: Any, method: str, url: str, host: str, **_: Any) -> Any:
        return await client.get(url)  # address pinning is test_ssrf's business; these tests are about the cache
    monkeypatch.setattr(tools, "_open_pinned", by_name)
    s = {**llm.DEFAULT_SETTINGS, "readerFallback": False}
    tb = Toolbox(None, None, None, lambda: s)  # type: ignore[arg-type]
    tb.web_cache = webread.WebCache(Database(tmp_path))
    return tb


def fetch(tb: Toolbox, ctx: dict[str, Any] | None = None, **kw: Any) -> Any:
    return run(tb.specs["fetch_url"].fn(ctx if ctx is not None else {}, **kw))


PAGE = ("<html><body><article><h1>Widgets</h1><p>" + "Widgets are small useful things we make every day. " * 12 + "</p>"
        '<p>Read the <a href="/guide">guide</a> or <a href="https://other.org/x">partner</a> to learn how widgets are made. '
        + "Widgets are small useful things we make every day. " * 6 + "</p></article></body></html>")


def test_a_calendar_note_and_a_doc_are_stripped() -> None:
    pat = "github_pat_11AAAAAAA0AAAAAAAAAAAAAAAAAAAA"

    class G:
        def calendar_get(self, event_id: str, calendar_id: str = "primary") -> dict[str, str]:
            return {"id": event_id, "summary": "sync", "description": f"bring {pat}",
                    "location": "https://example.com/a?token=secretvalue"}

        def docs_get(self, document_id: str) -> dict[str, str]:
            return {"id": document_id, "title": "Notes", "text": f"key {pat}"}

        def drive_read(self, file_id: str, max_chars: int = 8000) -> dict[str, str]:
            return {"id": file_id, "name": "a.txt", "content": pat}

        def sheets_read(self, spreadsheet_id: str, cell_range: str | None = None) -> dict[str, Any]:
            return {"id": spreadsheet_id, "title": "Budget", "values": [["ok", pat]]}

    tb = Toolbox(None, None, None, lambda: {}, google=G())  # type: ignore[arg-type]
    event = run(tb.specs["calendar_get"].fn({}, event_id="e1"))
    assert pat not in event["description"] and "secretvalue" not in event["location"]
    assert event["summary"] == "sync"
    doc = run(tb.specs["google_docs_read"].fn({}, document_id="d1"))
    assert pat not in doc["text"] and doc["title"] == "Notes"
    drive = run(tb.specs["google_drive_read"].fn({}, file_id="f1"))
    assert pat not in drive["content"] and drive["name"] == "a.txt"
    sheet = run(tb.specs["google_sheets_read"].fn({}, spreadsheet_id="s1"))
    assert pat not in sheet["values"][0][1] and sheet["values"][0][0] == "ok"


def test_an_email_body_is_stripped() -> None:
    pat = "github_pat_11AAAAAAA0AAAAAAAAAAAAAAAAAAAA"

    class G:
        def gmail_get(self, message_id: str) -> dict[str, str]:
            return {"id": message_id, "from": "a@b.com", "subject": "hi", "body": f"use {pat}"}

        def gmail_search(self, query: str, n: int) -> list[dict[str, str]]:
            return [{"id": "1", "from": "a@b.com", "subject": "hi", "snippet": pat}]

    tb = Toolbox(None, None, None, lambda: {}, google=G())  # type: ignore[arg-type]
    read = run(tb.specs["gmail_read"].fn({}, message_id="m1"))
    assert pat not in read["body"] and read["from"] == "a@b.com" and "[github-pat]" in read["body"]
    found = run(tb.specs["gmail_search"].fn({}, query="in:inbox", max_results=5))
    assert pat not in found["messages"][0]["snippet"] and found["messages"][0]["from"] == "a@b.com"


def test_github_file_text_is_stripped(box: Toolbox, monkeypatch: pytest.MonkeyPatch) -> None:
    from personal_os import reach
    pat = "github_pat_11AAAAAAA0AAAAAAAAAAAAAAAAAAAA"

    async def fake(*_a: Any, **_k: Any) -> dict[str, Any]:
        return {"repo": "o/n", "path": "a.py", "text": f"token {pat}", "comments": [{"author": "a", "body": pat}]}

    monkeypatch.setattr(reach, "github_read", fake)
    out = run(box.specs["github_read"].fn({}, repo="o/n", path="a.py"))
    assert pat not in out["text"] and pat not in out["comments"][0]["body"]
    assert out["path"] == "a.py" and out["comments"][0]["author"] == "a"
    assert "[github-pat]" in out["text"]


def test_a_token_in_the_page_is_stripped(box: Toolbox) -> None:
    pat = "github_pat_11AAAAAAA0AAAAAAAAAAAAAAAAAAAA"
    html = ("<html><body><article><p>The key is " + pat + ". "
             + "Widgets are small useful things we make every day. " * 20 + "</p></article></body></html>")
    Net.routes["https://a.com/secret"] = FakeResp("https://a.com/secret", html.encode(), "text/html")
    out = fetch(box, url="https://a.com/secret")
    assert pat not in out["text"] and "[github-pat]" in out["text"]
    assert pat not in out["excerpt"] and "[github-pat]" in out["excerpt"]
    assert "Widgets are small" in out["text"]


def test_a_token_in_a_fetched_address_is_stripped(box: Toolbox) -> None:
    pat = "github_pat_11AAAAAAA0AAAAAAAAAAAAAAAAAAAA"
    url = f"https://a.com/{pat}"
    html = ("<html><body><article><p>Hello. "
            + "Widgets are small useful things we make every day. " * 20 + "</p></article></body></html>")
    Net.routes[url] = FakeResp(url, html.encode(), f"text/html; note={pat}")
    out = fetch(box, url=url)
    assert pat not in str(out)
    assert "[github-pat]" in out["url"] and "[github-pat]" in out["content_type"]
    assert "Hello" in out["text"]
    err = fetch(box, {"tainted": True, "allowed_urls": set()}, url=url)
    assert pat not in str(err) and "restricted" in err["error"] and "[github-pat]" in err["error"]


def test_second_fetch_is_cached_and_fresh_bypasses(box: Toolbox) -> None:
    Net.routes["https://a.com/p"] = FakeResp("https://a.com/p", PAGE.encode(), "text/html")
    first = fetch(box, url="https://a.com/p")
    assert first["kind"] == "html" and first["cached"] is False and len(Net.calls) == 1
    again = fetch(box, url="https://a.com/p")
    assert again["cached"] is True and len(Net.calls) == 1 and again["text"] == first["text"]
    assert fetch(box, url="https://a.com/p", fresh=True)["cached"] is False and len(Net.calls) == 2
    box.settings()["fetchCacheSeconds"] = 0
    assert fetch(box, url="https://a.com/p")["cached"] is False and len(Net.calls) == 3
    box.settings()["fetchCacheSeconds"] = 3600


def test_links_numbered_with_references(box: Toolbox) -> None:
    Net.routes["https://a.com/p"] = FakeResp("https://a.com/p", PAGE.encode(), "text/html")
    out = fetch(box, url="https://a.com/p", links=True)
    urls = [l["url"] for l in out["links"]]
    assert "https://a.com/guide" in urls and "https://other.org/x" in urls
    assert "## References" in out["text"] and "[1]: https://a.com/guide" in out["text"]
    plain = fetch(box, url="https://a.com/p", fresh=True)
    assert "links" not in plain and "References" not in plain["text"]


def test_pdf_returns_text_not_mojibake(box: Toolbox, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(webread, "extract_text", lambda name, data, mime="": "Quarterly report: revenue grew")
    Net.routes["https://a.com/r.pdf"] = FakeResp("https://a.com/r.pdf", b"%PDF-1.4\x00\xff\xfe binary", "application/pdf")
    out = fetch(box, url="https://a.com/r.pdf")
    assert out["kind"] == "pdf" and out["text"] == "Quarterly report: revenue grew"


def test_binary_is_refused_and_json_is_pretty(box: Toolbox) -> None:
    Net.routes["https://a.com/i.png"] = FakeResp("https://a.com/i.png", b"\x89PNG\x00", "image/png")
    assert "binary" in fetch(box, url="https://a.com/i.png")["error"]
    Net.routes["https://a.com/d.json"] = FakeResp("https://a.com/d.json", b'{"k":[1]}', "application/json")
    assert fetch(box, url="https://a.com/d.json")["text"].startswith('{\n "k"')


def test_offset_paging_and_focus(box: Toolbox) -> None:
    paras = [f"Paragraph {i} is about gardening and the weather in spring." for i in range(400)]
    paras[300] = "The pricing model charges per seat and per month for enterprise customers."
    Net.routes["https://a.com/long"] = FakeResp("https://a.com/long", "\n\n".join(paras).encode(), "text/plain")
    p1 = fetch(box, url="https://a.com/long", max_chars=5000)
    assert p1["truncated"] and p1["next_offset"] == 5000 and p1["total_chars"] > 5000
    p2 = fetch(box, url="https://a.com/long", max_chars=5000, offset=p1["next_offset"])
    assert p2["text"] and p2["text"] != p1["text"] and len(Net.calls) == 1  # paging reads the cache
    f = fetch(box, url="https://a.com/long", max_chars=3000, focus="pricing enterprise")
    assert f["focused"] and "pricing model" in f["text"] and len(f["text"]) <= 3100


def test_tainted_run_cannot_read_a_cached_url_it_could_not_fetch(box: Toolbox) -> None:
    Net.routes["https://a.com/p"] = FakeResp("https://a.com/p", PAGE.encode(), "text/html")
    assert fetch(box, url="https://a.com/p")["cached"] is False  # an untainted run fills the cache
    blocked = fetch(box, {"tainted": True}, url="https://a.com/p")
    assert "restricted" in blocked["error"] and len(Net.calls) == 1
    ok = fetch(box, {"tainted": True, "allowed_urls": ["https://a.com/p"]}, url="https://a.com/p")
    assert ok["cached"] is True


def test_setting_default_present() -> None:
    assert llm.DEFAULT_SETTINGS["fetchCacheSeconds"] == 3600
