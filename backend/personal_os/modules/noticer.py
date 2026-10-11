"""Noticer as a feature module: a background scan, the routes behind the Today "Noticed" card, nothing else.

The scan is read-only toward Gmail and Calendar. Adding a noticed event to the calendar happens only in
POST /noticed/{id}/accept, which is a user click, and it is created and read back by Google.calendar_create.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from datetime import datetime, timezone
from typing import Any, Callable

from fastapi import APIRouter, HTTPException

from .. import llm, noticer as nz
from ..cache import bypass
from ..google import GoogleNotConnected
from . import Module, ModuleContext

log = logging.getLogger(__name__)

SCAN_EVERY_S = 45 * 60
FIRST_SCAN_DELAY_S = 90


def _clock() -> datetime:
    return datetime.now(timezone.utc)


class NoticerModule(Module):
    key = "noticer"
    label = "Noticer"

    def __init__(self, ctx: ModuleContext, llm_fn: nz.LlmFn | None = None, clock: Callable[[], datetime] = _clock) -> None:
        super().__init__(ctx)
        self.store = nz.Noticed(ctx.db)
        self.clock = clock
        self._llm = llm_fn or self._default_llm
        self._task: asyncio.Task[None] | None = None
        self._scanning = asyncio.Lock()
        self.last_scan: dict[str, Any] | None = None

    def enabled(self) -> bool:
        return self.ctx.settings().get("noticer", True) is not False

    async def _default_llm(self, system: str, user: str) -> str:
        s = self.ctx.settings()
        model = s.get("extractionModel") or s.get("defaultModel") or ""
        return await asyncio.wait_for(
            llm.complete(s, model, [{"role": "system", "content": system}, {"role": "user", "content": user}],
                         kind="noticer", effort="low", deadline=time.monotonic() + nz.TIMEOUT_S), nz.TIMEOUT_S + 2)

    def _read(self, force: bool) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
        """Blocking Google reads (a worker thread). Raises GoogleNotConnected."""
        g = self.ctx.google
        me = g._me()
        if not me:
            raise GoogleNotConnected("Google is not connected.")
        stored = self.ctx.settings().get("mailWatch") or {}
        query = stored.get("query") or nz.DEFAULT_QUERY
        with bypass():
            threads = g.gmail_threads_recent(query)
            events = g.calendar_events(60, "primary", 0, None, ["all"])
        return me, threads, events

    async def scan(self, force: bool = False) -> dict[str, int]:
        async with self._scanning:
            me, threads, events = await asyncio.to_thread(self._read, force)
            out = await nz.scan_calendar_gap(self.store, threads, events, me, self.clock(), self._llm, force)
            self.last_scan = {**out, "at": time.time()}
            return out

    def router(self) -> APIRouter:
        r = APIRouter()

        @r.get("/noticed")
        def list_noticed() -> dict[str, Any]:
            return {"enabled": self.enabled(), "items": [nz.public(x) for x in self.store.list("pending")],
                    "last_scan": self.last_scan}

        @r.post("/noticed/scan")
        async def scan_now() -> dict[str, Any]:
            if not self.enabled():
                raise HTTPException(409, "Noticing is turned off in Settings.")
            try:
                out = await self.scan(force=True)
            except GoogleNotConnected as e:
                raise HTTPException(409, str(e)) from e
            except Exception as e:  # noqa: BLE001
                raise HTTPException(502, f"Scan failed: {e}") from e
            return {**out, "items": [nz.public(x) for x in self.store.list("pending")]}

        @r.post("/noticed/{nid}/accept")
        async def accept(nid: str) -> dict[str, Any]:
            row = self.store.get(nid)
            if not row:
                raise HTTPException(404)
            if row["status"] != "pending":
                raise HTTPException(409, f"Already {row['status']}.")
            try:
                out = await asyncio.to_thread(nz.ACCEPTORS[row["kind"]], row, self.ctx.google)
            except GoogleNotConnected as e:
                raise HTTPException(409, str(e)) from e
            except RuntimeError as e:  # written but not proven: the card stays so the user can look
                raise HTTPException(502, str(e)) from e
            except Exception as e:  # noqa: BLE001
                raise HTTPException(502, f"Could not add it: {e}") from e
            self.store.set_status(nid, "accepted", event_id=out.get("id"), link=out.get("link"))
            return {"item": nz.public(self.store.get(nid) or row), "event": {"id": out.get("id"), "link": out.get("link")}}

        @r.post("/noticed/{nid}/dismiss")
        def dismiss(nid: str) -> dict[str, Any]:
            if not self.store.set_status(nid, "dismissed"):
                raise HTTPException(404)
            return nz.public(self.store.get(nid))  # type: ignore[arg-type]

        return r

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="noticer")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    async def _loop(self) -> None:
        await asyncio.sleep(FIRST_SCAN_DELAY_S)
        while True:
            if self.enabled():
                try:
                    await self.scan()
                except GoogleNotConnected:
                    pass
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001 - a bad scan must not stop the loop
                    log.warning("noticer scan failed", exc_info=True)
            await asyncio.sleep(SCAN_EVERY_S)

    def today(self) -> dict[str, Any]:
        return {"noticed": {"pending": len(self.store.list("pending"))}}
