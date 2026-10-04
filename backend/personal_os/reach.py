"""Platform readers: per-platform read and search paths.

Each platform gets the most reliable upstream path (Jina Reader for any page, Exa for search, yt-dlp for
YouTube, the GitHub API, feedparser for RSS). The agent has no shell, so the routing lives here as plain
functions that tools.py exposes as tools.

Everything in this module talks to a fixed, first-party host (r.jina.ai, mcp.exa.ai / api.exa.ai, api.github.com,
YouTube). URLs the model chose are checked by tools.py (_check_url: SSRF and the taint rule) before they get here;
the one arbitrary-URL read, RSS, is fetched by tools.py through guarded_request and only *parsed* here.

Not ported: Twitter/X, Reddit, XiaoHongShu, Facebook, Instagram. Every one of them needs a logged-in session or
exported cookies (Reddit's anonymous JSON and Jina both get a 403 as of 2026-10), which is a credential this app
should not be scraping out of the user's browser.
"""
from __future__ import annotations

import asyncio
import html
import json
import re
import shutil
import subprocess
import time
import urllib.parse
from typing import Any

import httpx

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Grain/0.1"


class ReachError(Exception):
    """A platform path failed in a way worth telling the model about (message is model-facing)."""


def _strip_html(s: str) -> str:
    s = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", s or "", flags=re.S | re.I)
    s = html.unescape(re.sub(r"<[^>]+>", " ", s))
    return re.sub(r"\s+", " ", s).strip()


def _clip(s: str, n: int) -> str:
    s = s or ""
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


# ---- bounded reads: a response is never buffered whole ----

MAX_BODY = 5_000_000   # bytes kept from any one response
BODY_DEADLINE_S = 45.0  # wall-clock for a whole request; httpx's timeout is per read, so a trickle never trips it


async def read_capped(r: httpx.Response, cap: int = MAX_BODY) -> httpx.Response:
    """Read a streamed response up to `cap` bytes, close it, and leave the bytes on r.content.

    `r.extensions["body_truncated"]` says whether the rest was dropped.
    """
    chunks: list[bytes] = []
    n, truncated = 0, False
    try:
        async for chunk in r.aiter_bytes():
            chunks.append(chunk)
            n += len(chunk)
            if n > cap:
                truncated = True
                break
    finally:
        await r.aclose()
    r._content = b"".join(chunks)[:cap]  # httpx's own cache slot: makes .content/.text work on a closed stream
    r.extensions["body_truncated"] = truncated
    return r


# ---- Jina Reader: any URL -> markdown, rendered on Jina's side ----

JINA = "https://r.jina.ai/"
_JINA_FAILED = ("target url returned error", "requiring captcha", "title: just a moment...",
                "## performing security verification", "attention required! | cloudflare")


async def jina_read(url: str, *, timeout: float = 30.0) -> dict[str, Any]:
    """Read a public page through Jina Reader. `url` must already have passed tools._check_url.

    Jina fetches the page from its own servers, so a site that blocks our plain HTTP client, or that only renders
    with JavaScript, usually still comes back as text. The cost is that Jina sees the URL -- which is why fetch_url
    only reaches for it as a fallback, and the user can switch that off (settings.readerFallback).
    """
    async def _get() -> httpx.Response:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as c:
            # An honest UA: Jina forwards it, and a fake browser string gets refused by sites (Wikipedia: 403) that
            # are happy to serve a named client.
            async with c.stream("GET", JINA + url, headers={"User-Agent": "Grain/0.1 (+desktop assistant)", "Accept": "text/plain"}) as resp:
                return await read_capped(resp)
    try:
        r = await asyncio.wait_for(_get(), BODY_DEADLINE_S)
    except asyncio.TimeoutError:
        raise ReachError("Jina Reader took too long") from None
    if r.status_code != 200:
        raise ReachError(f"Jina Reader answered {r.status_code}")
    body = r.text
    head = body[:4096].casefold()
    if any(m in head for m in _JINA_FAILED):
        raise ReachError("the site blocked Jina Reader too (error or anti-bot page)")
    title = ""
    if m := re.match(r"Title:\s*(.*)", body):
        title = m.group(1).strip()
    cut = body.find("Markdown Content:")
    text = body[cut + len("Markdown Content:"):].strip() if cut >= 0 else body.strip()
    return {"title": title, "text": text}


