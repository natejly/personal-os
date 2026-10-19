"""Terminals an agent drives: a shell it opens, types lines into, reads and waits on, that the user can take over.

The PTYs live in the Electron main process (src/main/terminals.ts), the one place node-pty is used. This side owns what
the agent may do with them. The backend never starts a process for a terminal: it builds the argv (the user's login shell
under the same OS sandbox `shell_run` uses), and the main process, which polls `TerminalBridge.next` and answers through
`reply`, runs it. The renderer never supplies a command, argv or folder.

Ownership: a terminal belongs to the agent that opened it (a chat's agent and its later replies are one owner; a worker,
a subagent, a workflow step, another chat or a job is another). Anyone else gets the same "No such terminal" a missing
id gets, so ids cannot be probed.

A line typed into a terminal is judged like a `shell_run` command (`judge`), and what cannot be judged is refused: the
command outlives the call, so nothing can be diffed afterwards. What comes back is untrusted program output: control
sequences stripped, credentials scrubbed, cut to a tail, and it taints the run.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import secrets
import shutil
import tempfile
import time
from collections import deque
from typing import TYPE_CHECKING, Any

from ... import logs, mac, permrules, redact, reliability, shell
from ...bounds import clamp_int
from ...db import new_id
from . import opencode, route

if TYPE_CHECKING:
    from .extension import Coding

log = logging.getLogger(__name__)

HOLD_S, CALL_TIMEOUT_S, POLLER_SEEN_S = 25.0, 10.0, 3.0  # a bridge poll is held this long; a command is answered within; a poller is "there" this long
MAX_PER_OWNER, MAX_AGENT = 3, 6
SEND_MAX, SENDS_PER_MIN = 4096, 30
READ_MAX = 16 * 1024
RAW_MAX = 256 * 1024  # what one read may pull from the hub before cleaning (it keeps 200 KB)
SCREEN_COLS, SCREEN_ROWS = (20, 300), (5, 100)
WAIT_MIN_S, WAIT_MAX_S, WAITS_PER_OWNER = 1, 600, 4
IDLE_MS = (200, 60_000)
POLL_S = 0.25
QUIET_PROMPT_S = 0.4
MATCH_CAP = 64 * 1024  # characters of new text a pattern is run on
PATTERN_MAX = 200
IDLE_CLOSE_S = 30 * 60
KEYS = {"ctrl-c": "\x03", "ctrl-d": "\x04", "escape": "\x1b", "up": "\x1b[A", "down": "\x1b[B", "right": "\x1b[C", "left": "\x1b[D",
        "tab": "\t"}
OPAQUE = re.compile(r"python[\d.]*|node|deno|bun|perl|ruby|sh|bash|zsh|fish|vim|vi|nano|emacs|less|man")
DIR_CHANGERS = frozenset({"cd", "pushd", "popd", "chdir"})
PROMPT_END = re.compile(r"(\?|:|>|\$|#|%|\]|\(y/n\)|\(Y/n\)|\(y/N\))\s*$")
CSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
OSC = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
ESC = re.compile(r"\x1b[@-Z\\-_]|\x1b[()][0-9A-Za-z]|\x1b.")
CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
LINE_BAD = re.compile(r"[\x00-\x08\x0a-\x1f\x7f]")  # a typed line may hold a tab and nothing else below space


class TerminalError(ValueError):
    """What the model is told. `alt` is what it can do instead."""

    def __init__(self, message: str, alt: str | None = None) -> None:
        super().__init__(message)
        self.alt = alt


HUB_NO_SUCH = "No such terminal"  # what the hub answers for an id it does not have
NO_SUCH = HUB_NO_SUCH + "."


class TerminalBridge:
    """The backend's end of the link to the main process: it long-polls `next` for commands and answers each through `reply`."""

    def __init__(self) -> None:
        self._commands: asyncio.Queue[dict[str, Any]] | None = None
        self._replies: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._seen = 0.0
        self._holding = 0

    @property
    def connected(self) -> bool:
        """Whether a main process is polling: one is waiting now, or finished a poll a moment ago."""
        return self._holding > 0 or time.monotonic() - self._seen < POLLER_SEEN_S

    async def next(self, hold: float = HOLD_S) -> dict[str, Any] | None:
        if self._commands is None:
            self._commands = asyncio.Queue()
        self._holding += 1
        try:
            async with asyncio.timeout(hold):
                while True:
                    command = await self._commands.get()
                    if command["id"] in self._replies:  # a command whose caller gave up waiting is never delivered
                        return command
        except TimeoutError:
            return None
        finally:
            self._holding -= 1
            self._seen = time.monotonic()

    def reply(self, command_id: str, result: dict[str, Any]) -> bool:
        waiting = self._replies.get(command_id)
        if waiting is None or waiting.done():
            return False
        waiting.set_result(result)
        return True

    async def call(self, command: dict[str, Any], timeout: float = CALL_TIMEOUT_S) -> dict[str, Any]:
        """Send one command and wait for the main process's answer; a TerminalError says what went wrong."""
        if not self.connected:
            raise TerminalError("Terminals need the Grain desktop app, and it is not connected.")
        if self._commands is None:
            self._commands = asyncio.Queue()
        command_id = new_id()
        waiting: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._replies[command_id] = waiting
        await self._commands.put({"id": command_id, **command})
        try:
            result = await asyncio.wait_for(waiting, timeout)
        except asyncio.TimeoutError:
            raise TerminalError("The desktop app did not answer the terminal command in time.") from None
        finally:
            self._replies.pop(command_id, None)
        if result.get("error"):
            raise TerminalError(str(result["error"]))
        return result


