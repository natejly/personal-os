#!/usr/bin/env bash
# Starts the LiteLLM proxy with litellm.yaml, loading keys from .env
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] && set -a && . ./.env && set +a
: "${FIREWORKS_API_KEY:?Set FIREWORKS_API_KEY in .env}"
if [ -x backend/.venv/bin/litellm ]; then LITELLM=backend/.venv/bin/litellm
elif command -v litellm >/dev/null; then LITELLM=litellm
else
  echo "» Installing litellm[proxy] into backend/.venv…"
  (cd backend && uv pip install --quiet 'litellm[proxy]')
  LITELLM=backend/.venv/bin/litellm
fi
exec "$LITELLM" --config litellm.yaml --port "${LITELLM_PORT:-4000}"
