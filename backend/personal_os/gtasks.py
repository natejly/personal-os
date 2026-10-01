"""Two-way sync between the native todo list and one Google Tasks list.

Full-state merge rather than incremental: every pass fetches the whole remote list
(personal task lists are small) and reconciles. Per linked todo:
- locally edited since last sync  -> push;
- remotely edited since last sync -> pull;
- both                            -> last write wins by timestamp.
Unlinked local todos become remote tasks, unmatched remote tasks become local todos,
tombstones (todos.py) carry local deletes out, and a task missing remotely deletes its
local mirror. Priority and project stay local-only — Google Tasks has no equivalent.

The loop (started at app startup) syncs every `intervalMinutes`, or ~2 s after a todo
changes (todos.on_change -> poke), so edits land in Google almost immediately.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
import time
from typing import Any, Callable

from .google import Google, GoogleNotConnected, _parse_iso
from .todos import Todos

log = logging.getLogger(__name__)

DEFAULT_CONFIG: dict[str, Any] = {"enabled": True, "tasklist": "@default", "intervalMinutes": 5}
# A poke waits this long before syncing so a burst of edits becomes one pass.
POKE_DEBOUNCE = 2.0


def _remote_wins(rt: dict[str, Any], td: dict[str, Any]) -> bool:
    try:
        return _parse_iso(rt.get("updated") or "").timestamp() >= float(td["updated_at"])
    except ValueError:
        return True

def _date_only(due: str | None) -> str | None:
    # Tasks due dates are midnight UTC with a meaningless time part; keep the date.
    return due[:10] if due else None


def _remote_body(td: dict[str, Any]) -> dict[str, Any]:
    return {"title": td["title"] or "(untitled)", "notes": td["notes"] or "", "due": td["due"],
            "status": "completed" if td["done"] else "needsAction"}


def _is_missing(e: Exception) -> bool:
    s = str(e)
    return "404" in s or "not found" in s.lower()


class TasksSync:
    def __init__(self, todos: Todos, google: Google, get_settings: Callable[[], dict[str, Any]], set_settings: Callable[[dict[str, Any]], None]):
        self.todos = todos
        self.google = google
        self.get_settings = get_settings
        self.set_settings = set_settings
        self.last_sync: float | None = None
        self.last_error: str | None = None
        self.last_result: dict[str, int] | None = None
        self._lock = threading.Lock()  # manual "sync now" may race the loop
        self._event: asyncio.Event | None = None
        self._loop_ref: asyncio.AbstractEventLoop | None = None
        self._syncing = False

    # ---- config / status ----
    def config(self) -> dict[str, Any]:
        stored = self.get_settings().get("googleTasksSync") or {}
        return {**DEFAULT_CONFIG, **{k: v for k, v in stored.items() if k in DEFAULT_CONFIG}}

    def set_config(self, patch: dict[str, Any]) -> dict[str, Any]:
        cfg = {**self.config(), **{k: v for k, v in patch.items() if k in DEFAULT_CONFIG}}
        self.set_settings({"googleTasksSync": cfg})
        if cfg["enabled"]:
            self.poke()
        return cfg

    def status(self) -> dict[str, Any]:
        return {"config": self.config(), "last_sync": self.last_sync, "last_error": self.last_error,
                "last_result": self.last_result, "syncing": self._syncing}

    # ---- scheduling ----
    def poke(self) -> None:
        """Ask the loop to sync soon; safe from any thread (todo routes run in a threadpool).

        The loop reference outlives the loop itself if the backend is torn down and rebuilt in
        one process, so a closed loop is ignored rather than raising into the caller's request.
        """
        loop, ev = self._loop_ref, self._event
        if not loop or not ev or loop.is_closed():
            return
        with contextlib.suppress(RuntimeError):
            loop.call_soon_threadsafe(ev.set)

    async def loop(self) -> None:
        """Periodic sync plus fast follow-up after local edits. Started once at app startup."""
        self._event = asyncio.Event()
        self._loop_ref = asyncio.get_running_loop()
        while True:
            try:
                interval = max(60.0, float(self.config().get("intervalMinutes") or 5) * 60)
                try:
                    await asyncio.wait_for(self._event.wait(), timeout=interval)
                    await asyncio.sleep(POKE_DEBOUNCE)
                except asyncio.TimeoutError:
                    pass
                self._event.clear()
                if self.config()["enabled"]:
                    await asyncio.to_thread(self.sync_once)
            except asyncio.CancelledError:
                raise
            except GoogleNotConnected:
                pass  # recorded in last_error; nothing to do until the user reconnects
            except Exception as e:  # noqa: BLE001 - the loop must outlive any single failure
                log.warning("google tasks sync: %s", e)
                await asyncio.sleep(30)

    # ---- the merge ----
    def sync_once(self) -> dict[str, int]:
        """One full two-way pass. Blocking; run it off the event loop."""
        with self._lock:
            self._syncing = True
            try:
                counts = self._merge(self.config().get("tasklist") or "@default")
                self.last_sync = time.time()
                self.last_error = None
                self.last_result = counts
                return counts
            except Exception as e:  # noqa: BLE001
                self.last_error = str(e) if isinstance(e, GoogleNotConnected) else f"{type(e).__name__}: {e}"
                raise
            finally:
                self._syncing = False

    def _merge(self, tasklist: str) -> dict[str, int]:
        counts = {"pulled": 0, "pushed": 0, "created_local": 0, "created_remote": 0,
                  "deleted_local": 0, "deleted_remote": 0}
        remote = {t["id"]: t for t in self.google.tasks_all(tasklist)}

        # Local deletions first: a tombstone means a synced todo was deleted here.
        for ts in self.todos.tombstones():
            eid = ts["external_id"]
            if eid in remote:
                try:
                    self.google.tasks_delete(eid, tasklist)
                    counts["deleted_remote"] += 1
                except Exception as e:  # noqa: BLE001
                    if not _is_missing(e):
                        raise
                remote.pop(eid, None)
            self.todos.clear_tombstone(eid)

        linked: set[str] = set()
        for td in self.todos.all_for_sync():
            eid = td.get("external_id")
            if not eid:
                continue
            linked.add(eid)
            rt = remote.get(eid)
            if rt is None or rt.get("deleted"):
                # Its Google Task is gone; no tombstone, or we would delete it remotely again.
                self.todos.delete(td["id"], notify=False, tombstone=False)
                counts["deleted_local"] += 1
                continue
            local_changed = float(td["updated_at"]) > float(td.get("synced_at") or 0)
            remote_changed = (rt.get("updated") or "") != (td.get("remote_updated") or "")
            if remote_changed and (not local_changed or _remote_wins(rt, td)):
                counts["pulled"] += self._pull(td, rt)
            elif local_changed:
                self._push(td, eid, tasklist)
                counts["pushed"] += 1

        # Remote tasks nothing points at yet -> new local todos. Untitled ones are usually
        # rows someone is still typing into; skip them until they have a name.
        for eid, rt in remote.items():
            if eid in linked or rt.get("deleted") or not (rt.get("title") or "").strip():
                continue
            td = self.todos.create(rt["title"], notes=rt.get("notes") or "", due=_date_only(rt.get("due")),
                                   source="google", external_id=eid, notify=False)
            if rt.get("status") == "completed":
                td = self.todos.update(td["id"], {"done": True}, notify=False) or td
            self.todos.set_sync_state(td["id"], eid, rt.get("updated"), td["updated_at"])
            counts["created_local"] += 1

        # Local todos never synced -> new remote tasks.
        for td in self.todos.all_for_sync():
            if td.get("external_id"):
                continue
            rt = self.google.tasks_insert(_remote_body(td), tasklist)
            self.todos.set_sync_state(td["id"], rt["id"], rt.get("updated"), td["updated_at"])
            counts["created_remote"] += 1
        return counts

    def _pull(self, td: dict[str, Any], rt: dict[str, Any]) -> int:
        patch: dict[str, Any] = {}
        title = (rt.get("title") or "").strip()
        if title and title != td["title"]:
            patch["title"] = title
        if (rt.get("notes") or "") != td["notes"]:
            patch["notes"] = rt.get("notes") or ""
        if _date_only(rt.get("due")) != td["due"]:
            patch["due"] = _date_only(rt.get("due"))
        done = 1 if rt.get("status") == "completed" else 0
        if done != td["done"]:
            patch["done"] = done
        cur = td
        if patch:
            cur = self.todos.update(td["id"], patch, notify=False) or td
        self.todos.set_sync_state(td["id"], td["external_id"], rt.get("updated"), cur["updated_at"])
        return 1 if patch else 0

    def _push(self, td: dict[str, Any], task_id: str, tasklist: str) -> None:
        rt = self.google.tasks_update(task_id, _remote_body(td), tasklist)
        self.todos.set_sync_state(td["id"], task_id, rt.get("updated"), td["updated_at"])
