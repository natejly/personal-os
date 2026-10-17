-- The hook bridge: the per-session key that authorizes /coding-hooks/<session>/pre|post while the session is live, whether
-- its hooks were started gating, the recipe run that started it, and the Claude Code options a recipe set (tool lists,
-- step limit), which a follow-up carries too.
ALTER TABLE coding_sessions ADD COLUMN hook_key TEXT;
ALTER TABLE coding_sessions ADD COLUMN hook_gate INTEGER NOT NULL DEFAULT 0;
ALTER TABLE coding_sessions ADD COLUMN recipe_run_id TEXT;
ALTER TABLE coding_sessions ADD COLUMN cli_opts TEXT;
