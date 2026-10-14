# AGENTS.md

Instructions for an agent asked to get Grain running from nothing. Follow the steps in
order; each one has a check you can run before moving on.

Grain is an Electron desktop app (React renderer, TypeScript main process) talking to a
Python FastAPI backend over localhost. macOS only.

Once the repository is cloned, these files cover more ground than this one and win where
they disagree:

- `CLAUDE.md` — repo rules, and they override everything else.
- `.claude/skills/grain/SKILL.md` — the map of the app itself: views, tools, data dirs,
  how to drive the backend over HTTP, how to run the test suites.
- [README.md](README.md) for what the app does and a tour of its features,
  [docs/releasing.md](docs/releasing.md) for signing and publishing.

This file only covers what those cannot: getting from no checkout to a running app.

## Ask first: build the app, or run it in dev?

**Before you build or start anything, ask the user which one they want, and wait for the
answer. Do not pick for them.** Steps 0 to 3 are the same either way, so you may do them
while you wait. Then follow only the matching path.

> Do you want to **(a) build the app**: a packaged `Grain.app` you install and use every
> day (takes about 5 minutes, then it opens like any Mac app), or **(b) run it in dev**:
> `./scripts/dev.sh` with hot reload, good for trying Grain out or changing its code (it
> needs a terminal process running, and a dev window, while you use it)?

- **(a) Build the app** is for using Grain. Go to step 4a.
- **(b) Run in dev** is for hacking on Grain or taking a quick look. Go to step 4b.

Two rules apply to both:

- **Never run Electron in the foreground.** `./scripts/dev.sh`, `npm run dev` and the
  packaged app all block until the app quits, so an agent that runs them directly hangs.
  Start them in the background with output to a log file, poll the backend `/health`
  endpoint and the log until it is up, and kill what you started when you are done.
- **If the packaged app is already in use** (or might be), never let dev share its data.
  The default data directory is the same SQLite folder the packaged app uses, and two
  backends on one database means two schedulers; the dev process's startup recovery marks
  the real app's live runs interrupted. Give dev its own:
  `PERSONAL_OS_DATA_DIR="$HOME/Library/Application Support/Grain-dev/data"`.

## 0. Prerequisites

| Tool | Version | Check |
| --- | --- | --- |
| macOS | 11+, Apple Silicon or Intel | `sw_vers -productVersion` |
| Node | 20.x | `node --version` |
| uv | any recent | `uv --version` |
| Xcode CLI tools | any (`npm install` compiles `node-pty`) | `xcode-select -p` |

Install Node with `nvm install 20 && nvm use 20`. Missing uv: `brew install uv`.

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
(`dev.sh` creates the backend venv itself if it is missing; the packaged build uses its own
bundled Python and does not need this venv at all, only `uv` and `npm install`.)

**Check:** `backend/.venv/bin/python -c "import personal_os; print('ok')"` prints `ok`.

## 3. Model key

Grain needs one model provider to do anything.

- **Dev (path b):** add it in the app.

  ```bash
  cp .env.example .env   # optional: Google client, Fireworks key for the LiteLLM proxy
  ```

  Models are not configured in `.env`. Start the app and add a provider in
  Settings → Models (or the first-run wizard): pick a preset or paste any OpenAI-compatible
  address, add its key, and Grain verifies it before saving. `backend/personal_os/providers.py`
  is the preset list.
- **Packaged (path a):** nothing to edit. **`.env` is read in development only.** A packaged
  build sets `PERSONAL_OS_PACKAGED=1`, which skips `.env` loading entirely
  (`backend/personal_os/__main__.py`). The first-run wizard asks for the provider and key; have
  the key ready.

Google Workspace (Mail, Calendar, Tasks, Docs) needs your own OAuth client of type
**Desktop app** in `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` (in `.env` for dev; the
packaged app walks you through pasting one in Settings → Integrations). Leave them blank to
skip — those features stay dark and nothing else is affected. README.md § Google Workspace
has the Cloud Console steps.

