"""The Review flow for agent-drafted extensions (design doc 6.3): list proposals with their diffs, accept or reject one, and
enable or disable an installed one. Accepting installs it disabled; only the user's enable (this API) loads its code."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Callable

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .defaults import Defaults
from .devapi import adopt, set_hidden_view
from .drafts import Drafts, DraftError
from .manifest import ManifestError, load_manifest

if TYPE_CHECKING:
    from ..tools import Toolbox
    from .host import ExtensionContext, ExtensionHost, ExtensionScope

log = logging.getLogger(__name__)


class EnableIn(BaseModel):
    grants: list[str] = []
    with_requires: bool = False  # also turn on the extensions this one requires, first
    confirm: bool = False  # a profile that holds a looser permission preset is applied only with this


class ActivateIn(BaseModel):
    event: str = Field(pattern=r"^onView:.+")  # the only activation event the renderer raises


class AcceptIn(BaseModel):
    digest: str


class Review:
    """What the routes and the app's startup share: the installed extensions are listed in `hiddenViews` while disabled."""

    def __init__(self, drafts: Drafts, host: ExtensionHost, ctx: ExtensionContext, toolbox: Callable[[], Toolbox]) -> None:
        self.drafts, self.host, self.ctx, self.toolbox = drafts, host, ctx, toolbox

    def _hide(self, ext_id: str, hidden: bool) -> None:
        set_hidden_view(self.host, self.ctx, ext_id, hidden)

    async def _load(self, ext_id: str) -> ExtensionScope:
        if "runtime:loop" in self.drafts.slot_grants(ext_id):
            self.host.slot_grants.add(ext_id)
        scope = await self.host.load_dev(self.drafts.installed / ext_id, self.ctx, first_load=False)
        await adopt(self.host, self.toolbox(), scope)
        return scope

    async def stand_down(self, ext_id: str) -> None:
        """Disable and unload, so that it cannot come back except through `enable`."""
        scope = self.host.extensions.get(ext_id)
        if scope and scope.manifest.kind == "profile":
            scope.defaults.revert(ext_id)  # a profile's defaults are not tied to hiddenViews: undo them here, as its own disable does
        self._hide(ext_id, True)
        self.host.slot_grants.discard(ext_id)
        self.drafts.revoke_grants(ext_id)
        if scope:
            self.toolbox().drop_extension_tools(list(scope.tools))
            await self.host.unload(ext_id)

    async def enable(self, ext_id: str, grants: list[str]) -> None:
        if ext_id not in self.drafts.installed_ids():
            raise KeyError(ext_id)
        if ext_id not in self.host.extensions:
            self.drafts.approve(ext_id, grants)
            await self._load(ext_id)  # still in hiddenViews: loaded disabled
            self._hide(ext_id, False)
        scope = self.host.extensions[ext_id]
        if scope.manifest.kind != "profile" or scope.enabled():
            return
        # A profile is on when its defaults are applied, not when it leaves hiddenViews, and also when it is loaded but switched off.
        # A looser permission preset needs the user's click on /profiles, so a profile that holds one stays off here.
        if any(c.looser for c in scope.defaults.plan(scope.manifest)):
            return
        before = self.ctx.db.get_settings()
        scope.defaults.apply(scope.manifest)
        self.host._settings_changed(before, self.ctx.db.get_settings())
        self.host.refresh_enabled()

    def _declared(self, ext_id: str) -> tuple[tuple[str, ...], tuple[str, ...]] | None:
        if (declared := self.host._declared(ext_id)) is not None:
            return declared
        if ext_id in self.drafts.installed_ids():
            try:
                m = load_manifest(self.drafts.installed / ext_id / "extension.json")
            except ManifestError:
                return None
            return m.requires_extensions, m.requires_services
        return None

    def name_of(self, ext_id: str) -> str:
        if scope := self.host.extensions.get(ext_id):
            return scope.manifest.name
        return (self.drafts.card(ext_id) or {}).get("name") or ext_id

    def known(self, ext_id: str) -> bool:
        """An installed extension or a bundled one. A folder loaded from the dev panel is neither: it cannot pass the review gates here."""
        scope = self.host.extensions.get(ext_id)
        return (scope is not None and not scope.dev) or ext_id in self.drafts.installed_ids()

    async def _turn_on(self, ext_id: str, grants: list[str], confirm: bool) -> bool:
        """Turn one extension or profile on; False when it was held back (a looser preset without `confirm`) or cannot be switched."""
        if ext_id in self.drafts.installed_ids():
            await self.enable(ext_id, grants)
            return self.host.is_on(ext_id)
        scope = self.host.extensions[ext_id]
        if scope.manifest.kind == "profile":
            if scope.enabled():
                return True
            if any(c.looser for c in scope.defaults.plan(scope.manifest)) and not confirm:
                return False
            before = self.ctx.db.get_settings()
            scope.defaults.apply(scope.manifest, confirmed=confirm)
            self.host._settings_changed(before, self.ctx.db.get_settings())
            self.host.refresh_enabled()
            return True
        if not scope.toggleable:
            return scope.enabled()
        if not scope.enabled():
            self._hide(ext_id, False)
        return scope.enabled()

    async def enable_with_requires(self, ext_id: str, grants: list[str], with_requires: bool, confirm: bool = False) -> dict[str, Any]:
        """Enable ext_id. When a required extension is off: with `with_requires` they are turned on first, in dependency order, then
        ext_id; without it nothing changes and `needs` names them. The providers of required services are turned on either way.
        Raises KeyError for an unknown id."""
        if not self.known(ext_id):
            raise KeyError(ext_id)
        chain, named = self.host.enable_plan(ext_id, self._declared)
        if named and not with_requires:
            return {"ok": False, "enabled": [], "needs": [{"id": i, "name": self.name_of(i), "enabled": False} for i in named]}
        done: list[str] = []
        for i in [*chain, ext_id]:
            if self.host.is_on(i):
                continue
            if not await self._turn_on(i, grants if i == ext_id else [], confirm):
                return {"ok": False, "enabled": done, "needs": [], "held": True}
            done.append(i)
        return {"ok": True, "enabled": done, "needs": []}

    async def disable_one(self, ext_id: str) -> dict[str, Any]:
        """Turn ext_id off and nothing else: what needs it keeps its state. Raises KeyError for an unknown id."""
        if not self.known(ext_id):
            raise KeyError(ext_id)
        was = self.host.is_on(ext_id) or ext_id in self.drafts.installed_ids()
        if ext_id in self.drafts.installed_ids():
            await self.stand_down(ext_id)
        else:
            scope = self.host.extensions[ext_id]
            if scope.manifest.kind == "profile":
                before = self.ctx.db.get_settings()
                scope.defaults.revert(ext_id)
                self.host._settings_changed(before, self.ctx.db.get_settings())
                self.host.refresh_enabled()
            elif not scope.toggleable:
                raise ValueError(f"extension {ext_id!r} cannot be turned off")
            else:
                self._hide(ext_id, True)
        return {"ok": True, "disabled": [ext_id] if was else []}

    async def load_installed(self) -> None:
        """At startup: load what the user enabled. One whose files changed, or that fails to load, is disabled instead."""
        for ext_id in self.drafts.installed_ids():
            if ext_id in self.ctx.hidden_views():
                continue
            try:
                if not self.drafts.intact(ext_id):
                    raise DraftError("its files are not the ones that were accepted")
                if refused := (self.drafts.card(ext_id) or {}).get("refused"):  # hiddenViews can be rewritten by hand, so re-check what enable checks
                    raise DraftError(f"its drafting session read untrusted content, so it may not hold {', '.join(refused)}")
                await self._load(ext_id)
            except Exception as e:  # noqa: BLE001 - the app starts either way
                log.warning("installed extension %s is disabled: %s", ext_id, e)
                Defaults(self.ctx.db, self.ctx.preset_keys).revert(ext_id)  # a profile that never loads cannot revert itself; nothing recorded is a no-op
                self._hide(ext_id, True)

    def listing(self) -> dict[str, Any]:
        installed = [{**card, "enabled": card["id"] in self.host.extensions and self.host.extensions[card["id"]].enabled()}
                     for ext_id in self.drafts.installed_ids() if (card := self.drafts.card(ext_id))]
        return {"proposals": self.drafts.proposals(), "installed": installed}


