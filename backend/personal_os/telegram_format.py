"""What a reply looks like on a phone: markdown to Telegram HTML, plain-text fallback, and safe splitting.

Pure functions, no I/O. `render` turns markdown into HTML for parse_mode "HTML" (every tag opened is closed in the same
message, `&`, `<` and `>` are escaped everywhere); wide or long tables are left out of the text and returned as .csv
files. `split_html` / `split_plain` cut at paragraph, line, sentence or word boundaries and never inside a tag, an entity
or a <pre> block: a block that must span two messages is closed at the end of one and reopened at the start of the next.
`plan` decides between sending the parts or a short summary with the whole reply attached.
"""
from __future__ import annotations

import csv
import html as _html
import io
import re
from dataclasses import dataclass, field

MESSAGE_MAX = 4096  # Bot API: characters in one text message
LONG_REPLY_CHARS = 6000  # a longer reply goes as a summary plus reply.md
MAX_PARTS = 4  # as does a reply that would take more messages than this
SUMMARY_MAX = 600
SUMMARY_TAIL = "Full reply attached."
TABLE_MAX_WIDTH = 60  # columns a phone shows without wrapping a monospace line
TABLE_MAX_ROWS = 40
TABLE_PLACEHOLDER = "(table attached as a .csv)"

# ---------------------------------------------------------------- plain text

_FENCE = re.compile(r"^\s*(```|~~~)")


def _plain_inline(line: str) -> str:
    line = re.sub(r"!\[[^\]]*\]\(([^)\s]+)[^)]*\)", r"\1", line)
    line = re.sub(r"\[([^\]]+)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)",
                  lambda m: m.group(2) if m.group(1).strip() == m.group(2) else f"{m.group(1)} ({m.group(2)})", line)
    line = re.sub(r"\*\*(.+?)\*\*|__(.+?)__", lambda m: m.group(1) or m.group(2), line)
    line = re.sub(r"~~(.+?)~~", r"\1", line)
    line = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"\1", line)
    line = re.sub(r"(?<![\w])_(?!\s)(.+?)(?<!\s)_(?![\w])", r"\1", line)
    return re.sub(r"`([^`]+)`", r"\1", line)


def to_plain(md: str) -> str:
    """Markdown to what a plain text message can show: markers gone, content kept."""
    out: list[str] = []
    in_fence = False
    for line in (md or "").replace("\r\n", "\n").split("\n"):
        if _FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            out.append(line)
            continue
        line = re.sub(r"^\s{0,3}#{1,6}\s+", "", line)
        line = re.sub(r"^\s{0,3}>\s?", "", line)
        line = re.sub(r"^(\s*)[-*+]\s+", r"\1• ", line)
        out.append(_plain_inline(line))
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


def html_to_plain(h: str) -> str:
    """The visible text of one HTML message: tags gone, entities decoded, a link's address kept after its text."""
    def link(m: re.Match[str]) -> str:
        text = re.sub(r"<[^>]+>", "", m.group(2))
        return text if _html.unescape(text).strip() == _html.unescape(m.group(1)) else f"{text} ({m.group(1)})"

    h = re.sub(r'<a href="([^"]*)">(.*?)</a>', link, h, flags=re.S)
    return _html.unescape(re.sub(r"<[^>]+>", "", h))


# ---------------------------------------------------------------- markdown to HTML

def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


_CODE = re.compile(r"`([^`]+)`")
_IMG = re.compile(r"!\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
_BARE = re.compile(r"https?://[^\s]+")
_BOLD = re.compile(r"\*\*(.+?)\*\*|__(.+?)__")
_STRIKE = re.compile(r"~~(.+?)~~")
_ITAL_STAR = re.compile(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])")
_ITAL_US = re.compile(r"(?<![\w])_(?!\s)(.+?)(?<!\s)_(?![\w])")
_HOLE = re.compile(r"\x00(\d+)\x00")
_TAG = re.compile(r"<(/?)([a-z]+)(?: [^>]*)?>")
_LINK_SCHEMES = ("http://", "https://", "mailto:", "tg://")


def _balanced(h: str) -> bool:
    stack: list[str] = []
    for m in _TAG.finditer(h):
        if not m.group(1):
            stack.append(m.group(2))
        elif not stack or stack.pop() != m.group(2):
            return False
    return not stack


def _emph(s: str, bold: bool) -> str:
    s = _BOLD.sub(lambda m: f"<b>{m.group(1) or m.group(2)}</b>" if bold else (m.group(1) or m.group(2)), s)
    s = _STRIKE.sub(r"<s>\1</s>", s)
    s = _ITAL_STAR.sub(r"<i>\1</i>", s)
    return _ITAL_US.sub(r"<i>\1</i>", s)


