# Releasing Grain

A packaged Grain.app is self-contained: Electron plus a pinned python-build-standalone CPython 3.12
with the backend and its dependencies installed in it. The machine that runs it needs no Python, uv,
Homebrew or LiteLLM.

## Build

```
npm run package          # this Mac's arch (arm64 on Apple Silicon)
npm run package:x64      # Intel build
```

`package` runs, in order:

1. `scripts/bundle-backend.sh [arm64|x64]` downloads the pinned CPython (tag and sha256 are constants
   at the top of the script; the download is checksum-verified and cached in `build/.cache`), runs
   `uv pip install ./backend[activity]` into it (non-editable, with the macOS pyobjc extras), strips
   tests, caches, unused stdlib and Google discovery documents, precompiles bytecode, and smoke-imports
   the result from a neutral directory. Output: `build/backend-bundle/` (gitignored, ~315 MB).
   Needs `uv` on the build machine only.
2. `electron-vite build`
3. `electron-builder --mac --config electron-builder.config.cjs`, which copies the bundle to
   `Contents/Resources/backend` and emits `dist/Grain-<ver>-<arch>.dmg` and `-mac.zip` plus
   `latest-mac.yml`.

In a packaged app `src/main/backend.ts` runs
`Resources/backend/python/bin/python3 -m personal_os --port N --data-dir <userData>/data`. Dev mode
(`npm run dev`) still uses `backend/.venv`.

To bump Python, change `PBS_TAG`, `PBS_PY` and the two checksums in `bundle-backend.sh`
(checksums come from the release's `SHA256SUMS`).

## Signing

- No signing variables: electron-builder skips signing and `scripts/adhoc-sign.cjs` ad-hoc signs the
  bundled Python binaries and the app (hardened runtime, same entitlements). This runs locally; on
  another Mac, Gatekeeper needs right-click, Open the first time. Auto-update cannot install an
  ad-hoc build.
- `CSC_NAME` (a Developer ID Application identity in your keychain) or `CSC_LINK` + `CSC_KEY_PASSWORD`
  (a .p12): electron-builder signs everything, including the Python extension modules, with
  `build/entitlements.mac.plist`.
- `APPLE_ID`, `APPLE_TEAM_ID`, `APPLE_APP_SPECIFIC_PASSWORD` all set: the app is also notarized.

Entitlements (`build/entitlements.mac.plist`): `allow-jit`, `allow-unsigned-executable-memory`,
`disable-library-validation` (Python loads wheel-signed `.so` files), `device.audio-input`, and
`automation.apple-events`. Info.plist usage strings are in `electron-builder.config.cjs`
(microphone, Apple events, screen capture, Desktop/Documents/Downloads). Calendar and Contacts are
not requested: Google Calendar is reached over the API, not EventKit.

## Publish

```
export CSC_NAME="Developer ID Application: <Name> (TEAMID)"
export APPLE_ID=... APPLE_TEAM_ID=... APPLE_APP_SPECIFIC_PASSWORD=...
export GH_TOKEN=...                       # repo scope on natejly/personal-os
npm version <x.y.z> --no-git-tag-version
npm run package -- --publish always       # uploads dmg, zip, blockmaps, latest-mac.yml as a GitHub Release
```

Publishing as a draft first (`--publish always` with `releaseType: draft` via
`-c.publish.releaseType=draft`) lets you test before users see it.

## Auto-update

`src/main/updater.ts` (packaged builds only; `GRAIN_DISABLE_UPDATES=1` turns it off) checks GitHub
Releases at startup and every 6 hours, downloads in the background, and shows an "Update ready"
dialog with Restart / Later. Later still installs on the next quit. Updates need a Developer
ID signed build (macOS refuses to swap in an ad-hoc one).

## Verifying a build

```
codesign --verify --deep --strict dist/mac-arm64/Grain.app
dist/mac-arm64/Grain.app/Contents/Resources/backend/python/bin/python3 -m personal_os \
  --port 8797 --data-dir "$(mktemp -d)"      # then: curl http://127.0.0.1:8797/health
```

Launching the .app itself uses the real `~/Library/Application Support/personal-os` data directory (the
legacy-dir fallback in `src/main/index.ts` wins over `--user-data-dir`), so test it on a machine or
account whose data you do not mind touching.
