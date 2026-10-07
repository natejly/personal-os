#!/bin/zsh
# One-time setup for a fresh worktree: make sure node_modules, backend/.venv and .env exist, link Playwright, build.
# Usage: tests/e2e/setup-worktree.sh [source-checkout]
#   Missing node_modules / backend/.venv / .env are linked or copied from source-checkout when given,
#   otherwise installed fresh (npm ci, uv sync). Playwright comes from PLAYWRIGHT_NODE_MODULES (a node_modules
#   dir holding @playwright/test), else source-checkout's tests/e2e/node_modules.
set -e
fail() { print -u2 "setup-worktree: $*"; exit 1; }
SRC=""
[ -z "${1:-}" ] || SRC="$(cd "$1" 2>/dev/null && pwd)" || fail "source checkout not found: $1"
cd "$(dirname "$0")/../.."

if [ ! -e node_modules ]; then
  if [ -n "$SRC" ]; then
    [ -d "$SRC/node_modules" ] || fail "no node_modules in $SRC"
    ln -s "$SRC/node_modules" node_modules
  else
    npm ci
  fi
fi
if [ ! -e backend/.venv ]; then
  if [ -n "$SRC" ]; then
    [ -d "$SRC/backend/.venv" ] || fail "no backend/.venv in $SRC"
    ln -s "$SRC/backend/.venv" backend/.venv
  else
    (cd backend && uv sync && uv pip install pytest)  # pytest is not a declared dependency
  fi
fi
if [ ! -e .env ]; then
  if [ -n "$SRC" ] && [ -f "$SRC/.env" ]; then cp "$SRC/.env" .env
  else echo "setup-worktree: no .env (only E2E_LLM=real needs one)"; fi
fi

if [ ! -e tests/e2e/node_modules ]; then
  PW="${PLAYWRIGHT_NODE_MODULES:-}"
  if [ -z "$PW" ] && [ -n "$SRC" ] && [ -e "$SRC/tests/e2e/node_modules" ]; then
    PW="$(readlink "$SRC/tests/e2e/node_modules" || echo "$SRC/tests/e2e/node_modules")"
  fi
  [ -n "$PW" ] || fail "set PLAYWRIGHT_NODE_MODULES to a node_modules dir with @playwright/test, or pass a source checkout"
  [ -d "$PW/@playwright/test" ] || fail "no @playwright/test in $PW"
  ln -s "$PW" tests/e2e/node_modules
fi

npm run build 2>&1 | tail -3
echo "setup done in $PWD"
