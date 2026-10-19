"""Routes for the extensions catalog and for installing from outside the app (see install.py and catalog_index.py).

Enable and disable are in review.py: an installed extension is switched on and off by the same routes as any other.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .catalog_index import Catalog, bundled_ids
from .devapi import set_hidden_view
from .install import InstallError, Installer, tree_state
from .manifest import ID, ManifestError, load_manifest

if TYPE_CHECKING:
    from ..db import Database
    from .host import ExtensionContext, ExtensionHost
    from .review import Review

log = logging.getLogger(__name__)
_TABLE = re.compile(r"[a-z_][a-z0-9_]*")


class InstallIn(BaseModel):
    url: str | None = None
    path: str | None = None
    ref: str | None = None
    subdir: str | None = None


def _fail(e: InstallError) -> HTTPException:
    detail: Any = {"message": str(e), "findings": e.findings} if e.findings else str(e)
    return HTTPException(e.status, detail)


def drop_owned_tables(db: Database, ext_id: str, tables: list[str]) -> tuple[list[str], list[str]]:
    """Drop the tables an installed extension declared, and its migration records so a later install builds them again.
    Only tables named for the extension (`<id with _>_...`) are touched: the loader enforces the same rule, and a manifest that
    names another table does not get it dropped. Returns (dropped, kept)."""
    prefix = f"{ext_id.replace('-', '_')}_"
    mine = [t for t in tables if _TABLE.fullmatch(t) and t.startswith(prefix)]
    with contextlib.closing(db.connect()) as c:
        for t in mine:
            c.execute(f'DROP TABLE IF EXISTS "{t}"')
        with contextlib.suppress(Exception):
            c.execute("DELETE FROM schema_migrations WHERE typeof(version) = 'text' AND version LIKE ?", (f"ext:{ext_id}:%",))
        c.commit()
    return mine, [t for t in tables if t not in mine]


def router(host: ExtensionHost, ctx: ExtensionContext, review: Review, db: Database, *, default_catalog_url: str = "") -> APIRouter:
    r = APIRouter(prefix="/extensions")
    drafts = review.drafts
    catalog = Catalog(host, drafts.installed, db.get_settings,
                      tree_state=lambda ext_id: tree_state(drafts, ext_id), default_url=default_catalog_url)
    installer = Installer(db.data_dir, drafts, catalog.remote_ids)

    @r.get("/catalog")
    def get_catalog() -> dict[str, Any]:
        return catalog.build()

    @r.post("/install")
    async def install(body: InstallIn) -> dict[str, Any]:
        try:
            return await asyncio.to_thread(lambda: installer.stage(url=body.url, path=body.path, ref=body.ref, subdir=body.subdir))
        except InstallError as e:
            raise _fail(e) from e

    @r.post("/install/{ext_id}/confirm")
    async def confirm(ext_id: str) -> dict[str, Any]:
        try:
            installer.staged(ext_id)
        except KeyError:
            raise HTTPException(404, "nothing is waiting for review under that id") from None
        scope = host.extensions.get(ext_id)
        was_enabled = bool(scope and scope.enabled())
        await review.stand_down(ext_id)  # the old version leaves before its files do; a new install is held off in hiddenViews
        try:
            info = await asyncio.to_thread(installer.commit, ext_id)
        except InstallError as e:
            raise _fail(e) from e
        if info["update"] and was_enabled and not info["grew"]:
            try:
                grants = (drafts.card(ext_id) or {}).get("needsGrant", [])  # unchanged permissions: what was granted before still stands
                await review.enable(ext_id, grants)
            except Exception as e:  # noqa: BLE001 - it stays disabled, and the card says so
                log.warning("updated extension %s stays disabled: %s", ext_id, e)
        card = next((c for c in catalog.build()["installed"] if c["id"] == ext_id), None)
        return {"card": card}

    @r.delete("/install/{ext_id}")
    def discard(ext_id: str) -> dict[str, bool]:
        try:
            installer.discard(ext_id)
        except KeyError:
            raise HTTPException(404, "nothing is waiting for review under that id") from None
        return {"ok": True}

    @r.get("/{ext_id}/consent")
    def consent(ext_id: str) -> dict[str, Any]:
        found = catalog.consent_bundled(ext_id)
        if found is None:
            raise HTTPException(404, "no bundled extension with that id")
        return found

    @r.post("/{ext_id}/update")
    async def update(ext_id: str) -> dict[str, Any]:
        try:
            return await asyncio.to_thread(installer.update, ext_id)
        except KeyError:
            raise HTTPException(404, "no installed extension with that id") from None
        except InstallError as e:
            raise _fail(e) from e

    @r.delete("/{ext_id}")
    async def delete(ext_id: str, keep_data: bool = False) -> dict[str, Any]:
        if ext_id in bundled_ids():
            raise HTTPException(400, "an extension that ships with Grain cannot be deleted: turn it off instead")
        if not ID.fullmatch(ext_id) or ext_id not in drafts.installed_ids():
            raise HTTPException(404, "no installed extension with that id")
        tables: list[str] = []
        with contextlib.suppress(ManifestError, OSError):
            tables = list(load_manifest(drafts.installed / ext_id / "extension.json").data.get("tables", ()))
        await review.stand_down(ext_id)
        installer.remove_files(ext_id)
        set_hidden_view(host, ctx, ext_id, False)  # nothing to hide any more
        dropped: list[str] = []
        kept = tables
        if not keep_data:
            shutil.rmtree(Path(db.data_dir) / "ext" / ext_id, ignore_errors=True)
            dropped, kept = await asyncio.to_thread(drop_owned_tables, db, ext_id, tables)
        return {"ok": True, "tablesDropped": dropped, "tablesKept": kept}

    return r
