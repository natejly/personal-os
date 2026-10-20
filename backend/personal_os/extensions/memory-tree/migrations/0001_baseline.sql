-- The history digest is a rebuildable cache over `memories`: one append-only log of leaves per scope ('personal' or
-- 'project:<id>'), and a node for each aligned power-of-two block of that log that is full.
-- `seq` is the global key; `ord` is the dense position in the scope's log, which is what the block arithmetic and the API use.
CREATE TABLE IF NOT EXISTS memory_tree_leaf (
  seq INTEGER PRIMARY KEY,
  memory_id TEXT NOT NULL,
  scope TEXT NOT NULL,
  ord INTEGER NOT NULL,
  text TEXT NOT NULL,
  ts REAL,
  tombstoned INTEGER NOT NULL DEFAULT 0,
  tainted INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_tree_leaf_ord ON memory_tree_leaf(scope, ord);
CREATE INDEX IF NOT EXISTS idx_memory_tree_leaf_memory ON memory_tree_leaf(memory_id);

CREATE TABLE IF NOT EXISTS memory_tree_node (
  scope TEXT NOT NULL,
  level INTEGER NOT NULL,
  k INTEGER NOT NULL,
  summary TEXT NOT NULL DEFAULT '',
  children_hash TEXT NOT NULL DEFAULT '',
  stale INTEGER NOT NULL DEFAULT 0,
  updated_at REAL,
  PRIMARY KEY (scope, level, k)
);
