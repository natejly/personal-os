# End-to-end tests

Playwright drives the built Electron app against an isolated backend (own data dir, own port, file-backed secrets)
and a mock OpenAI-compatible provider, so nothing here can touch the real data directory or the Keychain.

```bash
PLAYWRIGHT_NODE_MODULES=<dir> tests/e2e/setup-worktree.sh [source-checkout]   # fresh worktree: deps, Playwright link, build
npm run build                                    # the harness loads out/main/index.js
ln -sfn <dir with @playwright/test>/node_modules tests/e2e/node_modules
node tests/e2e/node_modules/.bin/playwright test -c tests/e2e/playwright.config.mjs [file-or-grep]
E2E_LLM=real …                                   # use the LiteLLM proxy from .env instead of the mock
E2E_KEEP=1 …                                     # keep scratch dirs; E2E_WORKERS=n; E2E_RETRIES=n
E2E_FOREGROUND=1 …                                # debugging: normal windows (default is GRAIN_E2E_BACKGROUND=1: no Dock icon, showInactive(), app never frontmost)
```

`fixtures.mjs` gives each test a fresh `grain` ({page, api, llm, backend, dataDir}); `mockllm.mjs` documents the
`!!reply` / `!!tool` / `!!slow` / `!!fail` prompt directives that steer the mock's answers.
