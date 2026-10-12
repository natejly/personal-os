"""Model duels (developer mode): one request sent to two models at once, a vote on which result is better.

Each side runs its whole turn: app.py `_chat_stream` is run again for a hidden copy of the chat (a "shadow" conversation)
per side, with `SIDE` set so every tool call is judged by `Side.gate` (Toolbox.call, _mcp_call):

* reads run for real, on both sides;
* file writes run against the side's overlay, a temp copy of the desk folder / chat outbox (`Mount`): the workspace
  resolves its folder through `workspace.OVERLAY`, and a path argument that names the real folder is rewritten to the
  copy (and back in the result). Writes elsewhere on the Mac are held;
* a shell command runs in the OS sandbox with writes confined to the overlay and a scratch dir (osplat confine);
  without that sandbox, or with open network, it is held;
* everything else (mail, calendar, tasks, docs, memory, connectors, workers, coding sessions, the browser) is held: it
  does not run, the model is told it is queued, and the call is kept on the side as a pending action.

On a pick the winner's overlay is promoted into the real folders, the loser's is deleted, and the held actions run
for real through the normal tool loop (permission rules and approval cards apply). `merge` interleaves the two sides'
events, each tagged with its side. `Votes` keeps the head-to-head record and `standings` turns it into wins, a simple Elo
and mean latency per model. Cost doubles while a duel runs: both sides pay for every round.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import time
from contextvars import ContextVar
from pathlib import Path
from typing import Any, AsyncIterator, Callable


WINNERS = ("a", "b", "tie", "bad")
ELO_START, ELO_K = 1000.0, 32.0
EXCERPT_CHARS = 200


async def merge(streams: dict[str, AsyncIterator[dict[str, Any]]]) -> AsyncIterator[tuple[str, dict[str, Any]]]:
    """Yield (side, event) from every stream as events arrive. A stream that raises ends with an `error` event."""
    q: asyncio.Queue[tuple[str, dict[str, Any] | None]] = asyncio.Queue()

    async def pump(side: str, s: AsyncIterator[dict[str, Any]]) -> None:
        try:
            async for ev in s:
                await q.put((side, ev))
        except Exception as e:  # noqa: BLE001 - one side failing must not end the other
            await q.put((side, {"type": "error", "message": str(e) or type(e).__name__}))
        finally:
            await q.put((side, None))

    tasks = [asyncio.create_task(pump(side, s)) for side, s in streams.items()]
    live = len(tasks)
    try:
        while live:
            side, ev = await q.get()
            if ev is None:
                live -= 1
            else:
                yield side, ev
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def call_summary(call: dict[str, Any]) -> dict[str, str]:
    """A proposed tool call as the duel column shows it: name and a short argument line."""
    raw = call.get("arguments")
    text = raw if isinstance(raw, str) else json.dumps(raw or {}, ensure_ascii=False)
    return {"name": str(call.get("name") or ""), "args": text[:300]}


# The side whose turn the current task runs, if any. Set in the side's own task (merge() makes one per side).
SIDE: ContextVar["Side | None"] = ContextVar("duel_side", default=None)

HELD_NOTE = ("Not run yet: this is a preview of your answer, so the action is queued and runs once the user picks it. "
             "Carry on as if it were queued: do not repeat it and do not tell the user it already happened.")
HOLD_GROUPS = frozenset({"agents", "browser", "setup"})  # workers, the shared browser session, setup cards
HOLD_NAMES = frozenset({"desk_done", "screenshot", "send_files", "deep_research"})
# Writes that stay inside the overlay by construction (the workspace resolves its folder through it).
OVERLAY_WRITERS = frozenset({"desk_write_file", "desk_trash_file", "todo_write", "shell_kill"})
# Tools that take a real path: they run only when every write argument lands inside an overlay.
PATH_WRITERS = {"fs_edit": ("path",), "fs_copy": ("dst",), "fs_mkdir": ("path",), "write_local_file": ("path",),
                "move_local_file": ("path", "to"), "convert_document": ("path",), "render_preview": ("path",)}
SKIP_DIRS = frozenset({".baseline", ".trash", "inputs"})  # the workspace's own bookkeeping: never listed as produced
MAX_LIST = 200


def ab_root(data_dir: str | Path) -> Path:
    return Path(data_dir) / "tmp" / "ab"


def _stat_tree(root: Path) -> dict[str, tuple[int, int]]:
    """rel path -> (size, mtime_ns) of every file under root."""
    out: dict[str, tuple[int, int]] = {}
    for base, _dirs, names in os.walk(root):
        for n in names:
            p = Path(base) / n
            try:
                st = p.stat()
            except OSError:
                continue
            out[p.relative_to(root).as_posix()] = (st.st_size, st.st_mtime_ns)
    return out


class Mount:
    """One real folder a side writes through a private copy: `key` is the folder the workspace asks for, `real` where its
    files belong after a pick, `dst` the side's copy. A desk's folder is key == real; a chat's outbox is the shadow
    chat's folder (key) standing for the real chat's (real)."""

    def __init__(self, key: Path, real: Path, dst: Path, save: Callable[[Path], None] | None) -> None:
        self.key, self.real, self.dst, self.save = key, real, dst, save
        self.base: dict[str, tuple[int, int]] = {}

    def seed(self) -> None:
        # ponytail: a full copy of the folder; an overlay by path would copy only what a side touches, add when folders get huge
        if self.real.is_dir():
            shutil.copytree(self.real, self.dst, symlinks=True, dirs_exist_ok=True)
        self.dst.mkdir(parents=True, exist_ok=True)
        self.base = _stat_tree(self.dst)

    def changes(self) -> tuple[list[str], list[str]]:
        """(files written or changed, files removed) since the copy was made."""
        now = _stat_tree(self.dst)
        return [r for r, v in now.items() if self.base.get(r) != v], [r for r in self.base if r not in now]

    def produced(self) -> list[dict[str, Any]]:
        out = []
        for rel in self.changes()[0]:
            if rel.split("/", 1)[0] in SKIP_DIRS or any(part.startswith(".") for part in rel.split("/")):
                continue
            out.append({"path": rel, "size": (self.dst / rel).stat().st_size})
        return sorted(out, key=lambda f: f["path"])[:MAX_LIST]

    def promote(self) -> list[Path]:
        """Copy the side's changes over the real folder, delete what it removed, and record each file written."""
        written, removed = self.changes()
        done: list[Path] = []
        for rel in written:
            dst = self.real / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self.dst / rel, dst)
            done.append(dst)
            if self.save is not None:
                try:
                    self.save(dst)
                except Exception:  # noqa: BLE001 - recording a file must not undo the promotion
                    pass
        for rel in removed:
            (self.real / rel).unlink(missing_ok=True)
        return done


