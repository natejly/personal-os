-- Recipes: a saved coding task with a verify command. A version is immutable; a save inserts the next one.
CREATE TABLE IF NOT EXISTS coding_recipes (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  current_version INTEGER NOT NULL,
  archived INTEGER NOT NULL DEFAULT 0,
  created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS coding_recipe_versions (
  recipe_id TEXT NOT NULL,
  version INTEGER NOT NULL,
  body TEXT NOT NULL,
  created_by TEXT NOT NULL,
  approved_at REAL,
  created_at REAL NOT NULL,
  PRIMARY KEY (recipe_id, version));
-- One run of a version. `resolved` is the exact settings the run used, written once before the agent starts.
CREATE TABLE IF NOT EXISTS coding_recipe_runs (
  id TEXT PRIMARY KEY,
  recipe_id TEXT NOT NULL,
  recipe_version INTEGER NOT NULL,
  origin TEXT NOT NULL,
  conversation_id TEXT,
  job_id TEXT,
  inputs TEXT NOT NULL,
  resolved TEXT NOT NULL,
  session_id TEXT,
  status TEXT NOT NULL,
  attempt INTEGER NOT NULL DEFAULT 1,
  attempts TEXT NOT NULL DEFAULT '[]',
  verify_exit INTEGER,
  verify_tail TEXT,
  created_at REAL NOT NULL,
  started_at REAL,
  ended_at REAL);
CREATE INDEX IF NOT EXISTS idx_coding_recipe_runs ON coding_recipe_runs(recipe_id, created_at);