def router(review: Review) -> APIRouter:
    r = APIRouter(prefix="/extensions")

    @r.get("")
    def listing() -> dict[str, Any]:
        return review.listing()

    @r.post("/activate")
    async def activate(body: ActivateIn) -> dict[str, bool]:
        await review.host.activate(body.event)
        return {"ok": True}

    @r.post("/{ext_id}/accept")
    async def accept(ext_id: str, body: AcceptIn) -> dict[str, Any]:
        if (proposal := review.drafts.proposal(ext_id)) is None:
            raise HTTPException(404, "no proposal with that id")
        if proposal["digest"] != body.digest:
            raise HTTPException(409, "the proposal changed after you reviewed it; review it again")
        await review.stand_down(ext_id)  # an update drops the installed version back to disabled
        try:
            review.drafts.accept(ext_id, body.digest)
        except DraftError as e:
            raise HTTPException(409, str(e)) from e
        return review.listing()

    @r.post("/{ext_id}/reject")
    def reject(ext_id: str) -> dict[str, Any]:
        try:
            review.drafts.reject(ext_id)
        except KeyError:
            raise HTTPException(404, "no proposal with that id") from None
        return review.listing()

    @r.post("/{ext_id}/enable")
    async def enable(ext_id: str, body: EnableIn | None = None) -> dict[str, Any]:
        body = body or EnableIn()
        try:
            out = await review.enable_with_requires(ext_id, body.grants, body.with_requires, body.confirm)
        except KeyError:
            raise HTTPException(404, "no extension with that id") from None
        except DraftError as e:
            raise HTTPException(409, str(e)) from e
        except Exception as e:  # noqa: BLE001 - the reason goes to the card
            raise HTTPException(400, f"{type(e).__name__}: {e}") from e
        return {**review.listing(), **out}

    @r.post("/{ext_id}/disable")
    async def disable(ext_id: str) -> dict[str, Any]:
        try:
            out = await review.disable_one(ext_id)
        except KeyError:
            raise HTTPException(404, "no extension with that id") from None
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return {**review.listing(), **out}

    return r
