"""GET/PUT /runtime and GET /runtime/resolve (design doc section 10.7.3): the one door selections go through. Only the
user's settings write here; there is no tool for it, and an extension can provide a slot but never select it."""
from __future__ import annotations

import threading
from dataclasses import replace
from typing import Any, Callable, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import llm
from ..db import Database
from ..extensions.defaults import runtime_profiles
from ..osplat.base import DIMENSIONS
from ..repos import Conversations, Projects
from .slots import ACCEPT_PARTIAL, SLOTS, Runtime, Scopes, run_id
from .sources import ToolSources


class Selection(BaseModel):
    slot: str
    scope: Literal["global", "project", "chat"]
    scope_id: str | None = None
    value: str | dict[str, list[str]] | None = None  # None clears the selection; tools.source takes {enable, disable}
    confirm: bool = False


class Accept(BaseModel):
    provider: str
    dimensions: list[str]


class Probe(BaseModel):
    slot: str
    provider: str


class Selections:
    """Where each scope keeps its selections, and the Scopes a lookup reads."""

    def __init__(self, db: Database, convos: Conversations, projects: Projects) -> None:
        self.db, self.convos, self.projects = db, convos, projects
        self.lock = threading.Lock()  # the routes run in the thread pool: a read-merge-write must not interleave with another

    def scopes(self, conversation_id: str | None = None, project_id: str | None = None, *, layers: tuple[str, ...] = ("chat", "project")) -> Scopes:
        """The selections that apply to a chat and project; `layers` can leave the narrower ones out."""
        stored = self.db.get_settings()
        conv = self.convos.get(conversation_id, with_messages=False) if conversation_id and "chat" in layers else None
        project_id = project_id or (conv or {}).get("project_id")
        project = self.projects.get(project_id) if project_id and "project" in layers else None
        return Scopes(chat=((conv or {}).get("settings") or {}).get("runtime") or {}, project=(project or {}).get("runtime") or {},
                      profiles=runtime_profiles(stored), global_=stored.get("runtime") or {},
                      conversation_id=conversation_id, project_id=project_id, run_id=run_id.get())

    def current(self) -> Scopes:
        """The scopes of the chat the running call belongs to (the usage context names it)."""
        ctx = llm.usage_context.get()
        return self.scopes(ctx.get("conversation_id"), ctx.get("project_id"))

    def read(self, scope: str, scope_id: str | None) -> dict[str, Any]:
        s = self.scopes(scope_id if scope == "chat" else None, scope_id if scope == "project" else None, layers=(scope,))
        return {"chat": s.chat, "project": s.project, "global": s.global_}[scope]

    def write(self, scope: str, scope_id: str | None, value: dict[str, Any]) -> None:
        if scope == "global":
            self.db.set_user_settings({"runtime": value})
        elif scope == "project":
            self.projects.update(scope_id or "", {"runtime": value})
        else:
            self.convos.update(scope_id or "", {"settings": {"runtime": value}})

    def change(self, scope: str, scope_id: str | None, edit: Callable[[dict[str, Any]], dict[str, Any]]) -> dict[str, Any]:
        """Applies `edit` to a scope's selections and stores the result; returns what was there before."""
        with self.lock:
            current = self.read(scope, scope_id)
            self.write(scope, scope_id, edit(current))
            return current


