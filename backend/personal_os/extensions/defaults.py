"""contributes.defaults: set once when an extension or profile is enabled, undone when it is disabled (design doc section 6.1).

Each default is recorded as source `ext:<id>` in the private setting `extDefaults`, as {id: {key: {"was", "set"}}}.
Revert puts `was` back only while the value is still `set`: whatever the user changed in the meantime stays.
When two enabled extensions set the same value, the one still enabled keeps it: the other's `was` is handed over.
A permission preset looser than the current one is never applied unless the caller says the user clicked for it, and revert never loosens it.
`defaults.permissionPreset` names a permission preset (presets.py): each key it sets is one change, held by the same rule.
Every write is recorded in setting_sources as `profile:<id>` (a profile) or `ext:<id>`, with the value it replaced.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

from .. import llm, permissions, setting_sources
from .manifest import Manifest

if TYPE_CHECKING:
    from ..db import Database

KEY = "extDefaults"
SETTING_OF = {"model": "defaultModel", "effort": "defaultEffort", "theme": "theme"}
STRICTEST_FIRST = ("manual", "auto", "allow_all")  # permissionMode, by how much runs without a card
VIEW = "view:"
PERM = "perm:"  # a key of a permission preset
DEFAULT_KEYS = ("hiddenViews", *SETTING_OF.values(), permissions.KEY)  # the settings a defaults write can change


@dataclass(frozen=True)
class Change:
    key: str  # "view:<id>", "model", "effort", "theme", "permissions" or "perm:<permission key>"
    was: Any
    value: Any

    @property
    def looser(self) -> bool:
        if self.key.startswith(PERM):
            key = self.key.removeprefix(PERM)
            return bool(permissions.loosening({key: self.was}, {key: self.value}))
        return self.key == "permissions" and STRICTEST_FIRST.index(self.value) > STRICTEST_FIRST.index(self.was)

    def to_dict(self) -> dict[str, Any]:
        return {"key": self.key, "was": self.was, "set": self.value}


def _wanted(manifest: Manifest) -> list[tuple[str, Any]]:
    d = manifest.contributes.get("defaults", {})
    return [*((f"{VIEW}{view}", on) for view, on in d.get("views", {}).items()),
            *((k, d[k]) for k in (*SETTING_OF, "permissions") if k in d)]


class Defaults:
    def __init__(self, db: Database, preset_keys: Callable[[str], dict[str, Any]] | None = None) -> None:
        self.db = db
        self.preset_keys = preset_keys  # a preset id -> the permission keys it sets at global scope (Presets.keys)

    def applied(self, ext_id: str) -> bool:
        return ext_id in (self.db.get_settings().get(KEY) or {})

    def plan(self, manifest: Manifest) -> list[Change]:
        """What enabling the manifest would change now; a default that already holds is not a change."""
        return [c for c in self._changes(manifest) if c.was != c.value]

    def _changes(self, manifest: Manifest) -> list[Change]:
        stored = self.db.get_settings()
        wanted = _wanted(manifest)
        if (preset := manifest.contributes.get("defaults", {}).get("permissionPreset")) and self.preset_keys is not None:
            wanted += [(PERM + key, value) for key, value in self.preset_keys(preset).items()]
        return [Change(key, self._read(stored, key), value) for key, value in wanted]

    def apply(self, manifest: Manifest, *, confirmed: bool = False) -> list[Change]:
        """Apply the manifest's defaults once. Returns the changes held back: a looser permission preset without `confirmed`."""
        records = self.db.get_settings().get(KEY) or {}
        if manifest.id in records:
            return []
        changes = self._changes(manifest)  # includes a default that already holds, so reverting another profile leaves it be
        held = [c for c in changes if c.looser and not confirmed]
        done = [c for c in changes if c not in held]
        source = f"{'profile' if manifest.kind == 'profile' else 'ext'}:{manifest.id}"
        self._write([(c.key, c.value) for c in done if c.was != c.value], source, {c.key: c.was for c in done})
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
            elif (now := self._read(stored, key)) == r["set"] and not Change(key, now, r["was"]).looser:
                back.append((key, r["was"]))
        self._write(back, "user", {})
        self.db.set_settings({KEY: records})

    def _read(self, stored: dict[str, Any], key: str) -> Any:
        if key.startswith(VIEW):
            return key.removeprefix(VIEW) not in (stored.get("hiddenViews") or ())
        if key.startswith(PERM):
            return permissions.get(permissions.load(stored), key.removeprefix(PERM))
        if key == "permissions":
            return permissions.get(permissions.load(stored), "permissionMode")
        return stored.get(SETTING_OF[key], llm.DEFAULT_SETTINGS[SETTING_OF[key]])

    def _write(self, pairs: list[tuple[str, Any]], source: str, was: dict[str, Any]) -> None:
        """Write the changes and record `source` (and the value each replaced) for the settings they touched."""
        hidden = list(self.db.get_settings().get("hiddenViews") or ())
        patch: dict[str, Any] = {}
        perms: dict[str, Any] = {}
        prev: dict[str, Any] = {}
        for key, value in pairs:
            if key.startswith(VIEW):
                view = key.removeprefix(VIEW)
                hidden = [v for v in hidden if v != view] + ([] if value else [view])
                patch["hiddenViews"] = hidden
            elif key.startswith(PERM) or key == "permissions":
                name = "permissionMode" if key == "permissions" else key.removeprefix(PERM)
                perms[name] = value
                if key in was:
                    prev[name] = was[key]
            else:
                patch[SETTING_OF[key]] = value
                if key in was:
                    prev[SETTING_OF[key]] = was[key]
        if perms:
            permissions.save(self.db, perms, source=source, prev=prev)
        if patch:
            self.db.set_settings(patch)
            with self.db.tx() as c:
                setting_sources.stamp(c, patch, source, prev=prev)
