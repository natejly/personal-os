"""What fetch_url does with a response body: content-type routing, numbered link citations, query-focused
trimming, offset paging and a small TTL cache. Pure functions plus `WebCache`; no network, no model."""
from __future__ import annotations

import html as html_mod
import json
import math
import re
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Any, Callable

from .extract_text import extract_text

MAX_CACHE_BODY = 2 * 1024 * 1024
MAX_CACHE_ROWS = 200
CACHE_MAX_AGE = 24 * 3600
LINK_CAP = 40


class Unreadable(Exception):
    """The body cannot be turned into text (binary type, or a PDF pypdf cannot open)."""


@dataclass
class Rendered:
    text: str
    links: list[dict[str, Any]] = field(default_factory=list)


def classify(content_type: str, url: str, head: bytes) -> str:
    ct = (content_type or "").split(";")[0].strip().lower()
    path = urllib.parse.urlsplit(url or "").path.lower()
    if ct == "application/pdf" or head[:5] == b"%PDF-" or (path.endswith(".pdf") and not ct.startswith("text/")):
        return "pdf"
    if ct in ("application/json", "text/json") or ct.endswith("+json"):
        return "json"
    if ct in ("text/html", "application/xhtml+xml"):
        return "html"
    if ct.startswith("text/") or ct in ("application/xml", "application/javascript", "application/x-yaml") or ct.endswith("+xml"):
        return "text"
    if not ct or ct == "application/octet-stream":
        low = head[:512].lstrip().lower()
        if low.startswith((b"<!doctype html", b"<html")):
            return "html"
        if b"\x00" not in head[:512]:
            return "text"
    return "binary"


_LINK = re.compile(r"(?<!\!)\[([^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")


def numberize_links(md: str, base_url: str = "") -> tuple[str, list[dict[str, Any]]]:
    """`[text](href)` -> `[text][n]`; relative hrefs resolved, non-http(s) dropped to plain text, duplicates share a number."""
    links: list[dict[str, Any]] = []
    index: dict[str, int] = {}

    def sub(m: re.Match[str]) -> str:
        label, href = m.group(1), m.group(2)
        try:
            absu = urllib.parse.urljoin(base_url, html_mod.unescape(href)) if base_url else href
            u = urllib.parse.urlsplit(absu)
        except ValueError:
            return label
        if u.scheme not in ("http", "https") or not u.netloc:
            return label
        absu = urllib.parse.urlunsplit(u._replace(fragment=""))
        n = index.get(absu)
        if n is None:
            n = index[absu] = len(links) + 1
            links.append({"n": n, "text": label.strip()[:120], "url": absu})
        return f"[{label}][{n}]" if label.strip() else ""

    return _LINK.sub(sub, md), links


def references(links: list[dict[str, Any]], cap: int = LINK_CAP) -> str:
    return "\n".join(f"[{l['n']}]: {l['url']}" for l in links[:cap])


