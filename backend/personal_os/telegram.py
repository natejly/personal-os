"""Text Grain from your own phone: a two-way bridge over a Telegram bot you own.

Inbound is a long poll of the Bot API (getUpdates): text, plus photos and files (downloaded, stored as uploads and attached
to the turn). Outbound is sendMessage for text and sendPhoto / sendMediaGroup / sendDocument for images and files. Only one paired private chat is
ever heard: pairing is a one-time code sent from that chat, and everything else (groups, other users, other chats) is
dropped without a reply. Everything with side effects (the HTTP caller, the chat/approval routes, state storage) is
injected, so nothing here imports the app and the tests never touch the network.

Privacy rule for logs: only type names, masked ids, lengths and outcomes. Never a message body, never the token.
"""
from __future__ import annotations

import asyncio
import contextlib
import fcntl
import hashlib
import hmac
import inspect
import json
import mimetypes
import logging
import random
import re
import secrets
import tempfile
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

import httpx

from . import redact
from .workers import strip_no_reply
from .telegram_format import html_to_plain, plan, render, split_plain, to_plain  # noqa: F401 - to_plain is re-exported

log = logging.getLogger("personal_os.telegram")
for _n in ("httpx", "httpcore"):
    logging.getLogger(_n).setLevel(logging.WARNING)  # httpx logs every request URL at INFO, and the URL holds the token

API = "https://api.telegram.org"
POLL_TIMEOUT = 25  # seconds Telegram holds a getUpdates open
CLIENT_TIMEOUT = 35.0
RETRY_SECONDS = 30.0  # locked / conflict
MAX_BACKOFF = 60.0
STALE_SECONDS = 600  # on a restart, a message older than this is history, not an instruction
PAIRING_TTL = 15 * 60
MESSAGE_MAX = 4096
TYPING_EVERY = 4.5
SECRET_NAME = "telegramBotToken"
TOKEN_RE = re.compile(r"\d{5,}:[A-Za-z0-9_-]{30,}")
PROGRESS_MIN_SECONDS = 20.0  # at most one progress message per conversation in this window; the rest are coalesced
DOWNLOAD_MAX = 20 * 1024 * 1024  # Bot API: bots can download files up to 20 MB
PHOTO_MAX = 10 * 1024 * 1024  # sendPhoto
DOC_MAX = 50 * 1024 * 1024  # sendDocument
CAPTION_MAX = 1024
GROUP_MAX = 10  # items in one sendMediaGroup
PHOTO_MIMES = frozenset({"image/png", "image/jpeg", "image/gif", "image/webp"})  # svg is a document
UNSUPPORTED = "Send text, a photo or a file; voice and video aren't supported yet."
TOO_BIG = "That file is too big: Telegram lets bots download files up to 20 MB."
FETCH_FAILED = "Couldn't fetch that attachment from Telegram."
_OTHER_MEDIA = ("voice", "audio", "video", "video_note", "animation", "sticker")
ALLOWED_UPDATES = ["message", "callback_query"]
APP_ONLY_TOOLS = frozenset({"propose_plan", "ask_user", "desk_ask"})  # a plan or a question is answered in the app

_URL_TOKEN = re.compile(r"bot\d+:[A-Za-z0-9_-]+")
_RAW_TOKEN = re.compile(r"\d{6,}:[A-Za-z0-9_-]{30,}")


def sanitize(s: Any, token: str | None = None) -> str:
    """Text safe to store, log or return: the token (exact, or anything shaped like one) becomes <token>."""
    s = str(s)
    if token:
        s = s.replace(token, "<token>")
    return _RAW_TOKEN.sub("<token>", _URL_TOKEN.sub("bot<token>", s))


class TelegramError(Exception):
    """A failed Bot API call. `code` is Telegram's error_code (or the HTTP status); 0 means it never got an answer."""

    def __init__(self, code: int, description: str, retry_after: float | None = None) -> None:
        super().__init__(description)
        self.code, self.description, self.retry_after = code, description, retry_after


class HttpApi:
    """The real caller: POST https://api.telegram.org/bot<token>/<method>. The client is created on first use."""

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=CLIENT_TIMEOUT)
        return self._client

    async def __call__(self, token: str, method: str, params: dict[str, Any], timeout: float = CLIENT_TIMEOUT,
                       files: dict[str, tuple[str, bytes, str]] | None = None) -> Any:
        """`files` ({field: (filename, bytes, mime)}) makes it a multipart upload; the other params ride as form fields."""
        kw: dict[str, Any] = {"json": params}
        if files:
            kw = {"data": {k: v if isinstance(v, str) else json.dumps(v) for k, v in params.items()}, "files": files}
        try:
            r = await self._http().post(f"{API}/bot{token}/{method}", timeout=timeout, **kw)
        except httpx.HTTPError as e:
            raise TelegramError(0, sanitize(f"{type(e).__name__}: {e}", token)) from None
        try:
            data = r.json()
        except ValueError:
            raise TelegramError(r.status_code, f"HTTP {r.status_code}") from None
        if isinstance(data, dict) and data.get("ok"):
            return data.get("result")
        data = data if isinstance(data, dict) else {}
        retry = (data.get("parameters") or {}).get("retry_after")
        raise TelegramError(int(data.get("error_code") or r.status_code), sanitize(data.get("description") or f"HTTP {r.status_code}", token),
                            float(retry) if retry else None)

    async def download(self, token: str, file_path: str) -> bytes:
        """The bytes of a file `getFile` named. TelegramError carries only sanitized text."""
        try:
            r = await self._http().get(f"{API}/file/bot{token}/{file_path}", timeout=60.0)
        except httpx.HTTPError as e:
            raise TelegramError(0, sanitize(f"{type(e).__name__}: {e}", token)) from None
        if r.status_code != 200:
            raise TelegramError(r.status_code, f"HTTP {r.status_code}")
        return r.content

    async def aclose(self) -> None:
        c, self._client = self._client, None
        if c:
            await c.aclose()


