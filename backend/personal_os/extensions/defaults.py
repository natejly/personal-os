"""contributes.defaults: set once when an extension or profile is enabled, undone when it is disabled (design doc section 6.1).

Each default is recorded as source `ext:<id>` in the private setting `extDefaults`, as {id: {key: {"was", "set"}}}.
Revert puts `was` back only while the value is still `set`: whatever the user changed in the meantime stays.
When two enabled extensions set the same value, the one still enabled keeps it: the other's `was` is handed over.
A permission preset looser than the current one is never applied unless the caller says the user clicked for it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .. import llm, permissions
from .manifest import Manifest

if TYPE_CHECKING:
    from ..db import Database

KEY = "extDefaults"
SETTING_OF = {"model": "defaultModel", "effort": "defaultEffort", "theme": "theme"}
STRICTEST_FIRST = ("manual", "auto", "allow_all")  # permissionMode, by how much runs without a card
VIEW = "view:"


@dataclass(frozen=True)
class Change:
    key: str  # "view:<id>", "model", "effort", "theme" or "permissions"
    was: Any
    value: Any

    @property
    def looser(self) -> bool:
        return self.key == "permissions" and STRICTEST_FIRST.index(self.value) > STRICTEST_FIRST.index(self.was)

    def to_dict(self) -> dict[str, Any]:
        return {"key": self.key, "was": self.was, "set": self.value}


def _wanted(manifest: Manifest) -> list[tuple[str, Any]]:
    d = manifest.contributes.get("defaults", {})
    return [*((f"{VIEW}{view}", on) for view, on in d.get("views", {}).items()),
            *((k, d[k]) for k in (*SETTING_OF, "permissions") if k in d)]


class Defaults:
    def __init__(self, db: Database) -> None:
        self.db = db

    def applied(self, ext_id: str) -> bool:
        return ext_id in (self.db.get_settings().get(KEY) or {})

    def plan(self, manifest: Manifest) -> list[Change]:
        """What enabling the manifest would change now; a default that already holds is not a change."""
        return [c for c in self._changes(manifest) if c.was != c.value]

    def _changes(self, manifest: Manifest) -> list[Change]:
        stored = self.db.get_settings()
        return [Change(key, self._read(stored, key), value) for key, value in _wanted(manifest)]

    def apply(self, manifest: Manifest, *, confirmed: bool = False) -> list[Change]:
        """Apply the manifest's defaults once. Returns the changes held back: a looser permission preset without `confirmed`."""
        records = self.db.get_settings().get(KEY) or {}
        if manifest.id in records:
            return []
        changes = self._changes(manifest)  # includes a default that already holds, so reverting another profile leaves it be
        held = [c for c in changes if c.looser and not confirmed]
        done = [c for c in changes if c not in held]
        self._write([(c.key, c.value) for c in done if c.was != c.value])
        self.db.set_settings({KEY: {**records, manifest.id: {c.key: {"was": c.was, "set": c.value} for c in done}}})
        return held

    def revert(self, ext_id: str) -> None:
        records = dict(self.db.get_settings().get(KEY) or {})
        if (recorded := records.pop(ext_id, None)) is None:
            return
        stored = self.db.get_settings()
        back = []
        for key, r in recorded.items():
            if r["was"] == r["set"]:
                continue  # it changed nothing, so there is nothing to put back
            if heir := next((rec[key] for rec in records.values() if rec.get(key, {}).get("set") == r["set"]), None):
                heir["was"] = r["was"]  # another enabled extension wants this value too: it inherits the way back
            elif self._read(stored, key) == r["set"]:
                back.append((key, r["was"]))
        self._write(back)
        self.db.set_settings({KEY: records})

    def _read(self, stored: dict[str, Any], key: str) -> Any:
        if key.startswith(VIEW):
            return key.removeprefix(VIEW) not in (stored.get("hiddenViews") or ())
        if key == "permissions":
            return permissions.get(permissions.load(stored), "permissionMode")
        return stored.get(SETTING_OF[key], llm.DEFAULT_SETTINGS[SETTING_OF[key]])

    def _write(self, pairs: list[tuple[str, Any]]) -> None:
        hidden = list(self.db.get_settings().get("hiddenViews") or ())
        patch: dict[str, Any] = {}
        for key, value in pairs:
            if key.startswith(VIEW):
                view = key.removeprefix(VIEW)
                hidden = [v for v in hidden if v != view] + ([] if value else [view])
                patch["hiddenViews"] = hidden
            elif key == "permissions":
                permissions.save(self.db, {"permissionMode": value})
            else:
                patch[SETTING_OF[key]] = value
        if patch:
            self.db.set_settings(patch)
