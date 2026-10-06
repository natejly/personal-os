"""Text Grain from your own phone: a two-way bridge over the Messages app on this Mac.

Inbound is a read-only poll of the Messages database (never held open, never written). Outbound is AppleScript,
with the message text and target handed over as `on run argv` items and never spliced into the script source.
Everything with side effects (the database path, the script runner, the chat/approval routes) is injected, so
nothing here imports the app and the tests never touch a real Messages database or a real send.

Privacy rule for logs: only masked handles, ROWIDs, lengths and outcomes. Never a body, never a full handle.
"""
from __future__ import annotations

import asyncio
import contextlib
import fcntl
import hashlib
import inspect
import logging
import random
import re
import sqlite3
import subprocess
import tempfile
import time
import unicodedata
import urllib.parse
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from . import redact

log = logging.getLogger("personal_os.imessage")

DEFAULT_CHAT_DB = str(Path("~/Library/Messages/chat.db").expanduser())
FDA_URL = "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles"
POLL_SECONDS = 2.5
LOCKED_RETRY_SECONDS = 30.0
FDA_RETRY_SECONDS = 15.0
MAX_BACKOFF = 60.0
BATCH = 200
ECHO_SECONDS = 180  # our own sends coming back as inbound text
STALE_SECONDS = 600  # on a restart, anything older than this is history, not an instruction
APPLE_EPOCH = 978307200  # 2001-01-01 in unix seconds
DEFAULT_MARKER = "🌾 "  # prefixed to everything Grain sends, so the self chat can tell its own voice from the user's
LEDGER_KEEP = 86400  # sent-ledger entries older than this are dropped
LEDGER_CAP = 500
LEDGER_HASH_WINDOW = 600  # a row whose text matches something we sent this recently is ours
MIRROR_WINDOW = 60  # the same phone text can land twice in the self chat (sent copy + received copy)

# The sources never change; only argv does. Item 1 is the target, item 2 the text.
SCRIPT_CHAT = """on run argv
tell application "Messages" to send (item 2 of argv) to chat id (item 1 of argv)
end run"""
SCRIPT_PARTICIPANT = """on run argv
tell application "Messages"
send (item 2 of argv) to participant (item 1 of argv) of (1st account whose service type = iMessage)
end tell
end run"""


class NeedsFullDiskAccess(Exception):
    """The Messages database cannot be opened: Full Disk Access is not granted (or the file is missing)."""


# ---------------------------------------------------------------- decoding

_CLASS_MARKERS = (b"NSString", b"NSMutableString")


def decode_attributed_body(blob: bytes | None) -> str | None:
    """The plain text out of a typedstream NSAttributedString, for messages whose `text` column is empty.

    The string sits after the NSString class marker, behind a `+` type byte and a length: one byte below 0x80,
    or 0x81 + 2 bytes / 0x82 + 4 bytes little-endian. None for anything that does not parse; never raises."""
    try:
        if not blob:
            return None
        blob = bytes(blob)
        hits = [i + len(m) for m in _CLASS_MARKERS if (i := blob.find(m)) >= 0]
        if not hits:
            return None
        start = min(hits)
        plus = blob.find(b"+", start, start + 40)
        if plus < 0:
            return None
        pos = plus + 1
        first = blob[pos]
        if first < 0x80:
            n, pos = first, pos + 1
        elif first == 0x81:
            n, pos = int.from_bytes(blob[pos + 1:pos + 3], "little"), pos + 3
        elif first == 0x82:
            n, pos = int.from_bytes(blob[pos + 1:pos + 5], "little"), pos + 5
        else:
            return None
        raw = blob[pos:pos + n]
        if len(raw) != n:
            return None
        text = raw.decode("utf-8", errors="replace").replace("￼", "").strip()
        return text or None
    except Exception:  # noqa: BLE001 - a malformed row must cost one message, not the poller
        return None


# ---------------------------------------------------------------- handles

_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
_PHONE_CHARS = re.compile(r"[\d\s+\-().]+")


def normalize_handle(s: Any) -> str | None:
    """One spelling per sender: lowercase email, or +E.164-ish digits (10 digits = US). None if it is neither."""
    if not isinstance(s, str):
        return None
    s = s.strip()
    if not s or s.startswith("-"):
        return None
    if "@" in s:
        s = s.lower()
        return s if _EMAIL.fullmatch(s) else None
    if not _PHONE_CHARS.fullmatch(s):
        return None
    digits = re.sub(r"\D", "", s)
    if len(digits) == 10:
        return "+1" + digits
    if len(digits) == 11 and digits[0] == "1":
        return "+" + digits
    return "+" + digits if 8 <= len(digits) <= 15 else None


def mask_handle(h: str | None) -> str:
    """Enough to tell two senders apart in a log, not enough to identify one."""
    if not h:
        return "?"
    if "@" in h:
        local, _, domain = h.partition("@")
        return f"{local[:1]}…@…{domain.rsplit('.', 1)[-1]}"
    return "…" + re.sub(r"\D", "", h)[-4:]


# ---------------------------------------------------------------- reading the Messages database

def _open_ro(path: str) -> sqlite3.Connection:
    """A fresh read-only connection. Opening is lazy in sqlite, so the PRAGMA is what surfaces a permission error."""
    try:
        c = sqlite3.connect(f"file:{urllib.parse.quote(path)}?mode=ro", uri=True, timeout=1.0)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA query_only=1").fetchall()
        return c
    except PermissionError as e:
        raise NeedsFullDiskAccess from e
    except sqlite3.OperationalError as e:
        if any(w in str(e).lower() for w in ("unable to open", "authorization denied", "not authorized", "permission")):
            raise NeedsFullDiskAccess from e
        raise


def max_rowid(path: str) -> int:
    c = _open_ro(path)
    try:
        return int(c.execute("SELECT COALESCE(MAX(ROWID), 0) FROM message").fetchone()[0])
    finally:
        c.close()