# ---- what comes back from a terminal ----
def clean_output(raw: str, cfg: dict[str, Any]) -> str:
    """Terminal output as the model may see it: control sequences gone, a progress line that rewrote itself kept as its last
    frame, credentials scrubbed (patterns, then the registered secret values)."""
    text = raw.replace("\r\n", "\n")
    text = OSC.sub("", text)
    text = CSI.sub("", text)
    text = ESC.sub("", text)
    text = "\n".join(line.rsplit("\r", 1)[-1] for line in text.split("\n"))
    text = CONTROL.sub("", text)
    return logs.redact_literals(redact.scrub_command_output(text), reliability.secret_values(cfg))


def tail(text: str, cap: int = READ_MAX) -> tuple[str, int]:
    """(the last `cap` characters, how many were cut)."""
    return (text[-cap:], len(text) - cap) if len(text) > cap else (text, 0)


# ---- a pattern the agent waits for ----
def safe_pattern(pattern: str) -> re.Pattern[str]:
    """A compiled pattern that cannot run away: short, without backreferences or lookarounds, and with no alternation or
    repeat inside an unbounded repeat. Python's `re` cannot be interrupted, so what could backtrack badly is refused. If
    the parser is not there to ask, every pattern is refused (a literal still works)."""
    if len(pattern) > PATTERN_MAX:
        raise TerminalError(f"pattern is over {PATTERN_MAX} characters.", "use `literal` for fixed text")
    try:
        from re import _parser  # the private module that parses a pattern; absent on a Python that moved it
        parsed = _parser.parse(pattern)
        compiled = re.compile(pattern)
    except ImportError:
        raise TerminalError("regular expressions are not available here.", "use `literal`") from None
    except re.error as e:
        raise TerminalError(f"pattern is not a valid regular expression: {e}.", "use `literal` for fixed text") from None
    if why := _risky(parsed, False, _parser):
        raise TerminalError(f"pattern is refused ({why}).", "use `literal`, or a simpler pattern")
    return compiled


def _risky(items: Any, inside_loop: bool, parser: Any) -> str | None:
    unbounded = parser.MAXREPEAT
    for op, arg in items:
        name = str(op)
        if name in ("GROUPREF", "GROUPREF_EXISTS"):
            return "backreferences"
        if name in ("ASSERT", "ASSERT_NOT"):
            return "lookaround"
        if name in ("MAX_REPEAT", "MIN_REPEAT", "POSSESSIVE_REPEAT"):
            _, hi, body = arg
            loops = hi == unbounded
            if inside_loop and hi > 1:
                return "a repeat inside a repeat"
            if why := _risky(body, inside_loop or loops, parser):
                return why
        elif name == "SUBPATTERN":
            if why := _risky(arg[-1], inside_loop, parser):
                return why
        elif name == "BRANCH":
            if inside_loop:
                return "an alternation inside a repeat"
            for branch in arg[1]:
                if why := _risky(branch, inside_loop, parser):
                    return why
        elif name in ("ATOMIC_GROUP",):
            if why := _risky(arg, inside_loop, parser):
                return why
    return None


