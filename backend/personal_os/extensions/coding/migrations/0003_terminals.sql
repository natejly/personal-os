-- Terminals an agent opened through the terminal hub. The output is never stored here: only the hub's ring holds it.
CREATE TABLE IF NOT EXISTS coding_terminals (
  id TEXT PRIMARY KEY,
  owner_key TEXT NOT NULL,
  owner_run_id TEXT,
  session_id TEXT,
  conversation_id TEXT,
  name TEXT NOT NULL DEFAULT '',
  cwd TEXT NOT NULL,
  argv_digest TEXT NOT NULL,
  created_at REAL NOT NULL,
  last_used_at REAL NOT NULL,
  state TEXT NOT NULL,
  exit_code INTEGER,
  user_attached INTEGER NOT NULL DEFAULT 0,
  user_dirty INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS idx_coding_terminals_owner ON coding_terminals(owner_key, state);