def router(rt: Runtime, selections: Selections, sources: ToolSources) -> APIRouter:
    r = APIRouter()

    def provider_row(p: Any) -> dict[str, Any]:
        locality, enforcement = rt.traits(p.slot, p.id)
        return {"id": p.id, "label": p.label, "tier": p.tier, "extension": p.extension, "meta": p.meta, "core": p.core,
                "available": p.available, "locality": locality, "enforcement": enforcement}

    def resolution(slot: str, scopes: Scopes) -> dict[str, Any]:
        res = rt.resolve(slot, scopes)
        locality, enforcement = rt.traits(slot, res.provider)
        return {**res.to_dict(), "locality": locality, "enforcement": enforcement}

    def check(slot: str, value: Any) -> None:
        if slot not in SLOTS:
            raise HTTPException(404, f"no runtime slot {slot!r}")
        if SLOTS[slot].multi != isinstance(value, dict) and value is not None:
            raise HTTPException(422, f"{slot} takes " + ("{enable, disable} lists" if SLOTS[slot].multi else "one provider id"))
        if isinstance(value, dict) and not (value and set(value) <= {"enable", "disable"}
                                            and all(isinstance(v, list) and all(isinstance(i, str) for i in v) for v in value.values())):
            raise HTTPException(422, f"{slot} takes {{enable, disable}} lists of provider ids")
        wanted = (value.get("enable", []) + value.get("disable", [])) if isinstance(value, dict) else [] if value is None else [value]
        for provider_id in wanted:
            p = rt.provider(slot, provider_id)
            if p is None or not p.available:
                raise HTTPException(422, f"{provider_id!r} is not an available provider for {slot}")
        if isinstance(value, dict) and (core := [i for i in value.get("disable", []) if rt.provider(slot, i).core]):
            raise HTTPException(422, f"{', '.join(core)} cannot be turned off")

    def known(conversation_id: str | None, project_id: str | None) -> None:
        if conversation_id and selections.convos.get(conversation_id, with_messages=False) is None:
            raise HTTPException(404, "no such chat")
        if project_id and selections.projects.get(project_id) is None:
            raise HTTPException(404, "no such project")

    @r.get("/runtime")
    def get_runtime(conversation_id: str | None = None, project_id: str | None = None) -> dict[str, Any]:
        known(conversation_id, project_id)
        scopes = selections.scopes(conversation_id, project_id)
        return {"slots": [{"id": d.id, "noun": d.noun, "multi": d.multi, "default_id": d.default_id,
                           "providers": [provider_row(p) for p in rt.providers(d.id)],
                           "resolution": resolution(d.id, scopes),
                           "selected": {"global": scopes.global_.get(d.id), "project": scopes.project.get(d.id) if scopes.project_id else None,
                                        "chat": scopes.chat.get(d.id) if conversation_id else None}} for d in SLOTS.values()],
                "accept_partial": scopes.global_.get(ACCEPT_PARTIAL, {}),
                "withheld_tools": [{"slug": t.slug, "name": t.name, "source": t.provider.label} for t in sources.pending(scopes)]}

    @r.get("/runtime/resolve")
    def resolve(conversation_id: str | None = None, project_id: str | None = None) -> dict[str, Any]:
        known(conversation_id, project_id)
        scopes = selections.scopes(conversation_id, project_id)
        return {slot: resolution(slot, scopes) for slot in SLOTS}

    @r.put("/runtime")
    def select(body: Selection) -> dict[str, Any]:
        check(body.slot, body.value)
        if body.scope != "global" and not body.scope_id:
            raise HTTPException(422, f"a {body.scope} selection needs scope_id")
        if body.scope == "chat" and selections.convos.get(body.scope_id or "", with_messages=False) is None:
            raise HTTPException(404, "no such chat")
        if body.scope == "project" and selections.projects.get(body.scope_id or "") is None:
            raise HTTPException(404, "no such project")
        layers = {"chat": ("chat", "project"), "project": ("project",), "global": ()}[body.scope]
        scopes = selections.scopes(body.scope_id if body.scope == "chat" else None, body.scope_id if body.scope == "project" else None,
                                   layers=layers)
        was = rt.resolve(body.slot, scopes)
        multi = SLOTS[body.slot].multi
        if body.value is None or multi:  # clearing hands the slot to whatever is next in line; a source list is judged by what it leaves on
            layer = "global_" if body.scope == "global" else body.scope
            after = rt.resolve(body.slot, replace(scopes, **{layer: {k: v for k, v in getattr(scopes, layer).items() if k != body.slot}
                                                           | ({} if body.value is None else {body.slot: body.value})}))
            wanted = {"enable": list(after.extra)} if multi else after.provider
        else:
            wanted = body.value
        if (diff := rt.loosening(body.slot, was, wanted)) and not body.confirm:
            raise HTTPException(409, {"needs_confirm": True, "diff": diff})
        current = selections.change(body.scope, body.scope_id, lambda cur: {k: v for k, v in cur.items() if k != body.slot}
                                    | ({} if body.value is None else {body.slot: body.value}))
        rt.bus.emit_sync("runtime.changed", {"slot": body.slot, "scope": body.scope, "scope_id": body.scope_id,
                                             "old": current.get(body.slot), "new": body.value, "by": "user"})
        return resolve(body.scope_id if body.scope == "chat" else None, body.scope_id if body.scope == "project" else None)

    @r.put("/runtime/accept-partial")
    def accept_partial(body: Accept) -> dict[str, list[str]]:
        if rt.provider("sandbox.backend", body.provider) is None:
            raise HTTPException(422, f"{body.provider!r} is not a sandbox backend")
        if bad := [d for d in body.dimensions if d not in DIMENSIONS]:
            raise HTTPException(422, f"unknown dimension(s): {', '.join(bad)}")
        selections.change("global", None, lambda cur: {**cur, ACCEPT_PARTIAL: {k: v for k, v in cur.get(ACCEPT_PARTIAL, {}).items() if k != body.provider}
                                                       | ({body.provider: body.dimensions} if body.dimensions else {})})
        return selections.read("global", None)[ACCEPT_PARTIAL]

    @r.post("/runtime/tools/{slug}/accept")
    def accept_tool(slug: str) -> dict[str, bool]:
        """The user has read a tool source's changed declaration: offer the tool again."""
        if not sources.accept(slug):
            raise HTTPException(404, "no such withheld tool")
        return {"ok": True}

    @r.post("/runtime/test")
    async def test(body: Probe) -> dict[str, Any]:
        if body.slot not in SLOTS:
            raise HTTPException(404, f"no runtime slot {body.slot!r}")
        try:
            return await rt.test(body.slot, body.provider)
        except KeyError:
            raise HTTPException(404, f"no provider {body.provider!r} in slot {body.slot!r}") from None
        except ValueError as e:
            raise HTTPException(422, str(e)) from None

    return r
