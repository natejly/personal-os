# AGENTS.md

Instructions for an agent asked to get Grain running from nothing. Follow the steps in
order; each one has a check you can run before moving on.

Grain is an Electron desktop app (React renderer, TypeScript main process) talking to a
Python FastAPI backend over localhost. macOS only.

Once the repository is cloned, three files cover more ground than this one and win where
they disagree:

- `CLAUDE.md` — repo rules, and they override everything else.
- `.claude/skills/grain/SKILL.md` — the map of the app itself: views, tools, data dirs,
  how to drive the backend over HTTP, how to run the test suites.
- [README.md](README.md) for what the app does, [docs/releasing.md](docs/releasing.md)
  for signing and publishing.

This file only covers what those cannot: getting from no checkout to a running app.

## 0. Prerequisites

| Tool | Version | Check |
| --- | --- | --- |
| macOS | 11+, Apple Silicon or Intel | `sw_vers -productVersion` |
| Node | 20.x — **not 22** | `node --version` |
| uv | any recent | `uv --version` |
| Xcode CLI tools | any | `xcode-select -p` |

Node 22 breaks the test runner: `npm test` ends in `node --test out/test`, and a bare
directory argument only works on Node 20. Install with `nvm install 20 && nvm use 20`.

Missing uv: `brew install uv`.

## 1. Clone

The repository is private, so an unauthenticated `git clone` returns 404 rather than a
permission error.

```bash
gh auth status || gh auth login          # needs repo scope
gh repo clone natejly/personal-os grain
cd grain
```

**Check:** `ls backend/personal_os/app.py` exists.

## 2. Install dependencies

```bash
npm install
cd backend && uv venv && uv pip install -e . && cd ..
```

`uv pip install -e .` does not install pytest — it is not a declared dependency. Add it
only if you intend to run the backend tests: `cd backend && uv pip install pytest`.

**Check:** `backend/.venv/bin/python -c "import personal_os; print('ok')"` prints `ok`.

## 3. Configure

```bash
cp .env.example .env
```

Grain needs one model provider to do anything. The lowest-friction option is to skip the
LiteLLM proxy and point straight at a provider's OpenAI-compatible endpoint. Edit `.env`:

```
PERSONAL_OS_BASE_URL=https://api.fireworks.ai/inference
PERSONAL_OS_API_KEY=fw_your_key_here
PERSONAL_OS_DEFAULT_MODEL=accounts/fireworks/models/kimi-k3
```

Any OpenAI-compatible endpoint works — OpenAI, Anthropic, OpenRouter, a local Ollama on
`http://localhost:11434/v1`, or a LiteLLM proxy. `backend/personal_os/providers.py` is the
list the setup wizard offers.

Google Workspace (Mail, Calendar, Tasks, Docs) needs your own OAuth client of type
**Desktop app** in `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET`. Leave them blank to skip —
those features stay dark and nothing else is affected. README.md § Google Workspace has
the Cloud Console steps.

**`.env` is read in development only.** A packaged build sets `PERSONAL_OS_PACKAGED=1`,
which skips `.env` loading entirely (`backend/personal_os/__main__.py`) and env-seeding of
settings (`backend/personal_os/app.py`); a packaged app is configured through its
first-run wizard instead.

## 4. Run it

Two ways. Pick by what you were asked for.

### Development — fast, hot reload

**Never run Electron in the foreground.** `dev.sh` does not return until the app quits, so
an agent that runs it directly hangs. Background it, log it, poll for readiness, and kill
what you started when you are done.

```bash
mkdir -p "$HOME/Library/Application Support/Grain-dev/data"
PERSONAL_OS_DATA_DIR="$HOME/Library/Application Support/Grain-dev/data" \
  ./scripts/dev.sh > /tmp/grain-dev.log 2>&1 &
until curl -sf http://127.0.0.1:8765/health >/dev/null; do sleep 1; done; echo ready
```

`dev.sh` creates the backend venv if missing, starts the backend with autoreload, starts
LiteLLM when `FIREWORKS_API_KEY` is set and the proxy port is free, then runs
`electron-vite dev`. A window opens a few seconds after `/health` answers.

**Always set `PERSONAL_OS_DATA_DIR`.** The default is the same SQLite folder the packaged
app uses, and two backends on one database means two schedulers — the dev process's
startup recovery marks the real app's live runs interrupted. Use the real data directory
only with the packaged app quit.

Dev logs go to `<userData>/logs`; `PERSONAL_OS_LOG_DIR` overrides it. Note that mail,
calendar and task writes from a dev window still hit the real Google account.

**Check:** `curl -s http://127.0.0.1:8765/health` returns `{"ok":true}`, and `/tmp/grain-dev.log`
shows no traceback.

### Packaged — the real app, ~5 minutes

```bash
npm run package                        # Apple Silicon; use package:x64 for Intel
open -g dist/mac-arm64/Grain.app       # -g launches without stealing focus
```

This runs `scripts/bundle-backend.sh` (downloads a pinned CPython 3.12, installs the
backend into it, strips it to ~315 MB), `electron-vite build`, then `electron-builder`.
Output is `dist/mac-arm64/Grain.app` plus a `.dmg` and `.zip` of about 800 MB each.

A packaged build ignores `.env` and uses the real data directory
`~/Library/Application Support/personal-os/data`. To keep it off that database:

```bash
GRAIN_USER_DATA=$(mktemp -d) open -n dist/mac-arm64/Grain.app
```

**Check:** the bundled backend answers on its own:

```bash
dist/mac-arm64/Grain.app/Contents/Resources/backend/python/bin/python3 \
  -m personal_os --port 8799 --data-dir "$(mktemp -d)"
curl -s http://127.0.0.1:8799/health      # {"ok":true}
```

Logs for a packaged run are at `~/Library/Logs/Grain/backend.log`.

## 5. Tests

```bash
npm run typecheck                    # tsc, both projects
npm test                             # renderer + main unit tests (Node 20)
cd backend && .venv/bin/python -m pytest        # needs pytest installed per step 2
```

End-to-end tests need Playwright and a build first: `tests/e2e/setup-worktree.sh`.

## Driving it without the UI

The backend is a plain HTTP API, which is usually a better way for an agent to exercise a
change than clicking. Every route except `/health` and the OAuth callbacks needs a token,
sent as `X-Personal-OS-Token: <token>` or `Authorization: Bearer <token>`. It is
`PERSONAL_OS_AUTH_TOKEN` when set, otherwise the contents of `<data dir>/.auth_token`.
Interactive docs are at `/api-docs` — not `/docs`, which is the user's notes.

`.claude/skills/grain/SKILL.md` has the route table and a worked example.

## Traps

- **Running Electron in the foreground** — `dev.sh`, `npm run dev` and the packaged app all
  block until the app quits. Background them.
- **Node 22** — `npm test` fails with MODULE_NOT_FOUND. Use Node 20.
- **Sharing the data directory** — a dev backend beside the packaged app corrupts the
  packaged app's in-flight runs. Set `PERSONAL_OS_DATA_DIR`.
- **Port 4000** — LiteLLM is shared. If it is already up, `dev.sh` reuses it rather than
  starting a second one.
- **An unsigned build will not open on someone else's Mac.** Without a Developer ID
  certificate the app is ad-hoc signed, so Gatekeeper rejects a downloaded copy with
  "damaged and can't be opened." See docs/releasing.md.
- **Google sign-in with a "Web application" OAuth client** fails with
  `redirect_uri_mismatch`. The backend uses a fresh loopback port each launch and only
  Desktop-app clients may vary the port.
- **`uv pip install -e .` gives you no pytest.** Install it separately.
