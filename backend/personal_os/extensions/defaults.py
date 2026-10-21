"""contributes.defaults: set once when an extension is enabled, undone when it is disabled (design doc section 6.1).

Each default is recorded as source `ext:<id>` in the private setting `extDefaults`, as {id: {key: {"was", "set"}}}.
Revert puts `was` back only while the value is still `set`: whatever the user changed in the meantime stays.
When two enabled extensions set the same value, the one still enabled keeps it: the other's `was` is handed over.
Only an extension applied later inherits, and only if it found the value already in place: an earlier one whose value the user
changed since lost its claim, so reverting the later one restores what the user had.
A permission mode looser than the current one is never applied unless the caller says the user clicked for it, and revert never loosens it.
A runtime slot default (`runtime:<slot>`) is only that record: the extension layer of a slot's selection is read from the records of the
enabled extensions, and a provider looser than the one in effect is held like a looser permission mode.
Every write is recorded in setting_sources as `ext:<id>`, with the value it replaced.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .. import llm, permissions, runtime, setting_sources
from ..runtime.slots import Scopes
from .manifest import Manifest

if TYPE_CHECKING:
    from ..db import Database

KEY = "extDefaults"
SETTING_OF = {"model": "defaultModel", "effort": "defaultEffort", "theme": "theme"}
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
        loosens = self.key == "permissions" and bool(permissions.loosening({"permissionMode": self.was}, {"permissionMode": self.value}))
        return bool(self.diff) or loosens

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
        wanted = _wanted(manifest)
        return [Change(key, self._read(stored, key), value, self._loosens(stored, key, value)) for key, value in wanted]

    @staticmethod
    def _loosens(stored: dict[str, Any], key: str, value: Any) -> tuple[str, ...]:
        if not key.startswith(RUNTIME):
            return ()
        slot = key.removeprefix(RUNTIME)
        return tuple(runtime.active.loosening(slot, runtime.active.resolve(slot, runtime_scopes(stored)), value))

    def apply(self, manifest: Manifest, *, confirmed: bool = False) -> list[Change]:
        """Apply the manifest's defaults once. Returns the changes held back: a looser permission mode or provider without `confirmed`."""
        records = self.db.get_settings().get(KEY) or {}
        if manifest.id in records:
            return []
        changes = self._changes(manifest)  # includes a default that already holds, so reverting another extension leaves it be
        held = [c for c in changes if c.looser and not confirmed]
        done = [c for c in changes if c not in held]
        source = f"ext:{manifest.id}"
        self._write([(c.key, c.value) for c in done if c.was != c.value], source, {c.key: c.was for c in done})
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
        later = list(records.values())[list(self.db.get_settings()[KEY]).index(ext_id):]  # applied after it
        stored = self.db.get_settings()
        back = []
        for key, r in recorded.items():
            if r["was"] == r["set"] or key.startswith(RUNTIME):
                continue  # it changed nothing, or is only a record: there is nothing to put back
            if heir := next((rec[key] for rec in later if rec.get(key, {}).get("set") == r["set"] == rec[key]["was"]), None):
                heir["was"] = r["was"]  # another enabled extension wants this value too: it inherits the way back
            elif (now := self._read(stored, key)) == r["set"] and not Change(key, now, r["was"]).looser:
                back.append((key, r["was"]))
        self._write(back, "user", {})
        self.db.set_settings({KEY: records})

    def _read(self, stored: dict[str, Any], key: str) -> Any:
        if key.startswith(VIEW):
            return key.removeprefix(VIEW) not in (stored.get("hiddenViews") or ())
        if key == "permissions":
            return permissions.get(permissions.load(stored), "permissionMode")
        if key.startswith(RUNTIME):
            return next((sel[slot] for _, sel in runtime_profiles(stored) if (slot := key.removeprefix(RUNTIME)) in sel), None)
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
            elif key.startswith(RUNTIME):
                continue
            elif key == "permissions":
                perms["permissionMode"] = value
                if key in was:
                    prev["permissionMode"] = was[key]
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