# ---------------------------------------------------------------- formatting (see telegram_format.py)

def split_reply(text: str, size: int = MESSAGE_MAX) -> list[str]:
    """Plain-text messages of at most `size` characters, cut at a paragraph, line, sentence or word where one is near the end."""
    return split_plain(text, size)


# ---------------------------------------------------------------- commands and limits

_COMMAND = re.compile(r"/?(status|stop|new|help|approve|yes|y|deny|no|n)(?:@\w+)?(?:\s+(\d{1,6}))?\s*[.!]?", re.I)
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

    def __init__(self, limit: int = 20, window: float = 60.0, clock: Callable[[], float] = time.monotonic) -> None:
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


def lock_path(token: str) -> Path:
    """One poller per bot token per user, whichever data directory a backend runs on (TMPDIR is per user)."""
    return Path(tempfile.gettempdir()) / f"grain-telegram-{hashlib.sha1(token.encode()).hexdigest()[:12]}.lock"


def _mask(i: Any) -> str:
    return "…" + str(i)[-3:] if i is not None else "?"


# ---------------------------------------------------------------- the bridge

@dataclass
class Deps:
    get_settings: Callable[[], dict[str, Any]]
    load_state: Callable[[], dict[str, Any]]
    save_state: Callable[[dict[str, Any]], None]
    get_token: Callable[[], str | None]
    start_turn: Callable[..., Any]  # (conversation id, text[, attachment ids]) -> {"run_id": ...}; sync or async. Ids only when the message had a file
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
    api: Callable[..., Awaitable[Any]] = field(default_factory=HttpApi)  # (token, method, params, timeout[, files]) -> result; raises TelegramError
    lock_path: Path | None = None  # tests; the default is keyed on the token (lock_path())
    message_attachments: Callable[[str], list[dict[str, Any]]] = lambda mid: []  # message id -> [{id, name, mime, size}]
    attachment_path: Callable[[str], Path | None] = lambda doc_id: None  # document id -> its stored original, None if gone
    store_upload: Callable[[str, str, bytes], Any] | None = None  # (name, mime, data) -> {"id", ...}; sync or async
    download: Callable[[str, str], Awaitable[bytes]] | None = None  # (token, file_path) -> bytes; default: api.download
    mark_texts_conversation: Callable[[str, bool], None] = lambda cid, flag: None  # (conversation id, is the Texts chat): the app labels it


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
    at: float  # when we sent it


@dataclass
class _Update:
    """Progress waiting for its turn in the per-conversation window."""
    texts: list[str] = field(default_factory=list)
    atts: list[dict[str, Any]] = field(default_factory=list)
    last: float | None = None  # monotonic time of the last flush
    timer: asyncio.Task[None] | None = None


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


HELP = ("Commands: /status, /stop, /new (fresh conversation), yes / no (answer an approval; add its code if several "
        "wait, e.g. yes 2), /help. Anything else goes to Grain as a message.")
_ANSWER_WORD = re.compile(r"(yes|no|y|n|approve|deny)\b", re.I)
SLOW_DOWN = "Slow down: too many messages in the last minute."
RUN_ERROR = "The run ended with an error — details are in Grain."
GONE = "No longer pending."


