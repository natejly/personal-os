#!/bin/zsh
# zsh tests/e2e/run-pytest.sh backend/tests/test_x.py ...   (sets PYTHONPATH so the worktree's own package is imported)
cd "$(dirname "$0")/../.."
export PYTHONPATH="$PWD/backend"
export GRAIN_SECRETS_BACKEND=file
cd backend && exec .venv/bin/pytest "$@"
