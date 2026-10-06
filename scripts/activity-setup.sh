#!/usr/bin/env bash
# Installs everything the activity monitor can use, and reports what only you can grant.
#
# Three kinds of thing stand between the monitor and a signal:
#   packages    pyobjc for window titles, the keystroke tap, native audio, Speech
#   binaries    ffmpeg / BlackHole only as fallbacks on older Macs
#   permissions Accessibility, Input Monitoring, Screen Recording,
#               Automation, Microphone, Speech Recognition
set -euo pipefail
cd "$(dirname "$0")/.."

say() { printf '\n» %s\n' "$1"; }

if [ "$(uname -s)" != "Darwin" ]; then
  echo "The activity monitor is macOS-only; nothing to install here."
  exit 0
fi

say "Native bridge (pyobjc)"
if [ ! -x backend/.venv/bin/python ]; then
  echo "No backend venv yet - run scripts/dev.sh once first."
  exit 1
fi
if command -v uv >/dev/null; then
  (cd backend && uv pip install -e '.[activity]')
else
  backend/.venv/bin/pip install -e 'backend[activity]'
fi

say "ffmpeg (optional fallback)"
if command -v ffmpeg >/dev/null; then
  echo "already installed: $(command -v ffmpeg)"
elif command -v brew >/dev/null; then
  echo "Not on PATH. Native capture does not need it; install only as a fallback: brew install ffmpeg"
else
  echo "Homebrew not found - native capture does not need ffmpeg."
fi

say "Loopback driver (only if this Mac is older than 14.2)"
# macOS 14.2+ records system output with a Core Audio process tap. BlackHole is the fallback.
if backend/.venv/bin/python -c "
import sys; sys.path.insert(0, 'backend')
from personal_os import native_audio
from personal_os.activity import audio_devices, looks_like_loopback
sys.exit(0 if native_audio.system_available() or any(looks_like_loopback(d['name']) for d in audio_devices()) else 1)
" 2>/dev/null; then
  echo "system audio capture is available (process tap or a loopback device)"
elif command -v brew >/dev/null; then
  echo "No process tap and no loopback device. Installing BlackHole asks for your password."
  brew install --cask blackhole-2ch || echo "Skipped. Run it yourself later: brew install --cask blackhole-2ch"
else
  echo "No process tap. Install BlackHole or Loopback by hand for the system-audio signal on this Mac."
fi

say "What this machine can do now"
backend/.venv/bin/python -c "
import sys; sys.path.insert(0, 'backend')
from personal_os.activity import capabilities
for c in capabilities({'audio': {'model': 'whisper-1'}}):
    mark = 'ok  ' if c['ok'] else ('--  ' if c['optional'] else 'XX  ')
    state = f\" [{c['state']}]\" if c['state'] else ''
    print(f\"  {mark}{c['label']}{state}\")
    if not c['ok'] and c['fix']:
        print(f\"        {c['fix']}\")
"

cat <<'NOTE'

The permissions are yours to grant, and macOS attributes them to the app bundle that runs the
backend - "Personal OS" in a packaged build, "Electron" in development. Easiest path: open the
Activity panel and press Grant on each row; it asks macOS directly and deep-links the pane when
macOS refuses to ask twice. Restart the app after granting, or the keystroke tap stays dead.

The states printed above are for THIS shell's process, not for the app - the app's own panel is
the honest reading.
NOTE