def _inline(line: str, bold: bool = True) -> str:
    """One line of markdown as HTML. Code spans, links and bare URLs are set aside while the emphasis is applied, so the
    underscores in `snake_case` or an address are never italics. Mis-nested emphasis falls back to the plain text."""
    holes: list[str] = []

    def keep(h: str) -> str:
        holes.append(h)
        return f"\x00{len(holes) - 1}\x00"

    def anchor(m: re.Match[str]) -> str:
        text, url = m.group(1), m.group(2)
        if text.strip() == url or not url.startswith(_LINK_SCHEMES):
            return keep(url if text.strip() == url else f"{_emph(text, bold)} ({url})")
        return keep(f'<a href="{url.replace(chr(34), "&quot;")}">{_emph(text, bold)}</a>')

    s = _CODE.sub(lambda m: keep(f"<code>{_esc(m.group(1))}</code>"), line.replace("\x00", ""))
    s = _esc(s)
    s = _IMG.sub(lambda m: keep(m.group(1)), s)
    s = _LINK.sub(anchor, s)
    s = _BARE.sub(lambda m: keep(m.group(0)), s)
    s = _emph(s, bold)
    while "\x00" in s:
        s = _HOLE.sub(lambda m: holes[int(m.group(1))], s)
    return s if _balanced(s) else _esc(_plain_inline(line))


def _pre(body: str, lang: str = "") -> str:
    body = body.strip("\n")
    if not body.strip():
        return ""
    lang = re.sub(r"[^\w+#.-]", "", lang)
    code = _esc(body)
    return f'<pre><code class="language-{lang}">{code}</code></pre>' if lang else f"<pre>{code}</pre>"


_FENCE_OPEN = re.compile(r"^\s*(```+|~~~+)\s*([^\s`]*)")
_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$")
_HR = re.compile(r"^\s*([-*_])(\s*\1){2,}\s*$")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$")
_QUOTE = re.compile(r"^\s{0,3}>\s?(.*)$")
_BULLET = re.compile(r"^(\s*)[-*+]\s+(.*)$")


def _cells(line: str) -> list[str]:
    line = line.strip()
    line = line[1:] if line.startswith("|") else line
    line = line[:-1] if line.endswith("|") and not line.endswith("\\|") else line
    return [_plain_inline(c.strip().replace("\\|", "|")) for c in re.split(r"(?<!\\)\|", line)]


def _table(header: list[str], rows: list[list[str]]) -> tuple[str, bytes | None]:
    """(text to show, csv bytes). The text is a monospace block, or the placeholder when the table does not fit a phone."""
    n = len(header)
    rows = [(r + [""] * n)[:n] for r in rows]
    widths = [max(len(r[c]) for r in [header, *rows]) for c in range(n)]
    if sum(widths) + 3 * (n - 1) > TABLE_MAX_WIDTH or len(rows) > TABLE_MAX_ROWS:
        buf = io.StringIO()
        csv.writer(buf).writerows([header, *rows])
        return TABLE_PLACEHOLDER, buf.getvalue().encode()

    def fmt(r: list[str]) -> str:
        return " | ".join(c.ljust(w) for c, w in zip(r, widths)).rstrip()

    return _pre("\n".join([fmt(header), "-+-".join("-" * w for w in widths), *(fmt(r) for r in rows)])), None


@dataclass
class Rendered:
    html: str
    plain: str  # to_plain(md)
    files: list[tuple[str, str, bytes]] = field(default_factory=list)  # (name, mime, bytes): tables left out of the text


def render(md: str) -> Rendered:
    md = (md or "").replace("\r\n", "\n").replace("\x00", "")
    lines = md.split("\n")
    out: list[str] = []
    files: list[tuple[str, str, bytes]] = []

    def blank() -> None:
        if out and out[-1] != "":
            out.append("")

    i = 0
    while i < len(lines):
        line = lines[i]
        if m := _FENCE_OPEN.match(line):
            marker, body = m.group(1), []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith(marker):
                body.append(lines[i])
                i += 1
            i += 1
            if block := _pre("\n".join(body), m.group(2)):
                out.append(block)
            continue
        if "|" in line and i + 1 < len(lines) and "|" in lines[i + 1] and _TABLE_SEP.match(lines[i + 1]):
            header, rows = _cells(line), []
            i += 2
            while i < len(lines) and lines[i].strip() and "|" in lines[i]:
                rows.append(_cells(lines[i]))
                i += 1
            text, data = _table(header, rows)
            out.append(text)
            if data is not None:
                files.append((f"table{'-%d' % (len(files) + 1) if files else ''}.csv", "text/csv", data))
            continue
        if _QUOTE.match(line):
            quote = []
            while i < len(lines) and (q := _QUOTE.match(lines[i])):
                quote.append(_inline(q.group(1)))
                i += 1
            out.append(f"<blockquote>{chr(10).join(quote)}</blockquote>")
            continue
        i += 1
        if not line.strip():
            blank()
        elif _HR.match(line):
            out.append("──────────")
        elif h := _HEADING.match(line):
            out.append(f"<b>{_inline(h.group(1), bold=False)}</b>")
        elif b := _BULLET.match(line):
            out.append(f"{b.group(1)}• {_inline(b.group(2))}")
        else:
            out.append(_inline(line))
    return Rendered("\n".join(out).strip(), to_plain(md), files)


