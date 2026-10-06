"""macOS probes and permission requests: pyobjc, the frontmost app, and the Privacy & Security grants.

Every grant lands on the app bundle that started this process ("Grain" in a packaged build, "Electron"
in development) because the backend is a child of the Electron app. Three rules hold for everything here:

  probing never prompts  - the *_status() and permission_state() functions only read stored state
  asking is explicit     - request_permission() is the one function that can show a system dialog, and
                           it runs only from the button the user pressed
  nothing here is fatal  - a missing framework or a refused grant degrades to UNKNOWN or DENIED

macOS hands some grants to a process only at launch, so one made while the app is running can need a
restart before it takes.
"""
from __future__ import annotations

import contextlib
import logging
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

log = logging.getLogger("personal_os.macos")

IS_MAC = sys.platform == "darwin"

_pyobjc: dict[str, Any] = {}
_pyobjc_error = ""


def _load_pyobjc() -> dict[str, Any]:
    """Import the AppKit/Quartz bits once. Returns {} when pyobjc is not installed."""
    global _pyobjc_error
    if _pyobjc or _pyobjc_error:
        return _pyobjc
    if not IS_MAC:
        _pyobjc_error = "not macOS"
        return {}
    try:
        import AppKit  # type: ignore[import-not-found]
        import Quartz  # type: ignore[import-not-found]
        from ApplicationServices import (  # type: ignore[import-not-found]
            AXIsProcessTrusted,
            AXUIElementCopyAttributeValue,
            AXUIElementCreateApplication,
        )
    except Exception as e:  # noqa: BLE001 - optional dependency
        _pyobjc_error = str(e)
        log.info("pyobjc unavailable (%s)", e)
        return {}
    _pyobjc.update(
        AppKit=AppKit, Quartz=Quartz, AXIsProcessTrusted=AXIsProcessTrusted,
        AXCopy=AXUIElementCopyAttributeValue, AXCreate=AXUIElementCreateApplication,
    )
    return _pyobjc


_carbon: Any = None


def secure_input_active() -> bool:
    """True while a secure text field is focused anywhere (macOS sets this system-wide for
    password fields). Keystroke capture stops completely while it is set."""
    global _carbon
    if not IS_MAC:
        return False
    try:
        if _carbon is None:
            import ctypes

            _carbon = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/Carbon.framework/Carbon")
            _carbon.IsSecureEventInputEnabled.restype = ctypes.c_bool
        return bool(_carbon.IsSecureEventInputEnabled())
    except Exception:  # noqa: BLE001
        return False  # can't tell; the app denylist and redaction still apply


def accessibility_trusted() -> bool:
    fn = _load_pyobjc().get("AXIsProcessTrusted")
    try:
        return bool(fn()) if fn else False
    except Exception:  # noqa: BLE001
        return False


def frontmost_app() -> tuple[str, str, int]:
    """(name, bundle id, pid). Falls back to lsappinfo, which needs no permission at all."""
    ak = _load_pyobjc().get("AppKit")
    if ak:
        try:
            a = ak.NSWorkspace.sharedWorkspace().frontmostApplication()
            if a is not None:
                return (str(a.localizedName() or ""), str(a.bundleIdentifier() or ""), int(a.processIdentifier()))
        except Exception:  # noqa: BLE001
            pass
    if IS_MAC:
        try:
            asn = subprocess.run(["lsappinfo", "front"], capture_output=True, text=True, timeout=3).stdout.strip()
            if asn:
                out = subprocess.run(["lsappinfo", "info", "-only", "name", asn],
                                     capture_output=True, text=True, timeout=3).stdout
                m = re.search(r'"LSDisplayName"\s*=\s*"([^"]*)"', out)
                if m:
                    return (m.group(1), "", 0)
        except Exception:  # noqa: BLE001
            pass
    return ("", "", 0)


def focused_window_title(pid: int) -> str:
    """Title of the frontmost window. The accessibility API first, since it knows which window is
    actually focused; the window list second, for the apps that leave AXTitle empty."""
    p = _load_pyobjc()
    if not p or not pid:
        return ""
    try:
        el = p["AXCreate"](pid)
        err, win = p["AXCopy"](el, "AXFocusedWindow", None)
        if not err and win is not None:
            err, title = p["AXCopy"](win, "AXTitle", None)
            if not err and title:
                return str(title)
    except Exception:  # noqa: BLE001
        pass
    return window_list_title(pid)


