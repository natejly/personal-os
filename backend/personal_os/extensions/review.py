"""The Review flow for agent-drafted extensions (design doc 6.3): list proposals with their diffs, accept or reject one, and
enable or disable an installed one. Accepting installs it disabled; only the user's enable (this API) loads its code."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Callable

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .devapi import adopt
from .drafts import Drafts, DraftError

if TYPE_CHECKING:
    from ..tools import Toolbox
    from .host import ExtensionContext, ExtensionHost

log = logging.getLogger(__name__)


class EnableIn(BaseModel):
    grants: list[str] = []


class Review:
    """What the routes and the app's startup share: the installed extensions are listed in `hiddenViews` while disabled."""

    def __init__(self, drafts: Drafts, host: ExtensionHost, ctx: ExtensionContext, toolbox: Callable[[], Toolbox]) -> None:
        self.drafts, self.host, self.ctx, self.toolbox = drafts, host, ctx, toolbox

    def _hide(self, ext_id: str, hidden: bool) -> None:
        views = [v for v in self.ctx.hidden_views() if v != ext_id]
        self.ctx.set_settings({"hiddenViews": [*views, ext_id] if hidden else views})
        self.host.refresh_enabled()

    async def _load(self, ext_id: str) -> None:
        scope = await self.host.load_dev(self.drafts.installed / ext_id, self.ctx, first_load=False)
        await adopt(self.host, self.toolbox(), scope)

    async def stand_down(self, ext_id: str) -> None:
        """Disable and unload, so that it cannot come back except through `enable`."""
        self._hide(ext_id, True)
        if scope := self.host.extensions.get(ext_id):
            self.toolbox().drop_extension_tools(list(scope.tools))
            await self.host.unload(ext_id)

    async def enable(self, ext_id: str, grants: list[str]) -> None:
        if ext_id in self.host.extensions:
            return
        self.drafts.approve(ext_id, grants)
        await self._load(ext_id)  # still in hiddenViews: loaded disabled
        self._hide(ext_id, False)

    async def load_installed(self) -> None:
        """At startup: load what the user enabled. One whose files changed, or that fails to load, is disabled instead."""
        for ext_id in self.drafts.installed_ids():
            if ext_id in self.ctx.hidden_views():
                continue
            try:
                if not self.drafts.intact(ext_id):
                    raise DraftError("its files are not the ones that were accepted")
                await self._load(ext_id)
            except Exception as e:  # noqa: BLE001 - the app starts either way
                log.warning("installed extension %s is disabled: %s", ext_id, e)
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

    @r.post("/{ext_id}/accept")
    async def accept(ext_id: str) -> dict[str, Any]:
        if review.drafts.proposal(ext_id) is None:
            raise HTTPException(404, "no proposal with that id")
        await review.stand_down(ext_id)  # an update drops the installed version back to disabled
        review.drafts.accept(ext_id)
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
        try:
            await review.enable(ext_id, body.grants if body else [])
        except KeyError:
            raise HTTPException(404, "no installed extension with that id") from None
        except DraftError as e:
            raise HTTPException(409, str(e)) from e
        except Exception as e:  # noqa: BLE001 - the reason goes to the card
            raise HTTPException(400, f"{type(e).__name__}: {e}") from e
        return review.listing()

    @r.post("/{ext_id}/disable")
    async def disable(ext_id: str) -> dict[str, Any]:
        if ext_id not in review.drafts.installed_ids():
            raise HTTPException(404, "no installed extension with that id")
        await review.stand_down(ext_id)
        return review.listing()

    return r
