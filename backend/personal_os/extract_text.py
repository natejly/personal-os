"""Extract plain text from uploaded files."""
from __future__ import annotations

import io
from pathlib import Path

TEXT_EXT = {".txt", ".md", ".markdown", ".csv", ".json", ".yaml", ".yml", ".py", ".ts", ".tsx", ".js", ".html", ".css", ".log", ".rst", ".toml"}


def extract_text(name: str, data: bytes, mime: str = "") -> str:
    ext = Path(name).suffix.lower()
    if ext == ".pdf" or mime == "application/pdf":
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        return "\n\n".join((p.extract_text() or "") for p in reader.pages)
    if ext == ".docx":
        import docx

        d = docx.Document(io.BytesIO(data))
        return "\n\n".join(p.text for p in d.paragraphs if p.text.strip())
    if ext in TEXT_EXT or mime.startswith("text/"):
        return data.decode("utf-8", errors="replace")
    # Last resort: try utf-8
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as e:
        raise ValueError(f"Unsupported file type: {ext or mime or 'unknown'}") from e
