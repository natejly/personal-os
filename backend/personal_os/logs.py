"""Backend logging: a rotating file plus stderr, with secrets scrubbed before either sees a line.

Electron points PERSONAL_OS_LOG_DIR at the platform log folder (~/Library/Logs/Grain when packaged);
without it the logs live in <data dir>/logs. The redaction here is for credentials only: logs are for
supportability, so addresses and numbers stay readable.
"""
from __future__ import annotations

import logging
import os
import re
import sys
from collections import deque
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Iterable

MAX_BYTES = 5 * 1024 * 1024
BACKUPS = 5
LOG_NAME = "backend.log"
FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"

# Exact secret values this process knows (the sidecar token, the provider key). Matching a literal catches a
# secret in any shape, which a pattern cannot.
_secrets: set[str] = set()

_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Header-shaped: "Authorization: Bearer abc", "X-Personal-OS-Token: abc", with or without quotes.
    (re.compile(r"(?i)(authorization|x-personal-os-token|x-api-key|api-key|proxy-authorization|cookie|set-cookie)(['\"]?\s*[:=]\s*['\"]?)(?:bearer\s+|basic\s+)?[^\s'\",;}]+"), r"\1\2[redacted]"),
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}"), r"\1 [redacted]"),
    # key=value / "key": "value" for anything that names a secret (apiKey, googleClientSecret, access_token...).
    (re.compile(r"(?i)(['\"]?[\w-]*(?:api[_-]?key|secret|token|password|passwd|credential)[\w-]*['\"]?\s*[:=]\s*)(['\"]?)[^\s'\",;&}\]]+"), r"\1\2[redacted]"),
    # Query-string credentials in a URL.
    (re.compile(r"(?i)([?&](?:key|api_key|apikey|token|access_token|auth|code|client_secret)=)[^&\s'\"]+"), r"\1[redacted]"),
    # Well-known provider token shapes, bare in a message.
    (re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_-]{16,}\b"), "[redacted]"),
    (re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr|github_pat)_[A-Za-z0-9_]{16,}\b"), "[redacted]"),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"), "[redacted]"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[redacted]"),
    (re.compile(r"\bya29\.[A-Za-z0-9._-]{20,}\b"), "[redacted]"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"), "[redacted]"),
]


def register_secret(value: str | None) -> None:
    """Teach the filter an exact secret. Short values are ignored: they would shred ordinary words."""
    if value and len(value.strip()) >= 8:
        _secrets.add(value.strip())


def redact(text: str, extra: Iterable[str] = ()) -> str:
    if not text:
        return text
    for lit in sorted({*_secrets, *(e.strip() for e in extra if e and len(e.strip()) >= 8)}, key=len, reverse=True):
        text = text.replace(lit, "[redacted]")
    for pat, repl in _PATTERNS:
        text = pat.sub(repl, text)
    return text


class RedactingFilter(logging.Filter):
    """Scrubs the formatted message and any traceback before a handler writes the record."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = redact(record.getMessage())
            record.args = None
            if record.exc_info and not record.exc_text:
                record.exc_text = logging.Formatter().formatException(record.exc_info)
            if record.exc_text:
                record.exc_text = redact(record.exc_text)
            if record.stack_info:
                record.stack_info = redact(record.stack_info)
        except Exception:  # noqa: BLE001 - a logging fault must never become the app's fault
            record.msg, record.args = "[unprintable log record]", None
        return True


def log_dir() -> Path:
    env = os.environ.get("PERSONAL_OS_LOG_DIR")
    if env:
        return Path(env).expanduser()
    return Path(os.environ.get("PERSONAL_OS_DATA_DIR", "./data")).expanduser().resolve() / "logs"


def setup_logging(directory: Path | None = None, level: int = logging.INFO, stream: bool = True) -> Path | None:
    """Install the rotating file handler (5 MB x 5) and a stderr handler on the root logger, both redacting.

    Idempotent. Returns the log file, or None when the folder cannot be written (stderr logging still works:
    an unwritable log folder must not stop the backend from starting).
    """
    root = logging.getLogger()
    root.setLevel(level)
    ours = [h for h in root.handlers if getattr(h, "_grain", False)]
    if ours:
        return next((Path(h.baseFilename) for h in ours if isinstance(h, RotatingFileHandler)), None)
    flt = RedactingFilter()
    fmt = logging.Formatter(FORMAT)
    path: Path | None = None
    handlers: list[logging.Handler] = []
    if stream:
        handlers.append(logging.StreamHandler(sys.stderr))
    try:
        d = directory or log_dir()
        d.mkdir(parents=True, exist_ok=True)
        path = d / LOG_NAME
        handlers.append(RotatingFileHandler(path, maxBytes=MAX_BYTES, backupCount=BACKUPS, encoding="utf-8"))
    except OSError:
        path = None
    for h in handlers:
        h.setFormatter(fmt)
        h.addFilter(flt)
        h._grain = True  # type: ignore[attr-defined]
        root.addHandler(h)
    return path


def tail_lines(path: Path, n: int = 300) -> list[str]:
    """The last n lines of a log file, newest last; [] when it is missing or unreadable."""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            return [ln.rstrip("\n") for ln in deque(f, maxlen=n)]
    except OSError:
        return []
