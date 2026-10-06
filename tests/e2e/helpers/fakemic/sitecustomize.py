"""Test seam for the e2e harness: a microphone that is an in-process sine tone, and macOS permission
requests that are logged instead of shown.

Loaded through sitecustomize (PYTHONPATH), so product code is untouched. The recorder takes its normal
path (channels, segments, queue, transcription); only the audio source is swapped.
"""
import os
import sys

try:
    from personal_os import audiocap, native_audio

    native_audio.mic_available = lambda: True
    native_audio.system_available = lambda: False
    audiocap.native_mic_input = lambda uid="": audiocap.native_sine_input()
except Exception as e:  # noqa: BLE001
    print(f"fakemic: not installed: {e}", file=sys.stderr)

# A permission prompt or a System Settings window must never open from a test run: stub both and log the call
# to <data dir>/perm-calls.log, which the specs read.
try:
    from personal_os import activity

    def _log(line: str) -> None:
        with open(os.path.join(os.environ.get("PERSONAL_OS_DATA_DIR", "."), "perm-calls.log"), "a") as f:
            f.write(line + "\n")

    def _request(pid_, browser=""):
        _log(f"request {pid_} {browser}".strip())
        by_hand = pid_ == "screen_recording"
        return {"id": pid_, "prompted": not by_hand, "state": "denied",
                "note": f"stubbed request for {pid_}" + (": switch it on in the pane by hand" if by_hand else "")}

    def _open(pid_):
        _log(f"open {pid_}")
        return True

    activity.request_permission = _request
    activity.open_settings = _open
except Exception as e:  # noqa: BLE001
    print(f"fakemic: permission stubs not installed: {e}", file=sys.stderr)