_FETCH = """
SELECT m.ROWID AS rowid, m.guid AS msg_guid, m.text AS text, m.attributedBody AS body, m.is_from_me AS from_me,
       COALESCE(m.associated_message_type, 0) AS assoc, COALESCE(m.cache_has_attachments, 0) AS attachments,
       m.date AS date, m.service AS service, c.service_name AS chat_service, h.id AS handle, c.guid AS chat_guid, c.style AS style, c.chat_identifier AS chat_ident,
       (SELECT COUNT(*) FROM chat_handle_join j WHERE j.chat_id = c.ROWID) AS participants
FROM message m
LEFT JOIN handle h ON h.ROWID = m.handle_id
LEFT JOIN chat_message_join cm ON cm.message_id = m.ROWID
LEFT JOIN chat c ON c.ROWID = cm.chat_id
WHERE m.ROWID > ?
GROUP BY m.ROWID
ORDER BY m.ROWID
LIMIT ?
"""


def fetch_since(path: str, rowid: int, limit: int = 200) -> list[dict[str, Any]]:
    """Messages after `rowid`, oldest first. Nothing is decoded here: an untrusted sender's blob is never parsed."""
    c = _open_ro(path)
    try:
        rows = c.execute(_FETCH, (rowid, limit)).fetchall()
    except sqlite3.OperationalError as e:
        if "authorization denied" in str(e).lower() or "not authorized" in str(e).lower():
            raise NeedsFullDiskAccess from e
        raise
    finally:
        c.close()
    return [{
        "rowid": r["rowid"], "msg_guid": r["msg_guid"], "text": r["text"] or "", "body": r["body"], "from_me": bool(r["from_me"]),
        "assoc": int(r["assoc"] or 0), "attachments": bool(r["attachments"]), "date": r["date"],
        "service": r["service"] or r["chat_service"], "handle": r["handle"],
        "chat_guid": r["chat_guid"], "chat_ident": r["chat_ident"],
        "group": r["style"] == 43 or int(r["participants"] or 0) > 1,
    } for r in rows]


def row_text(row: dict[str, Any]) -> str:
    """The message text, decoded from attributedBody only when the text column is empty. Called once a row is trusted."""
    text = row["text"] or decode_attributed_body(row["body"]) or ""
    return text.replace("\ufffc", "").strip()


_INVISIBLE = dict.fromkeys(map(ord, "\ufeff\u200b\u200c\u200d\u2060\ufffc"))  # BOM, zero-width, word joiner, object replacement


def clean_text(text: str) -> str:
    """NFC, invisibles gone, trimmed: one spelling of what a bubble says."""
    return unicodedata.normalize("NFC", text or "").translate(_INVISIBLE).strip()


def text_hash(text: str, marker: str = DEFAULT_MARKER) -> str:
    """Marker-insensitive: Grain's "🌾 hello" and a row that lost its marker both hash like "hello"."""
    t = clean_text(text)
    for m in sorted({marker.strip(), DEFAULT_MARKER.strip()} - {""}, key=len, reverse=True):
        if t.startswith(m):
            t = t[len(m):]
            break
    return hashlib.sha256(t.strip().encode()).hexdigest()[:32]


def find_own_rows(path: str, guid: str, limit: int = 30) -> list[dict[str, Any]]:
    """The newest `limit` messages we sent (is_from_me) in one chat, oldest first. Our own rows, so decoding is fine."""
    c = _open_ro(path)
    try:
        rows = c.execute(
            "SELECT m.ROWID AS rowid, m.guid AS guid, m.text AS text, m.attributedBody AS body, m.date AS date FROM message m "
            "JOIN chat_message_join cm ON cm.message_id = m.ROWID JOIN chat ch ON ch.ROWID = cm.chat_id "
            "WHERE ch.guid = ? AND m.is_from_me = 1 ORDER BY m.ROWID DESC LIMIT ?", (guid, limit)).fetchall()
    finally:
        c.close()
    return [{"rowid": r["rowid"], "guid": r["guid"], "text": r["text"] or "", "body": r["body"], "date": r["date"]}
            for r in reversed(rows)]


_ACCOUNT_PREFIX = re.compile(r"^[EePp]:")


def self_chat_candidates(path: str, own: set[str]) -> list[dict[str, Any]]:
    """1:1 iMessage chats whose other party is one of our own addresses (the allowlist, or an account address found in
    the database): the note-to-self thread. Never reads a message body. Best first; exactly one is flagged `best`."""
    c = _open_ro(path)
    try:
        def cols(t: str) -> set[str]:
            return {r["name"] for r in c.execute(f"PRAGMA table_info({t})")}  # older macOS lacks some columns

        acct: set[str] = set()
        for table, col in (("chat", "account_login"), ("message", "account"), ("message", "destination_caller_id")):
            if col in cols(table):
                for r in c.execute(f"SELECT DISTINCT v FROM (SELECT {col} AS v FROM {table} WHERE {col} IS NOT NULL "
                                   "ORDER BY ROWID DESC LIMIT 2000)"):
                    if isinstance(r["v"], str) and (h := normalize_handle(_ACCOUNT_PREFIX.sub("", r["v"].strip()))):
                        acct.add(h)
        mine = set(own) | acct
        out: list[dict[str, Any]] = []
        for ch in c.execute("SELECT ROWID AS id, guid, chat_identifier AS ident, style, service_name AS svc, "
                            "(SELECT COUNT(*) FROM chat_handle_join j WHERE j.chat_id = chat.ROWID) AS n FROM chat").fetchall():
            h = normalize_handle(ch["ident"])
            if not h or h not in mine or ch["style"] == 43 or int(ch["n"] or 0) > 1 or str(ch["svc"] or "").lower() != "imessage":
                continue
            last = c.execute("SELECT MAX(m.date) FROM chat_message_join cm JOIN message m ON m.ROWID = cm.message_id "
                             "WHERE cm.chat_id = ?", (ch["id"],)).fetchone()[0]
            ts = apple_to_unix(last)
            out.append({"guid": ch["guid"], "handle": mask_handle(h), "last_activity": int(ts) if ts else None,
                        "source": "account" if h in acct else "allowlist", "best": False, "_rank": (h in acct, h in own)})
    finally:
        c.close()
    # The account's own address (and listed too) beats another listed person's 1:1 chat; then the most recent.
    out.sort(key=lambda d: (*d.pop("_rank"), d["last_activity"] or 0), reverse=True)
    if out:
        out[0]["best"] = True
    return out


def apple_to_unix(date: Any) -> float | None:
    """message.date: seconds since 2001 on old macOS, nanoseconds on new. 0/None means unknown."""
    try:
        d = float(date)
    except (TypeError, ValueError):
        return None
    if d <= 0:
        return None
    return (d / 1e9 if d > 1e11 else d) + APPLE_EPOCH


