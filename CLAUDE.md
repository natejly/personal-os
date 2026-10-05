# Grain

## Naming other products

Never mention other apps, products, companies or open-source projects that a feature is based on or compares to. Describe the mechanism itself instead ("deny rules win over allow rules", not "like <product>'s permission rules").

This applies to code, comments, identifiers, UI copy, settings labels, README and feature docs under `docs/`, test names, commit messages and PR descriptions. The only exception is `docs/research/`, whose comparative write-ups exist to name their sources.

Names of things Grain actually integrates with or depends on (Google Calendar, Gmail, LiteLLM, Fireworks, MCP, Docker, whisper.cpp, and so on) are fine.

## Dev while the app is in use

The packaged app is the daily driver. `./scripts/dev.sh` may run beside it (it skips the single-instance lock), but its default data directory is the same SQLite folder the packaged app uses: `~/Library/Application Support/personal-os/data`. Two backends on that file means two schedulers, and the dev process's startup recovery marks the real app's live runs interrupted.

Point dev at its own directory:

```bash
mkdir -p "$HOME/Library/Application Support/Grain-dev/data"
PERSONAL_OS_DATA_DIR="$HOME/Library/Application Support/Grain-dev/data" ./scripts/dev.sh
```

LiteLLM on port 4000 is shared; if it is already up, dev reuses it. Sign in to Google again in the dev window — that database has its own tokens.

Copy `personal-os/data` into `Grain-dev` once for a realistic snapshot. Treat the copy as a snapshot: mail, calendar, and task writes from dev still go to the real Google account.

Use the real data directory from dev only with the packaged app quit, and only to dogfood a change on the actual database. A backend reload drops in-flight runs.

## Launching Electron to test

Never run `./scripts/dev.sh`, `npm run dev`, or the packaged app as a foreground command: it blocks the session until the app quits. Start it as a background process with its output going to a log file, then poll the backend `/health` endpoint and the log to confirm it is up.

```bash
PERSONAL_OS_DATA_DIR="$HOME/Library/Application Support/Grain-dev/data" ./scripts/dev.sh > "$CLAUDE_JOB_DIR/tmp/dev.log" 2>&1 &
open -g dist/mac-arm64/Grain.app   # packaged build: -g keeps it from stealing focus
```

Kill what you started when the check is done, so a stray backend does not stay attached to the data directory.
