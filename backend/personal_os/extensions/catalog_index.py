"""The extensions catalog: what is installed, what is available to enable or install, and the plain-language facts about each.

`GET /extensions/catalog` returns `{installed, available, services, catalogUrl, catalogError?}` of cards. Available is every
bundled extension or profile that is off, plus the entries of a remote index (setting `extensionCatalogUrl`: an https URL, a
file:// URL or a local path). Reading the catalog never loads or runs anything from an installed or listed extension.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
import urllib.parse
from pathlib import Path
from typing import Any, Callable

import httpx

from ..runtime.slots import SLOTS
from .manifest import ID, Manifest, ManifestError, load_manifest

log = logging.getLogger(__name__)

BUILTIN_ROOT = Path(__file__).parent
SERVICE_IDS = frozenset({"google"})  # internal services: never a card in the list, only "Required by"
INDEX_TTL_S = 3600.0
INDEX_TIMEOUT_S = 5.0
INDEX_MAX_BYTES = 1_000_000
MAX_ENTRIES = 500
VOLATILE_CONTEXT_TOKENS = 150  # what a context:volatile extension is assumed to add per turn
SCHEMA_FALLBACK_TOKENS = 40  # a tool whose schema lives in code, not in a file the manifest names

PERMISSION_TEXT = {
    "db:own": "Keeps its own data in Grain's database, in tables only it can read.",
    "context:volatile": "Adds text to what the assistant reads on every turn.",
    "conversation:read": "Reads your conversations.",
    "input:rewrite": "Can rewrite what you type before it is sent.",
    "hooks:gate": "Can block a tool call or ask you before it runs.",
    "memory:gate": "Can decide what is saved to memory.",
    "prompt:inject": "Can add instructions to the assistant's prompt.",
    "ui:unsupported-css": "Can style the app beyond the supported design tokens.",
    "coding:read": "Reads your coding sessions.",
    "terminal:control": "Can control terminal sessions.",
}
SERVICE_LABELS = {"google": "your Google account", "mcp": "connected tool servers"}
_SLOT_PERMISSIONS = {d.permission: d.noun for d in SLOTS.values()}


# ---- facts from a manifest ----
def permission_text(manifest: Manifest) -> list[str]:
    """One plain sentence per capability the manifest asks for, plus what it adds (tools that write, hooks that gate)."""
    lines: list[str] = []
    tools = manifest.contributes.get("tools", ())
    risky = [t["name"] for t in tools if t.get("danger", "safe") != "safe"]
    if tools:
        lines.append(f"Adds {len(tools)} tool{'s' if len(tools) != 1 else ''} for the assistant" + (f"; {len(risky)} can change things or reach outside Grain" if risky else ""))
    for perm in manifest.permissions:
        if perm in PERMISSION_TEXT:
            lines.append(PERMISSION_TEXT[perm].rstrip("."))
        elif perm in _SLOT_PERMISSIONS:
            lines.append(f"Can replace a core part of Grain: the {_SLOT_PERMISSIONS[perm]}")
        elif perm.startswith("service:"):
            name = perm.removeprefix("service:")
            if name in SERVICE_LABELS:
                lines.append(f"Uses {SERVICE_LABELS[name]}")
    if manifest.hooks:
        lines.append(f"Listens to {len(manifest.hooks)} app event{'s' if len(manifest.hooks) != 1 else ''}")
    if manifest.contributes.get("views") or manifest.contributes.get("slots"):
        lines.append("Adds screens or panels to the app")
    return [ln.rstrip(". ") for ln in lines if ln.strip(". ")]


def contributes(manifest: Manifest) -> dict[str, Any]:
    c = manifest.contributes
    slots = c.get("slots", {})
    return {"tools": [{"name": t["name"], "danger": t.get("danger", "safe")} for t in c.get("tools", ())],
            "views": [v["id"] for v in c.get("views", ())], "slots": sorted(slots), "hooks": sorted(manifest.hooks),
            "commands": [e["name"] for e in c.get("commands", ())], "settingsFields": sorted(manifest.settings), "processes": []}


def context_cost(manifest: Manifest, root: Path | None) -> int:
    """Stable context tokens the extension adds per turn: each tool's name, description and schema, plus a flat share for a
    declared volatile context contribution. An estimate from the manifest alone."""
    from ..context import estimate_tokens
    if manifest.kind == "profile":
        return 0
    total = 0
    for tool in manifest.contributes.get("tools", ()):
        text = f"{tool['name']} {tool.get('description', '')}"
        rel = tool.get("schema")
        path = root / rel if root and isinstance(rel, str) and ".." not in Path(rel).parts and not Path(rel).is_absolute() else None
        try:
            schema = path.read_text("utf-8")[:20_000] if path and path.is_file() and not path.is_symlink() else None
        except (OSError, UnicodeDecodeError):
            schema = None
        total += estimate_tokens(text + (schema or "")) + (0 if schema else SCHEMA_FALLBACK_TOKENS)
    if "context:volatile" in manifest.permissions or "context.contribute" in manifest.hooks:
        total += VOLATILE_CONTEXT_TOKENS
    return total


def card_for_manifest(manifest: Manifest, *, root: Path | None, tier: str, source: str, enabled: bool, state: str = "ready",
                      reason: str | None = None, pinned: dict[str, str] | None = None, **extra: Any) -> dict[str, Any]:
    card: dict[str, Any] = {
        "id": manifest.id, "name": manifest.name, "description": manifest.description, "version": manifest.version, "tier": tier,
        "kind": manifest.kind, "source": source, "enabled": enabled,
        "requires": {"extensions": list(manifest.requires_extensions), "services": list(manifest.requires_services)}, "requiredBy": [],
        "contributes": contributes(manifest), "permissions": sorted(manifest.permissions), "permissionText": permission_text(manifest),
        "contextCostTokens": context_cost(manifest, root), "state": state}
    if reason:
        card["reason"] = reason
    if pinned:
        card["pinned"] = pinned
    card.update({k: v for k, v in extra.items() if v is not None})
    if tier == "installed" and (card["contributes"]["views"] or card["contributes"]["slots"]):
        card["uiNotLoaded"] = True  # the renderer half of a folder extension is not loaded yet
    return card


def bundled_manifests() -> dict[str, Manifest]:
    out: dict[str, Manifest] = {}
    for path in sorted(BUILTIN_ROOT.glob("*/extension.json")):
        try:
            m = load_manifest(path)
        except ManifestError as e:
            log.warning("bundled extension %s: %s", path.parent.name, e)
            continue
        out[m.id] = m
    return out


def bundled_ids() -> set[str]:
    return set(bundled_manifests()) | {"core"}


# ---- pins ----
def read_pin(installed: Path, ext_id: str) -> dict[str, Any] | None:
    path = installed / ".pins" / f"{ext_id}.json"
    try:
        raw = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return None
    return raw if isinstance(raw, dict) else None


# ---- typosquats ----
def edit_distance(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def typosquat(ext_id: str, known: set[str]) -> dict[str, str] | None:
    """The known id this one is within two edits of (not equal to), or None. Very short ids need a closer match to count."""
    best: tuple[int, str] | None = None
    for other in sorted(known):
        d = edit_distance(ext_id, other)
        if 0 < d <= 2 and min(len(ext_id), len(other)) > 2 * d and (best is None or d < best[0]):
            best = (d, other)
    return {"similarTo": best[1]} if best else None


# ---- the remote index ----
_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_cache_lock = threading.Lock()


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


def _read_index(url: str) -> Any:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme in ("http", "https"):
        with httpx.stream("GET", url, timeout=INDEX_TIMEOUT_S, follow_redirects=True) as r:
            r.raise_for_status()
            body = b""
            for chunk in r.iter_bytes():
                body += chunk
                if len(body) > INDEX_MAX_BYTES:
                    raise ValueError("the catalog index is larger than 1 MB")
        return json.loads(body)
    if parsed.scheme == "file":
        path = Path(urllib.parse.unquote(parsed.path))
    elif parsed.scheme == "":
        path = Path(url).expanduser()
    else:
        raise ValueError(f"unsupported catalog URL scheme {parsed.scheme!r}")
    if path.stat().st_size > INDEX_MAX_BYTES:
        raise ValueError("the catalog index is larger than 1 MB")
    return json.loads(path.read_text("utf-8"))


def _valid_source_url(url: str) -> bool:
    """A source a user can install from: https, ssh, file or a path. Never an option-looking string or an exotic scheme."""
    if not url or url != url.strip() or url.startswith("-") or any(c in url for c in "\0\n\r"):
        return False
    scheme = urllib.parse.urlsplit(url).scheme
    if scheme in ("https", "ssh", "file", ""):
        return True
    return False


def _subdir_ok(subdir: str) -> bool:
    p = Path(subdir)
    return bool(subdir) and not p.is_absolute() and ".." not in p.parts and "\\" not in subdir and "\0" not in subdir


def parse_index(raw: Any, taken: set[str]) -> list[dict[str, Any]]:
    """The usable entries of an index document. Anything malformed or colliding with a bundled id is skipped."""
    items = raw.get("extensions") if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        raise ValueError("the catalog index must be a list of extensions")
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for e in items[:MAX_ENTRIES]:
        if not isinstance(e, dict):
            continue
        ext_id, url = e.get("id"), e.get("url")
        if not (isinstance(ext_id, str) and ID.fullmatch(ext_id) and ext_id not in taken and ext_id not in seen):
            continue
        if not (isinstance(url, str) and _valid_source_url(url)):
            continue
        entry: dict[str, Any] = {"id": ext_id, "name": e["name"] if isinstance(e.get("name"), str) and e["name"].strip() else ext_id,
                                 "description": e["description"] if isinstance(e.get("description"), str) else "", "url": url,
                                 "author": e["author"] if isinstance(e.get("author"), str) else "", "verified": e.get("verified") is True}
        ref, subdir = e.get("ref"), e.get("subdir")
        if isinstance(ref, str) and ref and not ref.startswith("-") and "\0" not in ref:
            entry["ref"] = ref
        if isinstance(subdir, str) and _subdir_ok(subdir):
            entry["subdir"] = subdir
        seen.add(ext_id)
        out.append(entry)
    return out


def fetch_index(url: str, taken: set[str], *, force: bool = False) -> list[dict[str, Any]]:
    """The entries of the index at `url`; remote ones are cached for an hour. Raises on a fetch or format failure."""
    remote = urllib.parse.urlsplit(url).scheme in ("http", "https")
    now = time.monotonic()
    if remote and not force:
        with _cache_lock:
            hit = _cache.get(url)
        if hit and now - hit[0] < INDEX_TTL_S:
            return hit[1]
    entries = parse_index(_read_index(url), taken)
    if remote:
        with _cache_lock:
            _cache[url] = (now, entries)
    return entries


# ---- the catalog ----
class Catalog:
    """Builds the three lists from the host (what is loaded), the installed folder (and its pins) and the remote index."""

    def __init__(self, host: Any, installed: Path, settings: Callable[[], dict[str, Any]], *,
                 tree_state: Callable[[str], tuple[str, str | None]] | None = None, default_url: str = "") -> None:
        self.host, self.installed, self._settings, self.default_url = host, Path(installed), settings, default_url
        self._tree_state = tree_state or (lambda ext_id: ("ready", None))

    @property
    def url(self) -> str:
        v = self._settings().get("extensionCatalogUrl")
        return v.strip() if isinstance(v, str) and v.strip() else self.default_url

    def remote(self) -> tuple[list[dict[str, Any]], str | None]:
        url = self.url
        if not url:
            return [], None
        try:
            return fetch_index(url, bundled_ids()), None
        except Exception as e:  # noqa: BLE001 - a failing index is reported, never raised
            return [], f"{type(e).__name__}: {e}"

    def remote_ids(self) -> set[str]:
        return {e["id"] for e in self.remote()[0]}

    def _enabled(self, ext_id: str, manifest: Manifest) -> bool:
        scope = self.host.extensions.get(ext_id) if self.host else None
        if scope is not None:
            return bool(scope.enabled())
        return manifest.enabled is not False

    def installed_ids(self) -> list[str]:
        if not self.installed.is_dir():
            return []
        return sorted(p.name for p in self.installed.iterdir() if p.is_dir() and not p.name.startswith("."))

    def build(self) -> dict[str, Any]:
        bundled = bundled_manifests()
        on = {i: self._enabled(i, m) for i, m in bundled.items()}
        installed_cards, available, services = [], [], []
        folder = []
        for ext_id in self.installed_ids():
            folder.append(self._folder_card(ext_id, on))
        for ext_id, m in bundled.items():
            card = card_for_manifest(m, root=BUILTIN_ROOT / ext_id, tier="builtin", source="bundled", enabled=on[ext_id])
            if ext_id in SERVICE_IDS:
                services.append(card)
            elif on[ext_id]:
                installed_cards.append(card)
            else:
                available.append(card)
        # a loaded folder extension may be enabled; others rely on the host
        for card in folder:
            on[card["id"]] = card["enabled"]
        for card in [*installed_cards, *available, *folder, *services]:
            if card["enabled"] or card["tier"] == "installed":
                off = [r for r in card["requires"]["extensions"] if not on.get(r, False)]
                if card["enabled"] and off and card["state"] == "ready":
                    card["state"], card["reason"] = "disabled-dependency", f"needs {', '.join(off)}, which is off"
        all_cards = [*installed_cards, *available, *folder, *services]
        for card in all_cards:
            card["requiredBy"] = sorted(c["id"] for c in all_cards if card["id"] in c["requires"]["extensions"] or card["id"] in c["requires"]["services"])
        entries, error = self.remote()
        taken = {c["id"] for c in all_cards}
        for e in entries:
            if e["id"] in taken:
                continue
            available.append({
                "id": e["id"], "name": e["name"], "description": e["description"], "version": "", "tier": "installed", "kind": "extension",
                "source": "catalog", "author": e["author"] or None, "enabled": False, "requires": {"extensions": [], "services": []},
                "requiredBy": [], "contributes": {"tools": [], "views": [], "slots": [], "hooks": [], "commands": [], "settingsFields": [], "processes": []},
                "permissions": [], "permissionText": [], "contextCostTokens": 0, "state": "ready", "verified": e["verified"],
                "url": e["url"], **({"ref": e["ref"]} if "ref" in e else {}), **({"subdir": e["subdir"]} if "subdir" in e else {})})
        out: dict[str, Any] = {"installed": [*installed_cards, *folder], "available": available, "services": services, "catalogUrl": self.url}
        if error:
            out["catalogError"] = error
        return out

    def _folder_card(self, ext_id: str, on: dict[str, bool]) -> dict[str, Any]:
        root = self.installed / ext_id
        pin = read_pin(self.installed, ext_id) or {}
        src = pin.get("source") if isinstance(pin.get("source"), dict) else {}
        kind = src.get("kind") if src.get("kind") in ("git", "zip", "folder") else "folder"
        try:
            m = load_manifest(root / "extension.json")
        except ManifestError as e:
            return {"id": ext_id, "name": ext_id, "description": "", "version": "", "tier": "installed", "kind": "extension", "source": kind,
                    "enabled": False, "requires": {"extensions": [], "services": []}, "requiredBy": [],
                    "contributes": {"tools": [], "views": [], "slots": [], "hooks": [], "commands": [], "settingsFields": [], "processes": []},
                    "permissions": [], "permissionText": [], "contextCostTokens": 0, "state": "invalid", "reason": str(e)}
        scope = self.host.extensions.get(ext_id) if self.host else None
        enabled = bool(scope and scope.enabled())
        state, reason = self._tree_state(ext_id)
        if state == "needs-review":
            enabled = False
        pinned = {"sha256": pin["sourceSha256"], **({"sha": pin["commit"]} if pin.get("commit") else {})} if isinstance(pin.get("sourceSha256"), str) else None
        return card_for_manifest(m, root=root, tier="installed", source=kind, enabled=enabled, state=state, reason=reason, pinned=pinned,
                                 url=src.get("url"), ref=src.get("ref"), subdir=src.get("subdir"))

    def consent_bundled(self, ext_id: str) -> dict[str, Any] | None:
        m = bundled_manifests().get(ext_id)
        if m is None:
            return None
        root = BUILTIN_ROOT / ext_id
        enabled = self._enabled(ext_id, m)
        card = card_for_manifest(m, root=root, tier="builtin", source="bundled", enabled=enabled)
        for other_id, other in bundled_manifests().items():
            if ext_id in other.requires_extensions or ext_id in other.requires_services:
                card["requiredBy"].append(other_id)
        from .authoring.pack import content_hash, digest
        return {"id": ext_id, "card": card, "commands": [], "typosquat": None, "findings": [], "sha256": digest(content_hash(root)),
                "source": {"kind": "bundled"}, "quarantined": False}
