#!/usr/bin/env bash
# Build a self-contained backend for the packaged app: a pinned python-build-standalone CPython 3.12
# with ./backend (and its dependencies) installed non-editable. No system Python, uv or Homebrew is
# needed on the machine that runs Grain.app. Output: build/backend-bundle/ (gitignored), which
# electron-builder copies to Contents/Resources/backend.
#
#   scripts/bundle-backend.sh [arm64|x64]      (default: this machine's arch)
set -euo pipefail

PBS_TAG="20260929"
PBS_PY="3.12.14"
SHA_ARM64="de6b8f94fa765639b423ea353ab340669704c7186f96ee3cab389dcfde770c3c"
SHA_X64="f51ec8a7fa0ede129a5e2e942a2a453ba4830adaca2289da4449390e42443292"

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ARCH="${1:-$(uname -m)}"
case "$ARCH" in
  arm64|aarch64) TRIPLE="aarch64-apple-darwin"; SHA="$SHA_ARM64" ;;
  x64|x86_64)    TRIPLE="x86_64-apple-darwin";  SHA="$SHA_X64" ;;
  *) echo "unknown arch: $ARCH (use arm64 or x64)" >&2; exit 2 ;;
esac
command -v uv >/dev/null || { echo "uv is required to build the bundle (brew install uv)" >&2; exit 1; }

NAME="cpython-${PBS_PY}+${PBS_TAG}-${TRIPLE}-install_only.tar.gz"
URL="https://github.com/astral-sh/python-build-standalone/releases/download/${PBS_TAG}/${NAME//+/%2B}"
CACHE="$ROOT/build/.cache"
OUT="$ROOT/build/backend-bundle"
mkdir -p "$CACHE"

if [ ! -f "$CACHE/$NAME" ]; then
  echo "downloading $NAME"
  curl -fL --retry 3 -o "$CACHE/$NAME.part" "$URL"
  mv "$CACHE/$NAME.part" "$CACHE/$NAME"
fi
echo "$SHA  $CACHE/$NAME" | shasum -a 256 -c - || { rm -f "$CACHE/$NAME"; echo "checksum mismatch" >&2; exit 1; }

rm -rf "$OUT"
mkdir -p "$OUT"
tar -xzf "$CACHE/$NAME" -C "$OUT"   # -> $OUT/python
PY="$OUT/python/bin/python3"

# Non-editable install of the backend plus the macOS (mac), whistle and charts extras. The backend never imports
# LiteLLM; it only talks to a proxy over HTTP, so nothing LiteLLM-shaped is bundled.
uv pip install --python "$PY" --reinstall-package grain-backend "$ROOT/backend[mac,whistle,charts]"

# The standalone build's console scripts carry absolute shebangs into this build dir; nothing uses
# them (the app runs `python -m personal_os`), so drop them.
find "$OUT/python/bin" -type f ! -name 'python*' -delete
rm -f "$OUT/python/bin/python3-config" "$OUT/python/bin/python3.12-config"
find "$OUT/python" -type l ! -exec test -e {} \; -delete   # dangling links (idle3, pydoc3, ...)

# Shrink: caches, tests, docs, headers, unused stdlib GUI/test packages.
SP="$OUT/python/lib/python3.12"
find "$OUT" -name '__pycache__' -type d -prune -exec rm -rf {} +
find "$SP/site-packages" -type d \( -name tests -o -name test -o -name testing \) -prune -exec rm -rf {} + 2>/dev/null || true
rm -rf "$SP/test" "$SP/idlelib" "$SP/tkinter" "$SP/turtledemo" "$SP/lib2to3" "$SP/ensurepip" \
       "$OUT/python/include" "$OUT/python/share" "$SP/config-3.12-darwin"
rm -rf "$SP/site-packages/pip" "$SP"/site-packages/pip-*
# matplotlib ships sample data and test images; the backend only renders charts.
rm -rf "$SP/site-packages/matplotlib/mpl-data/sample_data" "$SP/site-packages/matplotlib/mpl-data/images"
find "$SP/site-packages" -name '*.pyi' -delete
# googleapiclient bundles ~500 discovery documents (110MB); google.py only builds these services.
DOCS="$SP/site-packages/googleapiclient/discovery_cache/documents"
find "$DOCS" -name '*.json' ! \( -name 'gmail.v1.json' -o -name 'calendar.v3.json' -o -name 'drive.v3.json' \
  -o -name 'tasks.v1.json' -o -name 'docs.v1.json' -o -name 'sheets.v4.json' -o -name 'people.v1.json' \) -delete
# pyobjc test suites (16MB) and babel's non-English locale data (pulled in transitively, unused).
rm -rf "$SP/site-packages/PyObjCTest"
find "$SP/site-packages/babel/locale-data" -name '*.dat' ! -name 'en*.dat' ! -name 'root.dat' -delete 2>/dev/null || true

# Smoke test from a neutral cwd so a stray source tree cannot mask a missing install.
SMOKE="$(mktemp -d)"
(cd / && PYTHONDONTWRITEBYTECODE=1 PERSONAL_OS_DATA_DIR="$SMOKE" "$PY" -c "import personal_os.app, personal_os.__main__; print('bundle ok:', personal_os.__file__)")
rm -rf "$SMOKE"
# Precompile now: the app runs with PYTHONDONTWRITEBYTECODE so nothing writes into the signed bundle.
# unchecked-hash: packaging resets every source file's mtime, which makes timestamp-checked .pyc files stale in the
# installed app. Then every launch recompiles in memory, and any child Python started without the flag rewrites
# them, which breaks the signature. Unchecked-hash .pyc files stay valid whatever the mtimes.
"$PY" -m compileall -q -f -j 0 --invalidation-mode unchecked-hash "$OUT/python/lib/python3.12" >/dev/null || true
du -sh "$OUT"