# ---------------------------------------------------------------- splitting

_TOKEN = re.compile(r"<[^>]*>|&#?\w+;|[\s\S]")
_OPEN_NAME = re.compile(r"<([a-z]+)")


def _split(tokens: list[str], size: int, markup: bool) -> list[str]:
    """Parts of at most `size` characters. A part ends at the best break past a third of the way in (paragraph, line,
    sentence, word), else as late as fits. Open tags are closed at the end of a part and reopened at the start of the next."""
    parts: list[str] = []
    stack: list[tuple[str, str]] = []  # (name, opening tag) carried into the next part
    i, n = 0, len(tokens)
    while i < n:
        if not stack:
            while i < n and tokens[i].isspace():
                i += 1
            if i >= n:
                break
        cur = list(stack)
        head = "".join(t for _, t in cur)
        length, close = len(head), sum(len(f"</{nm}>") for nm, _ in cur)
        in_pre = any(nm == "pre" for nm, _ in cur)
        cands: dict[int, tuple[int, list[tuple[str, str]]]] = {}
        prev, j = "", i
        while j < n:
            tok = tokens[j]
            is_tag = markup and tok.startswith("<")
            if is_tag:
                opening = not tok.startswith("</")
                name = _OPEN_NAME.match(tok).group(1) if opening else cur[-1][0] if cur else ""  # type: ignore[union-attr]
                new_close = close + len(f"</{name}>") if opening else close - len(tok)
            else:
                new_close = close
            if j > i and length + len(tok) + new_close > size:
                break
            length, close = length + len(tok), new_close
            if is_tag:
                cur.append((name, tok)) if opening else cur.pop()
                in_pre = any(nm == "pre" for nm, _ in cur)
            else:
                prio = 0 if tok == "\n" and prev == "\n" else 1 if tok == "\n" else None
                if tok == " " and not in_pre:
                    prio = 2 if prev in (".", "!", "?") else 3
                if prio is not None and length > size // 3:
                    cands[prio] = (j + 1, list(cur))
            prev = tok
            j += 1
        if j >= n:
            end, snap = n, cur
        elif cands:
            end, snap = cands[min(cands)]
        else:
            end, snap = j, cur
        body = "".join(tokens[i:end])
        body = body if snap else body.rstrip()
        part = head + body + "".join(f"</{nm}>" for nm, _ in reversed(snap))
        if part.strip():
            parts.append(part)
        stack, i = snap, end
    return parts


def split_html(h: str, size: int = MESSAGE_MAX) -> list[str]:
    """HTML messages of at most `size` characters (the raw string counts, tags included)."""
    return _split(_TOKEN.findall((h or "").strip()), size, True)


def split_plain(text: str, size: int = MESSAGE_MAX) -> list[str]:
    return _split(list((text or "").strip()), size, False)


# ---------------------------------------------------------------- what to send

@dataclass
class Plan:
    parts: list[str] = field(default_factory=list)  # HTML messages, in order
    summary: str | None = None  # plain text: when set, send it and attach the whole reply instead of `parts`
    files: list[tuple[str, str, bytes]] = field(default_factory=list)  # csv tables, sent after the text


def summarize(plain: str) -> str:
    """The first paragraph, cut at a sentence boundary to SUMMARY_MAX characters, and a pointer to the attachment."""
    para = re.split(r"\n\s*\n", plain.strip())[0].strip()
    if len(para) > SUMMARY_MAX:
        window = para[:SUMMARY_MAX]
        k = max(window.rfind(s) for s in (". ", "! ", "? ", ".\n", "!\n", "?\n"))
        if k > SUMMARY_MAX // 6:
            para = window[:k + 1]
        else:
            w = window.rfind(" ")
            para = window[:w if w > SUMMARY_MAX // 6 else SUMMARY_MAX].rstrip() + "…"
    return f"{para}\n\n{SUMMARY_TAIL}"


def plan(md: str, size: int = MESSAGE_MAX) -> Plan:
    r = render(md)
    parts, visible = split_html(r.html, size), html_to_plain(r.html)
    if len(visible) > LONG_REPLY_CHARS or len(parts) > MAX_PARTS:
        return Plan(summary=summarize(visible))
    return Plan(parts, None, r.files)
