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

`scripts/mac-signing.cjs` picks the identity, in this order:

- `CSC_NAME` (a Developer ID Application identity in your keychain) or `CSC_LINK` + `CSC_KEY_PASSWORD`
  (a .p12): electron-builder signs everything, including the Python extension modules, with
  `build/entitlements.mac.plist`.
- A self-signed **Grain Local Signing** code-signing identity in this Mac's keychain (checked with
  `security find-identity -v -p codesigning`): the build sets `CSC_NAME` to it and
  `CSC_IDENTITY_AUTO_DISCOVERY=false`, signs without a secure timestamp, and the ad-hoc hook is
  skipped. macOS then ties Grain's privacy grants (Full Disk Access, Automation, Screen Recording...)
  to that certificate instead of each build's cdhash, so they survive rebuilds. Re-grant once after
  the first build signed this way. Only this Mac trusts the certificate, so it does not replace
  Developer ID for builds you hand to other people. `GRAIN_ADHOC_SIGN=1` forces ad-hoc.
- Neither: electron-builder skips signing and `scripts/adhoc-sign.cjs` ad-hoc signs the
  bundled Python binaries and the app (hardened runtime, same entitlements). This is what CI and
  other Macs get. On another Mac, Gatekeeper needs right-click, Open the first time. Auto-update
  cannot install an ad-hoc build, and every rebuild drops privacy grants.

Creating the local identity (once per Mac; Keychain Access → Certificate Assistant → Create a
Certificate, Identity Type "Self Signed Root", Certificate Type "Code Signing", does the same):

```
d=$(mktemp -d) && cd "$d"
printf '[req]\ndistinguished_name=dn\nx509_extensions=ext\nprompt=no\n[dn]\nCN=Grain Local Signing\n[ext]\nbasicConstraints=critical,CA:false\nkeyUsage=critical,digitalSignature\nextendedKeyUsage=critical,codeSigning\n' > c.cnf
openssl req -x509 -newkey rsa:2048 -nodes -keyout k.pem -out c.pem -days 3650 -config c.cnf
pw=$(openssl rand -hex 24)
openssl pkcs12 -export -inkey k.pem -in c.pem -name "Grain Local Signing" -out g.p12 -passout "pass:$pw" \
  -keypbe PBE-SHA1-3DES -certpbe PBE-SHA1-3DES -macalg sha1
security import g.p12 -k ~/Library/Keychains/login.keychain-db -P "$pw" -T /usr/bin/codesign
security add-trusted-cert -p codeSign -k ~/Library/Keychains/login.keychain-db c.pem   # asks for your password
cd / && rm -rf "$d"
security find-identity -v -p codesigning | grep "Grain Local Signing"
```

If codesign ever asks for keychain access during a build, choose Always Allow.

Notarization:

- `APPLE_ID`, `APPLE_TEAM_ID`, `APPLE_APP_SPECIFIC_PASSWORD` all set: the app is also notarized.

Entitlements (`build/entitlements.mac.plist`): `allow-jit`, `allow-unsigned-executable-memory`,
`disable-library-validation` (Python loads wheel-signed `.so` files), `device.audio-input`, and
`automation.apple-events`. Info.plist usage strings are in `electron-builder.config.cjs`
(microphone, Apple events, screen capture, Desktop/Documents/Downloads). Calendar and Contacts are
not requested: Google Calendar is reached over the API, not EventKit.

## Installing a local build

Keep exactly one copy at `/Applications/Grain.app`, so Spotlight, the Dock and privacy grants all
point at the same app:

```
npm run install-app            # quit Grain first; copies the newest dist/mac*/Grain.app there
npm run install-app -- --open  # and launch /Applications/Grain.app
```

It verifies the signature, swaps the copy in, and unregisters the `dist/` copy from LaunchServices.
Launch by full path (`open /Applications/Grain.app`), not `open -a Grain`, which can pick a stale copy.

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

Launching the .app itself uses the real `~/Library/Application Support/personal-os` data directory by
default (the legacy-dir fallback in `src/main/index.ts` wins over `--user-data-dir`). To test safely,
set `GRAIN_USER_DATA` to a temp directory: `GRAIN_USER_DATA=$(mktemp -d) open -n dist/mac-arm64/Grain.app`
(or run the binary directly so the variable is inherited).
