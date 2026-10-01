"""Activity monitor: watch what the user actually does, summarize it, feed it back as context.

Everything here is off until the user turns it on, and each signal (apps, input, typed text,
microphone, system audio) is a separate opt-in. Nothing leaves the machine: raw samples live in
the app's own SQLite file under a short retention window, rolled-up summaries are written to
<data_dir>/context/activity.md, and the only network call is to the LLM endpoint already
configured for chat.

Three layers, in order:
  collectors - platform probes that produce raw observations (threads, blocking APIs)
  the gate   - excluded apps, secure input, redaction; runs before ANY write
  rollup     - an LLM pass every few minutes that turns observations into prose + the md file
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from . import stt
from .audiocap import IS_MAC, LOOPBACK_HINTS, audio_devices, ffmpeg_path, looks_like_loopback  # noqa: F401
from .db import Database, new_id, now, row_to_dict
from .redact import REDACTIONS, SECRET_ASSIGN  # noqa: F401

log = logging.getLogger("personal_os.activity")

# Signals the user can turn on one at a time, most benign first (the UI renders them in this order).
SIGNALS = ("apps", "browserUrls", "input", "text", "micAudio", "outputAudio")

DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": False,
    "signals": {
        "apps": True,          # frontmost app + window title
        "browserUrls": False,  # URL of the active tab in the frontmost browser
        "input": True,         # keystroke / click / scroll COUNTS and rhythm, never content
        "text": False,         # the characters actually typed (redacted; needs Input Monitoring)
        "micAudio": False,     # microphone, transcribed then discarded
        "outputAudio": False,  # what the speakers played, transcribed then discarded
    },
    "sampleSeconds": 5,
    "idleSeconds": 120,
    "rollupMinutes": 15,
    "retentionHours": 48,
    "summaryRetentionDays": 90,
    "contextDays": 3,
    "injectContext": True,
    "redact": True,
    "excludeApps": [
        "1Password", "Bitwarden", "Dashlane", "Enpass", "KeePassXC", "Keychain Access",
        "LastPass", "NordPass", "Passwords", "Proton Pass", "Authy", "Secretive", "Tor Browser",
    ],
    "excludeTitlePatterns": [
        "password", "passphrase", "sign in", "signin", "log in", "login", "2fa",
        "one-time code", "verification code", "authenticator", "seed phrase",
        "private key", "secret key", "api key", "incognito", "private browsing",
        "bank", "wire transfer", "routing number", "ssn", "social security",
    ],
    "audio": {
        "micDevice": "",
        "outputDevice": "",
        "chunkSeconds": 30,
        "model": "whisper-1",
        "minChars": 12,
    },
    "summaryModel": "",
    "profileEveryHours": 6,
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS activity_events (
  id TEXT PRIMARY KEY,
  ts REAL NOT NULL,
  kind TEXT NOT NULL,              -- focus | input | idle | audio | note
  app TEXT NOT NULL DEFAULT '',
  bundle TEXT NOT NULL DEFAULT '',
  title TEXT NOT NULL DEFAULT '',
  url TEXT NOT NULL DEFAULT '',
  text TEXT NOT NULL DEFAULT '',   -- redacted typed text or transcript; empty for count-only events
  meta TEXT NOT NULL DEFAULT '{}',
  duration_ms INTEGER NOT NULL DEFAULT 0,
  rolled_up INTEGER NOT NULL DEFAULT 0,
  expires_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_act_ts ON activity_events(ts DESC);
CREATE INDEX IF NOT EXISTS idx_act_pending ON activity_events(rolled_up, ts);
CREATE INDEX IF NOT EXISTS idx_act_expiry ON activity_events(expires_at);

CREATE TABLE IF NOT EXISTS activity_summaries (
  id TEXT PRIMARY KEY,
  day TEXT NOT NULL,               -- local YYYY-MM-DD
  period_start REAL NOT NULL,
  period_end REAL NOT NULL,
  headline TEXT NOT NULL DEFAULT '',
  body TEXT NOT NULL DEFAULT '',
  apps TEXT NOT NULL DEFAULT '[]',
  event_count INTEGER NOT NULL DEFAULT 0,
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_actsum_day ON activity_summaries(day, period_start);

CREATE TABLE IF NOT EXISTS activity_profile (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  content TEXT NOT NULL DEFAULT '',
  updated_at REAL NOT NULL DEFAULT 0
);
"""


# ---------------------------------------------------------------- the gate

# The named rules, and the "password is ..." sweep, live in redact.py: meetings need the
# credential subset without the identity rules, and one copy means one place to fix a pattern.
# Re-exported above, so activity.REDACTIONS and activity.SECRET_ASSIGN still resolve.


class Gate:
    """Decides what may be recorded. Consulted by every collector before it writes."""

    def __init__(self, config_fn: Callable[[], dict[str, Any]]):
        self._config = config_fn

    def cfg(self) -> dict[str, Any]:
        return self._config()

    def excluded(self, app: str, title: str = "", url: str = "") -> bool:
        """True when this window must not be recorded at all - not even its name."""
        c = self.cfg()
        app_l = (app or "").lower()
        for bad in c.get("excludeApps") or []:
            if bad and bad.lower() in app_l:
                return True
        hay = f"{title} {url}".lower()
        for pat in c.get("excludeTitlePatterns") or []:
            if pat and pat.lower() in hay:
                return True
        return False

    def scrub(self, text: str) -> str:
        if not text:
            return ""
        if not self.cfg().get("redact", True):
            return text
        out = text
        for pat, repl in REDACTIONS:
            out = pat.sub(repl, out)
        # Whatever follows a word like "password" is almost certainly the value itself.
        return SECRET_ASSIGN.sub("[secret]", out)


# ---------------------------------------------------------------- macOS probes
#
# Each probe degrades instead of raising: a missing framework, or a permission the user has not
# granted, makes the matching signal report unavailable - it never breaks the app.

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
        log.info("activity: pyobjc unavailable (%s)", e)
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


