"""Platform reader tools (reach.py + their Toolbox wiring). No network: every platform client is monkeypatched."""
from __future__ import annotations

import asyncio
import os
import sys
from typing import Any

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from personal_os import reach, tools  # noqa: E402
from personal_os.tools import Toolbox  # noqa: E402


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def make_toolbox(settings: dict[str, Any] | None = None) -> Toolbox:
    s = settings or {}
    return Toolbox(None, None, None, lambda: s)  # type: ignore[arg-type]


def call(tb: Toolbox, name: str, ctx: dict[str, Any] | None = None, **kw: Any) -> Any:
    return run(tb.specs[name].fn(ctx if ctx is not None else {}, **kw))


def test_caption_urls_stay_on_youtube_hosts() -> None:
    assert reach.caption_url_ok("https://www.youtube.com/api/timedtext?v=1")
    assert reach.caption_url_ok("https://manifest.googlevideo.com/api/timedtext?v=1")
    assert not reach.caption_url_ok("http://www.youtube.com/api/timedtext")
    assert not reach.caption_url_ok("https://0177.0.0.1/api/timedtext")
    assert not reach.caption_url_ok("https://evil.example/redirect")
    assert not reach.caption_url_ok("https://user:pass@www.youtube.com/api/timedtext")


# ---- parsers ----
EXA_BLOB = """Title: Write-Ahead Logging
URL: https://www.sqlite.org/wal.html
Published: N/A
Author: N/A
Highlights:
The default method by which SQLite implements atomic commit
is a rollback journal.

---

Title: SQLite in Production with WAL
URL: https://victoria.dev/posts/sqlite-in-production-with-wal/
Published: 2020-03-05T15:14:43.000Z
Author: N/A
Highlights:
POSIX system call fsync() commits buffered data
"""


def test_parse_exa_mcp_blocks() -> None:
    rows = reach.parse_exa_mcp(EXA_BLOB)
    assert [r["url"] for r in rows] == ["https://www.sqlite.org/wal.html", "https://victoria.dev/posts/sqlite-in-production-with-wal/"]
    assert rows[0]["title"] == "Write-Ahead Logging" and "published" not in rows[0]
    assert rows[1]["published"] == "2020-03-05"
    assert rows[0]["snippet"].startswith("The default method") and "\n" not in rows[0]["snippet"]


def test_sse_json_takes_the_rpc_message() -> None:
    body = 'event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{"content":[]}}\n\n'
    assert reach._sse_json(body)["result"] == {"content": []}
    assert reach._sse_json('{"jsonrpc":"2.0","id":1,"result":{}}')["result"] == {}


def test_parse_json3_stamps_every_30s() -> None:
    data = {"events": [{"tStartMs": 0, "segs": [{"utf8": "hello"}]}, {"tStartMs": 4000, "segs": [{"utf8": "world"}]},
                       {"tStartMs": 31000, "segs": [{"utf8": "later\non"}]}, {"tStartMs": 32000, "segs": [{"utf8": "\n"}]},
                       {"tStartMs": 3_700_000, "segs": [{"utf8": "an hour in"}]}]}
    assert reach.parse_json3(data).splitlines() == ["[0:00] hello world", "[0:31] later on", "[1:01:40] an hour in"]


def test_parse_vtt_drops_timing_and_scroll_repeats() -> None:
    vtt = "WEBVTT\nKind: captions\nLanguage: en\n\n00:00.000 --> 00:01.000\n<c>one</c>\n\n00:01.000 --> 00:02.000\none\ntwo\n"
    assert reach.parse_vtt(vtt) == "one two"


def test_pick_track_prefers_human_then_language() -> None:
    info = {"language": "en",
            "subtitles": {"de": [{"ext": "vtt", "url": "h-de"}]},
            "automatic_captions": {"en": [{"ext": "json3", "url": "a-en"}]}}
    assert reach._pick_track(info, "en")[1]["url"] == "h-de"  # any human track beats machine captions
    info["subtitles"]["en-GB"] = [{"ext": "srv1", "url": "h-engb"}, {"ext": "json3", "url": "h-engb-j"}]
    k, t, auto = reach._pick_track(info, "en")
    assert (k, t["url"], auto) == ("en-GB", "h-engb-j", False)
    assert reach._pick_track({"subtitles": {}, "automatic_captions": {}}, "en") is None


def test_parse_repo() -> None:
    assert reach.parse_repo("BerriAI/litellm") == "BerriAI/litellm"
    assert reach.parse_repo("https://github.com/adbar/trafilatura/blob/master/x.py") == "adbar/trafilatura"
    assert reach.parse_repo("github.com/a/b.git") == "a/b"
    for bad in ("a", "a/b/c", "../etc/passwd", "https://gitlab.com/a/b", "a/b?x=1"):
        with pytest.raises(reach.ReachError):
            reach.parse_repo(bad)


def test_github_read_refuses_dotdot_path() -> None:
    with pytest.raises(reach.ReachError):
        run(reach.github_read("a/b", path="docs/../../secrets", token=""))


def test_code_search_needs_a_token() -> None:
    with pytest.raises(reach.ReachError, match="login"):
        run(reach.github_search("code", "x", 5, token=""))


