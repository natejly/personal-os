"""Canvas Mode: spaces (canvases) and the floating windows on them."""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from .db import Database, new_id, now, row_to_dict

# Mirrors the WidgetKind union in src/shared/types.ts, which is the source of truth.
WIDGET_KINDS = (
    "chat", "todos", "calendar", "board", "note", "dashboard-widget",
    "memory", "graph", "documents", "recap", "project", "usage", "activity",
)
WINDOW_STATES = ("normal", "minimized", "maximized", "popped")
SNAP_MODES = ("off", "grid", "guides", "both")

DEFAULT_NAME = "Desk 1"
WINDOW_JSON = ("restore_bounds", "popout_bounds", "config")

# These tables are owned here, not by db.py: Database._migrate runs inside Database.__init__,
# before Canvases(db) exists, so a _migrate entry for them would see nothing. Post-release
# columns need an additive ALTER in __init__ below.
SCHEMA = """
CREATE TABLE IF NOT EXISTS canvases (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  project_id TEXT REFERENCES projects(id) ON DELETE SET NULL,  -- optional space -> project binding
  position INTEGER NOT NULL DEFAULT 0,
  snap_mode TEXT NOT NULL DEFAULT 'both',   -- off | grid | guides | both
  grid_size INTEGER NOT NULL DEFAULT 16,
  zoom REAL NOT NULL DEFAULT 1.0,
  pan_x REAL NOT NULL DEFAULT 0,
  pan_y REAL NOT NULL DEFAULT 0,
  wallpaper TEXT NOT NULL DEFAULT '',       -- '' | tint token
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS canvas_windows (
  id TEXT PRIMARY KEY,
  canvas_id TEXT NOT NULL REFERENCES canvases(id) ON DELETE CASCADE,
  kind TEXT NOT NULL,                       -- WidgetKind
  ref_id TEXT,                              -- conversation / board / dashboard / note id, no foreign key
  project_id TEXT REFERENCES projects(id) ON DELETE SET NULL,
  title TEXT NOT NULL DEFAULT '',           -- '' = derive from the underlying object
  x REAL NOT NULL, y REAL NOT NULL, w REAL NOT NULL, h REAL NOT NULL,
  z INTEGER NOT NULL DEFAULT 0,
  state TEXT NOT NULL DEFAULT 'normal',     -- normal | minimized | maximized | popped
  restore_bounds TEXT,                      -- Rect, for un-maximizing
  popout_bounds TEXT,                       -- PopoutBounds {x,y,width,height,display}
  pinned INTEGER NOT NULL DEFAULT 0,        -- always-on-top while popped
  config TEXT NOT NULL DEFAULT '{}',        -- per-widget options
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cw_canvas ON canvas_windows(canvas_id, z);
"""

_INSERT_WINDOW = (
    "INSERT INTO canvas_windows(id,canvas_id,kind,ref_id,project_id,title,x,y,w,h,z,state,restore_bounds,popout_bounds,pinned,config,created_at,updated_at)"
    " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
)


