"""Extract plain text from uploaded files.

Every file is accepted. PDF and Word yield their text. Anything that decodes as
text is kept. A binary file is stored with a short note so the chat can still
find it by name.
"""
from __future__ import annotations

import io
import re
import unicodedata
from collections.abc import Iterable
from pathlib import Path

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_INDEX_CHARS = 400_000
MAX_PDF_PAGES = 80
# A zip's declared uncompressed size, summed. A 180 KB docx can inflate to hundreds of MB of XML.
MAX_UNZIPPED_BYTES = 50 * 1024 * 1024
_TRUNCATED = "\n\n[Extract truncated. The full file is stored.]"

TEXT_EXT = {
    ".txt", ".md", ".markdown", ".mdx", ".csv", ".tsv", ".json", ".jsonl", ".ndjson",
    ".yaml", ".yml", ".xml", ".svg", ".html", ".htm", ".css", ".log", ".rst", ".toml",
    ".ini", ".cfg", ".conf", ".env", ".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
    ".rb", ".go", ".rs", ".java", ".c", ".h", ".cpp", ".hpp", ".cs", ".php", ".swift",
    ".kt", ".scala", ".lua", ".pl", ".r", ".sql", ".sh", ".bash", ".zsh", ".fish",
    ".tex", ".org", ".ipynb", ".rtf", ".ics", ".vcf", ".srt", ".vtt", ".vue", ".svelte",
    ".gradle", ".lock", ".geojson", ".kml", ".gpx", ".m", ".mm",
}
TEXT_MIME = {"application/json", "application/xml", "application/javascript", "application/x-javascript", "application/yaml"}


def safe_upload_name(name: str) -> str:
    """A single path segment. Directory pieces, controls, and bidi marks are dropped."""
    raw = (name or "").replace("\\", "/").replace("\x00", "")
    base = []
    for ch in Path(raw).name:
        if ch in "/\\" or unicodedata.category(ch) == "Cf" or not ch.isprintable():
            continue
        base.append(ch)
    cleaned = "".join(base).strip()
    if cleaned in {"", ".", ".."}:
        return "untitled"
    return cleaned[:180]


def for_index(text: str) -> str:
    """What search stores. The original file is kept whole on disk."""
    if len(text) <= MAX_INDEX_CHARS:
        return text
    return text[:MAX_INDEX_CHARS].rstrip() + _TRUNCATED


Block = dict  # {kind: 'heading'|'para'|'table'|'page', level: int, text: str, page: int | None}


def _blk(kind: str, text: str = "", level: int = 0, page: int | None = None) -> Block:
    return {"kind": kind, "level": level, "text": text, "page": page}


def _md_table(rows: list[list[str]]) -> str:
    rows = [[(c or "").replace("\n", " ").replace("|", "/").strip() for c in r] for r in rows if r]
    if not rows:
        return ""
    w = max(len(r) for r in rows)
    rows = [r + [""] * (w - len(r)) for r in rows]
    lines = ["| " + " | ".join(r) + " |" for r in rows]
    lines.insert(1, "| " + " | ".join("---" for _ in range(w)) + " |")
    return "\n".join(lines)


def markdown_blocks(text: str, page: int | None = None) -> list[Block]:
    """ATX headings, fenced code kept whole, pipe tables, blank-line paragraphs."""
    out: list[Block] = []
    para: list[str] = []
    fence: list[str] | None = None

    def flush() -> None:
        if para:
            body = "\n".join(para).strip()
            if body:
                is_table = all(ln.lstrip().startswith("|") for ln in para) and len(para) >= 2
                out.append(_blk("table" if is_table else "para", body, page=page))
            para.clear()

    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if fence is not None:
            fence.append(line)
            if line.strip().startswith("```"):
                out.append(_blk("para", "\n".join(fence), page=page))
                fence = None
            continue
        if line.strip().startswith("```"):
            flush()
            fence = [line]
            continue
        m = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", line)
        if m:
            flush()
            out.append(_blk("heading", m.group(2).strip(), level=len(m.group(1)), page=page))
        elif not line.strip():
            flush()
        else:
            para.append(line)
    if fence is not None:  # unterminated fence: keep what we have
        out.append(_blk("para", "\n".join(fence), page=page))
    flush()
    return out


def _guess_heading(para: str) -> int:
    """Conservative PDF heading guess: one short line, no sentence punctuation. 0 = not a heading."""
    line = para.strip()
    if "\n" in line or not 3 <= len(line) <= 80 or line[-1] in ".,;:!?" or not re.search(r"[A-Za-z]", line):
        return 0
    words = line.split()
    if line.isupper():
        return 1
    if 2 <= len(words) <= 6 and all(w[0].isupper() for w in words if len(w) > 3) and sum(1 for w in words if w[0].isupper()) >= 2:
        return 2
    return 0