def window_list_title(pid: int) -> str:
    """Frontmost on-screen window title for a pid, from the window server. Titles are the only
    field read and nothing is ever captured as an image - but macOS still gates the name behind
    Screen Recording, so this returns "" until that is granted."""
    q = _load_pyobjc().get("Quartz")
    if not q or not pid or screen_recording_status() != GRANTED:
        return ""
    try:
        opts = q.kCGWindowListOptionOnScreenOnly | q.kCGWindowListExcludeDesktopElements
        for w in q.CGWindowListCopyWindowInfo(opts, q.kCGNullWindowID) or []:
            if int(w.get("kCGWindowOwnerPID") or 0) != int(pid):
                continue
            if int(w.get("kCGWindowLayer") or 0) != 0:  # skip panels, menus and status items
                continue
            name = str(w.get("kCGWindowName") or "")
            if name:
                return name  # the list is front-to-back, so the first match is the front window
    except Exception:  # noqa: BLE001
        pass
    return ""


BROWSER_SCRIPTS = {
    "Safari": 'tell application "Safari" to return URL of front document',
    "Google Chrome": 'tell application "Google Chrome" to return URL of active tab of front window',
    "Brave Browser": 'tell application "Brave Browser" to return URL of active tab of front window',
    "Microsoft Edge": 'tell application "Microsoft Edge" to return URL of active tab of front window',
    "Arc": 'tell application "Arc" to return URL of active tab of front window',
    "Vivaldi": 'tell application "Vivaldi" to return URL of active tab of front window',
}


# Bundle ids for the same browsers: Automation permission is stored per (this app, that bundle id),
# so the panel needs the id to report whether the grant exists.
BROWSER_BUNDLES = {
    "Safari": "com.apple.Safari",
    "Google Chrome": "com.google.Chrome",
    "Brave Browser": "com.brave.Browser",
    "Microsoft Edge": "com.microsoft.edgemac",
    "Arc": "company.thebrowser.Browser",
    "Vivaldi": "com.vivaldi.Vivaldi",
}


def browser_url(app: str) -> str:
    """Active tab URL. Needs Automation permission for that browser; empty when refused."""
    script = BROWSER_SCRIPTS.get(app)
    if not script or not IS_MAC:
        return ""
    try:
        r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=4)
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:  # noqa: BLE001
        return ""



GRANTED, DENIED, UNASKED, UNKNOWN, NA = "granted", "denied", "unasked", "unknown", "n/a"

# Deep links straight into the right Privacy & Security pane, so the UI never says "go and find it".
_PANE = "x-apple.systempreferences:com.apple.preference.security?Privacy_"
SETTINGS_URLS = {
    "accessibility": _PANE + "Accessibility",
    "screen_recording": _PANE + "ScreenCapture",
    "automation": _PANE + "Automation",
    "microphone": _PANE + "Microphone",
    "full_disk": _PANE + "AllFiles",
    "speech_recognition": _PANE + "SpeechRecognition",
}

def screen_recording_status() -> str:
    """Preflight only - it reads the stored answer and never prompts. Both "denied" and "never
    asked" come back as False from the OS, so an unasked machine reports denied and the fix text
    covers either case."""
    q = _load_pyobjc().get("Quartz")
    fn = getattr(q, "CGPreflightScreenCaptureAccess", None) if q else None
    if not fn:
        return UNKNOWN
    try:
        return GRANTED if bool(fn()) else DENIED
    except Exception:  # noqa: BLE001
        return UNKNOWN


_AV_AUDIO = "soun"  # AVMediaTypeAudio


def microphone_status() -> str:
    """AVCaptureDevice's stored authorization, which distinguishes "never asked" from "refused"."""
    if not IS_MAC:
        return UNKNOWN
    try:
        import AVFoundation  # type: ignore[import-not-found]

        st = int(AVFoundation.AVCaptureDevice.authorizationStatusForMediaType_(_AV_AUDIO))
    except Exception:  # noqa: BLE001
        return UNKNOWN
    # 0 not determined, 1 restricted (MDM), 2 denied, 3 authorized
    return {0: UNASKED, 1: DENIED, 2: DENIED, 3: GRANTED}.get(st, UNKNOWN)


_AE_WILDCARD = 0x2A2A2A2A   # '****' typeWildCard - "may I talk to it at all", no specific event
_AE_BUNDLE_ID = 0x62756E64  # 'bund' typeApplicationBundleID