# ---------------------------------------------------------------- formatting

_FENCE = re.compile(r"^\s*(```|~~~)")


def to_plain(md: str) -> str:
    """Markdown to what a text bubble can show: markers gone, content kept."""
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
        line = re.sub(r"!\[[^\]]*\]\(([^)\s]+)[^)]*\)", r"\1", line)
        line = re.sub(r"\[([^\]]+)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)",
                      lambda m: m.group(2) if m.group(1).strip() == m.group(2) else f"{m.group(1)} ({m.group(2)})", line)
        line = re.sub(r"\*\*(.+?)\*\*|__(.+?)__", lambda m: m.group(1) or m.group(2), line)
        line = re.sub(r"~~(.+?)~~", r"\1", line)
        line = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"\1", line)
        line = re.sub(r"(?<![\w])_(?!\s)(.+?)(?<!\s)_(?![\w])", r"\1", line)
        line = re.sub(r"`([^`]+)`", r"\1", line)
        out.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


TRUNCATED = "…full reply in Grain"
_CUTS = ("\n\n", "\n", ". ", "! ", "? ", " ")


def _cut(s: str, size: int) -> int:
    window = s[:size]
    for sep in _CUTS:
        i = window.rfind(sep)
        if i > size // 3:
            return i + len(sep)
    return size


def split_reply(text: str, size: int = 1500, max_parts: int = 4) -> list[str]:
    """Bubbles of at most `size` characters, prefixed (i/n) when there is more than one."""
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= size:
        return [text]
    room = size - 8  # the "(i/n) " prefix
    parts, rest = [], text
    while len(rest) > room:
        cut = _cut(rest, room)
        parts.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip()
    if rest:
        parts.append(rest)
    if len(parts) > max_parts:
        parts = parts[:max_parts]
        last = parts[-1]
        if len(last) + len(TRUNCATED) + 1 > room:
            last = last[:room - len(TRUNCATED) - 1].rstrip()
        parts[-1] = f"{last}\n{TRUNCATED}"
    return [f"({i}/{len(parts)}) {p}" for i, p in enumerate(parts, 1)]


# ---------------------------------------------------------------- commands and limits

_COMMAND = re.compile(r"(status|stop|new|help|approve|yes|y|deny|no|n)(?:\s+(\d{1,6}))?\s*[.!]?", re.I)
_BARE_ANSWERS = {"yes", "y", "no", "n"}


def parse_command(text: str) -> tuple[str, str | None] | None:
    """A whole message that is one command word (optionally with an approval code). Anything else is a normal turn."""
    m = _COMMAND.fullmatch((text or "").strip())
    if not m:
        return None
    word, code = m.group(1).lower(), m.group(2)
    if word in ("approve", "yes", "y"):
        return "approve", code
    if word in ("deny", "no", "n"):
        return "deny", code
    return (word, None) if code is None else None


class RateLimiter:
    """Sliding window per key. `limited_first` is the one time to say so; after that, silence."""

    def __init__(self, limit: int = 10, window: float = 60.0, clock: Callable[[], float] = time.monotonic) -> None:
        self.limit, self.window, self.clock = limit, window, clock
        self._hits: dict[str, deque[float]] = {}
        self._warned: set[str] = set()

    def check(self, key: str) -> str:
        now = self.clock()
        q = self._hits.setdefault(key, deque())
        while q and now - q[0] >= self.window:
            q.popleft()
        if len(q) >= self.limit:
            if key in self._warned:
                return "limited"
            self._warned.add(key)
            return "limited_first"
        self._warned.discard(key)
        q.append(now)
        return "ok"


# ---------------------------------------------------------------- sending

async def default_runner(argv: list[str]) -> tuple[int, str]:
    """Run argv without a shell. A hung Messages app costs one send, not the poller."""
    proc = await asyncio.create_subprocess_exec(*argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), 20)
    except asyncio.TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        await proc.wait()
        return 124, "timeout"
    return proc.returncode or 0, out.decode("utf-8", "replace")[:500]


def open_full_disk_access() -> bool:
    try:
        return subprocess.run(["open", FDA_URL], check=False, capture_output=True, timeout=10).returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def lock_path(chat_db_path: str) -> Path:
    """One poller per Messages database per user, whichever data directory a backend runs on (TMPDIR is per user)."""
    return Path(tempfile.gettempdir()) / f"grain-imessage-{hashlib.sha1(chat_db_path.encode()).hexdigest()[:12]}.lock"


def _argv(script: str, target: str, text: str) -> list[str]:
    # A leading "-" in an item could be read as an osascript option.
    return ["osascript", "-e", script, target, " " + text if text.startswith("-") else text]


# ---------------------------------------------------------------- the bridge

APP_ONLY_TOOLS = frozenset({"propose_plan", "ask_user", "desk_ask"})  # a plan or a question is answered in the app


@dataclass
class Deps:
    get_settings: Callable[[], dict[str, Any]]
    load_state: Callable[[], dict[str, Any]]
    save_state: Callable[[dict[str, Any]], None]
    chat_db_path: str
    runner: Callable[[list[str]], Awaitable[tuple[int, str]]]
    start_turn: Callable[[str, str], Any]  # (conversation id, text) -> {"run_id": ...}; sync or async
    stop: Callable[[str], bool]
    decide: Callable[[str, str], Any]  # (call id, "allow" | "deny") -> {"live": bool}; sync or async, raises if it cannot be recorded
    pending_approvals: Callable[[], list[dict[str, Any]]]
    is_live: Callable[[str], bool]  # a run in this process is waiting on that call id
    active_runs: Callable[[], list[dict[str, Any]]]
    create_texts_conversation: Callable[[], str]
    conversation_exists: Callable[[str], bool]
    message_text: Callable[[str], str | None]
    conversation_title: Callable[[str], str | None]
    app_only_tools: frozenset[str] = APP_ONLY_TOOLS
    lock_path: Path | None = None  # tests; the default is keyed on the Messages database (lock_path())


@dataclass
class Target:
    guid: str | None
    handle: str | None
    conv_id: str | None = None


@dataclass
class _Memo:
    seen: dict[Any, Any] = field(default_factory=dict)
    cap: int = 300

    def add(self, key: Any, val: Any = True) -> None:
        self.seen[key] = val
        while len(self.seen) > self.cap:
            self.seen.pop(next(iter(self.seen)))


