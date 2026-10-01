#!/usr/bin/env bash
# Installs everything the activity monitor can use, and reports what only you can grant.
#
# Three kinds of thing stand between the monitor and a signal:
#   packages    pyobjc for window titles and the keystroke tap        - installed here
#   binaries    ffmpeg for audio, a loopback driver for system audio  - installed here (sudo for the driver)
#   permissions Accessibility, Input Monitoring, Screen Recording,
#               Automation, Microphone                                - only you can grant these, in
#                                                                      System Settings or from the
#                                                                      Grant buttons in the Activity panel
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

say "ffmpeg (both audio signals)"
if command -v ffmpeg >/dev/null; then
  echo "already installed: $(command -v ffmpeg)"
elif command -v brew >/dev/null; then
  brew install ffmpeg
else
  echo "Homebrew not found - install ffmpeg yourself if you want the audio signals."
fi

say "Loopback driver (system audio only)"
# macOS will not record its own output. A loopback driver carries it back in as an input device.
# This is a .pkg, so the installer asks for your password - it cannot be done unattended.
if backend/.venv/bin/python -c "
import sys; sys.path.insert(0, 'backend')
from personal_os.activity import audio_devices, looks_like_loopback
sys.exit(0 if any(looks_like_loopback(d['name']) for d in audio_devices()) else 1)
" 2>/dev/null; then
  echo "a loopback device is already visible to ffmpeg"
elif command -v brew >/dev/null; then
  echo "Installing BlackHole - macOS will ask for your password."
  brew install --cask blackhole-2ch || echo "Skipped. Run it yourself later: brew install --cask blackhole-2ch"
else
  echo "Homebrew not found - install BlackHole or Loopback by hand for the system-audio signal."
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
