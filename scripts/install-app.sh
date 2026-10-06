#!/usr/bin/env bash
# Installs a packaged build as the one copy of Grain at /Applications/Grain.app.
#
#   npm run install-app                 # newest dist/mac*/Grain.app
#   npm run install-app -- path/to/Grain.app
#   npm run install-app -- --open       # also launch it (by full path) afterwards
#
# Copies to a temp name, swaps it in, then unregisters the dist copy from LaunchServices so Spotlight,
# the Dock and `open -a Grain` keep resolving to /Applications. Refuses while that copy is running
# (replacing a running app's files breaks it); quit Grain first.
set -euo pipefail

DEST=/Applications/Grain.app
LSREGISTER=/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister
cd "$(dirname "$0")/.."

OPEN=0
SRC=""
for arg in "$@"; do
  case "$arg" in
    --open) OPEN=1 ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) SRC="$arg" ;;
  esac
done

if [[ -z "$SRC" ]]; then
  SRC=$(ls -dt dist/mac*/Grain.app 2>/dev/null | head -1 || true)
fi
if [[ -z "$SRC" || ! -d "$SRC" ]]; then
  echo "install-app: no built Grain.app found (run npm run package first, or pass a path)" >&2
  exit 1
fi
SRC=$(cd "$SRC" && pwd)

if pgrep -f "^$DEST/Contents/MacOS/" >/dev/null; then
  echo "install-app: $DEST is running; quit Grain and run this again" >&2
  exit 1
fi

codesign --verify --deep --strict "$SRC"
AUTH=$(codesign -dv --verbose=4 "$SRC" 2>&1 | awk -F= '/^Authority=/{print $2; exit}')
echo "install-app: $SRC (signed by: ${AUTH:-ad-hoc})"

TMP="$DEST.installing-$$"
rm -rf "$TMP"
ditto "$SRC" "$TMP"
rm -rf "$DEST"
mv "$TMP" "$DEST"

"$LSREGISTER" -u "$SRC" >/dev/null 2>&1 || true
"$LSREGISTER" -f "$DEST" >/dev/null 2>&1 || true
echo "install-app: installed $DEST"

if [[ "$OPEN" == 1 ]]; then
  open "$DEST"
fi