# ---- Exa: semantic web search ----

EXA_MCP = "https://mcp.exa.ai/mcp"  # Exa's hosted MCP server: no key needed, rate-limited
EXA_API = "https://api.exa.ai/search"


def parse_exa_mcp(text: str, snippet_chars: int = 600) -> list[dict[str, Any]]:
    """The hosted server returns one text blob: blocks of `Title:/URL:/Published:/Author:/Highlights:` split by ---."""
    rows = []
    for block in re.split(r"\n-{3,}\n", text or ""):
        url = re.search(r"(?m)^URL:\s*(\S+)", block)
        if not url:
            continue
        title = re.search(r"(?m)^Title:\s*(.*)$", block)
        pub = re.search(r"(?m)^Published:\s*(.*)$", block)
        hl = block.split("Highlights:", 1)[1] if "Highlights:" in block else ""
        row = {"title": title.group(1).strip() if title else "", "url": url.group(1),
               "snippet": _clip(re.sub(r"\s+", " ", hl).strip(), snippet_chars)}
        if pub and pub.group(1).strip() not in ("", "N/A"):
            row["published"] = pub.group(1).strip()[:10]
        rows.append(row)
    return rows


def _sse_json(body: str) -> dict[str, Any]:
    """A streamable-HTTP MCP reply is either plain JSON or SSE `data:` lines; take the JSON-RPC message."""
    body = body.strip()
    if body.startswith("{"):
        return json.loads(body)
    for line in body.splitlines():
        if line.startswith("data:"):
            msg = json.loads(line[5:].strip())
            if "result" in msg or "error" in msg:
                return msg
    raise ReachError("Exa returned no result")


async def exa_search(query: str, n: int, api_key: str = "") -> list[dict[str, Any]]:
    n = max(1, min(int(n), 25))
    async with httpx.AsyncClient(timeout=30, follow_redirects=False) as c:
        if api_key:
            r = await c.post(EXA_API, headers={"x-api-key": api_key, "Content-Type": "application/json"},
                             json={"query": query, "numResults": n, "contents": {"highlights": True}})
            if r.status_code != 200:
                raise ReachError(f"Exa answered {r.status_code}")
            out = []
            for w in r.json().get("results", []):
                row = {"title": w.get("title") or "", "url": w.get("url"),
                       "snippet": _clip(" ".join(w.get("highlights") or []) or w.get("text") or "", 600)}
                if w.get("publishedDate"):
                    row["published"] = str(w["publishedDate"])[:10]
                out.append(row)
            return out
        # Stateless call: the hosted server answers tools/call without an initialize handshake.
        r = await c.post(EXA_MCP, headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
                         json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                               "params": {"name": "web_search_exa",
                                          "arguments": {"query": query, "numResults": n, "objective": query}}})
        if r.status_code != 200:
            raise ReachError(f"Exa answered {r.status_code}")
        msg = _sse_json(r.text)
        if "error" in msg or (msg.get("result") or {}).get("isError"):
            raise ReachError("Exa refused the search")
        text = "\n".join(c.get("text", "") for c in msg["result"].get("content", []) if c.get("type") == "text")
        return parse_exa_mcp(text)


# ---- YouTube: metadata, transcript and search via yt-dlp ----

YOUTUBE_HOSTS = ("youtube.com", "youtu.be", "youtube-nocookie.com")
# Caption tracks are a second URL, chosen by yt-dlp, and they redirect. Only these hosts may be fetched.
CAPTION_HOSTS = YOUTUBE_HOSTS + ("googlevideo.com",)


def is_youtube(host: str) -> bool:
    return any(host == h or host.endswith("." + h) for h in YOUTUBE_HOSTS)


def caption_url_ok(url: str) -> bool:
    """A caption fetch may only hit YouTube or googlevideo over https. Redirects included."""
    try:
        parsed = urllib.parse.urlparse(url)
    except ValueError:
        return False
    if parsed.scheme != "https" or parsed.username or parsed.password:
        return False
    host = (parsed.hostname or "").lower().rstrip(".")
    return any(host == h or host.endswith("." + h) for h in CAPTION_HOSTS)