## 4a. Build the app (packaged `Grain.app`)

```bash
npm run package        # Apple Silicon; use `npm run package:x64` for Intel
```

About 5 minutes. It runs `scripts/bundle-backend.sh` (downloads a pinned CPython 3.12,
installs the backend into it, strips it to ~315 MB), `electron-vite build`, then
`electron-builder`. The app lands at **`dist/mac-arm64/Grain.app`** (`dist/mac/Grain.app`
for an Intel build), next to a `.dmg` and `.zip` of about 800 MB each.

**Check:** `ls dist/mac-arm64/Grain.app` exists, and the bundled backend answers on its own
(background it; it serves until killed):

```bash
dist/mac-arm64/Grain.app/Contents/Resources/backend/python/bin/python3 \
  -m personal_os --port 8799 --data-dir "$(mktemp -d)" > /tmp/grain-check.log 2>&1 &
until curl -sf http://127.0.0.1:8799/health >/dev/null; do sleep 1; done; echo ready   # {"ok":true}
kill %1
```

**Install and open it.** Copy it to `/Applications` and launch that copy. Finder hides
builds under dotted folders and Spotlight never indexes `dist/`, so the `/Applications`
copy is the one that behaves like a normal app:

```bash
osascript -e 'quit app "Grain"' 2>/dev/null   # quit a running copy first
npm run install-app -- --open                 # copies to /Applications/Grain.app and opens it
# by hand instead:
#   rm -rf /Applications/Grain.app && cp -R dist/mac-arm64/Grain.app /Applications/ && open /Applications/Grain.app
```

To just launch the build in place without taking focus: `open -g dist/mac-arm64/Grain.app`.

On first launch the setup wizard picks a model provider and key. A packaged app uses the
data directory `~/Library/Application Support/personal-os/data` and ignores `.env`. To try
a build without touching that: `GRAIN_USER_DATA=$(mktemp -d) open -n dist/mac-arm64/Grain.app`.
Logs for a packaged run are at `~/Library/Logs/Grain/backend.log`.

An unsigned build runs on the Mac that built it. See the traps below for other Macs.

## 4b. Run in dev (hot reload)

`dev.sh` creates the backend venv if missing, starts the backend with autoreload, starts
LiteLLM when `FIREWORKS_API_KEY` is set and the proxy port is free, then runs
`electron-vite dev`. Background it, log it, poll for readiness:

```bash
mkdir -p "$HOME/Library/Application Support/Grain-dev/data"
PERSONAL_OS_DATA_DIR="$HOME/Library/Application Support/Grain-dev/data" \
  ./scripts/dev.sh > /tmp/grain-dev.log 2>&1 &
until curl -sf http://127.0.0.1:8765/health >/dev/null; do sleep 1; done; echo ready
```

A window opens a few seconds after `/health` answers. **Always set `PERSONAL_OS_DATA_DIR`**
when the packaged app is, or may be, in use; use the real data directory from dev only with
the packaged app quit. Sign in to Google again in the dev window (that database has its
own tokens). Mail, calendar and task writes from a dev window still hit the real Google
account.

Dev logs go to `<userData>/logs`; `PERSONAL_OS_LOG_DIR` overrides it.

**Check:** `curl -s http://127.0.0.1:8765/health` returns `{"ok":true}`, and
`/tmp/grain-dev.log` shows no traceback. Stop it with `kill %1` (or the job's pid) when done.

## 5. Tests (optional)

```bash
npm run typecheck                    # tsc, both projects
npm test                             # renderer + main unit tests
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

## Once it is running: let Grain teach the user Grain

The app ships a built-in skill named `grain-guide` that the in-app assistant reads to
answer questions about itself. Tell the user to open a chat (⌘N) and ask it "teach me
Grain" or "how do I ...". It knows the views, settings, shortcuts, slash commands and
approvals. For a written tour of the features, point them to [README.md](README.md).

## Traps

- **Running Electron in the foreground** — `dev.sh`, `npm run dev` and the packaged app all
  block until the app quits. Background them.
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
