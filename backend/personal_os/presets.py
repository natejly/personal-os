"""Space presets: named templates of a canvas. Saving snapshots a canvas's windows; instantiating makes a new canvas."""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from .canvas import _INSERT_WINDOW, _RENAMED_DEFAULTS, FALLBACK_NAME, WIDGET_KINDS, Canvases, clamp_opacity
from .dashboards import Dashboards
from .db import Database, new_id, now, row_to_dict
from .notes import Notes

# Owned here, like canvas.py's tables: CREATE TABLE IF NOT EXISTS in the constructor covers existing databases.
SCHEMA = """
CREATE TABLE IF NOT EXISTS canvas_presets (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  project_id TEXT REFERENCES projects(id) ON DELETE SET NULL,
  snap_mode TEXT NOT NULL DEFAULT 'both',
  grid_size INTEGER NOT NULL DEFAULT 16,
  zoom REAL NOT NULL DEFAULT 1.0,
  pan_x REAL NOT NULL DEFAULT 0,
  pan_y REAL NOT NULL DEFAULT 0,
  wallpaper TEXT NOT NULL DEFAULT '',
  windows TEXT NOT NULL DEFAULT '[]',   -- JSON list[PresetWindow]
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_canvas_presets_created ON canvas_presets(created_at);
"""
PRESET_JSON = ("windows",)
# needsRef kinds -> the table their ref_id must still exist in. Mirrors `needsRef` in
# src/renderer/src/canvas/registry.ts (chat, note, dashboard-widget, project, artifact).
REF_TABLES = {"chat": "conversations", "note": "notes", "dashboard-widget": "widgets", "project": "projects", "artifact": "artifacts"}


def _snap_window(w: dict[str, Any], i: int) -> dict[str, Any]:
    """
    A window as a preset keeps it: content + geometry, no live state. Any window that is not 'normal'
    and holds restore bounds keeps those: a maximized one, and one minimized or popped out while
    maximized (x/y/w/h then still hold the full-viewport box; the store pays the bounds back on a
    return to 'normal').
    """
    b = w["restore_bounds"] if w["restore_bounds"] and w["state"] != "normal" else w
    return {
        "kind": w["kind"], "ref_id": w["ref_id"], "project_id": w["project_id"], "title": w["title"],
        "x": b["x"], "y": b["y"], "w": b["w"], "h": b["h"], "z": i,
        "pinned": int(bool(w["pinned"])), "opacity": clamp_opacity(w.get("opacity", 1.0)), "config": w["config"] or {},
    }


def _exists(c: sqlite3.Connection, table: str, rid: str | None) -> bool:
    """`table` only ever comes from REF_TABLES or the literal "projects", never from input."""
    # A trashed project or chat is as good as gone here: restoring a preset must not pin a window to it.
    soft = " AND deleted_at IS NULL" if table in ("projects", "conversations") else ""
    return bool(rid) and c.execute(f"SELECT 1 FROM {table} WHERE id=?{soft}", (rid,)).fetchone() is not None


