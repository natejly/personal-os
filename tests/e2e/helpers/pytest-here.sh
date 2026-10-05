#!/bin/zsh
# zsh tests/e2e/helpers/pytest-here.sh tests/test_x.py ...
# This worktree's venv is linked from a tree that has no pytest; use the one in the main checkout's venv
# against this worktree's own backend package.
cd "$(dirname "$0")/../../../backend"
export PYTHONPATH="$PWD"
export GRAIN_SECRETS_BACKEND=file
exec "/Users/natejly/Desktop/Personal OS/backend/.venv/bin/pytest" "$@"