def automation_status(bundle_id: str) -> str:
    """Whether we may send Apple events to one app, asked with askUserIfNeeded=False so it reports
    the stored answer without putting a dialog on screen."""
    if not IS_MAC or not bundle_id:
        return UNKNOWN
    try:
        import ctypes

        cs = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/CoreServices.framework/CoreServices")

        class _AEDesc(ctypes.Structure):
            _fields_ = [("descriptorType", ctypes.c_uint32), ("dataHandle", ctypes.c_void_p)]

        cs.AEDeterminePermissionToAutomateTarget.restype = ctypes.c_int
        desc = _AEDesc()
        raw = bundle_id.encode()
        if int(cs.AECreateDesc(ctypes.c_uint32(_AE_BUNDLE_ID), raw, ctypes.c_long(len(raw)), ctypes.byref(desc))) != 0:
            return UNKNOWN
        try:
            err = int(cs.AEDeterminePermissionToAutomateTarget(
                ctypes.byref(desc), ctypes.c_uint32(_AE_WILDCARD), ctypes.c_uint32(_AE_WILDCARD), ctypes.c_bool(False),
            ))
        finally:
            with contextlib.suppress(Exception):
                cs.AEDisposeDesc(ctypes.byref(desc))
    except Exception:  # noqa: BLE001
        return UNKNOWN
    # 0 granted; -1744 the user would have to be asked; -1743 refused; -600 that app is not running
    return {0: GRANTED, -1744: UNASKED, -1743: DENIED, -600: UNASKED}.get(err, UNKNOWN)


def full_disk_access() -> bool:
    """Read one byte of a file only Full Disk Access opens. It is the one grant that cannot be
    requested programmatically at all."""
    for p in ("~/Library/Application Support/com.apple.TCC/TCC.db", "~/Library/Safari/Bookmarks.plist"):
        try:
            with open(Path(p).expanduser(), "rb") as fh:
                fh.read(1)
            return True
        except Exception:  # noqa: BLE001
            continue
    return False


def installed_browsers() -> list[str]:
    """The browsers from BROWSER_SCRIPTS that actually exist on this Mac, so the panel asks for
    Automation on those and stays quiet about the rest."""
    if not IS_MAC:
        return []
    out: list[str] = []
    ws = None
    ak = _load_pyobjc().get("AppKit")
    if ak:
        with contextlib.suppress(Exception):
            ws = ak.NSWorkspace.sharedWorkspace()
    for name, bundle in BROWSER_BUNDLES.items():
        found = False
        if ws is not None:
            with contextlib.suppress(Exception):
                found = ws.URLForApplicationWithBundleIdentifier_(bundle) is not None
        if not found:
            found = any(Path(d, f"{name}.app").exists() for d in ("/Applications", Path.home() / "Applications"))
        if found:
            out.append(name)
    return out