class Side:
    """One side of a full duel: its model, its overlay mounts, the actions it was refused and what it produced."""

    def __init__(self, key: str, model: str, root: Path) -> None:
        self.key, self.model, self.dir = key, model, root
        self.mounts: list[Mount] = []
        self.held: list[dict[str, Any]] = []
        self.shadow_id: str | None = None  # the hidden chat this side's turn runs in
        self.live: tuple[list[dict[str, Any]], Callable[[], int], Callable[[], Any]] | None = None  # (messages, base index, plan message) of the turn
        self.done: dict[str, Any] = {}  # the shadow turn's `done` payload: tool events, taint, error

    # ---- overlay ----
    def add_mount(self, key: Path, real: Path, name: str, save: Callable[[Path], None] | None) -> None:
        self.mounts.append(Mount(key, real, self.dir / name, save))

    def seed(self) -> None:
        for m in self.mounts:
            m.seed()

    def remap(self, folder: Path) -> Path:
        """workspace.OVERLAY: the side's copy of a folder it has one for, else the folder itself."""
        for m in self.mounts:
            if folder == m.key:
                return m.dst
        return folder

    def _swap(self, value: Any, pairs: list[tuple[str, str]]) -> Any:
        if isinstance(value, str):
            for a, b in pairs:
                value = value.replace(a, b)
            return value
        if isinstance(value, list):
            return [self._swap(v, pairs) for v in value]
        if isinstance(value, dict):
            return {k: self._swap(v, pairs) for k, v in value.items()}
        return value

    def _pairs(self, back: bool) -> list[tuple[str, str]]:
        # Only a mount whose key is the real folder (a desk) needs paths rewritten: the model was told the real one.
        out = []
        for m in self.mounts:
            if m.key == m.real:
                a, b = str(m.real), str(m.dst)
                out.append((b, a) if back else (a, b))
        return out

    def to_overlay(self, args: Any) -> Any:
        return self._swap(args, self._pairs(False))

    def to_real(self, result: Any) -> Any:
        return self._swap(result, self._pairs(True))

    def inside(self, raw: Any, ctx: dict[str, Any]) -> bool:
        """True when a path argument lands in one of this side's overlay folders."""
        s = str(raw or "").strip()
        if not s:
            return False
        p = Path(os.path.expanduser(s))
        if not p.is_absolute():
            return bool(ctx.get("desk_id"))  # relative to the desk folder (already the overlay), else to home
        try:
            r = p.resolve()
        except OSError:
            return False
        return any(r == m.dst.resolve() or r.is_relative_to(m.dst.resolve()) for m in self.mounts)

    # ---- the gate ----
    def runs(self, tb: Any, name: str, args: dict[str, Any], ctx: dict[str, Any]) -> bool:
        """True when this call may really run on the side: a read, a write into the overlay, a confined shell."""
        spec = tb.specs.get(name)
        if spec is None:
            return False
        args = args if isinstance(args, dict) else {}
        if name in PATH_WRITERS:
            given = [args.get(a) for a in PATH_WRITERS[name] if args.get(a)]
            return bool(given) and all(self.inside(a, ctx) for a in given)
        if name == "shell_run":
            from . import permissions, shell
            return (not args.get("unsandboxed") and shell.sandbox_available()
                    and not permissions.get(ctx.get("settings") or tb.settings(), "shellNetwork"))
        if name == "run_python":
            from .tools import python_unsandboxed
            return not python_unsandboxed()
        if name in OVERLAY_WRITERS:
            return True
        if spec.danger in ("safe", "network"):
            return spec.group not in HOLD_GROUPS and name not in HOLD_NAMES
        return False

    def hold(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        self.held.append({"name": name, "arguments": args})
        return {"held": True, "status": "queued", "note": HELD_NOTE}

    def gate(self, tb: Any, name: str, args: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any] | None:
        """None when the call runs; else the result a held call returns (the call is kept as a pending action)."""
        return None if self.runs(tb, name, self.to_overlay(args), ctx) else self.hold(name, args)

    # ---- outcome ----
    def transcript(self) -> list[dict[str, Any]]:
        """The model messages the turn added after its context: assistant tool turns, tool results, notes."""
        if self.live is None:
            return []
        messages, base, plan = self.live
        skip = plan()
        return [m for m in messages[base():] if m is not skip]

    def files(self) -> list[dict[str, Any]]:
        return [f for m in self.mounts for f in m.produced()]

    def promote(self) -> list[Path]:
        return [p for m in self.mounts for p in m.promote()]

    def read_file(self, rel: str, limit: int = 20_000) -> dict[str, Any] | None:
        """A produced file's text (cut at `limit`), or None when this side has no such file."""
        for m in self.mounts:
            p = (m.dst / rel).resolve()
            if p.is_file() and p.is_relative_to(m.dst.resolve()):
                data = p.read_bytes()
                binary = b"\0" in data[:4096]
                return {"name": rel, "size": len(data), "binary": binary, "text": "" if binary else data[:limit].decode("utf-8", "replace")}
        return None

    def discard(self) -> None:
        shutil.rmtree(self.dir, ignore_errors=True)


def discard_all(data_dir: str | Path, message_id: str | None = None) -> None:
    """Remove a duel's temp folders (every duel's when no id is given: the sweep at startup)."""
    shutil.rmtree(ab_root(data_dir) / message_id if message_id else ab_root(data_dir), ignore_errors=True)


def elo(votes: list[dict[str, Any]]) -> dict[str, float]:
    """Elo per model from votes in order. A tie is half a win; `bad` (both bad) moves nobody."""
    r: dict[str, float] = {}
    for v in votes:
        if v["winner"] == "bad" or v["model_a"] == v["model_b"]:
            continue
        ra, rb = r.setdefault(v["model_a"], ELO_START), r.setdefault(v["model_b"], ELO_START)
        sa = {"a": 1.0, "b": 0.0, "tie": 0.5}[v["winner"]]
        ea = 1 / (1 + 10 ** ((rb - ra) / 400))
        r[v["model_a"]], r[v["model_b"]] = ra + ELO_K * (sa - ea), rb + ELO_K * ((1 - sa) - (1 - ea))
    return r


def standings(votes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per model: wins, losses, ties, duels, win rate (a tie counts half), Elo and mean latency, best Elo first."""
    rows: dict[str, dict[str, Any]] = {}
    ms: dict[str, list[int]] = {}
    for v in votes:
        for side, model in (("a", v["model_a"]), ("b", v["model_b"])):
            row = rows.setdefault(model, {"model": model, "wins": 0, "losses": 0, "ties": 0, "bad": 0})
            if v["winner"] == "tie":
                row["ties"] += 1
            elif v["winner"] == "bad":
                row["bad"] += 1
            elif v["winner"] == side:
                row["wins"] += 1
            else:
                row["losses"] += 1
            if (t := v.get(f"{side}_ms")) is not None:
                ms.setdefault(model, []).append(int(t))
    ratings = elo(votes)
    out = []
    for m, row in rows.items():
        n = row["wins"] + row["losses"] + row["ties"]
        out.append({**row, "duels": n + row["bad"], "win_rate": round((row["wins"] + row["ties"] / 2) / n, 3) if n else None,
                    "elo": round(ratings.get(m, ELO_START)), "mean_ms": round(sum(ms[m]) / len(ms[m])) if m in ms else None})
    return sorted(out, key=lambda r: -r["elo"])


class Votes:
    def __init__(self, db: Any):
        self.db = db

    def add(self, *, conversation_id: str, model_a: str, model_b: str, winner: str, prompt: str,
            a_ms: int | None, b_ms: int | None, a_tokens: int | None, b_tokens: int | None) -> None:
        if winner not in WINNERS:
            raise ValueError(f"winner must be one of {WINNERS}")
        with self.db.tx() as c:
            c.execute("INSERT INTO model_votes(created_at, conversation_id, model_a, model_b, winner, prompt_excerpt, a_ms, b_ms, a_tokens, b_tokens)"
                      " VALUES(?,?,?,?,?,?,?,?,?,?)", (time.time(), conversation_id, model_a, model_b, winner, prompt[:EXCERPT_CHARS],
                                                        a_ms, b_ms, a_tokens, b_tokens))

    def all(self) -> list[dict[str, Any]]:
        with self.db.tx() as c:
            return [dict(r) for r in c.execute("SELECT * FROM model_votes ORDER BY created_at, id")]

    def standings(self) -> dict[str, Any]:
        votes = self.all()
        return {"votes": len(votes), "models": standings(votes)}

    def reset(self) -> None:
        with self.db.tx() as c:
            c.execute("DELETE FROM model_votes")