async def _get_caption(url: str) -> httpx.Response:
    """Follow caption redirects by hand, and refuse any hop that leaves the caption hosts."""
    async with httpx.AsyncClient(timeout=30, follow_redirects=False, headers={"User-Agent": UA}) as client:
        current = url
        for _ in range(5):
            if not caption_url_ok(current):
                raise ReachError("caption URL is not a YouTube host")
            response = await client.get(current)
            if response.status_code not in (301, 302, 303, 307, 308):
                return response
            location = response.headers.get("location")
            if not location:
                raise ReachError("caption redirect had no location")
            current = urllib.parse.urljoin(current, location)
        raise ReachError("too many caption redirects")


def _ydl(opts: dict[str, Any]) -> Any:
    try:
        import yt_dlp
    except ImportError:  # optional at runtime: the rest of the backend works without it
        raise ReachError("yt-dlp is not installed in the backend environment (pip install yt-dlp)") from None
    return yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": True,
                             "socket_timeout": 20, **opts})


def _pick_track(info: dict[str, Any], lang: str) -> tuple[str, dict[str, Any], bool] | None:
    """Human subtitles before auto captions; exact language, then a regional variant, then the video's own language."""
    want = (lang or "en").lower()
    for auto, pool in ((False, info.get("subtitles") or {}), (True, info.get("automatic_captions") or {})):
        keys = list(pool)
        orig = (info.get("language") or "").lower()
        cands = ([k for k in keys if k.lower() == want] + [k for k in keys if k.lower().startswith(want + "-")]
                 + ([k for k in keys if k.lower() in (orig, orig + "-orig")] if orig else []))
        if not auto and not cands:
            cands = keys[:1]  # a human track in any language beats machine captions
        for k in cands:
            tracks = pool.get(k) or []
            for ext in ("json3", "vtt", "srv1"):
                for t in tracks:
                    if t.get("ext") == ext and t.get("url"):
                        return k, t, auto
    return None


def _ts(ms: float) -> str:
    s = int(ms // 1000)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def parse_json3(data: dict[str, Any], every_ms: int = 30_000) -> str:
    """YouTube's json3 captions -> paragraphs stamped every ~30s, so the model can cite a moment."""
    paras: list[str] = []
    cur: list[str] = []
    start = None
    for ev in data.get("events") or []:
        text = "".join(s.get("utf8", "") for s in ev.get("segs") or []).replace("\n", " ").strip()
        if not text:
            continue
        t = float(ev.get("tStartMs") or 0)
        if start is None:
            start = t
        elif t - start >= every_ms:
            paras.append(f"[{_ts(start)}] " + " ".join(cur))
            cur, start = [], t
        cur.append(text)
    if cur:
        paras.append(f"[{_ts(start or 0)}] " + " ".join(cur))
    return re.sub(r"[ \t]+", " ", "\n".join(paras))


def parse_vtt(body: str) -> str:
    out: list[str] = []
    for line in body.splitlines():
        line = line.strip()
        if not line or line == "WEBVTT" or "-->" in line or line.isdigit() or line.startswith(("Kind:", "Language:", "NOTE")):
            continue
        line = re.sub(r"<[^>]+>", "", line)
        if out and out[-1] == line:  # auto captions repeat each line as it scrolls
            continue
        out.append(line)
    return " ".join(out)


async def youtube_video(url: str, lang: str = "en") -> dict[str, Any]:
    info = await asyncio.to_thread(lambda: _ydl({}).extract_info(url, download=False))
    if not info:
        raise ReachError("yt-dlp found no video at that URL")
    out: dict[str, Any] = {
        "title": info.get("title"), "channel": info.get("channel") or info.get("uploader"),
        "url": info.get("webpage_url") or url, "duration_seconds": info.get("duration"),
        "upload_date": info.get("upload_date"), "view_count": info.get("view_count"),
        "description": _clip(info.get("description") or "", 2000),
    }
    picked = _pick_track(info, lang)
    if not picked:
        out["transcript"] = ""
        out["transcript_note"] = "this video has no subtitles or auto captions"
        return out
    k, track, auto = picked
    r = await _get_caption(track["url"])  # googlevideo/youtube timedtext; redirects must stay on those hosts
    if r.status_code != 200:
        raise ReachError(f"YouTube refused the caption track ({r.status_code})")
    out["transcript"] = parse_json3(r.json()) if track.get("ext") == "json3" else parse_vtt(r.text)
    out["transcript_language"], out["auto_generated"] = k, auto
    return out


async def youtube_search(query: str, n: int) -> list[dict[str, Any]]:
    n = max(1, min(int(n), 20))
    info = await asyncio.to_thread(lambda: _ydl({"extract_flat": "in_playlist"}).extract_info(f"ytsearch{n}:{query}", download=False))
    rows = []
    for e in (info or {}).get("entries") or []:
        vid = e.get("id")
        if not vid:
            continue
        rows.append({"title": e.get("title"), "url": f"https://www.youtube.com/watch?v={vid}",
                     "channel": e.get("channel") or e.get("uploader"), "duration_seconds": e.get("duration"),
                     "view_count": e.get("view_count"), "snippet": _clip(e.get("description") or "", 300)})
    return rows


# ---- GitHub: REST API, with the gh CLI's token when the user is logged in ----

GH_API = "https://api.github.com"
REPO_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9._-]{1,100}$")
_gh_token: tuple[float, str] = (0.0, "")


