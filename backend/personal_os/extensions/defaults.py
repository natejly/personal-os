"""contributes.defaults: set once when an extension or profile is enabled, undone when it is disabled (design doc section 6.1).

Each default is recorded as source `ext:<id>` in the private setting `extDefaults`, as {id: {key: {"was", "set"}}}.
Revert puts `was` back only while the value is still `set`: whatever the user changed in the meantime stays.
When two enabled extensions set the same value, the one still enabled keeps it: the other's `was` is handed over.
A permission preset looser than the current one is never applied unless the caller says the user clicked for it, and revert never loosens it.
A runtime slot default (`runtime:<slot>`) is only that record: the profile layer of a slot's selection is read from the records of the
enabled extensions, and a provider looser than the one in effect is held like a looser permission preset.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .. import llm, permissions, runtime
from ..runtime.slots import Scopes
from .manifest import Manifest

if TYPE_CHECKING:
    from ..db import Database

KEY = "extDefaults"
SETTING_OF = {"model": "defaultModel", "effort": "defaultEffort", "theme": "theme"}
STRICTEST_FIRST = ("manual", "auto", "allow_all")  # permissionMode, by how much runs without a card
VIEW = "view:"
RUNTIME = "runtime:"
DEFAULT_KEYS = ("hiddenViews", *SETTING_OF.values(), permissions.KEY)  # the settings a defaults write can change


@dataclass(frozen=True)
class Change:
    key: str  # "view:<id>", "model", "effort", "theme", "permissions" or "runtime:<slot>"
    was: Any
    value: Any
    diff: tuple[str, ...] = ()  # a runtime provider: what it loosens compared with the one in effect

    @property
    def looser(self) -> bool:
        return bool(self.diff) or (self.key == "permissions" and STRICTEST_FIRST.index(self.value) > STRICTEST_FIRST.index(self.was))

    def to_dict(self) -> dict[str, Any]:
        return {"key": self.key, "was": self.was, "set": self.value, **({"diff": list(self.diff)} if self.diff else {})}


def runtime_profiles(stored: dict[str, Any]) -> tuple[tuple[str, dict[str, Any]], ...]:
    """The profile layer of every slot's selection: what each enabled extension's defaults chose, the one applied last first."""
    records = stored.get(KEY) or {}
    return tuple((ext_id, {k.removeprefix(RUNTIME): r["set"] for k, r in rec.items() if k.startswith(RUNTIME)})
                 for ext_id, rec in reversed(records.items()) if any(k.startswith(RUNTIME) for k in rec))


def runtime_scopes(stored: dict[str, Any]) -> Scopes:
    """The selections that hold with no chat or project in view: the profile layer and the global one."""
    return Scopes(profiles=runtime_profiles(stored), global_=stored.get("runtime") or {})


def _wanted(manifest: Manifest) -> list[tuple[str, Any]]:
    d = manifest.contributes.get("defaults", {})
    return [*((f"{VIEW}{view}", on) for view, on in d.get("views", {}).items()),
            *((k, d[k]) for k in (*SETTING_OF, "permissions") if k in d),
            *((f"{RUNTIME}{slot}", provider) for slot, provider in d.get("runtime", {}).items())]


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
        return [Change(key, self._read(stored, key), value, self._loosens(stored, key, value)) for key, value in _wanted(manifest)]

    @staticmethod
    def _loosens(stored: dict[str, Any], key: str, value: Any) -> tuple[str, ...]:
        if not key.startswith(RUNTIME):
            return ()
        slot = key.removeprefix(RUNTIME)
        return tuple(runtime.active.loosening(slot, runtime.active.resolve(slot, runtime_scopes(stored)), value))

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
        self._announce(manifest.id, [c for c in done if c.key.startswith(RUNTIME) and c.was != c.value])
        return held

    @staticmethod
    def _announce(ext_id: str, changes: list[Change]) -> None:
        for c in changes:
            runtime.active.bus.emit_sync("runtime.changed", {"slot": c.key.removeprefix(RUNTIME), "scope": "profile", "scope_id": ext_id,
                                                             "old": c.was, "new": c.value, "by": f"profile:{ext_id}"})

    def revert(self, ext_id: str) -> None:
        records = dict(self.db.get_settings().get(KEY) or {})
        if (recorded := records.pop(ext_id, None)) is None:
            return
        stored = self.db.get_settings()
        back = []
        for key, r in recorded.items():
            if r["was"] == r["set"] or key.startswith(RUNTIME):
                continue  # it changed nothing, or is only a record: there is nothing to put back
            if heir := next((rec[key] for rec in records.values() if rec.get(key, {}).get("set") == r["set"]), None):
                heir["was"] = r["was"]  # another enabled extension wants this value too: it inherits the way back
            elif (now := self._read(stored, key)) == r["set"] and not Change(key, now, r["was"]).looser:
                back.append((key, r["was"]))
        self._write(back)
        self.db.set_settings({KEY: records})

    def _read(self, stored: dict[str, Any], key: str) -> Any:
        if key.startswith(VIEW):
            return key.removeprefix(VIEW) not in (stored.get("hiddenViews") or ())
        if key == "permissions":
            return permissions.get(permissions.load(stored), "permissionMode")
        if key.startswith(RUNTIME):
            return next((sel[slot] for _, sel in runtime_profiles(stored) if (slot := key.removeprefix(RUNTIME)) in sel), None)
        return stored.get(SETTING_OF[key], llm.DEFAULT_SETTINGS[SETTING_OF[key]])

    def _write(self, pairs: list[tuple[str, Any]]) -> None:
        hidden = list(self.db.get_settings().get("hiddenViews") or ())
        patch: dict[str, Any] = {}
        for key, value in pairs:
            if key.startswith(VIEW):
                view = key.removeprefix(VIEW)
                hidden = [v for v in hidden if v != view] + ([] if value else [view])
                patch["hiddenViews"] = hidden
            elif key.startswith(RUNTIME):
                continue
            elif key == "permissions":
                permissions.save(self.db, {"permissionMode": value})
            else:
                patch[SETTING_OF[key]] = value
        if patch:
            self.db.set_settings(patch)