def extract_structured(name: str, data: bytes, mime: str = "") -> list[Block]:
    """Blocks in reading order: headings, paragraphs, tables and (PDF) page markers."""
    ext = Path(name).suffix.lower()
    if ext == ".pdf" or mime == "application/pdf":
        from pypdf import PdfReader

        out: list[Block] = []
        for n, p in enumerate(PdfReader(io.BytesIO(data)).pages, start=1):
            out.append(_blk("page", page=n))
            for para in re.split(r"\n\s*\n", p.extract_text() or ""):
                para = para.strip()
                if not para:
                    continue
                lvl = _guess_heading(para)
                out.append(_blk("heading", para, level=lvl, page=n) if lvl else _blk("para", para, page=n))
        return out
    if ext == ".docx":
        import docx
        from docx.table import Table
        from docx.text.paragraph import Paragraph

        d = docx.Document(io.BytesIO(data))
        out = []
        for child in d.element.body.iterchildren():
            tag = child.tag.rsplit("}", 1)[-1]
            if tag == "p":
                para = Paragraph(child, d)
                if not para.text.strip():
                    continue
                style = (para.style.name if para.style is not None else "") or ""
                m = re.match(r"^Heading (\d)$", style)
                if m or style == "Title":
                    out.append(_blk("heading", para.text.strip(), level=int(m.group(1)) if m else 1))
                else:
                    out.append(_blk("para", para.text.strip()))
            elif tag == "tbl":
                t = _md_table([[c.text for c in r.cells] for r in Table(child, d).rows])
                if t:
                    out.append(_blk("table", t))
        return out
    if ext in TEXT_EXT or mime.startswith("text/"):
        return markdown_blocks(data.decode("utf-8", errors="replace"))
    return markdown_blocks(extract_text(name, data, mime))


def extract_text(name: str, data: bytes, mime: str = "") -> str:
    ext = Path(name).suffix.lower()
    mime = (mime or "").split(";")[0].strip().lower()
    if ext == ".docx" or mime == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        # Tables included (the old paragraph-only join dropped them from read_document too).
        try:
            return "\n\n".join(b["text"] for b in extract_structured(name, data, mime) if b["kind"] != "page")
        except Exception:  # noqa: BLE001 - a damaged file falls through to the plain readers below
            pass
    mime = (mime or "").split(";")[0].strip().lower()
    parsed = _parsed(ext, mime, data)
    if parsed is not None:
        return parsed
    if _looks_textual(data, mime, ext):
        return data.decode("utf-8", errors="replace")
    kind = mime or ext or "binary"
    return f"[File {name} ({kind}, {len(data)} bytes). No text could be extracted. The file is stored and can be referred to by name.]"


def _parsed(ext: str, mime: str, data: bytes) -> str | None:
    try:
        if ext == ".pdf" or mime == "application/pdf":
            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(data))
            parts = ((p.extract_text() or "") for p in reader.pages)
            return _bounded(parts, MAX_PDF_PAGES)
        if ext == ".docx" or mime == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
            import docx

            _check_zip(data)
            d = docx.Document(io.BytesIO(data))
            parts = (p.text for p in d.paragraphs if p.text.strip())
            return _bounded(parts, MAX_PDF_PAGES)
    except Exception:
        return None
    return None


def _check_zip(data: bytes) -> None:
    """Refuse a zip-based document that is not a zip or would expand past the cap. The caller
    treats the raise like any unreadable format: the file is stored with a note instead."""
    import zipfile

    with zipfile.ZipFile(io.BytesIO(data)) as z:
        if sum(i.file_size for i in z.infolist()) > MAX_UNZIPPED_BYTES:
            raise ValueError("Archive expands too large to extract")


def _bounded(parts: Iterable[str], limit: int) -> str:
    out: list[str] = []
    n = 0
    cut = False
    for i, part in enumerate(parts):
        if i >= limit or n >= MAX_INDEX_CHARS:
            cut = True
            break
        room = MAX_INDEX_CHARS - n
        if len(part) > room:
            out.append(part[:room])
            cut = True
            break
        out.append(part)
        n += len(part) + (2 if out else 0)
    text = "\n\n".join(out)
    if cut:
        return text.rstrip() + _TRUNCATED
    return text


def _looks_textual(data: bytes, mime: str, ext: str) -> bool:
    if mime.startswith("text/") or mime in TEXT_MIME or ext in TEXT_EXT:
        return True
    sample = data[:8192]
    if not sample or b"\x00" in sample:
        return False
    try:
        text = sample.decode("utf-8")
    except UnicodeDecodeError:
        return False
    printable = sum(ch.isprintable() or ch in "\n\r\t" for ch in text)
    return printable / len(text) > 0.9