def gh_token(settings: dict[str, Any]) -> str:
    """settings.githubToken, else `gh auth token` (cached 10 min). Unauthenticated works for public reads, at 60/h."""
    global _gh_token
    if tok := str(settings.get("githubToken") or "").strip():
        return tok
    at, tok = _gh_token
    if time.monotonic() - at < 600 and at:
        return tok
    tok = ""
    if gh := shutil.which("gh"):
        try:
            p = subprocess.run([gh, "auth", "token"], capture_output=True, text=True, timeout=5)
            tok = p.stdout.strip() if p.returncode == 0 else ""
        except (OSError, subprocess.SubprocessError):
            tok = ""
    _gh_token = (time.monotonic(), tok)
    return tok


def parse_repo(repo: str) -> str:
    """'owner/name', or a github.com URL pointing into one."""
    s = (repo or "").strip()
    if "github.com" in s:
        u = urllib.parse.urlsplit(s if "://" in s else "https://" + s)
        if (u.hostname or "").lower() not in ("github.com", "www.github.com"):
            raise ReachError("not a github.com URL")
        s = "/".join(u.path.strip("/").split("/")[:2])
    s = s.removesuffix(".git")
    if not REPO_RE.match(s):
        raise ReachError(f"'{repo}' is not an owner/name repository")
    return s