def decode(content_type: str, body: bytes) -> str:
    m = re.search(r"charset=([\w-]+)", content_type or "", re.I)
    try:
        return body.decode(m.group(1) if m else "utf-8", errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")


def _strip_html(body: str) -> str:
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", body, flags=re.S | re.I)
    text = html_mod.unescape(re.sub(r"<[^>]+>", " ", text))
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n\n", text)).strip()


def render(kind: str, body: bytes, text_hint: str, final_url: str, *, include_links: bool = True) -> Rendered:
    """`text_hint` is the already-decoded body (httpx's r.text) for text-like kinds."""
    if kind == "binary":
        raise Unreadable("binary content, cannot be read as text")
    if kind == "pdf":
        try:
            return Rendered(extract_text("x.pdf", body, "application/pdf"))
        except Exception as e:  # noqa: BLE001
            raise Unreadable(f"could not read PDF ({type(e).__name__})") from None
    if kind == "json":
        try:
            return Rendered(json.dumps(json.loads(text_hint), indent=1, ensure_ascii=False))
        except ValueError:
            return Rendered(text_hint)
    if kind == "text":
        return Rendered(text_hint)
    md = ""
    try:
        import trafilatura

        md = trafilatura.extract(text_hint, output_format="markdown", include_links=include_links, include_tables=True) or ""
    except Exception:  # noqa: BLE001
        md = ""
    if not md:
        return Rendered(_strip_html(text_hint))
    if not include_links:
        return Rendered(md)
    md, links = numberize_links(md, final_url)
    return Rendered(md, links)


STOP = frozenset("a an and are as at be but by for from has have he her his i in is it its of on or she that the their them they this to was we were what when where which who will with you your not no can do does how".split())


def _tokens(s: str) -> list[str]:
    return [t for t in re.findall(r"\w+", s.lower()) if t not in STOP]


def _blocks(text: str) -> list[str]:
    out: list[str] = []
    carry = ""
    for b in re.split(r"\n\s*\n", text):
        b = b.strip()
        if not b:
            continue
        b = f"{carry}\n\n{b}" if carry else b
        carry = ""
        if len(b) < 40:
            carry = b
            continue
        out.append(b)
    if carry:
        if out:
            out[-1] += "\n\n" + carry
        else:
            out.append(carry)
    return out


def bm25_focus(text: str, query: str, budget: int) -> str:
    """The blocks that best match `query`, in document order, within `budget` chars. Block 0 (title/lede) always stays."""
    q = set(_tokens(query))
    if not q or len(text) <= budget:
        return text
    blocks = _blocks(text)
    if len(blocks) < 2:
        return text
    toks = [_tokens(b) for b in blocks]
    n = len(blocks)
    avg = (sum(len(t) for t in toks) / n) or 1.0
    df = {w: sum(1 for t in toks if w in t) for w in q}
    k1, b = 1.5, 0.75
    scores: list[float] = []
    for t in toks:
        s = 0.0
        for w in q:
            f = t.count(w)
            if not f:
                continue
            idf = math.log(1 + (n - df[w] + 0.5) / (df[w] + 0.5))
            s += idf * f * (k1 + 1) / (f + k1 * (1 - b + b * len(t) / avg))
        scores.append(s)
    keep, used = {0}, len(blocks[0])
    for i in sorted(range(1, n), key=lambda i: (-scores[i], i)):
        if scores[i] <= 0:
            break
        if used + len(blocks[i]) + 2 > budget:
            continue
        keep.add(i)
        used += len(blocks[i]) + 2
    parts: list[str] = []
    prev = -1
    for i in sorted(keep):
        if prev != -1 and i != prev + 1:
            parts.append("[...]")
        parts.append(blocks[i])
        prev = i
    if prev != n - 1:
        parts.append("[...]")
    return "\n\n".join(parts)


def page_window(text: str, offset: int, max_chars: int) -> tuple[str, int, int | None]:
    off = max(0, int(offset))
    end = off + max_chars
    return text[off:end], len(text), (end if end < len(text) else None)


class WebCache:
    """Raw response bodies by URL, in the app database. Consulted only after the SSRF/taint check."""

    def __init__(self, db: Any, clock: Callable[[], float] = time.time):
        self.db, self.clock = db, clock

    def get(self, url: str, ttl: float) -> dict[str, Any] | None:
        if ttl <= 0:
            return None
        with self.db.connect() as c:
            r = c.execute("SELECT * FROM web_cache WHERE url=?", (url,)).fetchone()
        if not r or self.clock() - r["fetched_at"] > ttl:
            return None
        return {"status": r["status"], "content_type": r["content_type"] or "", "body": bytes(r["body"] or b""), "final_url": r["final_url"] or url}

    def put(self, url: str, status: int, content_type: str, body: bytes, final_url: str) -> None:
        if len(body) > MAX_CACHE_BODY:
            return
        now = self.clock()
        with self.db.tx() as c:
            c.execute("INSERT OR REPLACE INTO web_cache(url, fetched_at, status, content_type, body, final_url) VALUES (?,?,?,?,?,?)",
                      (url, now, status, content_type, body, final_url))
            c.execute("DELETE FROM web_cache WHERE fetched_at < ?", (now - CACHE_MAX_AGE,))
            c.execute("DELETE FROM web_cache WHERE url IN (SELECT url FROM web_cache ORDER BY fetched_at DESC LIMIT -1 OFFSET ?)", (MAX_CACHE_ROWS,))
