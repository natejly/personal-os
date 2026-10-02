"""Reply tracker as a feature module: routes, the read-only `mail_followups` tool and the Today counts.

Read-only toward Gmail (it only calls `gmail_threads_recent`) and proposal-only toward todos: a follow-up
todo exists only after POST /mail/watch/{thread_id}/followup. There is no background loop; refresh is
on demand from the UI or the tool.
"""
from __future__ import annotations

import asyncio
import contextlib
from datetime import date, datetime, timezone
from typing import TYPE_CHECKING, Any, Callable

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import mailwatch as mw
from ..cache import bypass
from ..google import GoogleNotConnected
from ..mailwatch import MailWatch
from ..todos import Todos
from ..tools import ToolSpec, _obj, page, tool_error
from . import Module, ModuleContext

if TYPE_CHECKING:
    from ..tools import Toolbox


class DismissIn(BaseModel):
    dismissed: bool = True


class SnoozeIn(BaseModel):
    until: datetime | None = None  # None clears the snooze


class MailWatchConfigIn(BaseModel):
    enabled: bool | None = None
    awaitingAfterDays: int | None = None
    needsReplyAfterHours: int | None = None
    useLLM: bool | None = None
    query: str | None = None
    proposeFollowups: bool | None = None


def _clock() -> datetime:
    return datetime.now(timezone.utc)


class MailWatchModule(Module):
    key = "mailwatch"
    label = "Mail watch"

    def __init__(self, ctx: ModuleContext, llm_fn: Callable[[list[dict[str, str]]], Any] | None = None,
                 clock: Callable[[], datetime] = _clock) -> None:
        """`llm_fn` is the optional refinement hook: given [{subject, domain, snippet}] it returns one status per
        item. The app leaves it None, so nothing leaves the machine unless wiring is added deliberately."""
        super().__init__(ctx)
        self.store = MailWatch(ctx.db)
        self.todos = Todos(ctx.db)
        self.llm_fn = llm_fn
        self.clock = clock

    def config(self) -> dict[str, Any]:
        stored = self.ctx.settings().get("mailWatch") or {}
        return {**mw.DEFAULT_CONFIG, **{k: v for k, v in stored.items() if k in mw.DEFAULT_CONFIG}}

    def refresh_sync(self, force: bool = False) -> int:
        """Pull recent threads, classify, upsert. Runs on a worker thread; raises GoogleNotConnected."""
        cfg = self.config()
        g = self.ctx.google
        me = g._me()
        if not me:
            raise GoogleNotConnected("Google is not connected.")
        with bypass() if force else contextlib.nullcontext():
            threads = g.gmail_threads_recent(cfg["query"])
        now = self.clock()
        pairs = [(t, mw.classify(t, me, now, cfg)) for t in threads]
        if cfg["useLLM"] and self.llm_fn is not None and pairs:
            refined = mw.refine(pairs, self.llm_fn, me)
            pairs = [(t, r) for (t, _), r in zip(pairs, refined)]
        return self.store.refresh(pairs)

    def router(self) -> APIRouter:
        r = APIRouter()

        @r.get("/mail/watch/config")
        def get_config() -> dict[str, Any]:
            return self.config()

        @r.put("/mail/watch/config")
        def put_config(body: MailWatchConfigIn) -> dict[str, Any]:
            patch = body.model_dump(exclude_none=True)
            if "awaitingAfterDays" in patch and not 1 <= patch["awaitingAfterDays"] <= 60:
                raise HTTPException(422, "awaitingAfterDays must be between 1 and 60")
            if "needsReplyAfterHours" in patch and not 1 <= patch["needsReplyAfterHours"] <= 24 * 30:
                raise HTTPException(422, "needsReplyAfterHours must be between 1 and 720")
            self.ctx.set_settings({"mailWatch": {**self.config(), **patch}})
            return self.config()

        @r.get("/mail/watch")
        def list_watch(status: str | None = None) -> dict[str, Any]:
            if status is not None and status not in mw.STATUSES:
                raise HTTPException(400, f"status must be one of {', '.join(mw.STATUSES)}")
            cfg = self.config()
            now = self.clock()
            return {"threads": self.store.list(status, at=now), "counts": self.store.counts(cfg, now),
                    "followups": self.store.propose_followups(cfg, now, now.astimezone().date())}

        @r.post("/mail/watch/refresh")
        async def refresh() -> dict[str, Any]:
            try:
                n = await asyncio.to_thread(self.refresh_sync, True)
            except GoogleNotConnected as e:
                raise HTTPException(409, str(e)) from e
            except Exception as e:  # noqa: BLE001
                raise HTTPException(502, f"Gmail read failed: {e}") from e
            return {"refreshed": n, "counts": self.store.counts(self.config(), self.clock())}

        @r.put("/mail/watch/{thread_id}")
        def dismiss(thread_id: str, body: DismissIn) -> dict[str, Any]:
            if not self.store.dismiss(thread_id, body.dismissed):
                raise HTTPException(404)
            return self.store.get(thread_id)  # type: ignore[return-value]

        @r.put("/mail/watch/{thread_id}/snooze")
        def snooze(thread_id: str, body: SnoozeIn) -> dict[str, Any]:
            until = mw._aware(body.until) if body.until else None  # a naive stamp is read as UTC
            if until is not None and until <= self.clock():
                raise HTTPException(422, "until must be in the future")
            if not self.store.snooze(thread_id, until):
                raise HTTPException(404)
            return self.store.get(thread_id)  # type: ignore[return-value]

        @r.post("/mail/watch/{thread_id}/followup")
        def followup(thread_id: str) -> dict[str, Any]:
            todo = self.store.create_followup(thread_id, self.todos, self.clock().astimezone().date())
            if not todo:
                raise HTTPException(404)
            return todo

        return r

    def register_tools(self, box: Toolbox) -> None:
        async def mail_followups(ctx: dict[str, Any], kind: str = "awaiting_reply", limit: int = 10, offset: int = 0) -> Any:
            if kind not in ("awaiting_reply", "to_reply"):
                return tool_error(f"Unknown kind '{kind}'.", field="kind", expected="awaiting_reply or to_reply", example={"kind": "to_reply"})
            await asyncio.to_thread(self.refresh_sync)
            rows = [{"thread_id": t["thread_id"], "subject": t["subject"], "last_from": t["last_from"], "age_days": round(t["age_days"], 1), "reason": t["reason"]}
                    for t in self.store.list(kind, at=self.clock())]
            return page(rows, offset=offset, limit=limit, key="threads")
        box.specs["mail_followups"] = ToolSpec(
            "mail_followups",
            "Email threads waiting on someone: 'awaiting_reply' = the user sent last and is still waiting on a reply; 'to_reply' = someone wrote and the user owes an answer. Read-only; classified from headers and snippets. Offer follow-up todos but do not create or send anything unprompted.",
            _obj({"kind": {"type": "string", "enum": ["awaiting_reply", "to_reply"], "default": "awaiting_reply"}, "limit": {"type": "integer", "default": 10}, "offset": {"type": "integer", "default": 0}}, []),
            mail_followups, "google",
            examples=[{"kind": "awaiting_reply"}, {"kind": "to_reply", "limit": 5}], taints=True)

    def today(self) -> dict[str, Any]:
        """From the table only; never calls Google."""
        return {"mail_watch": self.store.counts(self.config(), self.clock())}

