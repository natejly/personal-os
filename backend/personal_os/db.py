"""SQLite storage. One file, WAL mode, FTS5 for document + memory search."""
from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS projects (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  system_prompt TEXT NOT NULL DEFAULT '',
  color TEXT NOT NULL DEFAULT '#d97757',
  tools TEXT NOT NULL DEFAULT '{}',
  created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS conversations (
  id TEXT PRIMARY KEY,
  project_id TEXT REFERENCES projects(id) ON DELETE CASCADE,
  title TEXT NOT NULL,
  model TEXT NOT NULL,
  settings TEXT NOT NULL DEFAULT '{}',
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_conv_project ON conversations(project_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS messages (
  id TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  role TEXT NOT NULL,
  content TEXT NOT NULL,
  model TEXT,
  error TEXT,
  context_used TEXT,
  tool_events TEXT,
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_msg_conv ON messages(conversation_id, created_at);

CREATE TABLE IF NOT EXISTS memories (
  id TEXT PRIMARY KEY,
  project_id TEXT REFERENCES projects(id) ON DELETE CASCADE,
  content TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'fact',
  source TEXT NOT NULL DEFAULT 'user',
  pinned INTEGER NOT NULL DEFAULT 0,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mem_project ON memories(project_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS kg_nodes (
  id TEXT PRIMARY KEY,
  project_id TEXT REFERENCES projects(id) ON DELETE CASCADE,
  label TEXT NOT NULL,
  type TEXT NOT NULL DEFAULT 'entity',
  properties TEXT NOT NULL DEFAULT '{}',
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_node_project ON kg_nodes(project_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_node_label ON kg_nodes(IFNULL(project_id, ''), lower(label));

CREATE TABLE IF NOT EXISTS kg_edges (
  id TEXT PRIMARY KEY,
  project_id TEXT REFERENCES projects(id) ON DELETE CASCADE,
  source_id TEXT NOT NULL REFERENCES kg_nodes(id) ON DELETE CASCADE,
  target_id TEXT NOT NULL REFERENCES kg_nodes(id) ON DELETE CASCADE,
  relation TEXT NOT NULL,
  properties TEXT NOT NULL DEFAULT '{}',
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_edge_project ON kg_edges(project_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_edge_uniq ON kg_edges(source_id, target_id, lower(relation));

CREATE TABLE IF NOT EXISTS documents (
  id TEXT PRIMARY KEY,
  project_id TEXT REFERENCES projects(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  mime TEXT NOT NULL DEFAULT '',
  size INTEGER NOT NULL DEFAULT 0,
  path TEXT NOT NULL,
  text TEXT NOT NULL DEFAULT '',
  chunk_count INTEGER NOT NULL DEFAULT 0,
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_doc_project ON documents(project_id, created_at DESC);

CREATE TABLE IF NOT EXISTS usage_log (
  id TEXT PRIMARY KEY,
  created_at REAL NOT NULL,
  model TEXT NOT NULL,
  kind TEXT NOT NULL,
  conversation_id TEXT,
  project_id TEXT,
  prompt_tokens INTEGER NOT NULL DEFAULT 0,
  completion_tokens INTEGER NOT NULL DEFAULT 0,
  duration_ms INTEGER NOT NULL DEFAULT 0,
  cost REAL,
  estimated INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS usage_log_created ON usage_log(created_at);
CREATE TABLE IF NOT EXISTS chunks (
  id TEXT PRIMARY KEY,
  document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  idx INTEGER NOT NULL,
  text TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chunk_doc ON chunks(document_id, idx);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
  text, chunk_id UNINDEXED, document_id UNINDEXED, tokenize='porter unicode61'
);
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
  content, memory_id UNINDEXED, tokenize='porter unicode61'
);
"""


def now() -> float:
    return time.time()


def new_id() -> str:
    return uuid.uuid4().hex[:16]


class Database:
    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "uploads").mkdir(exist_ok=True)
        self.path = self.data_dir / "personal-os.db"
        with self.connect() as c:
            c.executescript(SCHEMA)
            self._migrate(c)

    @staticmethod
    def _migrate(c: sqlite3.Connection) -> None:
        """Add columns introduced after the first release (CREATE TABLE IF NOT EXISTS won't)."""
        wanted = {
            "projects": {"tools": "TEXT NOT NULL DEFAULT '{}'"},
            "messages": {"tool_events": "TEXT", "trace": "TEXT"},
        }
        for table, cols in wanted.items():
            have = {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}
            for col, ddl in cols.items():
                if col not in have:
                    c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
        c.commit()

    def connect(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.path, check_same_thread=False)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        return c

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        c = self.connect()
        try:
            yield c
            c.commit()
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()

    # ---- settings ----
    def get_settings(self) -> dict[str, Any]:
        with self.tx() as c:
            rows = c.execute("SELECT key, value FROM settings").fetchall()
        return {r["key"]: json.loads(r["value"]) for r in rows}

    def set_settings(self, patch: dict[str, Any]) -> None:
        with self.tx() as c:
            for k, v in patch.items():
                c.execute(
                    "INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (k, json.dumps(v)),
                )


def row_to_dict(r: sqlite3.Row | None, json_fields: tuple[str, ...] = ()) -> dict[str, Any] | None:
    if r is None:
        return None
    d = dict(r)
    for f in json_fields:
        if f in d and isinstance(d[f], str):
            try:
                d[f] = json.loads(d[f])
            except ValueError:
                pass
    return d


def data_dir_from_env() -> Path:
    return Path(os.environ.get("PERSONAL_OS_DATA_DIR", "./data")).expanduser().resolve()