def idle_seconds() -> float:
    """Seconds since the last input event of any kind. Needs no permission."""
    q = _load_pyobjc().get("Quartz")
    if not q:
        return 0.0
    try:
        return float(q.CGEventSourceSecondsSinceLastEventType(q.kCGEventSourceStateCombinedSessionState, 0xFFFFFFFF))
    except Exception:  # noqa: BLE001
        return 0.0


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
    """Title of the frontmost window via the accessibility API. Empty without Accessibility."""
    p = _load_pyobjc()
    if not p or not pid:
        return ""
    try:
        el = p["AXCreate"](pid)
        err, win = p["AXCopy"](el, "AXFocusedWindow", None)
        if err or win is None:
            return ""
        err, title = p["AXCopy"](win, "AXTitle", None)
        return "" if err or title is None else str(title)
    except Exception:  # noqa: BLE001
        return ""


BROWSER_SCRIPTS = {
    "Safari": 'tell application "Safari" to return URL of front document',
    "Google Chrome": 'tell application "Google Chrome" to return URL of active tab of front window',
    "Brave Browser": 'tell application "Brave Browser" to return URL of active tab of front window',
    "Microsoft Edge": 'tell application "Microsoft Edge" to return URL of active tab of front window',
    "Arc": 'tell application "Arc" to return URL of active tab of front window',
    "Vivaldi": 'tell application "Vivaldi" to return URL of active tab of front window',
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


# ffmpeg_path, audio_devices, LOOPBACK_HINTS and looks_like_loopback moved to audiocap.py so the
# meetings recorder shares one copy of the device layer; they are re-exported above under the same
# names. audio_devices is TTL-cached there, which also kills the two 15-second ffmpeg probes that
# one status() poll used to spawn - capabilities(cfg) calls it, then status() calls it again.


def capabilities(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """What this machine can actually do right now, and how to fix what it can't.

    Rendered as a checklist in the UI. Read-only: probing never asks for a permission.
    """
    devices = audio_devices()
    loopbacks = [d for d in devices if looks_like_loopback(d["name"])]
    pyobjc_ok = bool(_load_pyobjc())
    out = [
        {
            "id": "platform", "label": "Supported platform", "ok": IS_MAC,
            "detail": f"Running on {sys.platform}.",
            "fix": "" if IS_MAC else "The collectors are macOS-only; the rest of the app is unaffected.",
        },
        {
            "id": "pyobjc", "label": "Native bridge (pyobjc)", "ok": pyobjc_ok,
            "detail": "App names, window titles, idle time and keystroke taps come through pyobjc."
                      if pyobjc_ok else f"pyobjc not importable: {_pyobjc_error or 'not installed'}.",
            "fix": "" if pyobjc_ok else "pip install pyobjc-framework-Cocoa pyobjc-framework-Quartz "
                                        "pyobjc-framework-ApplicationServices in the backend venv. "
                                        "Without it, app tracking falls back to lsappinfo (name only).",
        },
        {
            "id": "accessibility", "label": "Accessibility permission", "ok": accessibility_trusted(),
            "detail": "Needed for window titles and for the keystroke/click tap.",
            "fix": "System Settings -> Privacy & Security -> Accessibility, and enable Personal OS "
                   "(in dev builds: Electron). Restart the app afterwards.",
        },
        {
            "id": "ffmpeg", "label": "ffmpeg", "ok": bool(ffmpeg_path()),
            "detail": f"Found at {ffmpeg_path()}." if ffmpeg_path() else "Not on PATH.",
            "fix": "" if ffmpeg_path() else "brew install ffmpeg - needed for both audio signals.",
        },
        {
            "id": "mic", "label": "Microphone input", "ok": bool(devices),
            "detail": f"{len(devices)} audio input(s) visible to ffmpeg." if devices else "No audio inputs found.",
            "fix": "" if devices else "Grant Microphone permission to the app, then reopen this panel.",
        },
        {
            "id": "loopback", "label": "System audio capture", "ok": bool(loopbacks),
            "detail": (f"Loopback device available: {loopbacks[0]['name']}." if loopbacks
                       else "macOS cannot record its own output without a loopback driver."),
            "fix": "" if loopbacks else "Install BlackHole (brew install blackhole-2ch) or Loopback, route "
                                        "output through it, then pick it as the output device below.",
        },
        {
            "id": "transcription", "label": "Transcription model", "ok": bool((cfg.get("audio") or {}).get("model")),
            "detail": f"Audio is sent to {(cfg.get('audio') or {}).get('model') or '(unset)'} on your configured "
                      "LLM base URL and the recording is deleted straight after.",
            "fix": "" if (cfg.get("audio") or {}).get("model") else "Set a speech-to-text model your proxy exposes.",
        },
    ]
    return out


# ---------------------------------------------------------------- store


class Store:
    """Reads and writes the two activity tables. Every write goes through here."""

    def __init__(self, db: Database):
        self.db = db
        with db.tx() as c:
            c.executescript(SCHEMA)

    def add(self, kind: str, *, app: str = "", bundle: str = "", title: str = "", url: str = "",
            text: str = "", meta: dict[str, Any] | None = None, duration_ms: int = 0,
            retention_hours: float = 48.0, ts: float | None = None) -> str:
        eid = new_id()
        t = ts if ts is not None else now()
        with self.db.tx() as c:
            c.execute(
                "INSERT INTO activity_events(id,ts,kind,app,bundle,title,url,text,meta,duration_ms,rolled_up,expires_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,0,?)",
                (eid, t, kind, app, bundle, title, url, text, json.dumps(meta or {}), int(duration_ms),
                 t + max(0.25, float(retention_hours)) * 3600),
            )
        return eid

    def recent(self, limit: int = 200, since: float | None = None, kinds: list[str] | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM activity_events"
        where, args = [], []
        if since is not None:
            where.append("ts >= ?")
            args.append(since)
        if kinds:
            where.append("kind IN (" + ",".join("?" * len(kinds)) + ")")
            args.extend(kinds)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY ts DESC LIMIT ?"
        args.append(int(limit))
        with self.db.tx() as c:
            return [row_to_dict(r, ("meta",)) for r in c.execute(sql, args).fetchall()]  # type: ignore[misc]

    def pending(self, limit: int = 1200) -> list[dict[str, Any]]:
        with self.db.tx() as c:
            rows = c.execute(
                "SELECT * FROM activity_events WHERE rolled_up=0 ORDER BY ts LIMIT ?", (int(limit),)
            ).fetchall()
        return [row_to_dict(r, ("meta",)) for r in rows]  # type: ignore[misc]

    def mark_rolled(self, ids: list[str]) -> None:
        if not ids:
            return
        with self.db.tx() as c:
            c.executemany("UPDATE activity_events SET rolled_up=1 WHERE id=?", [(i,) for i in ids])

    def delete_event(self, eid: str) -> None:
        with self.db.tx() as c:
            c.execute("DELETE FROM activity_events WHERE id=?", (eid,))

    def counts(self) -> dict[str, int]:
        with self.db.tx() as c:
            ev = c.execute("SELECT COUNT(*) n FROM activity_events").fetchone()["n"]
            pend = c.execute("SELECT COUNT(*) n FROM activity_events WHERE rolled_up=0").fetchone()["n"]
            summ = c.execute("SELECT COUNT(*) n FROM activity_summaries").fetchone()["n"]
        return {"events": int(ev), "pending": int(pend), "summaries": int(summ)}

    def purge(self, scope: str = "expired", retention_hours: float = 48.0, summary_days: float = 90.0) -> dict[str, int]:
        """scope: expired (retention sweep) | events | summaries | all."""
        with self.db.tx() as c:
            if scope == "all":
                ev = c.execute("DELETE FROM activity_events").rowcount
                su = c.execute("DELETE FROM activity_summaries").rowcount
                c.execute("DELETE FROM activity_profile")
            elif scope == "events":
                ev = c.execute("DELETE FROM activity_events").rowcount
                su = 0
            elif scope == "summaries":
                ev = 0
                su = c.execute("DELETE FROM activity_summaries").rowcount
            else:
                ev = c.execute("DELETE FROM activity_events WHERE expires_at <= ?", (now(),)).rowcount
                su = c.execute("DELETE FROM activity_summaries WHERE created_at <= ?",
                               (now() - max(1.0, summary_days) * 86400,)).rowcount
        return {"events": max(0, ev), "summaries": max(0, su)}

    # ---- summaries ----
    def add_summary(self, day: str, start: float, end: float, headline: str, body: str,
                    apps: list[str], event_count: int) -> dict[str, Any]:
        sid = new_id()
        with self.db.tx() as c:
            c.execute(
                "INSERT INTO activity_summaries(id,day,period_start,period_end,headline,body,apps,event_count,created_at)"
                " VALUES(?,?,?,?,?,?,?,?,?)",
                (sid, day, start, end, headline, body, json.dumps(apps), int(event_count), now()),
            )
        return self.get_summary(sid)  # type: ignore[return-value]

    def get_summary(self, sid: str) -> dict[str, Any] | None:
        with self.db.tx() as c:
            return row_to_dict(c.execute("SELECT * FROM activity_summaries WHERE id=?", (sid,)).fetchone(), ("apps",))

    def summaries(self, day: str | None = None, since: float | None = None, limit: int = 200) -> list[dict[str, Any]]:
        sql = "SELECT * FROM activity_summaries"
        where, args = [], []
        if day:
            where.append("day = ?")
            args.append(day)
        if since is not None:
            where.append("period_end >= ?")
            args.append(since)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY period_start DESC LIMIT ?"
        args.append(int(limit))
        with self.db.tx() as c:
            return [row_to_dict(r, ("apps",)) for r in c.execute(sql, args).fetchall()]  # type: ignore[misc]

    def delete_summary(self, sid: str) -> None:
        with self.db.tx() as c:
            c.execute("DELETE FROM activity_summaries WHERE id=?", (sid,))

    # ---- durable profile ----
    def profile(self) -> dict[str, Any]:
        with self.db.tx() as c:
            r = c.execute("SELECT content, updated_at FROM activity_profile WHERE id=1").fetchone()
        return {"content": r["content"] if r else "", "updated_at": float(r["updated_at"]) if r else 0.0}

    def set_profile(self, content: str) -> None:
        with self.db.tx() as c:
            c.execute(
                "INSERT INTO activity_profile(id,content,updated_at) VALUES(1,?,?)"
                " ON CONFLICT(id) DO UPDATE SET content=excluded.content, updated_at=excluded.updated_at",
                (content, now()),
            )


# ---------------------------------------------------------------- collectors


class Collector(threading.Thread):
    """Base for the background probes: a daemon thread that respects stop and pause."""

    name_id = "collector"

    def __init__(self, monitor: Monitor):
        super().__init__(name=f"activity-{self.name_id}", daemon=True)
        self.m = monitor
        self.error = ""
        # Bound once, not read from the monitor each time: a restart swaps in a fresh event, and this
        # generation must keep watching the one it was started with or it would never notice the stop.
        self.halt = monitor.stop_event

    @property
    def active(self) -> bool:
        return self.m.running and not self.m.paused and not self.halt.is_set()

    def sleep(self, seconds: float) -> None:
        self.halt.wait(max(0.05, seconds))

    def run(self) -> None:
        try:
            self.work()
        except Exception as e:  # noqa: BLE001 - one broken probe must not take the app down
            self.error = f"{type(e).__name__}: {e}"
            log.warning("activity: %s stopped: %s", self.name_id, self.error)

    def work(self) -> None:  # pragma: no cover - overridden
        raise NotImplementedError

    def stop(self) -> None:
        """Extra teardown beyond stop_event (the input tap has a run loop to break)."""


class FocusCollector(Collector):
    """Samples the frontmost app, its window title and (optionally) the active tab URL.

    Emits one `focus` event per stretch of attention, with how long it lasted - so a rollup can
    say "35 minutes in Xcode" instead of listing 400 samples.
    """

    name_id = "focus"

    def work(self) -> None:
        cur: dict[str, Any] | None = None
        was_idle = False
        while not self.halt.is_set():
            cfg = self.m.config()
            period = float(cfg.get("sampleSeconds") or 5)
            if not self.active or not (cfg.get("signals") or {}).get("apps", True):
                self._close(cur)
                cur = None
                self.sleep(2)
                continue

            if idle_seconds() >= float(cfg.get("idleSeconds") or 120):
                if not was_idle:
                    self._close(cur)
                    cur = None
                    self.m.store.add("idle", meta={"since_seconds": round(idle_seconds())},
                                     retention_hours=cfg["retentionHours"])
                    was_idle = True
                self.sleep(period)
                continue
            if was_idle:
                was_idle = False
                self.m.store.add("note", text="came back to the machine", retention_hours=cfg["retentionHours"])

            app, bundle, pid = frontmost_app()
            if not app:
                self.sleep(period)
                continue
            title = focused_window_title(pid) if pid else ""
            url = browser_url(app) if (cfg.get("signals") or {}).get("browserUrls") else ""

            if self.m.gate.excluded(app, title, url):
                # Keep the time, drop the content: the timeline stays honest without recording
                # anything at all about an excluded window.
                app, bundle, title, url = "(private)", "", "", ""

            key = (app, title, url)
            if cur is None or cur["key"] != key:
                self._close(cur)
                cur = {"key": key, "app": app, "bundle": bundle, "title": title, "url": url, "start": now()}
            self.m.last_focus = {"app": app, "title": title, "url": url, "since": cur["start"]}
            self.sleep(period)
        self._close(cur)

    def _close(self, cur: dict[str, Any] | None) -> None:
        """Write the stretch that just ended, with its real duration."""
        if not cur:
            return
        dur = max(0.0, now() - cur["start"])
        if dur < 1.0:
            return
        cfg = self.m.config()
        self.m.store.add(
            "focus", app=cur["app"], bundle=cur["bundle"],
            title=self.m.gate.scrub(cur["title"]) if cur["title"] else "",
            url=cur["url"], duration_ms=int(dur * 1000), ts=cur["start"],
            retention_hours=cfg["retentionHours"],
        )


# Keys worth counting by name; everything else is just "a keystroke".
NAMED_KEYS = {51: "delete", 36: "return", 48: "tab", 53: "escape", 49: "space"}


class InputCollector(Collector):
    """A listen-only event tap over keystrokes, clicks and scrolls.

    Two modes. With `input` on it stores counts and rhythm only - how much typing, how many
    clicks, typing speed. With `text` also on it keeps the characters typed as well, after the
    gate has scrubbed them, and it records nothing at all while macOS reports secure input.
    """

    name_id = "input"
    FLUSH_SECONDS = 30.0

    def __init__(self, monitor: Monitor):
        super().__init__(monitor)
        self.lock = threading.Lock()
        self.loop_ref: Any = None
        self.secure_blocked = 0
        self.reset()

    def reset(self) -> None:
        self.keys = 0
        self.clicks = 0
        self.scrolls = 0
        self.named: dict[str, int] = {}
        self.buffer: list[str] = []
        self.first_key = 0.0
        self.last_key = 0.0
        self.window_start = now()

    def work(self) -> None:
        q = _load_pyobjc().get("Quartz")
        if not q:
            self.error = "pyobjc not installed - add pyobjc-framework-Quartz to capture input"
            return
        if not accessibility_trusted():
            self.error = "Accessibility permission not granted - enable it in System Settings, then restart the app"
            return

        mask = 0
        for ev in ("kCGEventKeyDown", "kCGEventLeftMouseDown", "kCGEventRightMouseDown",
                   "kCGEventOtherMouseDown", "kCGEventScrollWheel"):
            with contextlib.suppress(Exception):
                mask |= q.CGEventMaskBit(getattr(q, ev))

        tap = q.CGEventTapCreate(
            q.kCGSessionEventTap, q.kCGHeadInsertEventTap, q.kCGEventTapOptionListenOnly,
            mask, self._on_event, None,
        )
        if not tap:
            self.error = "the system refused the event tap (check Accessibility / Input Monitoring)"
            return
        source = q.CFMachPortCreateRunLoopSource(None, tap, 0)
        self.loop_ref = q.CFRunLoopGetCurrent()
        q.CFRunLoopAddSource(self.loop_ref, source, q.kCFRunLoopCommonModes)
        q.CGEventTapEnable(tap, True)

        threading.Thread(target=self._flush_loop, name="activity-input-flush", daemon=True).start()
        q.CFRunLoopRun()  # returns once stop() breaks the loop
        self._flush()

    def stop(self) -> None:
        q = _load_pyobjc().get("Quartz")
        if q and self.loop_ref is not None:
            with contextlib.suppress(Exception):
                q.CFRunLoopStop(self.loop_ref)

    # -- tap callback: stays fast, returns the event untouched (listen-only tap) --
    def _on_event(self, proxy: Any, etype: Any, event: Any, refcon: Any) -> Any:
        try:
            if not self.active:
                return event
            q = _pyobjc["Quartz"]
            with self.lock:
                if etype == q.kCGEventKeyDown:
                    if secure_input_active():
                        self.secure_blocked += 1
                        return event
                    self.keys += 1
                    t = now()
                    self.first_key = self.first_key or t
                    self.last_key = t
                    code = int(q.CGEventGetIntegerValueField(event, q.kCGKeyboardEventKeycode))
                    if code in NAMED_KEYS:
                        self.named[NAMED_KEYS[code]] = self.named.get(NAMED_KEYS[code], 0) + 1
                    if (self.m.config().get("signals") or {}).get("text") and len(self.buffer) < 4000:
                        ch = _unicode_for(q, event)
                        if ch:
                            self.buffer.append(ch)
                elif etype == q.kCGEventScrollWheel:
                    self.scrolls += 1
                else:
                    self.clicks += 1
        except Exception:  # noqa: BLE001 - raising inside a system callback is not an option
            pass
        return event

    def _flush_loop(self) -> None:
        while not self.halt.is_set():
            self.sleep(self.FLUSH_SECONDS)
            with contextlib.suppress(Exception):
                self._flush()

    def _flush(self) -> None:
        with self.lock:
            keys, clicks, scrolls = self.keys, self.clicks, self.scrolls
            named, buf = dict(self.named), "".join(self.buffer)
            first, last, start, blocked = self.first_key, self.last_key, self.window_start, self.secure_blocked
            self.secure_blocked = 0
            self.reset()
        if not (keys or clicks or scrolls or blocked):
            return
        cfg = self.m.config()
        span = max(1.0, (last or now()) - (first or now()))
        wpm = round((keys / 5.0) / (span / 60.0), 1) if keys > 2 else 0.0
        app = (self.m.last_focus or {}).get("app", "")
        title = (self.m.last_focus or {}).get("title", "")
        text = ""
        if buf and (cfg.get("signals") or {}).get("text") and not self.m.gate.excluded(app, title):
            text = self.m.gate.scrub(buf)[:2000]
        self.m.store.add(
            "input", app=app, text=text,
            meta={"keys": keys, "clicks": clicks, "scrolls": scrolls, "named": named,
                  "wpm": wpm, "secure_skipped": blocked},
            duration_ms=int((now() - start) * 1000), ts=start, retention_hours=cfg["retentionHours"],
        )


def _unicode_for(q: Any, event: Any) -> str:
    """The character a key-down produced, across pyobjc return shapes. '' when undecodable."""
    try:
        res = q.CGEventKeyboardGetUnicodeString(event, 8, None, None)
    except Exception:  # noqa: BLE001
        return ""
    parts = list(res) if isinstance(res, (tuple, list)) else [res]
    for p in reversed(parts):
        if isinstance(p, str):
            return p
        if isinstance(p, (bytes, bytearray)):
            return bytes(p).decode("utf-16-le", "ignore")
        if hasattr(p, "__len__") and not isinstance(p, (int, float, str)):
            with contextlib.suppress(Exception):
                return "".join(chr(x) for x in p if isinstance(x, int) and x)
    return ""


class AudioCollector(Collector):
    """Records short chunks with ffmpeg, transcribes them, keeps only the text.

    The wav never outlives the transcription call, and the transcription goes to the same LLM
    base URL the app already uses for chat - no new service, no new credentials.
    """

    def __init__(self, monitor: Monitor, channel: str):
        self.channel = channel  # "mic" | "output"
        self.name_id = f"audio-{channel}"
        super().__init__(monitor)

    @property
    def signal(self) -> str:
        return "micAudio" if self.channel == "mic" else "outputAudio"

    def work(self) -> None:
        ff = ffmpeg_path()
        if not ff:
            self.error = "ffmpeg not found on PATH (brew install ffmpeg)"
            return
        tmp = self.m.data_dir / "tmp" / "activity"
        tmp.mkdir(parents=True, exist_ok=True)
        while not self.halt.is_set():
            cfg = self.m.config()
            audio = cfg.get("audio") or {}
            if not self.active or not (cfg.get("signals") or {}).get(self.signal):
                self.sleep(3)
                continue
            device = str(audio.get("micDevice" if self.channel == "mic" else "outputDevice") or "").strip()
            if not device:
                self.error = f"no {self.channel} device selected"
                self.sleep(10)
                continue
            self.error = ""
            chunk = max(5, int(audio.get("chunkSeconds") or 30))
            path = tmp / f"{self.channel}-{new_id()}.wav"
            text = ""
            try:
                # The row's ts must be when recording STARTED: store.add defaults to now(), which
                # is when transcription returned, so ts + duration_ms pointed into the future.
                started = now()
                r = subprocess.run(
                    [ff, "-hide_banner", "-loglevel", "error", "-f", "avfoundation", "-i", f":{device}",
                     "-t", str(chunk), "-ac", "1", "-ar", "16000", "-y", str(path)],
                    capture_output=True, text=True, timeout=chunk + 30,
                )
                if r.returncode != 0 or not path.exists() or path.stat().st_size < 2048:
                    self.error = (r.stderr or "ffmpeg produced no audio").strip()[:200]
                    self.sleep(5)
                    continue
                text = self._transcribe(path, str(audio.get("model") or "whisper-1"))
            except subprocess.TimeoutExpired:
                self.error = "ffmpeg timed out"
            except Exception as e:  # noqa: BLE001
                self.error = f"{type(e).__name__}: {e}"
            finally:
                with contextlib.suppress(Exception):
                    path.unlink(missing_ok=True)

            text = (text or "").strip()
            if len(text) < int(audio.get("minChars") or 12):
                continue
            app = (self.m.last_focus or {}).get("app", "")
            if self.m.gate.excluded(app, (self.m.last_focus or {}).get("title", "")):
                continue
            self.m.store.add(
                "audio", app=app, text=self.m.gate.scrub(text)[:4000],
                meta={"channel": self.channel, "seconds": chunk},
                duration_ms=chunk * 1000, ts=started, retention_hours=cfg["retentionHours"],
            )

    def _transcribe(self, path: Path, model: str) -> str:
        """Hand the wav to stt.py, which never raises and reports its own failure."""
        # Pinned to the proxy backend: the monitor has always posted to the chat base URL, and a
        # silent switch to on-device whisper is a meetings setting, not an activity one.
        res = stt.transcribe(path, settings=self.m.settings(),
                             cfg={"sttBackend": "proxy", "sttModel": model}, data_dir=self.m.data_dir)
        if res["error"]:
            self.error = res["error"]
        return res["text"]


# ---------------------------------------------------------------- rollup prompts

ROLLUP_PROMPT = """You are the activity summarizer inside the user's personal AI OS.

You are given a digest of what the user did on their computer over one short period: which apps
and windows held their attention and for how long, how much they typed and clicked, and -- only
when they turned those signals on -- fragments of what they typed and transcripts of what was
said or played out loud.

Write a summary whose only purpose is to help the assistant be more useful to this person later.

Return ONLY a JSON object:
{
  "headline": "under 60 chars, concrete, e.g. 'Debugging the embedding config'",
  "summary": "2-5 sentences, past tense, third person ('They ...'). What they worked on, what they seemed to be trying to achieve, where they got stuck or switched away.",
  "topics": ["short topic or entity names they touched"],
  "signals": ["observations worth remembering long-term, e.g. 'works in long uninterrupted Cursor sessions', 'switches to Slack every few minutes when blocked'"]
}

Rules:
- Describe work and intent, not keystrokes. "Rewrote the LiteLLM config" beats "typed 412 characters".
- Fragments of typed text are noisy and out of order; use them only as a hint about the subject.
- Never copy anything that looks like a credential, an address, or a private message, even when it
  reached you unredacted. Leave it out.
- If the period is thin or ambiguous, say so briefly and keep arrays empty. Never invent activity.
"""

PROFILE_PROMPT = """You maintain a short, durable profile of how this user works, built only from
observed computer activity.

You get the current profile plus recent period summaries. Return the rewritten profile as markdown:
at most 12 bullets, grouped under `### Tools`, `### Rhythms` and `### Focus`.

Rules:
- Keep only patterns that recur. Drop anything that was a one-off, and drop bullets the recent
  evidence now contradicts.
- Write what is useful to an assistant: which tools they live in, when they do deep work, how they
  context-switch, what projects keep coming back.
- Never include specific content (no message text, no URLs, no names of private documents).
- Return markdown only, no preamble.
"""


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in patch.items():
        out[k] = _deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def _fmt_minutes(seconds: float) -> str:
    m = seconds / 60.0
    if m < 1:
        return f"{int(seconds)}s"
    if m < 60:
        return f"{m:.0f}m"
    return f"{m / 60:.1f}h"


def _day_of(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def _clock(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%H:%M")


def _parse_json(text: str) -> dict[str, Any]:
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except ValueError:
        return {}


# ---------------------------------------------------------------- the monitor


class Monitor:
    """Owns the collectors, the rollup loop and the markdown file.

    One instance per app. `start()` is safe to call repeatedly; nothing runs until it is called.
    """

    def __init__(self, db: Database, settings_fn: Callable[[], dict[str, Any]], complete_fn: Callable[..., Any]):
        self.db = db
        self.data_dir = db.data_dir
        self.settings = settings_fn
        self._complete = complete_fn
        self.store = Store(db)
        self.gate = Gate(self.config)
        self.stop_event = threading.Event()
        self.stop_event.set()  # nothing is running yet
        self.running = False
        self.paused = False
        self.pause_until = 0.0
        self.collectors: list[Collector] = []
        self.last_focus: dict[str, Any] = {}
        self.last_rollup = 0.0
        self.last_error = ""
        self._rollup_lock = asyncio.Lock()

    # ---- config ----
    def config(self) -> dict[str, Any]:
        stored = self.db.get_settings().get("activity")
        return _deep_merge(DEFAULT_CONFIG, stored if isinstance(stored, dict) else {})

    def set_config(self, patch: dict[str, Any]) -> dict[str, Any]:
        cfg = _deep_merge(self.config(), patch or {})
        cfg["signals"] = {k: bool(v) for k, v in (cfg.get("signals") or {}).items() if k in SIGNALS}
        self.db.set_settings({"activity": cfg})
        if not cfg.get("enabled"):
            if self.running:
                self.stop()         # an explicit enabled:false is a stop, not a restart
        elif self.running:
            self.restart()          # a signal may have been turned on or off
        else:
            self.start()
        return cfg

    @property
    def md_path(self) -> Path:
        return self.data_dir / "context" / "activity.md"

    # ---- lifecycle ----
    def start(self) -> dict[str, Any]:
        if self.running or not IS_MAC:
            return self.status()
        cfg = self.config()
        self.stop_event = threading.Event()
        self.running = True
        self.paused = False
        self.db.set_settings({"activity": {**cfg, "enabled": True}})
        sig = cfg.get("signals") or {}
        self.collectors = []
        if sig.get("apps", True) or sig.get("browserUrls"):
            self.collectors.append(FocusCollector(self))
        if sig.get("input") or sig.get("text"):
            self.collectors.append(InputCollector(self))
        if sig.get("micAudio"):
            self.collectors.append(AudioCollector(self, "mic"))
        if sig.get("outputAudio"):
            self.collectors.append(AudioCollector(self, "output"))
        for c in self.collectors:
            c.start()
        log.info("activity: started (%s)", ", ".join(c.name_id for c in self.collectors) or "no signals")
        return self.status()

    def stop(self, *, persist: bool = True) -> dict[str, Any]:
        self.running = False
        self.stop_event.set()
        for c in self.collectors:
            with contextlib.suppress(Exception):
                c.stop()
        self.collectors = []
        self.last_focus = {}
        if persist:
            self.db.set_settings({"activity": {**self.config(), "enabled": False}})
        log.info("activity: stopped")
        return self.status()

    def restart(self) -> dict[str, Any]:
        self.stop(persist=False)
        return self.start()

    def pause(self, minutes: float = 30.0) -> dict[str, Any]:
        """Stop recording without tearing the collectors down. Nothing is written while paused."""
        self.paused = True
        self.pause_until = now() + max(1.0, minutes) * 60
        return self.status()

    def resume(self) -> dict[str, Any]:
        self.paused = False
        self.pause_until = 0.0
        return self.status()

    def _check_pause(self) -> None:
        if self.paused and self.pause_until and now() >= self.pause_until:
            self.resume()

    # ---- status ----
    def status(self) -> dict[str, Any]:
        self._check_pause()
        cfg = self.config()
        prof = self.store.profile()
        return {
            "running": self.running,
            "paused": self.paused,
            "pause_until": self.pause_until or None,
            "platform_supported": IS_MAC,
            "config": cfg,
            "capabilities": capabilities(cfg),
            "collectors": [{"id": c.name_id, "alive": c.is_alive(), "error": c.error} for c in self.collectors],
            "counts": self.store.counts(),
            "now": self.now_line(),
            "last_rollup": self.last_rollup or None,
            "last_error": self.last_error,
            "profile_updated_at": prof["updated_at"] or None,
            "md_path": str(self.md_path),
            "audio_devices": audio_devices() if (cfg.get("signals") or {}).get("micAudio") or (cfg.get("signals") or {}).get("outputAudio") else [],
            "secure_input": secure_input_active(),
        }

    def now_line(self) -> str:
        """One live sentence about what is happening, computed without the LLM."""
        if not self.running:
            return "Activity monitor is off."
        if self.paused:
            return "Activity monitor is paused."
        f = self.last_focus
        if not f.get("app"):
            return "Watching, nothing sampled yet."
        idle = idle_seconds()
        if idle >= float(self.config().get("idleSeconds") or 120):
            return f"Away from the machine for {_fmt_minutes(idle)}."
        where = f["app"] + (f" - {f['title']}" if f.get("title") else "")
        held = _fmt_minutes(max(0.0, now() - float(f.get("since") or now())))
        return f"In {where} for {held}."

    # ---- rollup ----
    def _digest(self, events: list[dict[str, Any]]) -> tuple[str, list[str]]:
        """Fold raw events into a compact text digest plus the app ranking."""
        by_app: dict[str, float] = {}
        titles: dict[str, set[str]] = {}
        urls: set[str] = set()
        keys = clicks = scrolls = 0
        wpms: list[float] = []
        typed: list[str] = []
        heard: list[str] = []
        idle_total = 0.0
        secure_skipped = 0

        for e in events:
            meta = e.get("meta") or {}
            if e["kind"] == "focus":
                secs = e["duration_ms"] / 1000.0
                by_app[e["app"]] = by_app.get(e["app"], 0.0) + secs
                if e["title"]:
                    titles.setdefault(e["app"], set()).add(e["title"][:120])
                if e["url"]:
                    urls.add(e["url"][:200])
            elif e["kind"] == "input":
                keys += int(meta.get("keys") or 0)
                clicks += int(meta.get("clicks") or 0)
                scrolls += int(meta.get("scrolls") or 0)
                secure_skipped += int(meta.get("secure_skipped") or 0)
                if meta.get("wpm"):
                    wpms.append(float(meta["wpm"]))
                if e["text"]:
                    typed.append(e["text"])
            elif e["kind"] == "audio":
                heard.append(f"[{meta.get('channel', '?')}] {e['text']}")
            elif e["kind"] == "idle":
                idle_total += float(meta.get("since_seconds") or 0)

        ranked = sorted(by_app.items(), key=lambda kv: -kv[1])
        lines: list[str] = []
        if ranked:
            lines.append("Attention by app:")
            for app, secs in ranked[:10]:
                t = sorted(titles.get(app, []))[:6]
                lines.append(f"- {app}: {_fmt_minutes(secs)}" + (f" | windows: {'; '.join(t)}" if t else ""))
        if urls:
            lines.append("Pages visited:\n" + "\n".join(f"- {u}" for u in sorted(urls)[:20]))
        if keys or clicks or scrolls:
            avg = f", ~{sum(wpms) / len(wpms):.0f} wpm while typing" if wpms else ""
            lines.append(f"Input: {keys} keystrokes, {clicks} clicks, {scrolls} scrolls{avg}.")
        if secure_skipped:
            lines.append(f"({secure_skipped} keystrokes skipped - a password field was focused.)")
        if idle_total:
            lines.append(f"Idle: {_fmt_minutes(idle_total)} away from the machine.")
        if typed:
            joined = " ".join(typed)[:2500]
            lines.append(f"Fragments of typed text (noisy, out of order, redacted):\n{joined}")
        if heard:
            lines.append("Audio transcript excerpts:\n" + "\n".join(h[:600] for h in heard[:12]))
        return ("\n".join(lines) or "(nothing observed)", [a for a, _ in ranked[:8]])

    async def rollup_once(self, *, force: bool = False) -> dict[str, Any] | None:
        """Summarize everything not yet summarized. Returns the new summary, or None."""
        async with self._rollup_lock:
            cfg = self.config()
            events = self.store.pending()
            if not events:
                return None
            span = events[-1]["ts"] - events[0]["ts"]
            if not force and span < float(cfg.get("rollupMinutes") or 15) * 60 * 0.5:
                return None  # too little has happened; let it accumulate

            digest, ranked = self._digest(events)
            model = cfg.get("summaryModel") or self.settings().get("extractionModel") or self.settings().get("defaultModel")
            start, end = events[0]["ts"], max(e["ts"] + e["duration_ms"] / 1000.0 for e in events)
            head, body = "", ""
            try:
                raw = await self._complete(
                    self.settings(), model,
                    [{"role": "system", "content": ROLLUP_PROMPT},
                     {"role": "user", "content": f"Period: {_clock(start)}-{_clock(end)} on {_day_of(start)}\n\n{digest}"}],
                    kind="activity",
                )
                data = _parse_json(raw)
                head = str(data.get("headline") or "").strip()[:120]
                body = str(data.get("summary") or "").strip()
                extras = [str(s) for s in (data.get("signals") or []) if str(s).strip()][:6]
                topics = [str(t) for t in (data.get("topics") or []) if str(t).strip()][:10]
                if topics:
                    body += "\n\nTopics: " + ", ".join(topics)
                if extras:
                    body += "\n\nPatterns noticed:\n" + "\n".join(f"- {s}" for s in extras)
                self.last_error = ""
            except Exception as e:  # noqa: BLE001 - never lose the events over a bad LLM call
                self.last_error = f"rollup failed: {type(e).__name__}: {e}"
                log.warning("activity: %s", self.last_error)
                head = head or f"{_fmt_minutes(sum(e['duration_ms'] / 1000 for e in events))} of activity"
                body = body or f"Summary unavailable ({e}). Raw digest:\n\n{digest[:1500]}"

            summary = self.store.add_summary(_day_of(start), start, end, head, body, ranked, len(events))
            self.store.mark_rolled([e["id"] for e in events])
            self.last_rollup = now()
            self.write_markdown()
            return summary

    async def refresh_profile(self) -> str:
        """Rewrite the durable 'how this person works' section from recent summaries."""
        cfg = self.config()
        recent = self.store.summaries(since=now() - 14 * 86400, limit=60)
        if not recent:
            return self.store.profile()["content"]
        blocks = "\n\n".join(
            f"[{s['day']} {_clock(s['period_start'])}] {s['headline']}\n{s['body']}" for s in reversed(recent)
        )[:14000]
        model = cfg.get("summaryModel") or self.settings().get("extractionModel") or self.settings().get("defaultModel")
        try:
            out = await self._complete(
                self.settings(), model,
                [{"role": "system", "content": PROFILE_PROMPT},
                 {"role": "user", "content": f"Current profile:\n{self.store.profile()['content'] or '(empty)'}\n\n---\nRecent periods:\n{blocks}"}],
                kind="activity",
            )
            content = (out or "").strip()
            if content:
                self.store.set_profile(content)
                self.write_markdown()
            return content
        except Exception as e:  # noqa: BLE001
            self.last_error = f"profile refresh failed: {e}"
            return self.store.profile()["content"]

    # ---- the markdown file ----
    def write_markdown(self) -> Path:
        """Rewrite <data_dir>/context/activity.md. This file is the feature's real output: it is
        what gets injected into chats, and it is plain text the user can read or delete."""
        cfg = self.config()
        on = [s for s in SIGNALS if (cfg.get("signals") or {}).get(s)]
        days = max(1, int(cfg.get("contextDays") or 3))
        summaries = self.store.summaries(since=now() - days * 86400, limit=400)
        prof = self.store.profile()

        out: list[str] = [
            "# Activity context",
            "",
            "_Written by the Personal OS activity monitor. Stays on this machine._  ",
            f"_Updated {datetime.now().strftime('%Y-%m-%d %H:%M')} · signals on: {', '.join(on) or 'none'} · "
            f"raw samples kept {cfg.get('retentionHours')}h, summaries {cfg.get('summaryRetentionDays')}d._",
            "",
            "## Right now",
            "",
            self.now_line(),
            "",
        ]
        if prof["content"].strip():
            out += ["## How this person works", "", prof["content"].strip(), ""]

        by_day: dict[str, list[dict[str, Any]]] = {}
        for s in summaries:
            by_day.setdefault(s["day"], []).append(s)
        today = _day_of(now())
        for day in sorted(by_day, reverse=True):
            out.append(f"## {'Today - ' if day == today else ''}{day}")
            out.append("")
            for s in sorted(by_day[day], key=lambda x: -x["period_start"]):
                out.append(f"### {_clock(s['period_start'])}-{_clock(s['period_end'])} · {s['headline'] or 'Activity'}")
                out.append("")
                out.append(s["body"].strip())
                if s["apps"]:
                    out.append("")
                    out.append(f"_Apps: {', '.join(s['apps'][:8])}_")
                out.append("")
        if not summaries:
            out += ["## No summaries yet", "",
                    "Nothing has been rolled up. Turn the monitor on and use the machine for a few "
                    "minutes, or force a rollup from the Activity panel.", ""]

        self.md_path.parent.mkdir(parents=True, exist_ok=True)
        self.md_path.write_text("\n".join(out), encoding="utf-8")
        return self.md_path

    def read_markdown(self) -> str:
        try:
            return self.md_path.read_text(encoding="utf-8")
        except OSError:
            return ""

    # ---- what chat actually sees ----
    def context_block(self, max_chars: int = 4000) -> str:
        """The compact version injected into a chat's system prompt. Empty when off or opted out."""
        cfg = self.config()
        if not cfg.get("injectContext", True):
            return ""
        prof = self.store.profile()["content"].strip()
        recent = self.store.summaries(since=now() - 86400, limit=6)
        if not prof and not recent and not self.running:
            return ""
        parts = [
            "## What the user has been doing (from their activity monitor)",
            "This is observed computer activity, recorded locally with the user's consent. Use it to be "
            "more relevant; do not recite it back or comment on being able to see it unless asked.",
            "",
            f"Right now: {self.now_line()}",
        ]
        if prof:
            parts += ["", "How they work:", prof]
        if recent:
            parts += ["", "Recent periods:"]
            for s in recent:
                first = (s["body"].strip().split("\n\n")[0] or "").strip()
                parts.append(f"- {_clock(s['period_start'])}-{_clock(s['period_end'])} {s['headline']}: {first}")
        return "\n".join(parts)[:max_chars]

    # ---- background loop ----
    async def loop(self) -> None:
        """Rollup, retention sweep and profile refresh. Started once at app startup."""
        last_profile = 0.0
        while True:
            try:
                cfg = self.config()
                await asyncio.sleep(max(20.0, float(cfg.get("rollupMinutes") or 15) * 60 / 3))
                self._check_pause()
                self.store.purge("expired", float(cfg["retentionHours"]), float(cfg["summaryRetentionDays"]))
                if self.running:
                    await self.rollup_once()
                    hours = float(cfg.get("profileEveryHours") or 6)
                    if hours > 0 and now() - last_profile >= hours * 3600 and self.store.summaries(limit=1):
                        last_profile = now()
                        await self.refresh_profile()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 - the loop must outlive any single failure
                self.last_error = f"{type(e).__name__}: {e}"
                log.warning("activity loop: %s", e)
                await asyncio.sleep(30)