@dataclass
class _Texted:
    code: int
    needs_code: bool  # forced by untrusted content, or the preview was shortened: a bare yes is not enough
    at: float  # when we started texting it


async def _maybe(v: Any) -> Any:
    return await v if inspect.isawaitable(v) else v


def _one_line(v: Any, n: int) -> str:
    s = v if isinstance(v, str) else repr(v)
    s = " ".join(s.split())
    return s if len(s) <= n else s[:n - 1] + "…"


def _args_preview(args: Any, n: int = 200, each: int = 80) -> tuple[str, bool]:
    """(text, shortened). Each pair is scrubbed whole before it is cut, so a cut can never expose half a secret."""
    shortened = False
    pairs = []
    for k, v in (args.items() if isinstance(args, dict) else [("args", args)]):
        flat = " ".join(redact.scrub_secrets(f"{k}={v if isinstance(v, str) else repr(v)}").split())
        if len(flat) > each:
            flat, shortened = flat[:each - 1] + "…", True
        pairs.append(flat)
    text = ", ".join(pairs)
    if len(text) > n:
        text, shortened = text[:n - 1] + "…", True
    return text, shortened


HELP = ("Commands: status, stop, new (fresh conversation), yes / no (answer an approval; add its code if several "
        "wait, e.g. yes 2), help. Anything else goes to Grain as a message.")
_ANSWER_WORD = re.compile(r"(yes|no|y|n|approve|deny)\b", re.I)
SLOW_DOWN = "Slow down: too many messages in the last minute. I'll pick up again shortly."
RUN_ERROR = "The run ended with an error — details are in Grain."


