"""Text Grain from your own phone: a two-way bridge over a Telegram bot you own.

Inbound is a long poll of the Bot API (getUpdates); outbound is plain sendMessage. Only one paired private chat is
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

    async def __call__(self, token: str, method: str, params: dict[str, Any], timeout: float = CLIENT_TIMEOUT) -> Any:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=CLIENT_TIMEOUT)
        try:
            r = await self._client.post(f"{API}/bot{token}/{method}", json=params, timeout=timeout)
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

    async def aclose(self) -> None:
        c, self._client = self._client, None
        if c:
            await c.aclose()


# ---------------------------------------------------------------- formatting

_FENCE = re.compile(r"^\s*(```|~~~)")


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


def split_reply(text: str, size: int = MESSAGE_MAX, max_parts: int = 8) -> list[str]:
    """Messages of at most `size` characters, cut at a paragraph, line, sentence or word where one is near the end."""
    rest = (text or "").strip()
    parts: list[str] = []
    while len(rest) > size:
        cut = _cut(rest, size)
        parts.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip()
    if rest:
        parts.append(rest)
    if len(parts) > max_parts:
        parts = parts[:max_parts]
        room = size - len(TRUNCATED) - 1
        parts[-1] = f"{parts[-1][:room].rstrip()}\n{TRUNCATED}"
    return parts


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
    api: Callable[..., Awaitable[Any]] = field(default_factory=HttpApi)  # (token, method, params) -> result; raises TelegramError
    lock_path: Path | None = None  # tests; the default is keyed on the token (lock_path())


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

    def unpair(self) -> None:
        self._save(ownerChatId=None, ownerUserId=None, ownerName=None)
        self.issue_pairing()

    def clear(self) -> None:
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
        await self._owner_message(chat["id"], text, float(m.get("date") or 0) or None, st)

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
        await self._send(chat["id"], "Paired. Text me anything, or /help.")

    # ---- owner messages
    async def _owner_message(self, chat_id: int, text: str, sent_at: float | None, st: dict[str, Any]) -> None:
        where = chat_id
        if not text:
            await self._send(where, "Attachments aren't supported yet — send text.")
            return
        if text.split()[0].split("@")[0].lower() == "/start":
            await self._send(where, HELP)
            return
        cmd = parse_command(text)
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
        if _ANSWER_WORD.match(text) and self._live_texted():  # steering would deny the card; make them answer it
            await self._send(where, "Reply yes or no (or yes <code>) to the approval first.")
            return
        try:
            conv = await self._target()
            res = await _maybe(self.deps.start_turn(conv, text))
        except Exception as e:  # noqa: BLE001
            log.warning("telegram start failed: %s", type(e).__name__)
            await self._send(where, "Grain couldn't take that message right now. Try again in a moment.")
            return
        self._runs.add(res["run_id"])
        self._typing.add(res["run_id"])  # before the task starts: a fast reply must be able to switch it off
        self._spawn(self._typing_loop(res["run_id"], where))
        log.info("telegram chat=%s started run len=%s", _mask(chat_id), len(text))

    async def _typing_loop(self, run_id: str, chat_id: int) -> None:
        token = self.deps.get_token()
        end = time.monotonic() + 1800  # a run that never reports its end must not type forever
        while run_id in self._typing and token and time.monotonic() < end:
            with contextlib.suppress(Exception):
                await self.deps.api(token, "sendChatAction", {"chat_id": chat_id, "action": "typing"}, 10.0)
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
            cid = await _maybe(self.deps.create_texts_conversation())
            self._save(textsConversationId=cid)
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
                await self.deps.api(token, "editMessageText", {"chat_id": msg["chat"]["id"], "message_id": msg["message_id"],
                                                               "text": f"{msg.get('text') or ''}\n\n{outcome}"}, 10.0)

    # ---- sending
    async def _send(self, chat_id: int, text: str, markup: dict[str, Any] | None = None) -> bool:
        token = self.deps.get_token()
        if not token:
            return False
        params: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if markup:
            params["reply_markup"] = markup
        async with self._send_lock:
            for attempt in (0, 1):
                try:
                    await self.deps.api(token, "sendMessage", params, 15.0)
                    log.info("telegram send chat=%s len=%s ok=True", _mask(chat_id), len(text))
                    return True
                except TelegramError as e:
                    if e.code == 429 and attempt == 0:
                        await self._sleep(min(e.retry_after or 1.0, 30.0))
                        continue
                    log.info("telegram send chat=%s failed: code=%s", _mask(chat_id), e.code)
                except Exception as e:  # noqa: BLE001
                    log.info("telegram send chat=%s failed: %s", _mask(chat_id), type(e).__name__)
                return False
        return False

    async def _send_all(self, chat_id: int, text: str) -> None:
        for part in split_reply(text):
            await self._send(chat_id, part)

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
            self._spawn(self._notify_approvals(run, owner))
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
        if run.run_id in self._muted.seen or run.status == "interrupted":  # a backend going down sends nothing
            return
        text = to_plain((self.deps.message_text(run.message_id) if run.message_id else "") or "")
        if not text:
            text = RUN_ERROR if run.error else "Stopped."  # the error text itself stays off the phone
        await self._send_all(chat_id, text)

    async def _long_run_note(self, run: Any, took: float, chat_id: int) -> None:
        title = self.deps.conversation_title(run.conversation_id) or "a chat"
        await self._send(chat_id, f"Grain finished: {_one_line(title, 80)} ({max(1, round(took / 60))} min)")

    async def _notify_approvals(self, run: Any, chat_id: int) -> None:
        notify_all = bool(self._settings().get("telegramNotifyLongRuns"))
        async with self._approval_lock:  # the code and the sent mark are decided together, one card at a time
            target_conv = self._peek_target()
            for a in self.deps.pending_approvals():
                cid = a["call_id"]
                if a.get("run_id") != run.run_id or cid in self._texted.seen or cid in self._told.seen:
                    continue
                if not self.deps.is_live(cid) or not (a.get("conversation_id") == target_conv or notify_all):
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