class CanvasPresets:
    def __init__(self, db: Database, canvases: Canvases, notes: Notes | None = None, dashboards: Dashboards | None = None):
        self.db = db
        self.canvases = canvases
        self.notes = notes
        self.dashboards = dashboards
        with db.tx() as c:
            c.executescript(SCHEMA)
            for old, new in _RENAMED_DEFAULTS:
                c.execute("UPDATE canvas_presets SET name=? WHERE name=?", (new, old))

    def list(self) -> list[dict[str, Any]]:
        with self.db.tx() as c:
            return [row_to_dict(r, PRESET_JSON) for r in c.execute("SELECT * FROM canvas_presets ORDER BY created_at, id").fetchall()]  # type: ignore[misc]

    def get(self, id: str) -> dict[str, Any] | None:
        with self.db.tx() as c:
            return row_to_dict(c.execute("SELECT * FROM canvas_presets WHERE id=?", (id,)).fetchone(), PRESET_JSON)

    def create_from_canvas(self, canvas_id: str, name: str = "") -> dict[str, Any] | None:
        """None means the canvas does not exist. The server snapshots it, so the client never sends window data."""
        src = self.canvases.get(canvas_id)
        if not src:
            return None
        pid = new_id()
        t = now()
        windows = [_snap_window(w, i) for i, w in enumerate(src["windows"])]
        with self.db.tx() as c:
            c.execute(
                "INSERT INTO canvas_presets(id,name,project_id,snap_mode,grid_size,zoom,pan_x,pan_y,wallpaper,windows,created_at,updated_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (pid, name.strip() or src["name"] or FALLBACK_NAME, src["project_id"], src["snap_mode"], src["grid_size"],
                 src["zoom"], src["pan_x"], src["pan_y"], src["wallpaper"], json.dumps(windows), t, t),
            )
        return self.get(pid)

    def update(self, id: str, patch: dict[str, Any]) -> dict[str, Any] | None:
        fields = {k: v for k, v in patch.items() if k in {"name"}}
        if not fields:
            return self.get(id)
        fields["updated_at"] = now()
        sets = ", ".join(f"{k}=?" for k in fields)
        with self.db.tx() as c:
            c.execute(f"UPDATE canvas_presets SET {sets} WHERE id=?", (*fields.values(), id))
        return self.get(id)

    def delete(self, id: str) -> None:
        with self.db.tx() as c:
            c.execute("DELETE FROM canvas_presets WHERE id=?", (id,))

    # ---------- portable files ----------
    # Notes and dashboard widgets travel with their content; every other referent stays a ref (a chat
    # must never leave the machine, and projects/artifacts have no body that fits a window).
    def export(self, id: str) -> dict[str, Any] | None:
        p = self.get(id)
        if not p or not self.notes or not self.dashboards:
            return None
        embedded: dict[str, Any] = {}
        windows = []
        for w in p["windows"] or []:
            ref, kind = w.get("ref_id"), w.get("kind")
            if kind == "note" and ref:
                n = self.notes.get(ref)
                if not n:
                    continue
                embedded[ref] = {"kind": "note", "body": n["body"], "color": n["color"]}
            elif kind == "dashboard-widget" and ref:
                d = self.dashboards.widget(ref)
                if not d:
                    continue
                embedded[ref] = {"kind": "dashboard-widget", **{k: d[k] for k in ("title", "prompt", "code", "output", "width", "height", "refresh_minutes", "spec")}, "widget_kind": d["kind"]}
            windows.append({**w, "project_id": None})
        return {"grain_preset": 1, "name": p["name"], **{k: p[k] for k in ("snap_mode", "grid_size", "zoom", "pan_x", "pan_y", "wallpaper")},
                "windows": windows, "embedded": embedded}

    def import_file(self, data: dict[str, Any], instantiate: bool = True) -> dict[str, Any]:
        """Recreate each embedded referent under a new id, store the preset against the new ids, optionally make its canvas.
        Raises ValueError on a file that is not a preset export."""
        if data.get("grain_preset") != 1 or not isinstance(data.get("windows"), list) or not self.notes or not self.dashboards:
            raise ValueError("Not a preset file")
        emb = data.get("embedded") or {}
        remap: dict[str, str] = {}
        dash_id = ""
        for old, e in emb.items():
            if e.get("kind") == "note":
                remap[old] = self.notes.create(str(e.get("body", "")), str(e.get("color") or "yellow"))["id"]
            elif e.get("kind") == "dashboard-widget":
                dash_id = dash_id or self.dashboards.create(f"{data.get('name') or 'Imported'} widgets")["id"]
                # Data sources are machine-local, so an imported widget starts with none.
                remap[old] = self.dashboards.create_widget(
                    dash_id, str(e.get("title", "")), str(e.get("widget_kind", "")), str(e.get("prompt", "")), None, str(e.get("code", "")),
                    str(e.get("output", "")), int(e.get("width", 1)), int(e.get("height", 280)), int(e.get("refresh_minutes", 60)), e.get("spec") or {})["id"]
        windows = []
        for w in data["windows"]:
            if not isinstance(w, dict) or not all(isinstance(w.get(k), (int, float)) for k in ("x", "y", "w", "h")):
                continue
            windows.append({**w, "ref_id": remap.get(w.get("ref_id"), w.get("ref_id")), "project_id": None})
        pid = new_id()
        t = now()
        with self.db.tx() as c:
            c.execute(
                "INSERT INTO canvas_presets(id,name,project_id,snap_mode,grid_size,zoom,pan_x,pan_y,wallpaper,windows,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (pid, str(data.get("name") or "").strip() or FALLBACK_NAME, None, data.get("snap_mode") or "both", int(data.get("grid_size") or 16),
                 float(data.get("zoom") or 1.0), float(data.get("pan_x") or 0), float(data.get("pan_y") or 0), str(data.get("wallpaper") or ""), json.dumps(windows), t, t))
        out = {"preset": self.get(pid)}
        if instantiate:
            out["canvas"] = self.instantiate(pid)
        return out

    def instantiate(self, id: str, name: str | None = None) -> dict[str, Any] | None:
        """None means no such preset. Windows whose referent is gone (or whose kind is unknown) are skipped and counted.
        Refs are shared, not deep-copied, the same as Canvases.create(copy_from=...)."""
        preset = self.get(id)
        if not preset:
            return None
        cid = new_id()
        t = now()
        skipped = 0
        with self.db.tx() as c:
            project_id = preset["project_id"] if _exists(c, "projects", preset["project_id"]) else None
            pos = c.execute("SELECT COALESCE(MAX(position),-1)+1 FROM canvases").fetchone()[0]
            c.execute(
                "INSERT INTO canvases(id,name,project_id,position,snap_mode,grid_size,zoom,pan_x,pan_y,wallpaper,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (cid, (name or "").strip() or preset["name"] or FALLBACK_NAME, project_id, pos, preset["snap_mode"], preset["grid_size"],
                 preset["zoom"], preset["pan_x"], preset["pan_y"], preset["wallpaper"], t, t),
            )
            for w in preset["windows"] or []:
                kind = w.get("kind")
                if kind not in WIDGET_KINDS or (kind in REF_TABLES and not _exists(c, REF_TABLES[kind], w.get("ref_id"))):
                    skipped += 1
                    continue
                c.execute(_INSERT_WINDOW, (
                    new_id(), cid, kind, w.get("ref_id"),
                    w.get("project_id") if _exists(c, "projects", w.get("project_id")) else None,
                    w.get("title") or "", w["x"], w["y"], w["w"], w["h"], w.get("z", 0), "normal",
                    None, None, int(bool(w.get("pinned"))), clamp_opacity(w.get("opacity", 1.0)),
                    json.dumps(w.get("config") or {}), t, t,
                ))
        return {**self.canvases.get(cid), "skipped": skipped}  # type: ignore[dict-item]