class Canvases:
    def __init__(self, db: Database):
        self.db = db
        with db.tx() as c:
            c.executescript(SCHEMA)

    @staticmethod
    def _seed(c: sqlite3.Connection) -> None:
        """Guarded insert, so two clients racing on GET /canvases at launch cannot make two spaces."""
        t = now()
        c.execute(
            "INSERT INTO canvases(id,name,project_id,position,snap_mode,grid_size,zoom,pan_x,pan_y,wallpaper,created_at,updated_at)"
            " SELECT ?,?,NULL,0,'both',16,1.0,0,0,'',?,? WHERE NOT EXISTS(SELECT 1 FROM canvases)",
            (new_id(), DEFAULT_NAME, t, t),
        )

    def list(self) -> list[dict[str, Any]]:
        with self.db.tx() as c:
            self._seed(c)
            rows = c.execute("SELECT * FROM canvases ORDER BY position, created_at").fetchall()
            wins = c.execute("SELECT * FROM canvas_windows ORDER BY z, created_at").fetchall()
        grouped: dict[str, list[dict[str, Any]]] = {}
        for w in wins:
            grouped.setdefault(w["canvas_id"], []).append(row_to_dict(w, WINDOW_JSON))  # type: ignore[arg-type]
        return [{**row_to_dict(r), "windows": grouped.get(r["id"], [])} for r in rows]  # type: ignore[dict-item]

    def get(self, id: str) -> dict[str, Any] | None:
        with self.db.tx() as c:
            r = c.execute("SELECT * FROM canvases WHERE id=?", (id,)).fetchone()
            if not r:
                return None
            wins = c.execute("SELECT * FROM canvas_windows WHERE canvas_id=? ORDER BY z, created_at", (id,)).fetchall()
        return {**row_to_dict(r), "windows": [row_to_dict(w, WINDOW_JSON) for w in wins]}  # type: ignore[dict-item]

    def create(self, name: str = "Desk", project_id: str | None = None, copy_from: str | None = None) -> dict[str, Any] | None:
        """None means `copy_from` does not exist."""
        src = self.get(copy_from) if copy_from else None
        if copy_from and not src:
            return None
        cid = new_id()
        t = now()
        with self.db.tx() as c:
            pos = c.execute("SELECT COALESCE(MAX(position),-1)+1 FROM canvases").fetchone()[0]
            c.execute(
                "INSERT INTO canvases(id,name,project_id,position,snap_mode,grid_size,zoom,pan_x,pan_y,wallpaper,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (cid, name.strip() or "Desk", project_id, pos,
                 src["snap_mode"] if src else "both", src["grid_size"] if src else 16,
                 src["zoom"] if src else 1.0, src["pan_x"] if src else 0.0, src["pan_y"] if src else 0.0,
                 src["wallpaper"] if src else "", t, t),
            )
            for w in (src or {}).get("windows", []):
                c.execute(_INSERT_WINDOW, (
                    new_id(), cid, w["kind"], w["ref_id"], w["project_id"], w["title"],
                    w["x"], w["y"], w["w"], w["h"], w["z"], "normal",
                    json.dumps(w["restore_bounds"]) if w["restore_bounds"] else None, None,
                    w["pinned"], json.dumps(w["config"]), t, t,
                ))
        return self.get(cid)

    def update(self, id: str, patch: dict[str, Any]) -> dict[str, Any] | None:
        fields = {k: v for k, v in patch.items() if k in {"name", "project_id", "position", "snap_mode", "grid_size", "zoom", "pan_x", "pan_y", "wallpaper"}}
        if not fields:
            return self.get(id)
        if "name" in fields:
            fields["name"] = str(fields["name"]).strip() or "Desk"
        fields["updated_at"] = now()
        sets = ", ".join(f"{k}=?" for k in fields)
        with self.db.tx() as c:
            c.execute(f"UPDATE canvases SET {sets} WHERE id=?", (*fields.values(), id))
        return self.get(id)

    def delete(self, id: str) -> None:
        with self.db.tx() as c:
            c.execute("DELETE FROM canvases WHERE id=?", (id,))

    def set_layout(self, canvas_id: str, windows: list[dict[str, Any]]) -> int:
        """One transaction for a whole drag. The canvas_id guard makes a stale or foreign id a no-op."""
        t = now()
        updated = 0
        with self.db.tx() as c:
            for entry in windows:
                fields = {k: v for k, v in entry.items() if k in {"x", "y", "w", "h", "z", "state"} and v is not None}
                if not entry.get("id") or not fields:
                    continue
                sets = ", ".join(f"{k}=?" for k in fields)
                cur = c.execute(
                    f"UPDATE canvas_windows SET {sets}, updated_at=? WHERE id=? AND canvas_id=?",
                    (*fields.values(), t, entry["id"], canvas_id),
                )
                updated += cur.rowcount
            c.execute("UPDATE canvases SET updated_at=? WHERE id=?", (t, canvas_id))
        return updated

    # ---- windows ----
    def window(self, id: str) -> dict[str, Any] | None:
        with self.db.tx() as c:
            return row_to_dict(c.execute("SELECT * FROM canvas_windows WHERE id=?", (id,)).fetchone(), WINDOW_JSON)

    def add_window(self, canvas_id: str, kind: str, ref_id: str | None = None, project_id: str | None = None, title: str = "",
                   x: float = 0, y: float = 0, w: float = 520, h: float = 640, config: dict[str, Any] | None = None) -> dict[str, Any] | None:
        """None means the canvas does not exist."""
        wid = new_id()
        t = now()
        with self.db.tx() as c:
            if not c.execute("SELECT 1 FROM canvases WHERE id=?", (canvas_id,)).fetchone():
                return None
            z = c.execute("SELECT COALESCE(MAX(z),-1)+1 FROM canvas_windows WHERE canvas_id=?", (canvas_id,)).fetchone()[0]
            c.execute(_INSERT_WINDOW, (wid, canvas_id, kind, ref_id, project_id, title, x, y, w, h, z, "normal",
                                      None, None, 0, json.dumps(config or {}), t, t))
            c.execute("UPDATE canvases SET updated_at=? WHERE id=?", (t, canvas_id))
        return self.window(wid)

    def update_window(self, id: str, patch: dict[str, Any]) -> dict[str, Any] | None:
        """`config` merges into the stored object; a `canvas_id` move re-bases z on top of the destination."""
        fields = {k: v for k, v in patch.items() if k in {"title", "state", "pinned", "x", "y", "w", "h", "z", "canvas_id", "restore_bounds", "popout_bounds"}}
        with self.db.tx() as c:
            row = c.execute("SELECT canvas_id, config FROM canvas_windows WHERE id=?", (id,)).fetchone()
            if not row:
                return None
            if "pinned" in fields:
                fields["pinned"] = int(bool(fields["pinned"]))
            for k in ("restore_bounds", "popout_bounds"):
                if k in fields:
                    fields[k] = json.dumps(fields[k]) if fields[k] is not None else None
            dest = fields.pop("canvas_id", None)
            if dest and dest != row["canvas_id"]:
                if not c.execute("SELECT 1 FROM canvases WHERE id=?", (dest,)).fetchone():
                    return None
                fields["canvas_id"] = dest
                fields["z"] = c.execute("SELECT COALESCE(MAX(z),-1)+1 FROM canvas_windows WHERE canvas_id=?", (dest,)).fetchone()[0]
            if isinstance(patch.get("config"), dict):
                fields["config"] = json.dumps({**json.loads(row["config"]), **patch["config"]})
            fields["updated_at"] = now()
            sets = ", ".join(f"{k}=?" for k in fields)
            c.execute(f"UPDATE canvas_windows SET {sets} WHERE id=?", (*fields.values(), id))
            c.execute("UPDATE canvases SET updated_at=? WHERE id IN (?,?)", (fields["updated_at"], row["canvas_id"], dest or row["canvas_id"]))
        return self.window(id)

    def raise_window(self, id: str) -> dict[str, Any] | None:
        with self.db.tx() as c:
            row = c.execute("SELECT canvas_id, z FROM canvas_windows WHERE id=?", (id,)).fetchone()
            if not row:
                return None
            top = c.execute("SELECT MAX(z) FROM canvas_windows WHERE canvas_id=?", (row["canvas_id"],)).fetchone()[0]
            if top is not None and row["z"] < top:
                c.execute("UPDATE canvas_windows SET z=?, updated_at=? WHERE id=?", (top + 1, now(), id))
        return self.window(id)

    def delete_window(self, id: str) -> None:
        with self.db.tx() as c:
            c.execute("DELETE FROM canvas_windows WHERE id=?", (id,))

    def delete_windows_for(self, kind: str, ref_id: str) -> int:
        """ref_id carries no foreign key, so deleting the referent has to sweep its windows."""
        with self.db.tx() as c:
            return c.execute("DELETE FROM canvas_windows WHERE kind=? AND ref_id=?", (kind, ref_id)).rowcount

    def reset_popped(self) -> int:
        """Called at startup: no BrowserWindow survives a relaunch, so 'popped' rows are stale."""
        with self.db.tx() as c:
            return c.execute("UPDATE canvas_windows SET state='normal', updated_at=? WHERE state='popped'", (now(),)).rowcount