def test_parse_feed() -> None:
    pytest.importorskip("feedparser")
    rss = (b'<?xml version="1.0"?><rss version="2.0"><channel><title>Blog</title><link>https://b.example/</link>'
           b'<item><title>One &amp; two</title><link>https://b.example/1</link><description>&lt;p&gt;Hi&lt;/p&gt;</description></item>'
           b'<item><title>Three</title><link>https://b.example/3</link></item></channel></rss>')
    out = reach.parse_feed(rss, 1)
    assert out["feed"] == "Blog" and out["total_items"] == 2
    assert out["items"] == [{"title": "One & two", "url": "https://b.example/1", "published": "", "summary": "Hi"}]
    with pytest.raises(reach.ReachError):
        reach.parse_feed(b"<html>not a feed", 5)


# ---- Toolbox wiring ----
def test_registered_in_web_group_and_reserved() -> None:
    from personal_os.mcp_servers import RESERVED_TOOL_NAMES
    tb = make_toolbox()
    for name in tools.REACH_TOOLS:
        spec = tb.specs[name]
        assert (spec.group, spec.danger, spec.taints) == ("web", "network", True), name
        assert name in RESERVED_TOOL_NAMES and name in tools.ALTERNATIVE
        assert tb.available(name)


def test_web_search_uses_exa_without_keys_and_allows_results(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = {}

    async def fake_exa(query: str, n: int, api_key: str = "") -> list[dict[str, Any]]:
        seen.update(query=query, n=n, key=api_key)
        return [{"title": "t", "url": "https://x.example/a", "snippet": "s"}]
    monkeypatch.setattr(reach, "exa_search", fake_exa)
    ctx: dict[str, Any] = {}
    out = call(make_toolbox({"exaApiKey": "k"}), "web_search", ctx, query="q", max_results=3)
    assert out["results"][0]["url"] == "https://x.example/a"
    assert seen == {"query": "q", "n": 3, "key": "k"}
    assert "https://x.example/a" in ctx["allowed_urls"]


def test_web_search_falls_back_to_duckduckgo_when_exa_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    async def broken(*a: Any, **k: Any) -> Any:
        raise reach.ReachError("Exa answered 429")
    monkeypatch.setattr(reach, "exa_search", broken)

    class FakeDDGS:
        def __enter__(self) -> "FakeDDGS":
            return self

        def __exit__(self, *a: Any) -> None:
            return None

        def text(self, q: str, max_results: int) -> list[dict[str, str]]:
            return [{"title": "d", "href": "https://d.example/", "body": "b"}]
    import ddgs
    monkeypatch.setattr(ddgs, "DDGS", FakeDDGS)
    out = call(make_toolbox(), "web_search", query="q")
    assert out["results"][0]["url"] == "https://d.example/"


def test_youtube_video_refuses_other_hosts_and_tainted_urls(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_video(url: str, lang: str = "en") -> dict[str, Any]:
        return {"title": "v", "transcript": "x" * 5000}
    monkeypatch.setattr(reach, "youtube_video", fake_video)
    tb = make_toolbox()
    assert "not YouTube" in call(tb, "youtube_video", url="https://vimeo.com/1")["error"]
    assert "error" in call(tb, "youtube_video", url="http://127.0.0.1/watch?v=1")
    tainted = {"tainted": True, "allowed_urls": set()}
    assert "restricted" in call(tb, "youtube_video", tainted, url="https://www.youtube.com/watch?v=leak")["error"]
    tainted["allowed_urls"].add("https://www.youtube.com/watch?v=ok")
    out = call(tb, "youtube_video", tainted, url="https://www.youtube.com/watch?v=ok", max_chars=2000)
    assert out["title"] == "v" and len(out["transcript"]) == 2000 and out["transcript_truncated"]


def test_github_errors_are_tool_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(reach, "gh_token", lambda s: "")
    out = call(make_toolbox(), "github_read", repo="not a repo")
    assert "not an owner/name" in out["error"] and out["try_instead"]


def test_read_feed_applies_the_taint_rule() -> None:
    tainted = {"tainted": True, "allowed_urls": set()}
    out = call(make_toolbox(), "read_feed", tainted, url="https://evil.example/feed?d=secret")
    assert "restricted" in out["error"]


def test_fetch_url_falls_back_to_jina_for_js_shells(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    async def fake_resolve(host: str) -> list[str]:
        return ["93.184.216.34"]
    monkeypatch.setattr(tools, "_resolve", fake_resolve)

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/html"}, text="<html><div id=root></div></html>")
    real = httpx.AsyncClient

    def client(*a: Any, **k: Any) -> httpx.AsyncClient:
        k.pop("transport", None)
        return real(*a, transport=httpx.MockTransport(handler), **k)
    monkeypatch.setattr(tools.httpx, "AsyncClient", client)
    calls = []

    async def fake_jina(url: str, **k: Any) -> dict[str, Any]:
        calls.append(url)
        return {"title": "App", "text": "rendered " * 100}
    monkeypatch.setattr(reach, "jina_read", fake_jina)
    out = call(make_toolbox(), "fetch_url", url="https://app.example/")
    assert out["via"] == "jina-reader" and out["text"].startswith("rendered") and calls == ["https://app.example/"]
    calls.clear()
    out = call(make_toolbox({"readerFallback": False}), "fetch_url", url="https://app.example/")
    assert "via" not in out and calls == []