class Terminals:
    """The terminal rows, the bridge to the hub, and the rules for using them."""

    def __init__(self, coding: Coding) -> None:
        self.coding = coding
        self.db = coding.api.db
        self.bridge = TerminalBridge()
        self.opening: list[str] = []  # owners of opens in flight: their rows are not inserted yet, but they count toward the caps
        self.scratch: dict[str, tuple[str, str | None]] = {}  # terminal id -> (its temp folder, its proxy run token)
        self.cursors: dict[str, int] = {}  # terminal id -> where the agent last read to
        self.partial: dict[str, str] = {}  # terminal id -> text typed without Enter, so the whole line is judged
        self.edited: set[str] = set()  # terminals whose line was changed by a key, so what is on it is no longer what was judged
        self.sent: dict[str, deque[float]] = {}  # terminal id -> when it was last typed into
        self.waiting: dict[str, int] = {}  # owner -> waits in progress

    # ---- rows and ownership ----
    @staticmethod
    def owner_key(ctx: dict[str, Any]) -> str:
        """Who a terminal belongs to: the chat (or desk, or job's chat) and the agent in it. A chat's own replies are one owner,
        though each has a run id of its own: only a subagent's run id (depth above 0) tells it apart."""
        who = ctx.get("workflow_run_id") or (ctx.get("agent_run_id") if ctx.get("depth") else None) or ctx.get("agent_id") or ""
        return f"{ctx.get('conversation_id') or ctx.get('desk_id') or ''}:{who}"

    def row(self, terminal_id: str) -> dict[str, Any] | None:
        with self.db.tx() as c:
            r = c.execute("SELECT * FROM coding_terminals WHERE id = ?", (str(terminal_id),)).fetchone()
        return dict(r) if r else None

    def mine(self, ctx: dict[str, Any], terminal_id: str) -> dict[str, Any]:
        """The live terminal this caller owns, or the not-found error every other case gets."""
        row = self.row(terminal_id)
        if row is None or row["owner_key"] != self.owner_key(ctx) or row["state"] == "closed":
            raise TerminalError(NO_SUCH, "terminal_open starts one")
        return row

    def _live(self, owner: str | None = None) -> int:
        with self.db.tx() as c:
            return c.execute("SELECT COUNT(*) FROM coding_terminals WHERE state = 'open' AND (?1 IS NULL OR owner_key = ?1)", (owner,)).fetchone()[0]

    async def _call(self, row: dict[str, Any], command: dict[str, Any]) -> dict[str, Any]:
        """Ask the hub about a terminal. One it no longer has (the app restarted) is over: close the row, so it stops counting."""
        try:
            return await self.bridge.call(command)
        except TerminalError as e:
            if str(e) != HUB_NO_SUCH:
                raise
            await self._close(row)
            raise TerminalError(NO_SUCH, "terminal_open starts one") from None

    def _save(self, terminal_id: str, **cols: Any) -> None:
        with self.db.tx() as c:
            c.execute(f"UPDATE coding_terminals SET {', '.join(f'{k} = ?' for k in cols)} WHERE id = ?", (*cols.values(), terminal_id))

    def _touch(self, row: dict[str, Any], reply: dict[str, Any] | None = None) -> None:
        """Record use, and what the hub says about the user: whether they have it open or have typed into it."""
        cols: dict[str, Any] = {"last_used_at": time.time()}
        if reply:
            for key, col in (("attached", "user_attached"), ("dirty", "user_dirty")):
                if key in reply:
                    cols[col] = int(bool(reply[key]))
            if reply.get("exited"):
                cols.update(state="exited", exit_code=reply.get("exit_code"))  # still readable until the agent closes it
        self._save(row["id"], **cols)
        row.update(cols)

    # ---- open ----
    async def open(self, ctx: dict[str, Any], cwd: str | None, command: str | None, cols: int | None, rows: int | None,
                   name: str | None) -> dict[str, Any]:
        if not shell.sandbox_available():
            raise TerminalError(f"Terminals run in the OS sandbox, which is not available here ({shell.no_sandbox_reason()}).")
        owner = self.owner_key(ctx)
        if self._live(owner) + self.opening.count(owner) >= MAX_PER_OWNER or self._live() + len(self.opening) >= MAX_AGENT:
            raise TerminalError(f"At most {MAX_PER_OWNER} terminals per agent and {MAX_AGENT} in all can be open.",
                                "terminal_close one you are done with")
        self.opening.append(owner)
        try:
            return await self._open(ctx, owner, cwd, command, cols, rows, name)
        finally:
            self.opening.remove(owner)

    async def _open(self, ctx: dict[str, Any], owner: str, cwd: str | None, command: str | None, cols: int | None,
                    rows: int | None, name: str | None) -> dict[str, Any]:
        tb = self.coding.tb
        cfg = ctx.get("settings") or tb.settings()
        dr = opencode._desk_root(tb, ctx)
        try:
            where = shell.resolve_cwd(cwd, dr or mac.home(), dr)
        except shell.ShellError as e:
            raise TerminalError(shell._scrub(str(e))) from e
        if command is not None and (why := self.judge(ctx, str(where), command)):
            raise TerminalError(why)
        tmp = os.path.realpath(tempfile.mkdtemp(prefix="pos-term-"))
        env = shell.scrubbed_env(tmp)
        env.pop("NO_COLOR", None)  # a terminal shows colours; the main process sets TERM
        argv = [login_shell(), "-l"]
        try:
            confined = await shell.confine(tb.shell, argv, env, cfg, [str(where), tmp], str(where))
        except shell.ShellError as e:
            shutil.rmtree(tmp, ignore_errors=True)
            raise TerminalError(shell._scrub(str(e))) from e
        label = str(name or "").strip()[:60]  # before the hub opens anything: a failure after that would leave a terminal with no row
        terminal_id = "tm_" + secrets.token_hex(8)
        try:
            await self.bridge.call({"type": "open", "terminal_id": terminal_id, "argv": confined.argv, "env": env, "cwd": str(where),
                                    "cols": clamp_int(cols, *SCREEN_COLS, 120), "rows": clamp_int(rows, *SCREEN_ROWS, 30)})
        except TerminalError:
            self._release(tmp, confined.token)
            raise
        now = time.time()
        session = self._session_of(str(where))
        with self.db.tx() as c:
            c.execute("INSERT INTO coding_terminals(id, owner_key, owner_run_id, session_id, conversation_id, name, cwd, argv_digest, "
                      "created_at, last_used_at, state) VALUES(?,?,?,?,?,?,?,?,?,?,'open')",
                      (terminal_id, owner, ctx.get("run_id"), session, ctx.get("conversation_id"), label, str(where),
                       hashlib.sha256(" ".join(confined.argv).encode()).hexdigest()[:16], now, now))
        self.scratch[terminal_id] = (tmp, confined.token)
        if confined.network or confined.proxied:
            shell.taint(ctx, "terminal:network")  # whatever a networked shell prints may be third-party text
        out: dict[str, Any] = {"terminal_id": terminal_id, "cwd": shell._scrub(str(where)), "cursor": 0, "sandboxed": True}
        if session:
            out["session_id"] = session
        if command is not None:
            try:
                out["cursor"] = (await self.send(ctx, terminal_id, command, None, True, judged=True))["cursor"]
            except TerminalError:
                await self.close(ctx, terminal_id)  # a terminal that could not take its first line is not left open
                raise
        return out

    def _session_of(self, cwd: str) -> str | None:
        """The coding session whose worktree this folder is, or sits inside."""
        real = os.path.realpath(cwd)
        for r in self.coding.sessions.list(200):
            wt = os.path.realpath(r["worktree"])
            if real == wt or real.startswith(wt + os.sep):
                return r["id"]
        return None

    def _release(self, tmp: str, token: str | None) -> None:
        shutil.rmtree(tmp, ignore_errors=True)
        if token:
            self.coding.tb.shell.egress.revoke(token)

    # ---- judging a line ----
    def judge(self, ctx: dict[str, Any], cwd: str, line: str, partial: str = "") -> str | None:
        """Why this line may not be typed into a terminal, or None. The same refusals a `shell_run` gets, in order, plus the
        ones that follow from not being able to look at what the command did afterwards. The deny and ask rules and the
        destructive card are the approval gate's (permrules.subject_for, the tool's `floor`): they apply before this runs."""
        tb = self.coding.tb
        cfg = ctx.get("settings") or tb.settings()
        text = partial + line
        if len(text) > SEND_MAX or LINE_BAD.search(text):
            return f"A line typed into a terminal is one line of at most {SEND_MAX} characters with no control characters."
        if logs.redact_literals(text, reliability.secret_values(cfg)) != text or redact.scrub_command_output(text) != text:
            return "That line contains a credential; a secret is never typed into a terminal."
        if why := permrules.hardline(text):
            return f"Grain never runs this ({why})."
        if (why := permrules.touches_protected(text, cwd)) and "Grain's own" in why:
            return f"Grain's own data folder and app are off limits ({why.split(':')[0]})."
        if route.enabled(cfg):
            if self._changes_dir(text):
                return ("A terminal's folder is the one it was opened in, and a line that changes it would be judged from the wrong place. "
                        "Open a terminal with terminal_open cwd=<folder> instead.")
            if hit := route._shell_refusal(text, cwd):
                return route.refusal_text(cfg, f"a terminal line ({hit[0]})", hit[1])
            if route.repo_root(cwd) and self._opaque(text) and not route._only_verifies(text):
                return (route.refusal_text(cfg, "a terminal line that starts an interpreter, shell, editor or pager", route.repo_root(cwd) or cwd)
                        + " What it would do cannot be judged from here, so it is refused.")
        return None

    @staticmethod
    def _changes_dir(text: str) -> bool:
        parsed = permrules.split_command(text)
        return any((tokens := permrules.strip_wrappers(seg.words, True)) and tokens[0] in DIR_CHANGERS
                   for seg in parsed.segments + parsed.nested)

    @staticmethod
    def _opaque(text: str) -> bool:
        parsed = permrules.split_command(text)
        if parsed.opaque:
            return True
        return any((tokens := permrules.strip_wrappers(seg.words, True)) and OPAQUE.fullmatch(os.path.basename(tokens[0]))
                   for seg in parsed.segments + parsed.nested)

    # ---- typing ----
    async def send(self, ctx: dict[str, Any], terminal_id: str, text: str | None, key: str | None, submit: bool = True, *,
                   judged: bool = False) -> dict[str, Any]:
        """Type one line (judged), or one key from the short list, into a terminal the caller owns."""
        row = self.mine(ctx, terminal_id)
        if (text is None) == (key is None):
            raise TerminalError("Give exactly one of `text` or `key`.")
        if row["user_attached"]:
            raise TerminalError("The user has this terminal open in a window, so you cannot type into it. You can still read it.",
                                "ask the user, or open a terminal of your own")
        if row["user_dirty"] and key != "ctrl-c":
            raise TerminalError("The user typed into this terminal, so it may hold half a line you have not seen. Send key=ctrl-c to "
                                "clear the line first, or open a new terminal.", "terminal_send key=ctrl-c")
        if key is not None:
            if key not in KEYS:
                raise TerminalError(f"key must be one of {', '.join(KEYS)}.")
            data = KEYS[key]
        else:
            if not str(text):
                raise TerminalError("`text` is empty. A line is run by sending its text.")
            if terminal_id in self.edited:
                raise TerminalError("A key changed the line since it was judged, so it may not hold what you typed. Send key=ctrl-c to "
                                    "clear it first.", "terminal_send key=ctrl-c")
            if not judged and (why := self.judge(ctx, row["cwd"], str(text), self.partial.get(terminal_id, ""))):
                raise TerminalError(why)
            self._rate(terminal_id)
            data = str(text) + ("\r" if submit else "")
        reply = await self._call(row, {"type": "write", "terminal_id": terminal_id, "data": data, "clear_dirty": key == "ctrl-c"})
        if blocked := reply.get("blocked"):
            self._touch(row, {"attached": blocked == "attached", "dirty": blocked == "dirty"})
            raise TerminalError("The user is using this terminal, so it was not typed into." if blocked == "attached"
                                else "The user typed into this terminal first; send key=ctrl-c to clear the line.")
        if key in ("ctrl-c", "ctrl-d"):
            self.edited.discard(terminal_id)
        elif key is not None:
            self.edited.add(terminal_id)
        if key is not None or submit:
            self.partial.pop(terminal_id, None)
        else:
            self.partial[terminal_id] = self.partial.get(terminal_id, "") + str(text)
        self._touch(row, reply)
        return {"ok": True, "cursor": int(reply.get("cursor") or 0)}

    def _rate(self, terminal_id: str) -> None:
        recent = self.sent.setdefault(terminal_id, deque())
        now = time.monotonic()
        while recent and now - recent[0] > 60:
            recent.popleft()
        if len(recent) >= SENDS_PER_MIN:
            raise TerminalError(f"At most {SENDS_PER_MIN} lines a minute can be typed into one terminal.", "terminal_wait_for, then send")
        recent.append(now)

    # ---- reading ----
    async def read(self, ctx: dict[str, Any], terminal_id: str, mode: str = "new", cursor: int | None = None,
                   max_bytes: int | None = None) -> dict[str, Any]:
        row = self.mine(ctx, terminal_id)
        cfg = ctx.get("settings") or self.coding.tb.settings()
        cap = max(1, min(int(max_bytes or READ_MAX), READ_MAX))
        reply = await self._fetch(row, mode == "screen", self.cursors.get(terminal_id, 0) if cursor is None else int(cursor))
        raw = reply.get("screen") if mode == "screen" else reply.get("text")
        text, cut = tail(clean_output(str(raw or ""), cfg), cap)
        if mode != "screen":
            self.cursors[terminal_id] = int(reply["cursor"])  # a screen read is a look, not a read: what scrolled off it is still unread
        out = {"text": text, "cursor": int(reply["cursor"]), "dropped": cut, "truncated": cut > 0, "exited": bool(reply.get("exited")),
               "exit_code": reply.get("exit_code"), "user_attached": bool(row["user_attached"])}
        if reply.get("from_cursor_lost"):
            out["from_cursor_lost"] = True
        if text:
            shell.taint(ctx, f"terminal:{terminal_id}")  # program output
        return out

    async def _fetch(self, row: dict[str, Any], screen: bool, cursor: int) -> dict[str, Any]:
        reply = await self._call(row, {"type": "read", "terminal_id": row["id"], "cursor": max(0, cursor), "max_bytes": RAW_MAX,
                                       "screen": screen})
        self._touch(row, reply)
        return reply

    async def wait_for(self, ctx: dict[str, Any], terminal_id: str, *, pattern: str | None, literal: str | None, idle_ms: int | None,
                       cursor: int | None, timeout_s: Any) -> dict[str, Any]:
        """Wait, by polling the hub every POLL_S, until the terminal's new output matches, the process exits, it goes quiet or
        looks to be waiting for input, or the time is up. Matching runs here, never in the main process."""
        row = self.mine(ctx, terminal_id)
        cfg = ctx.get("settings") or self.coding.tb.settings()
        if isinstance(timeout_s, bool) or not isinstance(timeout_s, int) or not WAIT_MIN_S <= timeout_s <= WAIT_MAX_S:
            raise TerminalError(f"timeout_s is required, a whole number of seconds from {WAIT_MIN_S} to {WAIT_MAX_S}.")
        if pattern is not None and literal is not None:
            raise TerminalError("Give `pattern` or `literal`, not both.")
        compiled = safe_pattern(pattern) if pattern else re.compile(re.escape(literal)) if literal else None
        if idle_ms is not None and (isinstance(idle_ms, bool) or not isinstance(idle_ms, int) or not IDLE_MS[0] <= idle_ms <= IDLE_MS[1]):
            raise TerminalError(f"idle_ms must be a whole number from {IDLE_MS[0]} to {IDLE_MS[1]}.")
        owner = row["owner_key"]
        if self.waiting.get(owner, 0) >= WAITS_PER_OWNER:
            raise TerminalError(f"At most {WAITS_PER_OWNER} waits can run at once.")
        self.waiting[owner] = self.waiting.get(owner, 0) + 1
        try:
            return await self._wait(ctx, row, cfg, compiled, literal, idle_ms, self.cursors.get(terminal_id, 0) if cursor is None else int(cursor),
                                    float(timeout_s))
        finally:
            self.waiting[owner] -= 1

    async def _wait(self, ctx: dict[str, Any], row: dict[str, Any], cfg: dict[str, Any], compiled: re.Pattern[str] | None,
                    literal: str | None, idle_ms: int | None, start: int, timeout_s: float) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_s
        text, cursor, last_output, dropped = "", start, time.monotonic(), 0
        reason, extra = "timeout", {}
        while True:
            reply = await self._fetch(row, False, cursor)
            fresh = clean_output(str(reply.get("text") or ""), cfg)
            if fresh:
                text, last_output = text + fresh, time.monotonic()
                if len(text) > MATCH_CAP:  # a noisy process cannot grow the wait without bound
                    dropped, text = dropped + len(text) - MATCH_CAP, text[-MATCH_CAP:]
            cursor = int(reply["cursor"])
            quiet = time.monotonic() - last_output
            hit = compiled.search(text[-MATCH_CAP:]) if compiled and text else None
            if reply.get("exited"):
                reason, extra = "exited", {"exit_code": reply.get("exit_code")}
            elif hit:
                reason, extra = "matched", {"match": hit.group(0)[:200]}
            elif quiet >= QUIET_PROMPT_S and await self._looks_for_input(row):
                reason, extra = "stdin_read", {"confidence": "heuristic"}
            elif idle_ms is not None and quiet * 1000 >= idle_ms:
                reason = "idle"
            elif time.monotonic() >= deadline:
                reason = "timeout"
            else:
                await asyncio.sleep(min(POLL_S, max(0.0, deadline - time.monotonic())))
                continue
            break
        self.cursors[row["id"]] = cursor
        shown, cut = tail(text)
        cut += dropped
        if shown:
            shell.taint(ctx, f"terminal:{row['id']}")
        return {"reason": reason, "text": shown, "cursor": cursor, **({"dropped": cut} if cut else {}), **extra}

    async def _looks_for_input(self, row: dict[str, Any]) -> bool:
        """Whether the screen's last line ends the way a prompt does while the process is alive."""
        screen = await self._call(row, {"type": "read", "terminal_id": row["id"], "cursor": 0, "max_bytes": 1, "screen": True})
        lines = [ln for ln in clean_output(str(screen.get("screen") or ""), {}).splitlines() if ln.strip()]
        return bool(lines) and not screen.get("exited") and bool(PROMPT_END.search(lines[-1]))

    # ---- closing ----
    async def close(self, ctx: dict[str, Any], terminal_id: str) -> dict[str, Any]:
        return await self._close(self.mine(ctx, terminal_id))

    async def _close(self, row: dict[str, Any]) -> dict[str, Any]:
        code = None
        try:
            code = (await self.bridge.call({"type": "close", "terminal_id": row["id"]})).get("exit_code")
        except TerminalError:
            log.info("terminal %s could not be closed in the hub (gone already, or the app is not connected)", row["id"])
        self._save(row["id"], state="closed", exit_code=code)
        row["state"] = "closed"
        if scratch := self.scratch.pop(row["id"], None):
            self._release(*scratch)
        for memory in (self.cursors, self.partial, self.sent):
            memory.pop(row["id"], None)
        self.edited.discard(row["id"])
        return {"closed": True, **({"exit_code": code} if code is not None else {})}

    async def close_where(self, where: str, *args: Any) -> None:
        """Close every terminal not yet closed matching `where` (a SQL condition over coding_terminals) with `args`."""
        with self.db.tx() as c:
            rows = [dict(r) for r in c.execute(f"SELECT * FROM coding_terminals WHERE state != 'closed' AND {where}", args)]
        for row in rows:
            await self._close(row)

    async def watch(self) -> None:
        """Close terminals nobody has used for IDLE_CLOSE_S."""
        while True:
            await asyncio.sleep(60)
            try:
                await self.close_where("last_used_at < ?", time.time() - IDLE_CLOSE_S)
            except Exception:  # noqa: BLE001 - the reaper outlives any one bad row
                log.warning("terminal reaper failed", exc_info=True)

    async def live_status(self) -> list[dict[str, Any]]:
        """The open terminals, newest first, for the Terminal card: each asked about once, so a process that ended and the
        user's having it open are shown as they are."""
        with self.db.tx() as c:
            rows = [dict(r) for r in c.execute("SELECT * FROM coding_terminals WHERE state = 'open' ORDER BY created_at DESC")]
        out = []
        for row in rows:
            try:
                self._touch(row, await self._call(row, {"type": "status", "terminal_id": row["id"]}))
            except TerminalError:
                pass  # the app is not connected: show what was last known (a terminal it lost is closed, and drops out below)
            if row["state"] == "open":
                out.append({k: row[k] for k in ("id", "name", "cwd", "session_id", "owner_key", "created_at")}
                           | {"user_attached": bool(row["user_attached"]), "user_dirty": bool(row["user_dirty"]), "sandboxed": True})
        return out


def login_shell() -> str:
    """The user's own shell, as a terminal shows it."""
    configured = os.environ.get("SHELL") or ""
    return configured if configured.startswith("/") and os.access(configured, os.X_OK) else "/bin/zsh" if os.path.exists("/bin/zsh") else "/bin/sh"

