-- The coding tables as the core migration steps 11, 35 and 37 left them. Those steps stay as history; on an install that
-- has the tables this changes nothing, and on a fresh one it is the same shape.
CREATE TABLE IF NOT EXISTS coding_sessions (
  id TEXT PRIMARY KEY,
  agent TEXT NOT NULL,
  external_id TEXT,
  session_id TEXT,
  repo_path TEXT NOT NULL,
  worktree TEXT NOT NULL,
  branch TEXT,
  conversation_id TEXT,
  desk_id TEXT,
  run_id TEXT,
  name TEXT NOT NULL,
  prompt TEXT NOT NULL,
  model TEXT,
  permission_mode TEXT,
  status TEXT NOT NULL,
  detail TEXT,
  log_tail TEXT NOT NULL DEFAULT '',
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  ended_at REAL,
  base_sha TEXT);
CREATE INDEX IF NOT EXISTS idx_coding_sessions_conv ON coding_sessions(conversation_id, created_at);
CREATE TABLE IF NOT EXISTS coding_session_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL,
  seq INTEGER NOT NULL,
  ts TEXT NOT NULL,
  kind TEXT NOT NULL,
  text TEXT NOT NULL DEFAULT '',
  tool TEXT,
  data TEXT);
CREATE INDEX IF NOT EXISTS idx_coding_session_events ON coding_session_events(session_id, seq);
