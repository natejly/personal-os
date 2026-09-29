#!/usr/bin/env bash
# Runs LiteLLM (if configured), the Python backend (autoreload) and Electron (HMR) together.
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] && set -a && . ./.env && set +a
PORT="${PERSONAL_OS_PORT:-8765}"
DATA_DIR="${PERSONAL_OS_DATA_DIR:-$HOME/Library/Application Support/personal-os/data}"
PIDS=()
trap 'kill "${PIDS[@]}" 2>/dev/null || true' EXIT

if [ ! -x backend/.venv/bin/python ]; then
  echo "» Creating backend venv…"
  (cd backend && uv venv --quiet .venv && uv pip install --quiet -e .)
fi

if [ -n "${FIREWORKS_API_KEY:-}" ] && [[ "${PERSONAL_OS_BASE_URL:-http://localhost:4000}" == http://localhost:${LITELLM_PORT:-4000}* ]]; then
  if ! curl -sf "http://localhost:${LITELLM_PORT:-4000}/health/liveliness" >/dev/null 2>&1; then
    echo "» Starting LiteLLM proxy on :${LITELLM_PORT:-4000}"
    ./scripts/litellm.sh & PIDS+=($!)
  else
    echo "» LiteLLM already running on :${LITELLM_PORT:-4000}"
  fi
fi

echo "» Backend on http://127.0.0.1:$PORT  (data: $DATA_DIR)"
(cd backend && .venv/bin/python -m personal_os --port "$PORT" --data-dir "$DATA_DIR" --reload) & PIDS+=($!)
for _ in $(seq 1 40); do curl -sf "http://127.0.0.1:$PORT/health" >/dev/null && break; sleep 0.25; done

PERSONAL_OS_BACKEND_URL="http://127.0.0.1:$PORT" npm run dev