def request_permission(pid_: str, browser: str = "") -> dict[str, Any]:
    """Ask macOS for one permission. The only function in this module that can show a dialog, and
    it is reached only from the permissions panel's Grant button.

    Returns {id, state, prompted, note}. `prompted` is False when macOS will not ask - Screen
    Recording and Full Disk Access have to be switched on by hand - and then `note` says so.
    """
    out: dict[str, Any] = {"id": pid_, "prompted": False, "note": "", "state": UNKNOWN}
    if not IS_MAC:
        out["note"] = "macOS only."
        return out

    if pid_ == "accessibility":
        try:
            from ApplicationServices import (  # type: ignore[import-not-found]
                AXIsProcessTrustedWithOptions,
                kAXTrustedCheckOptionPrompt,
            )

            AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True})
            out["prompted"] = True
            out["note"] = ("macOS is showing the Accessibility request. Approving it adds the app to the list; "
                           "restart the app afterwards so the grant takes.")
        except Exception as e:  # noqa: BLE001
            out["note"] = f"Could not ask: {e}. Open the pane and add the app by hand."

    elif pid_ == "screen_recording":
        q = _load_pyobjc().get("Quartz")
        fn = getattr(q, "CGRequestScreenCaptureAccess", None) if q else None
        if not fn:
            out["note"] = "Not available in this build; use the Settings pane."
        else:
            try:
                fn()
                out["prompted"] = True
                out["note"] = ("macOS shows this request once per app, ever. If no dialog appeared, switch the app "
                               "on in the pane instead, then restart it.")
            except Exception as e:  # noqa: BLE001
                out["note"] = f"Could not ask: {e}"

    elif pid_ == "microphone":
        try:
            import AVFoundation  # type: ignore[import-not-found]

            answered = threading.Event()
            AVFoundation.AVCaptureDevice.requestAccessForMediaType_completionHandler_(_AV_AUDIO, lambda ok: answered.set())
            out["prompted"] = True
            out["note"] = "macOS is showing the Microphone request."
            _wait_for_dialog(answered)
        except Exception as e:  # noqa: BLE001
            out["note"] = f"Could not ask: {e}. Install pyobjc-framework-AVFoundation or use the pane."

    elif pid_ == "speech_recognition":
        try:
            from Speech import SFSpeechRecognizer  # type: ignore[import-not-found]

            answered = threading.Event()
            SFSpeechRecognizer.requestAuthorization_(lambda st: answered.set())
            out["prompted"] = True
            out["note"] = "macOS is showing the Speech Recognition request."
            _wait_for_dialog(answered)
        except Exception as e:  # noqa: BLE001
            out["note"] = f"Could not ask: {e}. Install pyobjc-framework-Speech or use the pane."

    elif pid_ == "automation":
        name = browser or ""
        if name not in BROWSER_SCRIPTS:
            out["note"] = f"Not a browser Grain can script: {name or '(none given)'}"
        else:
            # Running a script against the browser is what makes macOS ask, and it asks for
            # the pair (this app, that browser).
            url = browser_url(name)
            out["prompted"] = True
            out["state"] = automation_status(BROWSER_BUNDLES.get(name, ""))
            out["note"] = (f"Read a URL from {name}." if url else
                           f"Asked {name}. If no dialog appeared it is probably not running - open it and try again.")
            return out

    elif pid_ == "full_disk":
        out["note"] = ("Full Disk Access cannot be requested by a program. Open the pane, press +, and pick the app "
                       "(Grain, or Electron in a dev build).")

    else:
        out["note"] = f"Unknown permission: {pid_}"
        return out

    out["state"] = permission_state(pid_)
    return out


def _wait_for_dialog(answered: threading.Event, limit: float = 30.0) -> None:
    """Block until the system dialog is answered (or `limit` passes) so the state returned, and the
    caller's re-check, reflect the answer. Pumps this thread's run loop in case the completion
    handler is delivered there rather than on a background queue."""
    t0 = time.time()
    try:
        from Foundation import NSDate, NSDefaultRunLoopMode, NSRunLoop  # type: ignore[import-not-found]
    except Exception:  # noqa: BLE001
        answered.wait(limit)
        return
    while not answered.is_set() and time.time() - t0 < limit:
        NSRunLoop.currentRunLoop().runMode_beforeDate_(NSDefaultRunLoopMode, NSDate.dateWithTimeIntervalSinceNow_(0.1))


def permission_state(pid_: str) -> str:
    """Current stored state of one permission, without prompting."""
    if pid_ == "speech_recognition":
        from . import stt
        st = stt.speech_auth_status()
        return {0: UNASKED, 1: DENIED, 2: DENIED, 3: GRANTED}.get(st, UNKNOWN)
    if pid_ == "accessibility":
        return GRANTED if accessibility_trusted() else DENIED
    if pid_ == "screen_recording":
        return screen_recording_status()
    if pid_ == "microphone":
        return microphone_status()
    if pid_ == "full_disk":
        return GRANTED if full_disk_access() else DENIED
    if pid_ == "automation":
        states = [automation_status(BROWSER_BUNDLES.get(b, "")) for b in installed_browsers()]
        if not states:
            return NA
        if GRANTED in states:
            return GRANTED
        return DENIED if all(s == DENIED for s in states) else UNASKED
    return UNKNOWN


def open_settings(pid_: str) -> bool:
    """Open the Privacy & Security pane for one permission. Opening a pane grants nothing."""
    url = SETTINGS_URLS.get(pid_)
    if not url or not IS_MAC:
        return False
    try:
        subprocess.run(["open", url], capture_output=True, timeout=8)
        return True
    except Exception:  # noqa: BLE001
        return False




LABELS = {"accessibility": "Accessibility", "screen_recording": "Screen Recording", "microphone": "Microphone",
          "speech_recognition": "Speech Recognition", "automation": "Browser automation", "full_disk": "Full Disk Access"}


def permissions() -> list[dict[str, Any]]:
    """[{id, label, state}] for every permission Grain asks for. Reads stored state; never prompts."""
    return [{"id": k, "label": v, "state": permission_state(k)} for k, v in LABELS.items()]
