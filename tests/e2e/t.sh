#!/bin/zsh
# usage: zsh tests/e2e/t.sh <grep/file>  -> condensed failures
cd "$(dirname "$0")"
node node_modules/.bin/playwright test -c playwright.config.mjs --reporter=line "$@" 2>&1 | sed 's/\x1b\[[0-9;]*m//g' | grep -v "^\s*$" | grep -E "^\s*[0-9]+\) |Error|Expected|Received|Timeout|^\s+>|passed|failed|flaky|at .*spec|locator|Locator" | cut -c1-260 | head -${LINES_MAX:-80}