class TelegramBridge:
    def __init__(self, deps: Deps, *, rate_limit: int = 20) -> None:
        self.deps = deps
        self.limiter = RateLimiter(rate_limit)
        self.health = ""  # "" (fine so far) | "bad_token" | "conflict" | "locked" | "error"
        self.last_poll_at: float | None = None
        self.last_error: str | None = None
        self._task: asyncio.Task[None] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock_fd: Any = None
        self._token_fp: str | None = None  # the token the poller was last started with
        self._stale_before = time.time() - STALE_SECONDS
        self._sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep
        self._tasks: set[asyncio.Task[Any]] = set()
        self._send_lock = asyncio.Lock()  # one send at a time keeps replies in order
        self._order_lock = asyncio.Lock()  # a whole delivery (text + files + notes) before the next one starts
        self._updates: dict[str, _Update] = {}  # conversation id -> progress waiting out the window
        self._sent_atts = _Memo()  # attachment ids already sent as progress: the final reply does not repeat them
        self._approval_lock = asyncio.Lock()
        self._runs = _Memo()  # run ids started from Telegram: their reply goes back
        self._answered = _Memo()  # run ids whose end has been handled
        self._muted = _Memo()  # run ids stopped from Telegram: the "Stopped." reply already went out
        self._texted = _Memo()  # call_id -> _Texted
        self._told = _Memo()  # call ids of plans and questions we pointed at the app
        self._typing: set[str] = set()  # run ids whose "typing" indicator is on
        self._codes: dict[int, str] = {}
        self._code_seq = random.randint(10, 80)  # a stale "yes 3" from before a restart must not hit a new card

    # ---- lifecycle
    def _settings(self) -> dict[str, Any]:
        return self.deps.get_settings()

    def _enabled(self) -> bool:
        return bool(self._settings().get("telegramEnabled"))

    @staticmethod
    def _fp(token: str | None) -> str | None:
        return hashlib.sha1(token.encode()).hexdigest() if token else None

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._loop = asyncio.get_running_loop()
        self._stale_before = time.time() - STALE_SECONDS
        self.health, self.last_error = "", None
        self._token_fp = self._fp(self.deps.get_token())
        if self.paired():  # a Texts chat from before the flag existed gets it now
            self._mark(self._peek_target(), True)
        self._task = asyncio.create_task(self._run(), name="telegram-poller")

    async def poll_once(self) -> None:
        """One poll under the lock, without the loop around it: what the tests drive."""
        self._loop = asyncio.get_running_loop()
        token = self.deps.get_token()
        if not token or not self._acquire(token):
            self.health = "locked"
            return
        await self._poll(token)
        self.health = ""

    async def stop(self) -> None:
        t, self._task = self._task, None
        if t:
            t.cancel()
            await asyncio.gather(t, return_exceptions=True)
        for t in list(self._tasks):
            t.cancel()
        self._typing.clear()
        self._token_fp = None  # a deliberate stop forgets a dead-token verdict: switching it back on tries again
        self._release()
        if (aclose := getattr(self.deps.api, "aclose", None)):
            await aclose()

    async def reconcile(self) -> None:
        """Make the poller match the settings and the stored token: start, stop, or restart on a changed token."""
        token = self.deps.get_token()
        running = bool(self._task and not self._task.done())
        if not (self._enabled() and token):
            await self.stop()
        elif running and self._fp(token) != self._token_fp:
            await self.stop()
            await self.start()
        elif not running and not (self.health == "bad_token" and self._fp(token) == self._token_fp):
            await self.start()

    def _acquire(self, token: str) -> bool:
        if self._lock_fd is not None:
            return True
        fd = None
        try:
            fd = open(self.deps.lock_path or lock_path(token), "a+")  # noqa: SIM115 - held for as long as we poll
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
            while self._enabled() and (token := self.deps.get_token()):
                if not self._acquire(token):
                    self.health = "locked"
                    await self._sleep(RETRY_SECONDS)
                    continue
                delay = 0.0
                try:
                    await self._poll(token)
                    self.health, self.last_error, backoff = "", None, 0.0
                except TelegramError as e:
                    self.last_error = sanitize(f"{e.code or 'network'}: {e.description}", token)[:200]
                    if e.code in (401, 404):  # the token is dead: nothing to retry until it changes
                        self.health = "bad_token"
                        log.warning("telegram token rejected")
                        return
                    if e.code == 409:  # another getUpdates (a webhook, or a poller elsewhere) holds the bot
                        self.health, delay = "conflict", RETRY_SECONDS
                    elif e.code == 429:
                        self.health, delay = "error", min(max(e.retry_after or 1.0, 1.0), 300.0)
                    else:
                        self.health, backoff = "error", min(MAX_BACKOFF, backoff * 2 or 1.0)
                        delay = backoff
                    log.warning("telegram poll failed: code=%s", e.code)
                except Exception as e:  # noqa: BLE001 - back off and try again; the poller never dies
                    self.health, self.last_error = "error", sanitize(f"{type(e).__name__}: {e}", token)[:200]
                    backoff = min(MAX_BACKOFF, backoff * 2 or 1.0)
                    delay = backoff
                    log.warning("telegram poll failed: %s", type(e).__name__)
                await self._sleep(delay)  # 0 on a good poll: still a yield, so a misbehaving proxy cannot spin the loop
        finally:
            self._release()

    # ---- state
    def _state(self) -> dict[str, Any]:
        return dict(self.deps.load_state() or {})

    def _save(self, **patch: Any) -> dict[str, Any]:
        st = self._state()
        st.update(patch)
        self.deps.save_state(st)
        return st

    def paired(self) -> bool:
        return self._state().get("ownerChatId") is not None

    def issue_pairing(self) -> dict[str, Any]:
        pairing = {"code": "".join(secrets.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789") for _ in range(8)),
                   "expiresAt": time.time() + PAIRING_TTL}
        self._save(pairing=pairing)
        return pairing

    def _mark(self, cid: str | None, flag: bool) -> None:
        if not cid:
            return
        try:
            self.deps.mark_texts_conversation(cid, flag)
        except Exception as e:  # noqa: BLE001 - a label must never break the bridge
            log.warning("telegram chat flag failed: %s", type(e).__name__)

    def unpair(self) -> None:
        self._mark(self._peek_target(), False)  # it stays as an ordinary chat
        self._save(ownerChatId=None, ownerUserId=None, ownerName=None)
        self.issue_pairing()

    def clear(self) -> None:
        self._mark(self._peek_target(), False)
        self.deps.save_state({})

    async def check_token(self, token: str) -> dict[str, Any]:
        """getMe for a token not yet stored: the bot's {id, username}. TelegramError carries only sanitized text."""
        try:
            return await self.deps.api(token, "getMe", {}, 15.0)
        except TelegramError:
            raise
        except Exception as e:  # noqa: BLE001
            raise TelegramError(0, sanitize(f"{type(e).__name__}: {e}", token)) from None

    def adopt(self, me: dict[str, Any]) -> None:
        """Remember the bot a new token belongs to. A different bot is a different chat: owner and offset go."""
        st = self._state()
        if st.get("botId") != me.get("id"):
            st = {"textsConversationId": st.get("textsConversationId")}
            self._mark(st.get("textsConversationId"), False)  # unpaired again: pairing re-labels it
        st.update(botId=me.get("id"), botUsername=me.get("username"))
        self.deps.save_state(st)
        if st.get("ownerChatId") is None:
            self.issue_pairing()

    def _peek_target(self) -> str | None:
        cid = self._state().get("textsConversationId")
        return cid if cid and self.deps.conversation_exists(cid) else None

    async def _target(self) -> str:
        cid = self._peek_target()
        if cid:
            return cid
        cid = await _maybe(self.deps.create_texts_conversation())
        self._save(textsConversationId=cid)
        self._mark(cid, True)
        return cid

    # ---- polling
    async def _poll(self, token: str) -> None:
        params: dict[str, Any] = {"timeout": POLL_TIMEOUT, "allowed_updates": ALLOWED_UPDATES}
        offset = self._state().get("offset")
        if offset is not None:
            params["offset"] = offset
        updates = await self.deps.api(token, "getUpdates", params)
        self.last_poll_at = time.time()
        for u in updates or []:
            try:
                await self._handle(u)
            except Exception as e:  # noqa: BLE001 - one bad update must not stall the offset
                log.warning("telegram update failed: %s", type(e).__name__)
            self._save(offset=int(u["update_id"]) + 1)

    async def _handle(self, u: dict[str, Any]) -> None:
        if cb := u.get("callback_query"):
            await self._callback(cb)
            return
        m = u.get("message")
        if not isinstance(m, dict):  # edited messages, channel posts and the rest are never instructions
            return
        chat, frm = m.get("chat") or {}, m.get("from") or {}
        if chat.get("type") != "private":
            return
        if (m.get("date") or time.time()) < self._stale_before:
            return
        st = self._state()
        text = (m.get("text") or "").strip()
        if st.get("ownerChatId") is None:
            await self._pair(chat, frm, text, st)
            return
        if chat.get("id") != st.get("ownerChatId") or frm.get("id") != st.get("ownerUserId"):
            return
        photo = m.get("photo")
        media = (("photo", photo[-1]) if isinstance(photo, list) and photo and isinstance(photo[-1], dict)
                 else ("document", m["document"]) if isinstance(m.get("document"), dict) else None)
        if media:
            text = (m.get("caption") or "").strip()
        elif any(k in m for k in _OTHER_MEDIA):
            text = ""  # a voice note with a caption is still a voice note
        await self._owner_message(chat["id"], text, float(m.get("date") or 0) or None, st, media)

    # ---- pairing
    async def _pair(self, chat: dict[str, Any], frm: dict[str, Any], text: str, st: dict[str, Any]) -> None:
        """Unpaired: the only thing heard is "/start <code>" with the live code, from a private chat."""
        parts = text.split()
        pairing = st.get("pairing")
        if len(parts) != 2 or parts[0].split("@")[0].lower() != "/start" or not isinstance(pairing, dict):
            return
        if time.time() >= float(pairing.get("expiresAt") or 0) or not hmac.compare_digest(parts[1].encode(), str(pairing.get("code")).encode()):
            return
        name = frm.get("first_name") or (f"@{frm['username']}" if frm.get("username") else "owner")
        self._save(ownerChatId=chat["id"], ownerUserId=frm.get("id"), ownerName=name, pairing=None)
        log.info("telegram paired chat=%s", _mask(chat["id"]))
        try:
            self._mark(await self._target(), True)  # the chat is in Grain as soon as pairing works (an unpaired one is flagged again)
        except Exception as e:  # noqa: BLE001 - a DB hiccup must not break pairing
            log.warning("telegram chat create failed: %s", type(e).__name__)
        await self._send(chat["id"], "Paired. Text me anything, or /help.")

    # ---- owner messages
    async def _owner_message(self, chat_id: int, text: str, sent_at: float | None, st: dict[str, Any],
                             media: tuple[str, dict[str, Any]] | None = None) -> None:
        where = chat_id
        if not text and not media:
            await self._send(where, UNSUPPORTED)
            return
        if not media and text.split()[0].split("@")[0].lower() == "/start":
            await self._send(where, HELP)
            return
        cmd = None if media else parse_command(text)  # a file's caption is a message, never a command
        bare = re.sub(r"^/|[.!]+$", "", text.lower())
        if cmd and cmd[0] in ("approve", "deny") and cmd[1] is None and bare in _BARE_ANSWERS and not self._live_texted():
            cmd = None  # a plain "yes" with nothing waiting is the user answering Grain's own question
        if not (cmd and cmd[0] in ("stop", "deny")):  # stopping and refusing are never rate limited
            verdict = self.limiter.check(str(chat_id))
            if verdict != "ok":
                log.info("telegram chat=%s %s", _mask(chat_id), verdict)
                if verdict == "limited_first":
                    await self._send(where, SLOW_DOWN)
                return
        if cmd:
            log.info("telegram chat=%s command=%s", _mask(chat_id), cmd[0])
            await self._command(cmd, where, sent_at)
            return
        if not media and _ANSWER_WORD.match(text) and self._live_texted():  # steering would deny the card; make them answer it
            await self._send(where, "Reply yes or no (or yes <code>) to the approval first.")
            return
        ids: list[str] = []
        if media:
            doc_id = await self._fetch_attachment(chat_id, *media)
            if doc_id is None:  # already told the user why
                return
            ids = [doc_id]
        try:
            conv = await self._target()
            res = await _maybe(self.deps.start_turn(conv, text, ids) if ids else self.deps.start_turn(conv, text))
        except Exception as e:  # noqa: BLE001
            log.warning("telegram start failed: %s", type(e).__name__)
            await self._send(where, "Grain couldn't take that message right now. Try again in a moment.")
            return
        self._runs.add(res["run_id"])
        self._typing.add(res["run_id"])  # before the task starts: a fast reply must be able to switch it off
        self._spawn(self._typing_loop(res["run_id"], where))
        log.info("telegram chat=%s started run len=%s files=%s", _mask(chat_id), len(text), len(ids))

    async def _fetch_attachment(self, chat_id: int, kind: str, obj: dict[str, Any]) -> str | None:
        """An inbound photo or file -> the id of its stored upload; None after telling the user what went wrong."""
        size = obj.get("file_size")
        if isinstance(size, int) and size > DOWNLOAD_MAX:
            await self._send(chat_id, TOO_BIG)
            return None
        try:
            token, file_id = self.deps.get_token(), obj.get("file_id")
            fetch = self.deps.download or getattr(self.deps.api, "download", None)
            if not token or not file_id or fetch is None or self.deps.store_upload is None:
                raise TelegramError(0, "not configured")
            info = await self.deps.api(token, "getFile", {"file_id": file_id}, 15.0) or {}
            if int(info.get("file_size") or 0) > DOWNLOAD_MAX:
                await self._send(chat_id, TOO_BIG)
                return None
            data = await fetch(token, info["file_path"])
            name = Path(str(obj.get("file_name") or "")).name or f"photo-{int(time.time())}.jpg"
            mime = obj.get("mime_type") or ("image/jpeg" if kind == "photo" else mimetypes.guess_type(name)[0] or "application/octet-stream")
            stored = await _maybe(self.deps.store_upload(name, mime, data))
            doc_id = str(stored["id"])
        except Exception as e:  # noqa: BLE001
            log.info("telegram chat=%s attachment failed: %s", _mask(chat_id), type(e).__name__)
            await self._send(chat_id, FETCH_FAILED)
            return None
        log.info("telegram chat=%s attachment kind=%s bytes=%s", _mask(chat_id), kind, len(data))
        return doc_id

    async def _typing_loop(self, run_id: str, chat_id: int) -> None:
        token = self.deps.get_token()
        end = time.monotonic() + 1800  # a run that never reports its end must not type forever
        while run_id in self._typing and token and time.monotonic() < end:
            with contextlib.suppress(Exception):
                await self._call("sendChatAction", {"chat_id": chat_id, "action": "typing"}, 10.0)
            await self._sleep(TYPING_EVERY)

    # ---- commands
    def _live_texted(self) -> list[tuple[int, str, _Texted]]:
        """(code, call_id, info) of approvals we sent that are still pending and still waited on by a run."""
        pending = {a["call_id"] for a in self.deps.pending_approvals()}
        return [(code, cid, self._texted.seen[cid]) for code, cid in sorted(self._codes.items())
                if cid in pending and cid in self._texted.seen and self.deps.is_live(cid)]

    async def _command(self, cmd: tuple[str, str | None], where: int, sent_at: float | None = None) -> None:
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
                self._typing.discard(i)
            await self._send(where, "Stopped." if stopped else "Nothing is running.")
        elif word == "new":
            old = self._peek_target()
            cid = await _maybe(self.deps.create_texts_conversation())
            self._save(textsConversationId=cid)
            self._mark(old, False)
            self._mark(cid, True)
            await self._send(where, "Started a new conversation.")
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

    async def _decide(self, call_id: str, decision: str) -> str:
        """Record the answer; what to tell the user."""
        try:
            res = await _maybe(self.deps.decide(call_id, decision))
        except Exception as e:  # noqa: BLE001
            log.info("telegram decide failed: %s", type(e).__name__)
            return GONE
        if isinstance(res, dict) and res.get("live") is False:
            return "That approval is no longer waiting (the run ended)."
        return "Approved." if decision == "allow" else "Denied."

    async def _answer(self, word: str, code: str | None, where: int, sent_at: float | None) -> None:
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
            if sent_at is not None and sent_at < int(hit[2].at):  # typed before the request existed: it answers something else
                await self._send(where, "Reply again: that answer was sent before the approval request.")
                return
            if hit[2].needs_code and decision == "allow":
                await self._send(where, f"That one needs its code to approve: reply yes {hit[0]}.")
                return
        await self._send(where, await self._decide(hit[1], decision))

    async def _callback(self, cb: dict[str, Any]) -> None:
        """An Approve / Deny button. A tap is explicit, so it needs no code; only the paired owner's taps count."""
        st = self._state()
        msg = cb.get("message") or {}
        if st.get("ownerChatId") is None or (cb.get("from") or {}).get("id") != st.get("ownerUserId") \
                or (msg.get("chat") or {}).get("id") != st.get("ownerChatId"):
            return
        kind, _, code = str(cb.get("data") or "").partition(":")
        if kind not in ("ap", "dn") or not code.isdigit():
            return
        hit = next((x for x in self._live_texted() if x[0] == int(code)), None)
        outcome = await self._decide(hit[1], "allow" if kind == "ap" else "deny") if hit else GONE
        token = self.deps.get_token()
        if not token:
            return
        with contextlib.suppress(Exception):
            await self.deps.api(token, "answerCallbackQuery", {"callback_query_id": cb.get("id"), "text": outcome[:200]}, 10.0)
        if msg.get("message_id") is not None:
            with contextlib.suppress(Exception):
                await self._call("editMessageText", {"chat_id": msg["chat"]["id"], "message_id": msg["message_id"],
                                                     "text": f"{msg.get('text') or ''}\n\n{outcome}"}, 10.0)

    # ---- sending
    async def _call(self, method: str, params: dict[str, Any], timeout: float = 15.0, files: dict[str, tuple[str, bytes, str]] | None = None,
                    errs: list[int] | None = None) -> bool:
        """The one door every outbound call goes through: refuses any chat but the paired owner's, one call at a time, and
        waits out one 429. `params` carries the chat_id. True when Telegram accepted it; a refusal's code goes into `errs`."""
        token, chat_id = self.deps.get_token(), params.get("chat_id")
        if not token:
            return False
        if chat_id is None or chat_id != self._state().get("ownerChatId"):
            log.warning("telegram send refused: not owner")
            return False
        async with self._send_lock:
            for attempt in (0, 1):
                try:
                    await (self.deps.api(token, method, params, timeout, files) if files else self.deps.api(token, method, params, timeout))
                    return True
                except TelegramError as e:
                    if e.code == 429 and attempt == 0:
                        await self._sleep(min(e.retry_after or 1.0, 30.0))
                        continue
                    log.info("telegram send chat=%s %s failed: code=%s", _mask(chat_id), method, e.code)
                    if errs is not None:
                        errs.append(e.code)
                except Exception as e:  # noqa: BLE001
                    log.info("telegram send chat=%s %s failed: %s", _mask(chat_id), method, type(e).__name__)
                return False
        return False

    async def _send(self, chat_id: int, text: str, markup: dict[str, Any] | None = None, errs: list[int] | None = None) -> bool:
        params: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if markup:
            params["reply_markup"] = markup
        ok = await self._call("sendMessage", params, errs=errs)
        if ok:
            log.info("telegram send chat=%s len=%s ok=True", _mask(chat_id), len(text))
        return ok

    async def _send_doc(self, chat_id: int, name: str, mime: str, data: bytes) -> bool:
        return await self._call("sendDocument", {"chat_id": chat_id}, 60.0, {"document": (name, data, mime)})

    async def _send_part(self, chat_id: int, part: str, errs: list[int]) -> bool:
        """One HTML message. When Telegram answers 400 (it could not parse it) the same text goes again as plain text.
        False when it could not be sent; `errs` then ends with the last refusal's code."""
        plain = html_to_plain(part)
        if plain == part:  # no markup and nothing escaped: nothing to parse
            return await self._send(chat_id, part, errs=errs)
        params = {"chat_id": chat_id, "text": part, "parse_mode": "HTML", "link_preview_options": {"is_disabled": True}}
        if await self._call("sendMessage", params, errs=errs):
            log.info("telegram send chat=%s len=%s html ok=True", _mask(chat_id), len(part))
            return True
        if errs[-1:] != [400]:
            return False
        errs.clear()
        for piece in split_plain(plain):
            if not await self._send(chat_id, piece, errs=errs):
                return False
        return True

    async def _send_all(self, chat_id: int, text: str) -> None:
        """A reply (markdown) to the owner as formatted messages. Too long: a short summary and the whole reply as reply.md.
        If Telegram refuses even the plain text of a part (400), the whole reply goes as reply.md instead."""
        md = (text or "").strip()
        if not md:
            return
        pl = plan(md)
        full = ("reply.md", "text/markdown", md.encode())
        if pl.summary is not None:
            if await self._send(chat_id, pl.summary):
                await self._send_doc(chat_id, *full)
            return
        for part in pl.parts:
            errs: list[int] = []
            if not await self._send_part(chat_id, part, errs):
                if errs[-1:] == [400]:
                    await self._send_doc(chat_id, *full)
                return
        for name, mime, data in pl.files:
            await self._send_doc(chat_id, name, mime, data)

    @staticmethod
    def _is_photo(mime: str, data: bytes) -> bool:
        """Fits sendPhoto: an image type Telegram shows inline, <= 10 MB, sides summing to <= 10000 px, no more than 20:1."""
        if mime not in PHOTO_MIMES or len(data) > PHOTO_MAX:
            return False
        try:
            import io

            from PIL import Image
            w, h = Image.open(io.BytesIO(data)).size
        except ImportError:
            return True  # cannot measure it; a refused photo falls back to a document
        except Exception:  # noqa: BLE001 - not a readable image
            return False
        return bool(w and h) and w + h <= 10000 and max(w, h) / min(w, h) <= 20

    async def _deliver(self, chat_id: int, text: str, atts: list[dict[str, Any]] | None) -> None:
        """Text and files to the owner. Short text rides as the first file's caption, longer text goes first on its own."""
        if not atts:
            await self._send_all(chat_id, text)
            return
        md, r = text, render(text)
        if len(r.plain) > CAPTION_MAX or r.files:  # too long for a caption, or it carries a table: the text goes first on its own
            await self._send_all(chat_id, md)
            md, r = "", render("")
        cap = [r.plain]  # the caption (plain text) goes out once, with the first thing that gets through
        failed: list[str] = []
        photos: list[tuple[str, str, bytes]] = []
        docs: list[tuple[str, str, Path]] = []
        for a in atts:
            name, mime = str(a.get("name") or "file"), str(a.get("mime") or "application/octet-stream")
            path = self.deps.attachment_path(str(a.get("id") or ""))
            try:
                size = path.stat().st_size if path else None
            except OSError:
                size = None
            if size is None or size > DOC_MAX:  # gone, or more than a bot may send
                failed.append(name)
            else:
                try:
                    data = await asyncio.to_thread(path.read_bytes) if mime in PHOTO_MIMES and size <= PHOTO_MAX else b""
                except OSError:  # gone between stat and read
                    failed.append(name)
                    continue
                (photos.append((name, mime, data)) if data and self._is_photo(mime, data) else docs.append((name, mime, path)))

        async def one(method: str, field_: str, name: str, mime: str, data: bytes) -> bool:
            params: dict[str, Any] = {"chat_id": chat_id}
            if cap[0]:
                params["caption"] = cap[0]
            ok = await self._call(method, params, 60.0, {field_: (name, data, mime)})
            if ok:
                cap[0] = ""
            return ok

        async def doc(name: str, mime: str, data: bytes) -> None:
            if not await one("sendDocument", "document", name, mime, data):
                failed.append(name)

        for i in range(0, len(photos), GROUP_MAX):
            group = photos[i:i + GROUP_MAX]
            if len(group) == 1:
                name, mime, data = group[0]
                if not await one("sendPhoto", "photo", name, mime, data):
                    await doc(name, mime, data)
                continue
            media = [{"type": "photo", "media": f"attach://f{j}", **({"caption": cap[0]} if j == 0 and cap[0] else {})} for j in range(len(group))]
            if await self._call("sendMediaGroup", {"chat_id": chat_id, "media": media}, 60.0,
                                {f"f{j}": g for j, g in enumerate(group)}):
                cap[0] = ""
                continue
            for name, mime, data in group:
                await doc(name, mime, data)
        for name, mime, path in docs:
            try:
                data = await asyncio.to_thread(path.read_bytes)
            except OSError:
                failed.append(name)
                continue
            await doc(name, mime, data)
        if cap[0]:  # nothing carried it
            await self._send_all(chat_id, md)
        for name in failed:
            await self._send(chat_id, f"Couldn't deliver {name}.")
        log.info("telegram send chat=%s files=%s failed=%s", _mask(chat_id), len(atts), len(failed))

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
            log.debug("telegram run listener failed", exc_info=True)

    def _on_run_change(self, run: Any) -> None:
        if self._lock_fd is None:  # not the poller: this process owns the chat and sends nothing
            return
        rid = run.run_id
        owner = self._state().get("ownerChatId")
        if owner is None:
            return
        if run.status == "awaiting_approval":
            self._spawn(self._notify_approvals(owner, run_id=rid))
        if rid in self._runs.seen:
            if rid not in self._answered.seen and (run.replied or not run.live):
                self._answered.add(rid)
                self._typing.discard(rid)
                self._spawn(self._reply_final(run, owner))
        elif not run.live and rid not in self._answered.seen and run.kind == "chat":
            self._answered.add(rid)
            s = self._settings()
            minutes = float(s.get("telegramLongRunMinutes") or 3)
            took = (run.ended_at or time.time()) - run.started_at
            if s.get("telegramNotifyLongRuns") and took >= minutes * 60 and run.status != "interrupted":
                self._spawn(self._long_run_note(run, took, owner))

    async def _reply_final(self, run: Any, chat_id: int) -> None:
        if run.run_id in self._muted.seen or run.status == "interrupted" or getattr(run, "silent", False):
            return  # a backend going down sends nothing, and a reply that only handed work on was removed (its work shows in the app)
        raw = ((self.deps.message_text(run.message_id) if run.message_id else "") or "").strip()
        text = strip_no_reply(raw)  # NO_REPLY never reaches the phone
        if not to_plain(text):  # only the marker says nothing; an empty reply was an error or a stop
            text = "" if raw and not run.error else RUN_ERROR if run.error else "Stopped."  # the error text itself stays off the phone
        await self.flush_updates(run.conversation_id)  # progress waiting out its window goes before the answer
        atts = self._unsent(self.deps.message_attachments(run.message_id) if run.message_id else [])
        async with self._order_lock:  # and a delivery still in flight finishes first
            if text:
                await self._send_all(chat_id, text)
            if atts:
                await self._deliver(chat_id, "", atts)

    def _unsent(self, atts: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
        """The attachments not already delivered as a progress update (each id is consumed: a later run may send it again)."""
        sent = self._sent_atts.seen
        return [a for a in atts or [] if sent.pop(a.get("id"), None) is None]

    async def _long_run_note(self, run: Any, took: float, chat_id: int) -> None:
        title = self.deps.conversation_title(run.conversation_id) or "a chat"
        await self._send(chat_id, f"Grain finished: {_one_line(title, 80)} ({max(1, round(took / 60))} min)")

    async def _notify_approvals(self, chat_id: int, run_id: str | None = None, call_id: str | None = None) -> None:
        """Text the pending cards of one run, or the one card `call_id` (a background worker's: it has no run, and its chat
        may not be the Texts one, so it is sent whichever chat it came from)."""
        notify_all = bool(self._settings().get("telegramNotifyLongRuns"))
        async with self._approval_lock:  # the code and the sent mark are decided together, one card at a time
            target_conv = self._peek_target()
            for a in self.deps.pending_approvals():
                cid = a["call_id"]
                if (run_id and a.get("run_id") != run_id) or (call_id and cid != call_id) or cid in self._texted.seen or cid in self._told.seen:
                    continue
                if not self.deps.is_live(cid) or not (call_id or a.get("conversation_id") == target_conv or notify_all):
                    continue
                if a.get("tool") in self.deps.app_only_tools:
                    if await self._send(chat_id, f"{a.get('tool')} is waiting in Grain. Open the app to answer."):
                        self._told.add(cid)
                    continue
                preview, shortened = _args_preview(a.get("args"))
                forced = bool(a.get("forced"))
                code = self._code_seq + 1
                why = ("untrusted content asked for this; extra confirmation needed" if forced
                       else f"{a.get('danger') or 'external'} action")
                tail = "\n(shortened — check Grain for the full action)" if shortened else ""
                markup = {"inline_keyboard": [[{"text": "Approve", "callback_data": f"ap:{code}"},
                                               {"text": "Deny", "callback_data": f"dn:{code}"}]]}
                at = time.time()
                if await self._send(chat_id, f"Approval needed [{code}]: {a.get('tool')} — {why}\n{preview}{tail}", markup):
                    self._code_seq = code
                    self._texted.add(cid, _Texted(code, forced or shortened, at))
                    self._codes[code] = cid
                    if len(self._codes) > 300:
                        self._codes.pop(min(self._codes))

    def notify_worker_approval(self, call_id: str) -> None:
        """A background worker raised an approval card: text it, with Approve / Deny buttons, to the paired chat."""
        if self._lock_fd is None:  # not the poller: this process sends nothing
            return
        owner = self._state().get("ownerChatId")
        if owner is not None:
            self._spawn(self._notify_approvals(owner, call_id=call_id))

    def push(self, text: str, attachments: list[dict[str, Any]] | None = None) -> None:
        """Text the owner a reply written outside any run they started (a worker's result, after its wake turn)."""
        if self._lock_fd is None:
            return
        owner, md, atts = self._state().get("ownerChatId"), strip_no_reply(text), self._unsent(attachments)
        if owner is not None and (to_plain(md) or atts):
            self._spawn(self._deliver_in_order(owner, md, atts))

    def is_texts_conversation(self, conv_id: str | None) -> bool:
        return bool(conv_id) and conv_id == self._state().get("textsConversationId")

    def texts_conversation_id(self) -> str | None:
        """The Texts chat while the bot is paired (it exists in Grain from pairing on), else None."""
        return self._peek_target() if self.paired() else None

    def from_app(self, conversation_id: str | None, run_id: str, text: str, attachments: list[dict[str, Any]] | None = None) -> None:
        """A message typed in Grain into the Texts chat: it runs like a Telegram turn (its reply and files go to the phone)
        and the message itself is shown there first as "From Grain: ...". Returns at once; a no-op unless this process polls,
        the chat is the Texts one and an owner is paired. Only ever sent to the owner's chat."""
        owner = self._state().get("ownerChatId")
        if self._lock_fd is None or owner is None or not self.is_texts_conversation(conversation_id):
            return
        self._runs.add(run_id)
        if run_id not in self._typing:  # a steer into a reply already typing keeps its one loop
            self._typing.add(run_id)  # before the task starts: a fast reply must be able to switch it off
            self._spawn(self._typing_loop(run_id, owner))
        atts = [a for a in attachments or [] if a.get("id")]
        if text or atts:
            self._spawn(self._deliver_in_order(owner, f"From Grain: {text}".rstrip(), atts))
        log.info("telegram chat=%s app turn len=%s files=%s", _mask(owner), len(text), len(atts))

    def send_update(self, conversation_id: str | None, text: str, attachments: list[dict[str, Any]] | None = None) -> bool:
        """Progress from a tool mid-run, sent to the phone when this process polls and the conversation is the Texts one.
        True when accepted. At most one message per PROGRESS_MIN_SECONDS: sooner ones are joined and flushed by one timer."""
        md, atts = strip_no_reply(text), [a for a in attachments or [] if a.get("id")]
        if self._lock_fd is None or not self.is_texts_conversation(conversation_id) or self._state().get("ownerChatId") is None \
                or not (to_plain(md) or atts):
            return False
        for a in atts:
            self._sent_atts.add(a["id"])  # now, not at flush: a final reply that lands first must not repeat it
        self._spawn(self._queue_update(conversation_id, md, atts))  # type: ignore[arg-type]
        return True

    async def _queue_update(self, conv_id: str, text: str, atts: list[dict[str, Any]]) -> None:
        u = self._updates.setdefault(conv_id, _Update())
        if text:
            u.texts.append(text)
        u.atts += [a for a in atts if a["id"] not in {x["id"] for x in u.atts}]
        if u.timer:  # one is already waiting for the window to end
            return
        wait = PROGRESS_MIN_SECONDS - (time.monotonic() - u.last) if u.last is not None else 0.0
        if wait <= 0:
            await self.flush_updates(conv_id)
        else:
            u.timer = asyncio.create_task(self._flush_later(conv_id, wait))
            self._tasks.add(u.timer)
            u.timer.add_done_callback(self._tasks.discard)

    async def _flush_later(self, conv_id: str, wait: float) -> None:
        await self._sleep(wait)
        self._updates[conv_id].timer = None  # this task is finishing: flush_updates must not cancel it
        await self.flush_updates(conv_id)

    async def flush_updates(self, conv_id: str | None) -> None:
        """Send whatever progress is waiting for this conversation now."""
        u = self._updates.get(conv_id or "")
        if not u:
            return
        if u.timer and u.timer is not asyncio.current_task():
            u.timer.cancel()
        u.timer = None
        texts, atts, u.texts, u.atts = u.texts, u.atts, [], []
        owner = self._state().get("ownerChatId")
        if owner is None or not (texts or atts):
            return
        u.last = time.monotonic()
        await self._deliver_in_order(owner, "\n\n".join(texts), atts)

    async def _deliver_in_order(self, chat_id: int, text: str, atts: list[dict[str, Any]] | None) -> None:
        async with self._order_lock:
            await self._deliver(chat_id, text, atts)

    # ---- surface for the app
    def status(self) -> dict[str, Any]:
        st = self._state()
        has_token, enabled, paired = bool(self.deps.get_token()), self._enabled(), st.get("ownerChatId") is not None
        if not has_token:
            code = "no_token"
        elif not enabled:
            code = "disabled"
        elif self.health:
            code = self.health
        else:
            code = "connected" if paired else "not_paired"
        pairing = st.get("pairing")
        if has_token and not paired and isinstance(pairing, dict) and float(pairing.get("expiresAt") or 0) > time.time():
            pairing = {"code": pairing["code"], "link": f"https://t.me/{st.get('botUsername') or ''}?start={pairing['code']}",
                       "expires_at": pairing["expiresAt"]}
        else:
            pairing = None
        return {"enabled": enabled, "has_token": has_token, "bot_username": st.get("botUsername"), "paired": paired,
                "owner_name": st.get("ownerName") if paired else None, "status": code,
                "last_error": self.last_error if enabled and has_token else None,
                "last_poll_at": self.last_poll_at, "pairing": pairing}

    async def send_test(self) -> dict[str, Any]:
        owner = self._state().get("ownerChatId")
        if owner is None:
            return {"ok": False, "error": "not_paired"}
        return {"ok": True} if await self._send(owner, "Grain is connected.") else {"ok": False, "error": "send_failed"}
