-- Claude Code orchestration: one row per headless session, and its event log (capped per session by the code).
CREATE TABLE IF NOT EXISTS claude_code_sessions (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  repo_path TEXT NOT NULL,
  worktree TEXT NOT NULL,
  branch TEXT,
  base_sha TEXT,
  own_worktree INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL,
  detail TEXT,
  model TEXT,
  permission_mode TEXT,
  prompt TEXT NOT NULL,
  options TEXT,
  fleet_id TEXT,
  conversation_id TEXT,
  run_id TEXT,
  hook_key TEXT NOT NULL,
  claude_session_id TEXT,
  cost_usd REAL NOT NULL DEFAULT 0,
  num_turns INTEGER NOT NULL DEFAULT 0,
  result_subtype TEXT,
  result TEXT,
  pending_approval TEXT,
  last_text TEXT NOT NULL DEFAULT '',
  created_at REAL NOT NULL,
  started_at REAL,
  ended_at REAL);
CREATE INDEX IF NOT EXISTS idx_claude_code_sessions_fleet ON claude_code_sessions(fleet_id);
CREATE INDEX IF NOT EXISTS idx_claude_code_sessions_created ON claude_code_sessions(created_at);
CREATE TABLE IF NOT EXISTS claude_code_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL,
  seq INTEGER NOT NULL,
  kind TEXT NOT NULL,
  text TEXT NOT NULL DEFAULT '',
  tool TEXT,
  data TEXT,
  at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS idx_claude_code_events ON claude_code_events(session_id, seq);
