#!/bin/zsh
# One-time setup for a fresh worktree: link deps from the stress-test worktree, copy .env, build the app.
set -e
cd "$(dirname "$0")/../.."
SRC="${1:-/Users/natejly/Desktop/Personal OS/.claude/worktrees/stress-test-2026-10-04}"
[ -e node_modules ] || ln -s "$SRC/node_modules" node_modules
[ -e backend/.venv ] || ln -s "$SRC/backend/.venv" backend/.venv
[ -e .env ] || cp "$SRC/.env" .env
if [ ! -e tests/e2e/node_modules ]; then
  PW="$(readlink "$SRC/tests/e2e/node_modules" || true)"
  ln -s "${PW:-$SRC/tests/e2e/node_modules}" tests/e2e/node_modules
fi
npm run build 2>&1 | tail -3
echo "setup done in $PWD"