class IMessageBridge:
    LOOP_STREAK = 5  # more than this many accepted self-chat texts in a row (gaps under LOOP_GAP) within LOOP_WINDOW: pause
    LOOP_GAP = 10.0
    LOOP_WINDOW = 60.0
    record_delays: tuple[float, ...] = (1.0, 2.0, 4.0)  # when to look for our own sent row, to learn its ROWID/guid

    def __init__(self, deps: Deps, *, poll_seconds: float = POLL_SECONDS, send_gap: float = 1.0,
                 rate_limit: int = 10) -> None:
        self.deps = deps
        self.poll_seconds = poll_seconds
        self.send_gap = send_gap
        self.limiter = RateLimiter(rate_limit)
        self.status_code = "off"
        self.last_poll_at: float | None = None
        self.last_error: str | None = None
        self._fda_ok: bool | None = None
        self._task: asyncio.Task[None] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock_fd: Any = None
        self._fresh = False
        # Everything older than this is history, not an instruction, until a poll comes back short of a full batch.
        self._stale_before: float | None = time.time() - STALE_SECONDS
        self._ignored = 0  # counted per message, written once per poll
        self._tasks: set[asyncio.Task[Any]] = set()
        self._send_lock = asyncio.Lock()  # one send at a time keeps replies in order and the pacing honest
        self._approval_lock = asyncio.Lock()
        self._sent: deque[float] = deque()
        self._echo: deque[tuple[float, str]] = deque(maxlen=20)
        self._last_send = 0.0
        self._runs = _Memo()  # run_id -> Target: where its reply goes
        self._answered = _Memo()  # run ids whose end has been handled
        self._muted = _Memo()  # run ids stopped by a text: the "Stopped." reply already went out
        self._texted = _Memo()  # call_id -> _Texted
        self._told = _Memo()  # call ids of plans and questions we pointed at the app
        self._codes: dict[int, str] = {}
        self._code_seq = random.randint(10, 80)  # a stale "yes 3" from before a restart must not hit a new card
        self._streak: deque[float] = deque()  # monotonic times of accepted self-chat texts: the loop guard
        self._self_seen: deque[tuple[float, str, bool]] = deque(maxlen=20)  # (monotonic, hash, from_me) of accepted self-chat rows

    # ---- lifecycle
    def _settings(self) -> dict[str, Any]:
        return self.deps.get_settings()

    def _enabled(self) -> bool:
        return bool(self._settings().get("imessageEnabled"))

    async def start(self, fresh: bool = False) -> None:
        """`fresh` is the toggle going on: history is never replayed, the cursor starts at the newest row."""
        if self._task and not self._task.done():
            return
        self._loop = asyncio.get_running_loop()
        self._fresh = fresh
        if fresh:  # toggling texting off and on is how a loop-guard pause is cleared
            if self._paused():
                self._save(loopPaused=None)
            self._streak.clear()
            self._self_seen.clear()
        self._stale_before = time.time() - STALE_SECONDS
        self.status_code = "running"
        self._task = asyncio.create_task(self._run(), name="imessage-poller")

    async def poll_once(self) -> None:
        """One poll under the lock, without the loop around it: what the tests drive."""
        self._loop = asyncio.get_running_loop()
        if not self._acquire():
            self.status_code = "locked"
            return
        await self._poll()
        self.status_code = "running"

    async def stop(self) -> None:
        t, self._task = self._task, None
        if t:
            t.cancel()
            await asyncio.gather(t, return_exceptions=True)
        for t in list(self._tasks):
            t.cancel()
        self._release()
        self.status_code = "off"

    async def reconcile(self) -> None:
        if self._enabled():
            if not (self._task and not self._task.done()):
                await self.start(fresh=True)
        else:
            await self.stop()

    def _acquire(self) -> bool:
        if self._lock_fd is not None:
            return True
        fd = None
        try:
            fd = open(self.deps.lock_path or lock_path(self.deps.chat_db_path), "a+")  # noqa: SIM115 - held for as long as we poll
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            if fd:
                fd.close()
            return False
        self._lock_fd = fd
        return True

    def _release(self) -> None:
        if self._lock_fd is not None:
            with contextlib.suppress(OSError):
                fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
            self._lock_fd.close()
            self._lock_fd = None

    async def _run(self) -> None:
        backoff = 0.0
        try:
            while self._enabled():
                if not self._acquire():
                    self.status_code = "locked"
                    await asyncio.sleep(LOCKED_RETRY_SECONDS)
                    continue
                delay = self.poll_seconds
                try:
                    await self._poll()
                    self.status_code, self.last_error, backoff = "running", None, 0.0
                    self._fda_ok = True
                except NeedsFullDiskAccess:
                    self.status_code, self._fda_ok = "needs_full_disk_access", False
                    delay = FDA_RETRY_SECONDS
                except Exception as e:  # noqa: BLE001 - back off and try again; the poller never dies
                    self.status_code, self.last_error = "error", f"{type(e).__name__}: {str(e)[:160]}"
                    backoff = min(MAX_BACKOFF, max(self.poll_seconds, backoff * 2))
                    delay = backoff
                    log.warning("imessage poll failed: %s", type(e).__name__)
                self.last_poll_at = time.time()
                await asyncio.sleep(delay)
        finally:
            self._release()
            if self.status_code != "off" and not self._enabled():
                self.status_code = "off"

    # ---- state
    def _save(self, **patch: Any) -> dict[str, Any]:
        st = dict(self.deps.load_state() or {})
        st.update(patch)
        self.deps.save_state(st)
        return st

    def _people(self) -> list[str]:
        return [h for h in (normalize_handle(e) for e in self._settings().get("imessageHandles") or []) if h]

    def _allow(self) -> tuple[set[str], set[str]]:
        """(sender handles, group chat ids) from the allowlist; an entry that is not a handle names a group."""
        people: set[str] = set()
        groups: set[str] = set()
        for e in self._settings().get("imessageHandles") or []:
            h = normalize_handle(e)
            (people.add(h) if h else groups.add(str(e).strip()))
        return people, groups

    def _marker(self) -> str:
        m = self._settings().get("imessageReplyMarker")
        return m if isinstance(m, str) and m.strip() else DEFAULT_MARKER

    def _paused(self) -> bool:
        return bool((self.deps.load_state() or {}).get("loopPaused"))

    def _self_guid(self) -> str | None:
        g = self._settings().get("imessageSelfChatGuid")
        return g.strip() if isinstance(g, str) and g.strip() else None

    def _is_self(self, row: dict[str, Any]) -> bool:
        sg = self._self_guid()
        return bool(sg) and row["chat_guid"] == sg and not row["group"]

    def _self_ident(self, ident: Any = None) -> str | None:
        """Our own address in the self chat: its identifier, else the guid's tail, else the first allowlisted handle."""
        sg = self._self_guid()
        h = normalize_handle(ident) or (normalize_handle(sg.rsplit(";", 1)[-1]) if sg else None)
        return h or next(iter(self._people()), None)

    def self_chats(self) -> dict[str, Any]:
        """Blocking (reads the Messages database): the route runs it in a thread."""
        try:
            return {"chats": self_chat_candidates(self.deps.chat_db_path, set(self._people()))}
        except NeedsFullDiskAccess:
            return {"error": "needs_full_disk_access"}

    def _ledger_add(self, h: str, chat: str | None, at: float) -> None:
        sent = [e for e in (self.deps.load_state() or {}).get("sent") or [] if isinstance(e, dict) and e.get("at", 0) >= at - LEDGER_KEEP]
        sent.append({"h": h, "chat": chat, "at": at, "rowid": None, "guid": None})
        self._save(sent=sent[-LEDGER_CAP:])

    def _link_rows(self, rows: list[dict[str, Any]], guid: str, since: float) -> int:
        """Attach the ROWID/guid of rows we sent to their ledger entries (newest unrecorded entry with the same hash)."""
        sent = [dict(e) for e in (self.deps.load_state() or {}).get("sent") or [] if isinstance(e, dict)]
        known = {e.get("rowid") for e in sent if e.get("rowid") is not None}
        marker, n = self._marker(), 0
        for r in rows:
            ts = apple_to_unix(r["date"])
            if r["rowid"] in known or (ts is not None and ts < since - 5):
                continue
            h = text_hash(row_text(r), marker)
            e = next((e for e in reversed(sent) if e.get("h") == h and e.get("rowid") is None and e.get("chat") == guid), None)
            if e:
                e["rowid"], e["guid"] = r["rowid"], r["guid"]
                known.add(r["rowid"])
                n += 1
        if n:
            self._save(sent=sent)
        return n

    async def _record_rows(self, guid: str, since: float) -> None:
        """Best effort, off the send lock: Messages writes the row a moment after osascript returns."""
        for d in self.record_delays:
            await asyncio.sleep(d)
            try:
                rows = await asyncio.to_thread(find_own_rows, self.deps.chat_db_path, guid)
                if self._link_rows(rows, guid, since):
                    return
            except Exception:  # noqa: BLE001 - the hash match still covers an unrecorded row
                log.debug("imessage ledger lookup failed", exc_info=True)

    def _self_skip(self, row: dict[str, Any], text: str, sg: str) -> str | None:
        """Why a self-chat row is not the user speaking: Grain's own marker, a ledger hit, or the mirrored copy of a text."""
        if clean_text(text).startswith(self._marker().strip()):
            return "marker"
        st = self.deps.load_state() or {}
        sent = [e for e in st.get("sent") or [] if isinstance(e, dict)]
        if (row.get("msg_guid") and any(e.get("guid") == row["msg_guid"] for e in sent)) or any(e.get("rowid") == row["rowid"] for e in sent):
            return "ledger"
        h = text_hash(text, self._marker())
        now = time.time()
        if text and any(e.get("h") == h and now - float(e.get("at") or 0) <= LEDGER_HASH_WINDOW and e.get("chat") in (None, sg) for e in sent):
            return "ledger"
        mono = time.monotonic()
        if text and any(mono - t <= MIRROR_WINDOW and hh == h and fm != row["from_me"] for t, hh, fm in self._self_seen):
            return "mirror"
        return None

    def _loop_trips(self) -> bool:
        now = time.monotonic()
        if self._streak and now - self._streak[-1] >= self.LOOP_GAP:
            self._streak.clear()
        self._streak.append(now)
        while now - self._streak[0] > self.LOOP_WINDOW:
            self._streak.popleft()
        return len(self._streak) > self.LOOP_STREAK

    def _peek_target(self) -> str | None:
        explicit = self._settings().get("imessageConversationId")
        if explicit and self.deps.conversation_exists(explicit):
            return explicit
        cid = (self.deps.load_state() or {}).get("textsConversationId")
        return cid if cid and self.deps.conversation_exists(cid) else None

    async def _target(self) -> str:
        cid = self._peek_target()
        if cid:
            return cid
        cid = await _maybe(self.deps.create_texts_conversation())
        self._save(textsConversationId=cid)
        return cid

    # ---- polling
    async def _poll(self) -> None:
        path = self.deps.chat_db_path
        saved = (self.deps.load_state() or {}).get("cursor")
        newest = await asyncio.to_thread(max_rowid, path)  # a failure here leaves _fresh and the age filter as they were
        if self._fresh or saved is None or newest < int(saved):  # toggled on, never polled, or the database was rebuilt
            self._save(cursor=newest)
            self._fresh = False
            self._stale_before = None
            return
        cursor = int(saved)
        rows = await asyncio.to_thread(fetch_since, path, cursor, BATCH)
        stale_before = self._stale_before
        if len(rows) < BATCH:
            self._stale_before = None  # caught up: from here on, everything is live
        try:
            for row in rows:
                if row["chat_guid"] is None and (not row["from_me"] or self._self_guid()):  # self chat: ours can be input too
                    ts = apple_to_unix(row["date"])
                    if ts is not None and -60 <= time.time() - ts < 30:  # the chat join may land a beat after the message
                        break
                    cursor = row["rowid"]  # too old, undated or implausibly new: it has no chat to answer in
                    self._save(cursor=cursor)
                    continue
                try:
                    await self._handle(row, stale_before)
                except Exception as e:  # noqa: BLE001 - one bad message must not stall the cursor
                    log.warning("imessage rowid=%s failed: %s", row["rowid"], type(e).__name__)
                    await self._apologize(row)
                cursor = row["rowid"]
                self._save(cursor=cursor)
        finally:
            self._flush_ignored()

    def _ignore(self, rid: int, sender: str | None, why: str, count: bool = True) -> None:
        """`count`: an unknown sender's text, the number Settings shows. Our own replies and paused rows are only logged."""
        self._ignored += count
        log.info("imessage rowid=%s from=%s ignored (%s)", rid, mask_handle(sender), why)

    def _flush_ignored(self) -> None:
        if self._ignored:
            st = self.deps.load_state() or {}
            self._save(ignoredCount=int(st.get("ignoredCount") or 0) + self._ignored, lastIgnoredAt=time.time())
            self._ignored = 0

    async def _apologize(self, row: dict[str, Any]) -> None:
        """Best effort: tell an allowlisted sender their message was not taken."""
        try:
            if self._is_self(row):
                sender = self._self_ident(row["chat_ident"])
                if not row["from_me"] and normalize_handle(row["handle"]) not in self._people():
                    return
                if clean_text(row_text(row)).startswith(self._marker().strip()):  # never apologize to our own text
                    return
            else:
                sender = normalize_handle(row["handle"])
                if row["from_me"] or sender not in self._people():
                    return
            await self._send(Target(row["chat_guid"], None if row["group"] else sender),
                             "Grain couldn't take that message — try again.")
        except Exception:  # noqa: BLE001
            log.debug("imessage apology failed", exc_info=True)

    def _is_echo(self, text: str) -> bool:
        now, m = time.time(), self._marker().strip()
        bare = text[len(m):].strip() if text.startswith(m) else text  # the copy that comes back carries the marker
        return any(now - at < ECHO_SECONDS and sent in (text, bare) for at, sent in self._echo)

    async def _handle(self, row: dict[str, Any], stale_before: float | None) -> None:
        rid = row["rowid"]
        if self._paused():  # loop guard: nothing is heard until texting is switched off and on; the cursor still moves
            self._ignore(rid, None, "paused", count=False)
            return
        mine = self._is_self(row)  # the confirmed note-to-self chat: the one place our own sends can be instructions
        if row["from_me"] and not mine:  # our own sends and the user's other devices: never an instruction
            return
        if row["assoc"] != 0:  # tapbacks and reactions
            return
        if stale_before is not None and (ts := apple_to_unix(row["date"])) is not None and ts < stale_before:
            return
        sender = self._self_ident(row["chat_ident"]) if mine else normalize_handle(row["handle"])
        if str(row["service"] or "").lower() != "imessage":  # an SMS sender id can be spoofed
            self._ignore(rid, sender, "not iMessage")
            return
        people, groups = self._allow()
        if mine:
            if not row["from_me"] and (h := normalize_handle(row["handle"])) not in people:
                self._ignore(rid, sender, "not allowlisted", count=h != sender)  # our own address unlisted: not a stranger
                return
            text = row_text(row)  # past the allowlist: the blob is ours or an allowlisted sender's
            try:
                why = self._self_skip(row, text, row["chat_guid"])
            except Exception:  # noqa: BLE001 - when in doubt it is not the user: an apology here could feed itself
                why = "skip check failed"
            if why:
                self._ignore(rid, sender, why, count=False)
                return
            if self._loop_trips():
                self._save(loopPaused=time.time())
                log.warning("imessage loop guard tripped: paused (rowid=%s from=%s)", rid, mask_handle(sender))
                self._ignore(rid, sender, "paused", count=False)
                return
            if text:
                self._self_seen.append((time.monotonic(), text_hash(text, self._marker()), bool(row["from_me"])))
        else:
            if sender not in people or (row["group"] and not {row["chat_guid"], row["chat_ident"]} & groups):
                self._ignore(rid, sender, "not allowlisted")
                return
            text = row_text(row)
            if text and self._is_echo(text):
                self._ignore(rid, sender, "echo")
                return
        where = Target(row["chat_guid"], None if row["group"] else sender)
        sent_at = apple_to_unix(row["date"])
        cmd = parse_command(text) if text else None
        if cmd and cmd[0] in ("approve", "deny") and cmd[1] is None and text.lower().rstrip(".!") in _BARE_ANSWERS \
                and not self._live_texted():
            cmd = None  # a plain "yes" with nothing waiting is the user answering Grain's own question
        if not (cmd and cmd[0] in ("stop", "deny")):  # stopping and refusing are never rate limited
            verdict = self.limiter.check(sender or "")
            if verdict != "ok":
                log.info("imessage rowid=%s from=%s %s", rid, mask_handle(sender), verdict)
                if verdict == "limited_first":
                    await self._send(where, SLOW_DOWN)
                return
        if not row["group"]:
            home = {"guid": row["chat_guid"], "handle": sender}
            if (self.deps.load_state() or {}).get("homeChat") != home:
                self._save(homeChat=home)
        if not text:
            if row["attachments"]:
                await self._send(where, "Attachments aren't supported yet — send text.")
            return
        if cmd:
            log.info("imessage rowid=%s from=%s command=%s", rid, mask_handle(sender), cmd[0])
            await self._command(cmd, where, sent_at)
            return
        if _ANSWER_WORD.match(text) and self._live_texted():  # steering would deny the card; make them answer it
            await self._send(where, "Reply yes or no (or yes <code>) to the approval first.")
            return
        try:
            conv = await self._target()
            res = await _maybe(self.deps.start_turn(conv, text))
        except Exception as e:  # noqa: BLE001
            log.warning("imessage rowid=%s start failed: %s", rid, type(e).__name__)
            await self._send(where, "Grain couldn't take that message right now. Try again in a moment.")
            return
        where.conv_id = conv
        self._runs.add(res["run_id"], where)
        log.info("imessage rowid=%s from=%s started run len=%s", rid, mask_handle(sender), len(text))

    # ---- commands
    def _live_texted(self) -> list[tuple[int, str, _Texted]]:
        """(code, call_id, info) of approvals we texted that are still pending and still waited on by a run."""
        pending = {a["call_id"] for a in self.deps.pending_approvals()}
        return [(code, cid, self._texted.seen[cid]) for code, cid in sorted(self._codes.items())
                if cid in pending and cid in self._texted.seen and self.deps.is_live(cid)]

    async def _command(self, cmd: tuple[str, str | None], where: Target, sent_at: float | None = None) -> None:
        word, code = cmd
        if word == "help":
            await self._send(where, HELP)
        elif word == "status":
            await self._send(where, self._status_line())
        elif word == "stop":
            conv = self._peek_target()
            ids = [r["run_id"] for r in self.deps.active_runs() if r.get("conversation_id") == conv] if conv else []
            stopped = bool(conv) and bool(self.deps.stop(conv))
            for i in ids:
                self._muted.add(i)
            await self._send(where, "Stopped." if stopped else "Nothing is running.")
        elif word == "new":
            cid = await _maybe(self.deps.create_texts_conversation())
            self._save(textsConversationId=cid)
            pinned = self._settings().get("imessageConversationId")
            note = (" Settings still pins texts to one conversation, so that one keeps getting your messages until you "
                    "clear it.") if pinned and self.deps.conversation_exists(pinned) else ""
            await self._send(where, "Started a new Texts conversation." + note)
        else:
            await self._answer(word, code, where, sent_at)

    def _status_line(self) -> str:
        runs = self.deps.active_runs()
        conv = self._peek_target()
        here = next((r for r in runs if r.get("conversation_id") == conv), None) if conv else None
        parts = [f"{len(runs)} run{'s' if len(runs) != 1 else ''} active"]
        if here:
            secs = int(max(0, time.time() - float(here.get("started_at") or time.time())))
            parts.append(f"this chat: {here.get('status', 'running')} for {secs // 60}m {secs % 60:02d}s")
        else:
            parts.append("this chat: idle")
        n = len(self._live_texted())
        parts.append(f"{n} approval{'s' if n != 1 else ''} waiting")
        return ". ".join(p[0].upper() + p[1:] for p in parts) + "."

    async def _answer(self, word: str, code: str | None, where: Target, sent_at: float | None) -> None:
        decision = "allow" if word == "approve" else "deny"
        live = self._live_texted()
        if code is not None:
            hit = next((x for x in live if x[0] == int(code)), None)
            if not hit:
                await self._send(where, f"No pending approval with code {code}.")
                return
        elif not live:
            await self._send(where, "No approvals waiting.")
            return
        elif len(live) > 1:
            codes = ", ".join(str(x[0]) for x in live)
            await self._send(where, f"{len(live)} approvals are waiting. Reply yes <code> or no <code>. Codes: {codes}")
            return
        else:
            hit = live[0]
            if sent_at is not None and sent_at < hit[2].at:  # typed before the request existed: it answers something else
                await self._send(where, "Reply again: that answer was sent before the approval request.")
                return
            if hit[2].needs_code and decision == "allow":
                await self._send(where, f"That one needs its code to approve: reply yes {hit[0]}.")
                return
        try:
            res = await _maybe(self.deps.decide(hit[1], decision))
        except Exception as e:  # noqa: BLE001
            log.info("imessage decide failed: %s", type(e).__name__)
            await self._send(where, "That approval is no longer pending.")
            return
        if isinstance(res, dict) and res.get("live") is False:
            await self._send(where, "That approval is no longer waiting (the run ended).")
        else:
            await self._send(where, "Approved." if decision == "allow" else "Denied.")

    # ---- sending
    def _home(self) -> Target | None:
        """The 1:1 chat we last heard from, while that handle is still allowed; otherwise the first allowed handle."""
        if sg := self._self_guid():
            return Target(sg, self._self_ident())
        people = self._people()
        h = (self.deps.load_state() or {}).get("homeChat")
        if isinstance(h, dict) and h.get("handle") in people:
            return Target(h.get("guid"), h["handle"])
        return Target(None, people[0]) if people else None

    async def _send(self, to: Target, text: str) -> bool:
        if self._paused():
            log.info("imessage send to=%s dropped (loop guard) len=%s", mask_handle(to.handle), len(text))
            return False
        marker = self._marker()
        out = marker + text
        async with self._send_lock:
            now = time.monotonic()
            while self._sent and now - self._sent[0] >= 60:
                self._sent.popleft()
            if len(self._sent) >= 20:
                log.info("imessage send to=%s dropped (pacing) len=%s", mask_handle(to.handle), len(text))
                return False
            wait = self._last_send + self.send_gap - now
            if wait > 0:
                await asyncio.sleep(wait)
            ok = False
            at = time.time()
            self._ledger_add(text_hash(text, marker), to.guid, at)  # before the send: the row can land before osascript returns
            if to.guid:
                ok = await self._osascript(_argv(SCRIPT_CHAT, to.guid, out))
            if not ok and to.handle:
                ok = await self._osascript(_argv(SCRIPT_PARTICIPANT, to.handle, out))
            self._last_send = time.monotonic()
            self._sent.append(self._last_send)
            self._echo.append((time.time(), text.strip()))
            log.info("imessage send to=%s len=%s ok=%s", mask_handle(to.handle), len(text), ok)
            if ok and to.guid:
                self._spawn(self._record_rows(to.guid, at))
            return ok

    async def _osascript(self, argv: list[str]) -> bool:
        try:
            rc, _ = await self.deps.runner(argv)  # the output can echo the target, so it is never logged
        except Exception as e:  # noqa: BLE001
            log.info("imessage runner failed: %s", type(e).__name__)
            return False
        return rc == 0

    async def _send_all(self, to: Target, text: str) -> None:
        for part in split_reply(text):
            await self._send(to, part)

    # ---- run events
    def _spawn(self, coro: Awaitable[Any]) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            getattr(coro, "close", lambda: None)()
            return
        try:
            inside = asyncio.get_running_loop() is loop
        except RuntimeError:
            inside = False
        if not inside:
            loop.call_soon_threadsafe(self._spawn, coro)
            return
        t = loop.create_task(coro)  # type: ignore[arg-type]
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)

    def on_run_change(self, run: Any) -> None:
        try:
            self._on_run_change(run)
        except Exception:  # noqa: BLE001 - a listener must never break a run
            log.debug("imessage run listener failed", exc_info=True)

    def _on_run_change(self, run: Any) -> None:
        if self._lock_fd is None:  # not the poller: this process owns no texts and sends none
            return
        rid = run.run_id
        target = self._runs.seen.get(rid)
        if run.status == "awaiting_approval":
            self._spawn(self._notify_approvals(run))
        if target is not None:
            if rid not in self._answered.seen and (run.replied or not run.live):
                self._answered.add(rid)
                self._spawn(self._reply_final(run, target))
        elif not run.live and rid not in self._answered.seen and run.kind == "chat":
            self._answered.add(rid)
            s = self._settings()
            minutes = float(s.get("imessageLongRunMinutes") or 3)
            took = (run.ended_at or time.time()) - run.started_at
            if s.get("imessageNotifyLongRuns") and took >= minutes * 60 and run.status != "interrupted":
                self._spawn(self._long_run_note(run, took))

    async def _reply_final(self, run: Any, to: Target) -> None:
        if run.run_id in self._muted.seen or run.status == "interrupted":  # a backend going down texts nothing
            return
        text = to_plain((self.deps.message_text(run.message_id) if run.message_id else "") or "")
        if not text:
            text = RUN_ERROR if run.error else "Stopped."  # the error text itself stays off the phone
        await self._send_all(to, text)

    async def _long_run_note(self, run: Any, took: float) -> None:
        to = self._home()
        if not to:
            return
        title = self.deps.conversation_title(run.conversation_id) or "a chat"
        await self._send(to, f"Grain finished: {_one_line(title, 80)} ({max(1, round(took / 60))} min)")

    async def _notify_approvals(self, run: Any) -> None:
        notify_all = bool(self._settings().get("imessageNotifyLongRuns"))
        async with self._approval_lock:  # the code and the texted mark are decided together, one card at a time
            target_conv = self._peek_target()
            for a in self.deps.pending_approvals():
                cid = a["call_id"]
                if a.get("run_id") != run.run_id or cid in self._texted.seen or cid in self._told.seen:
                    continue
                if not self.deps.is_live(cid) or not (a.get("conversation_id") == target_conv or notify_all):
                    continue
                to = self._runs.seen.get(run.run_id) or self._home()
                if not to:
                    continue
                if a.get("tool") in self.deps.app_only_tools:
                    if await self._send(to, f"{a.get('tool')} is waiting in Grain — open the app to answer."):
                        self._told.add(cid)
                    continue
                preview, shortened = _args_preview(a.get("args"))
                forced = bool(a.get("forced"))
                code = self._code_seq + 1
                needs_code = forced or shortened
                crowded = bool(self._live_texted())  # another card is already waiting: a bare yes would be ambiguous
                reply = f"yes {code} or no {code}" if needs_code or crowded else "yes or no"
                why = ("untrusted content asked for this; extra confirmation needed" if forced
                       else f"{a.get('danger') or 'external'} action")
                tail = "\n(shortened — check Grain for the full action)" if shortened else ""
                at = time.time()
                if await self._send(to, f"Approval needed [{code}]: {a.get('tool')} — {why}\n{preview}{tail}\nReply {reply}"):
                    self._code_seq = code
                    self._texted.add(cid, _Texted(code, needs_code, at))
                    self._codes[code] = cid
                    if len(self._codes) > 300:
                        self._codes.pop(min(self._codes))

    # ---- surface for the app
    def status(self) -> dict[str, Any]:
        st = self.deps.load_state() or {}
        conv = self._peek_target()
        sg = self._self_guid()
        paused = bool(st.get("loopPaused")) and self.status_code != "off"
        return {
            "enabled": self._enabled(), "running": bool(self._task and not self._task.done()),
            "status": "paused_loop_guard" if paused else self.status_code, "fda_ok": self._fda_ok, "last_poll_at": self.last_poll_at,
            "last_error": self.last_error, "ignored_count": int(st.get("ignoredCount") or 0),
            "last_ignored_at": st.get("lastIgnoredAt"),
            "target_conversation": {"id": conv, "title": self.deps.conversation_title(conv)} if conv else None,
            "self_chat": {"guid": sg, "handle": mask_handle(self._self_ident()) if sg else None},
        }

    async def send_test(self, handle: str | None = None) -> dict[str, Any]:
        if self._paused():
            return {"ok": False, "error": "paused_loop_guard"}
        if sg := self._self_guid():  # the confirmed self chat wins: that is where Grain talks
            ok = await self._send(Target(sg, self._self_ident()), "Grain is connected ✅")
            return {"ok": True, "to": "self_chat"} if ok else {"ok": False, "to": "self_chat", "error": "send_failed"}
        people = self._people()
        if not handle and not people:
            return {"ok": False, "error": "no_self_chat"}
        h = normalize_handle(handle) if handle else people[0]
        if not h or h not in people:
            return {"ok": False, "error": "not_allowlisted"}
        home = (self.deps.load_state() or {}).get("homeChat") or {}
        to = Target(home.get("guid") if home.get("handle") == h else None, h)
        ok = await self._send(to, "Grain is connected ✅")
        return {"ok": True, "to": "handle"} if ok else {"ok": False, "to": "handle", "error": "send_failed"}
