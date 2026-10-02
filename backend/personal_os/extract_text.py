"""Extract plain text from uploaded files.

Every file is accepted. PDF and Word yield their text. Anything that decodes as
text is kept. A binary file is stored with a short note so the chat can still
find it by name.
"""
from __future__ import annotations

import io
import unicodedata
from collections.abc import Iterable
from pathlib import Path

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_INDEX_CHARS = 400_000
MAX_PDF_PAGES = 80
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


def extract_text(name: str, data: bytes, mime: str = "") -> str:
    ext = Path(name).suffix.lower()
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

            d = docx.Document(io.BytesIO(data))
            parts = (p.text for p in d.paragraphs if p.text.strip())
            return _bounded(parts, MAX_PDF_PAGES)
    except Exception:
        return None
    return None


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
