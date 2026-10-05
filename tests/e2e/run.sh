#!/bin/zsh
# zsh tests/e2e/run.sh [playwright args]   e.g.  zsh tests/e2e/run.sh chat --grep "regenerate"
cd "$(dirname "$0")"
exec node node_modules/.bin/playwright test -c playwright.config.mjs "$@"