async def _gh(c: httpx.AsyncClient, path: str, *, token: str, params: dict[str, Any] | None = None,
              raw: bool = False) -> httpx.Response:
    hdrs = {"Accept": "application/vnd.github.raw" if raw else "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "Grain/0.1"}
    if token:
        hdrs["Authorization"] = f"Bearer {token}"
    r = await c.get(GH_API + path, headers=hdrs, params=params)
    if r.status_code == 404:
        raise ReachError("GitHub: not found (or private and not visible to this token)")
    if r.status_code in (401, 403, 429):
        msg = ""
        try:
            msg = r.json().get("message", "")
        except ValueError:
            pass
        hint = "" if token else " Run `gh auth login` (or set a GitHub token in Settings) to raise the limit."
        raise ReachError(f"GitHub refused the request ({r.status_code}: {_clip(msg, 160)}).{hint}")
    if r.status_code >= 400:
        raise ReachError(f"GitHub answered {r.status_code}")
    return r


async def github_search(kind: str, query: str, n: int, *, token: str) -> list[dict[str, Any]]:
    kind = {"repo": "repositories", "repos": "repositories", "repositories": "repositories",
            "code": "code", "issue": "issues", "issues": "issues", "prs": "issues", "pulls": "issues"}.get((kind or "repos").lower())
    if not kind:
        raise ReachError("kind must be repos, code or issues")
    if kind == "code" and not token:
        raise ReachError("GitHub code search needs a login. Run `gh auth login`, or search repos/issues instead.")
    async with httpx.AsyncClient(timeout=20, follow_redirects=False) as c:
        r = await _gh(c, f"/search/{kind}", token=token, params={"q": query, "per_page": max(1, min(int(n), 30))})
    items = r.json().get("items", [])
    if kind == "repositories":
        return [{"repo": i["full_name"], "url": i["html_url"], "description": i.get("description") or "",
                 "stars": i.get("stargazers_count"), "language": i.get("language"),
                 "updated": (i.get("pushed_at") or "")[:10]} for i in items]
    if kind == "code":
        return [{"repo": i["repository"]["full_name"], "path": i["path"], "url": i["html_url"]} for i in items]
    return [{"repo": "/".join(i["repository_url"].split("/")[-2:]), "number": i["number"], "title": i["title"],
             "state": i["state"], "kind": "pr" if i.get("pull_request") else "issue", "url": i["html_url"],
             "comments": i.get("comments"), "updated": (i.get("updated_at") or "")[:10]} for i in items]


async def github_read(repo: str, *, path: str = "", number: int | None = None, ref: str = "", token: str,
                      max_chars: int = 20000) -> dict[str, Any]:
    full = parse_repo(repo)
    if ".." in (path or "").split("/"):
        raise ReachError("path may not contain '..'")
    q = urllib.parse.quote
    async with httpx.AsyncClient(timeout=25, follow_redirects=False) as c:
        if number is not None:
            issue = (await _gh(c, f"/repos/{full}/issues/{int(number)}", token=token)).json()
            comments = (await _gh(c, f"/repos/{full}/issues/{int(number)}/comments", token=token,
                                  params={"per_page": 30})).json() if issue.get("comments") else []
            body = _clip(issue.get("body") or "", max_chars // 2)
            thread, used = [], len(body)
            for cm in comments:
                t = _clip(cm.get("body") or "", 3000)
                if used + len(t) > max_chars:
                    break
                used += len(t)
                thread.append({"author": (cm.get("user") or {}).get("login"), "date": (cm.get("created_at") or "")[:10], "body": t})
            return {"repo": full, "number": issue["number"], "kind": "pr" if issue.get("pull_request") else "issue",
                    "title": issue.get("title"), "state": issue.get("state"), "author": (issue.get("user") or {}).get("login"),
                    "labels": [lb.get("name") for lb in issue.get("labels") or []], "url": issue.get("html_url"),
                    "body": body, "comments": thread, "total_comments": issue.get("comments", 0)}
        params = {"ref": ref} if ref else None
        if path:
            meta = (await _gh(c, f"/repos/{full}/contents/{q(path.strip('/'))}", token=token, params=params)).json()
            if isinstance(meta, list):
                return {"repo": full, "path": path, "type": "dir",
                        "entries": [{"name": e["name"], "type": e["type"], "size": e.get("size")} for e in meta]}
            text = (await _gh(c, f"/repos/{full}/contents/{q(path.strip('/'))}", token=token, params=params, raw=True)).text
            return {"repo": full, "path": path, "type": "file", "url": meta.get("html_url"), "size": meta.get("size"),
                    "text": text[:max_chars], "truncated": len(text) > max_chars}
        info = (await _gh(c, f"/repos/{full}", token=token)).json()
        try:
            readme = (await _gh(c, f"/repos/{full}/readme", token=token, params=params, raw=True)).text
        except ReachError:
            readme = ""
        try:
            top = (await _gh(c, f"/repos/{full}/contents", token=token, params=params)).json()
        except ReachError:
            top = []
    return {"repo": full, "url": info.get("html_url"), "description": info.get("description") or "",
            "stars": info.get("stargazers_count"), "forks": info.get("forks_count"), "language": info.get("language"),
            "topics": info.get("topics") or [], "license": (info.get("license") or {}).get("spdx_id"),
            "default_branch": info.get("default_branch"), "updated": (info.get("pushed_at") or "")[:10],
            "open_issues": info.get("open_issues_count"), "archived": info.get("archived"),
            "files": [{"name": e["name"], "type": e["type"]} for e in top][:200] if isinstance(top, list) else [],
            "readme": readme[:max_chars], "readme_truncated": len(readme) > max_chars}


# ---- RSS / Atom ----

def parse_feed(content: bytes, max_items: int) -> dict[str, Any]:
    try:
        import feedparser
    except ImportError:
        raise ReachError("feedparser is not installed in the backend environment (pip install feedparser)") from None
    d = feedparser.parse(content)
    if d.bozo and not d.entries:
        raise ReachError("that URL is not an RSS or Atom feed")
    items = []
    for e in d.entries[: max(1, min(int(max_items), 100))]:
        items.append({"title": _strip_html(e.get("title", "")), "url": e.get("link"),
                      "published": e.get("published") or e.get("updated") or "",
                      "summary": _clip(_strip_html(e.get("summary", "")), 500)})
    return {"feed": _strip_html(d.feed.get("title", "")), "site": d.feed.get("link"), "items": items,
            "total_items": len(d.entries)}
