"""Activity monitor: the gate, the store, the rollup and the markdown file.

The collectors themselves need a real macOS session and granted permissions, so they are not
exercised here; everything between "an observation arrived" and "the context block chats see" is.

Runs under pytest, or directly: python backend/tests/test_activity.py
"""
from __future__ import annotations

import asyncio
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os import activity  # noqa: E402
from personal_os.db import Database  # noqa: E402

SETTINGS = {"baseUrl": "http://localhost:4000", "apiKey": "", "defaultModel": "test-model", "extractionModel": ""}


class idle_for:
    """Pin what the OS reports as idle time.

    `activity.idle_seconds()` reads the real session once pyobjc is installed, so any assertion
    about the live status line is otherwise a coin flip on how long the machine has been sitting.
    """

    def __init__(self, seconds: float):
        self.seconds = seconds

    def __enter__(self) -> None:
        self.real = activity.idle_seconds
        activity.idle_seconds = lambda: self.seconds  # type: ignore[assignment]

    def __exit__(self, *exc: object) -> None:
        activity.idle_seconds = self.real  # type: ignore[assignment]


def _monitor(tmp: Path, reply: str = "") -> activity.Monitor:
    """A Monitor wired to a throwaway db and a stub LLM, with no collectors running."""
    calls: list[dict] = []

    async def fake_complete(settings, model, messages, kind="learn"):
        calls.append({"model": model, "messages": messages, "kind": kind})
        if reply == "__raise__":
            raise RuntimeError("proxy down")
        return reply

    m = activity.Monitor(Database(tmp), lambda: dict(SETTINGS), fake_complete)
    m.llm_calls = calls  # type: ignore[attr-defined]
    return m


# ---------------------------------------------------------------- the gate


def test_gate_excludes_password_managers_and_sensitive_titles() -> None:
    m = _monitor(Path(tempfile.mkdtemp()))
    g = m.gate
    assert g.excluded("1Password 8", "vault") is True
    assert g.excluded("Bitwarden", "") is True
    assert g.excluded("Safari", "Chase — Sign in") is True           # title pattern
    assert g.excluded("Safari", "Docs", "https://x.com/login") is True  # url pattern
    assert g.excluded("Cursor", "activity.py — Personal OS") is False


def test_gate_scrubs_credentials_and_pii() -> None:
    m = _monitor(Path(tempfile.mkdtemp()))
    scrub = m.gate.scrub

    assert "nate@example.com" not in scrub("mail nate@example.com now")
    assert "[email]" in scrub("mail nate@example.com now")
    assert "4111 1111 1111 1111" not in scrub("card 4111 1111 1111 1111")
    assert "sk-abcdefghijklmnopqrst" not in scrub("export KEY=sk-abcdefghijklmnopqrst")
    assert "AKIAIOSFODNN7EXAMPLE" not in scrub("AKIAIOSFODNN7EXAMPLE")
    assert "123-45-6789" not in scrub("ssn is 123-45-6789")
    # The value after a secret-announcing word goes, even when it looks innocuous on its own.
    assert "hunter2" not in scrub("my password is hunter2")
    assert "correcthorse" not in scrub("passphrase: correcthorse")
    assert "s3cr3t" not in scrub("API_KEY=s3cr3t")

    # Only the value goes, not everything around it. Typed text arrives as one long single line, so
    # dropping the whole line over one word would throw away the entire buffer.
    out = scrub("fixed the retry loop then set password hunter2 then rewrote the digest")
    assert "hunter2" not in out
    assert "fixed the retry loop" in out
    assert "rewrote the digest" in out

    # Ordinary prose survives untouched.
    kept = scrub("rewrote the embedding config and restarted litellm")
    assert "embedding config" in kept


def test_gate_redaction_can_be_turned_off() -> None:
    m = _monitor(Path(tempfile.mkdtemp()))
    m.set_config({"redact": False})
    assert "nate@example.com" in m.gate.scrub("mail nate@example.com")


# ---------------------------------------------------------------- config


def test_config_deep_merges_and_keeps_unknown_signals_out() -> None:
    m = _monitor(Path(tempfile.mkdtemp()))
    assert m.config()["signals"]["micAudio"] is False
    m.set_config({"signals": {"micAudio": True, "bogus": True}, "audio": {"chunkSeconds": 45}})
    cfg = m.config()
    assert cfg["signals"]["micAudio"] is True
    assert cfg["signals"]["apps"] is True          # untouched keys survive the merge
    assert "bogus" not in cfg["signals"]           # unknown signals are dropped
    assert cfg["audio"]["chunkSeconds"] == 45
    assert cfg["audio"]["model"] == "whisper-1"    # sibling audio keys survive too


# ---------------------------------------------------------------- store


def test_store_retention_and_purge() -> None:
    m = _monitor(Path(tempfile.mkdtemp()))
    s = m.store
    s.add("focus", app="Cursor", duration_ms=60_000, retention_hours=48)
    old = s.add("focus", app="Safari", retention_hours=48, ts=time.time() - 10 * 3600)
    with m.db.tx() as c:  # force that one past its expiry
        c.execute("UPDATE activity_events SET expires_at=? WHERE id=?", (time.time() - 60, old))

    assert s.counts()["events"] == 2
    assert s.purge("expired")["events"] == 1
    assert s.counts()["events"] == 1

    s.add("input", meta={"keys": 10})
    assert s.counts()["pending"] == 2
    s.mark_rolled([e["id"] for e in s.pending()])
    assert s.counts()["pending"] == 0

    s.purge("all")
    assert s.counts() == {"events": 0, "pending": 0, "summaries": 0}


def test_recent_filters_by_kind_and_window() -> None:
    m = _monitor(Path(tempfile.mkdtemp()))
    m.store.add("focus", app="Cursor")
    m.store.add("audio", text="hello there", meta={"channel": "mic"})
    m.store.add("focus", app="Slack", ts=time.time() - 100 * 3600)

    assert len(m.store.recent(since=time.time() - 3600)) == 2
    assert [e["kind"] for e in m.store.recent(since=time.time() - 3600, kinds=["audio"])] == ["audio"]
    assert len(m.store.recent(since=time.time() - 200 * 3600)) == 3


# ---------------------------------------------------------------- digest + rollup


def test_digest_folds_events_and_never_leaks_counts_as_prose() -> None:
    m = _monitor(Path(tempfile.mkdtemp()))
    events = [
        {"kind": "focus", "app": "Cursor", "title": "activity.py", "url": "", "text": "",
         "duration_ms": 1_800_000, "meta": {}, "ts": 0},
        {"kind": "focus", "app": "Safari", "title": "pyobjc docs", "url": "https://pyobjc.readthedocs.io",
         "text": "", "duration_ms": 300_000, "meta": {}, "ts": 0},
        {"kind": "input", "app": "Cursor", "title": "", "url": "", "text": "def work(self)",
         "duration_ms": 30_000, "meta": {"keys": 420, "clicks": 12, "scrolls": 3, "wpm": 62.0, "secure_skipped": 4}, "ts": 0},
        {"kind": "audio", "app": "zoom.us", "title": "", "url": "", "text": "let us ship the monitor",
         "duration_ms": 30_000, "meta": {"channel": "mic"}, "ts": 0},
        {"kind": "idle", "app": "", "title": "", "url": "", "text": "", "duration_ms": 0,
         "meta": {"since_seconds": 600}, "ts": 0},
    ]
    digest, ranked = m._digest(events)
    assert ranked[0] == "Cursor"                       # ranked by attention, not order
    assert "Cursor: 30m" in digest
    assert "Safari: 5m" in digest
    assert "420 keystrokes" in digest and "62 wpm" in digest
    assert "4 keystrokes skipped" in digest            # secure-input skips are reported, not hidden
    assert "https://pyobjc.readthedocs.io" in digest
    assert "let us ship the monitor" in digest
    assert "10m away from the machine" in digest


def test_rollup_writes_summary_marks_events_and_regenerates_markdown() -> None:
    tmp = Path(tempfile.mkdtemp())
    reply = (
        '{"headline": "Debugging the embedding config", '
        '"summary": "They worked through the LiteLLM embedding config in Cursor, checking pyobjc docs in Safari.", '
        '"topics": ["LiteLLM", "pyobjc"], "signals": ["long uninterrupted Cursor sessions"]}'
    )
    m = _monitor(tmp, reply)
    m.store.add("focus", app="Cursor", title="activity.py", duration_ms=1_800_000, ts=time.time() - 1800)
    m.store.add("input", app="Cursor", meta={"keys": 400, "wpm": 60})

    s = asyncio.run(m.rollup_once(force=True))
    assert s is not None
    assert s["headline"] == "Debugging the embedding config"
    assert "LiteLLM" in s["body"] and "long uninterrupted Cursor sessions" in s["body"]
    assert s["apps"] == ["Cursor"]
    assert m.store.counts()["pending"] == 0            # events consumed, not re-summarized next tick

    md = m.md_path.read_text()
    assert "# Activity context" in md
    assert "Debugging the embedding config" in md
    assert "_Apps: Cursor_" in md

    assert asyncio.run(m.rollup_once(force=True)) is None   # nothing pending, no second summary


def test_rollup_survives_a_dead_llm_and_still_consumes_events() -> None:
    m = _monitor(Path(tempfile.mkdtemp()), "__raise__")
    m.store.add("focus", app="Cursor", duration_ms=600_000, ts=time.time() - 600)

    s = asyncio.run(m.rollup_once(force=True))
    assert s is not None                                # the observation is not lost
    assert "Summary unavailable" in s["body"]
    assert "Cursor" in s["body"]                        # falls back to the raw digest
    assert "rollup failed" in m.last_error
    assert m.store.counts()["pending"] == 0             # and it will not retry forever


def test_rollup_waits_for_enough_to_have_happened_unless_forced() -> None:
    m = _monitor(Path(tempfile.mkdtemp()), '{"headline": "x", "summary": "y"}')
    m.store.add("focus", app="Cursor", duration_ms=1000)
    assert asyncio.run(m.rollup_once()) is None         # span far below rollupMinutes
    assert asyncio.run(m.rollup_once(force=True)) is not None


# ---------------------------------------------------------------- context


def test_context_block_is_empty_when_off_and_when_opted_out() -> None:
    m = _monitor(Path(tempfile.mkdtemp()))
    assert m.context_block() == ""                      # never ran, nothing recorded

    m.store.set_profile("### Tools\n- Lives in Cursor")
    assert "Lives in Cursor" in m.context_block()

    m.set_config({"injectContext": False})
    assert m.context_block() == ""                      # the per-user opt-out wins


def test_context_block_carries_now_profile_and_recent_periods() -> None:
    m = _monitor(Path(tempfile.mkdtemp()))
    m.running = True
    m.last_focus = {"app": "Cursor", "title": "activity.py", "since": time.time() - 300}
    m.store.set_profile("### Tools\n- Lives in Cursor")
    m.store.add_summary("2026-09-29", time.time() - 3600, time.time() - 1800,
                        "Wiring the monitor", "They wired the activity monitor into the context pipeline.",
                        ["Cursor"], 12)

    with idle_for(2):
        block = m.context_block()
        assert "activity monitor" in block
        assert "Right now: In Cursor" in block
        assert "Lives in Cursor" in block
        assert "Wiring the monitor" in block
        assert len(m.context_block(max_chars=200)) <= 200   # respects the budget


def test_build_context_includes_activity_only_when_the_chat_wants_it() -> None:
    from personal_os.context import build_context
    from personal_os.repos import Documents, Graph, Memories

    tmp = Path(tempfile.mkdtemp())
    m = _monitor(tmp)
    m.running = True
    m.last_focus = {"app": "Cursor", "title": "", "since": time.time() - 60}
    m.store.set_profile("### Tools\n- Lives in Cursor")

    db = m.db
    common = dict(memories=Memories(db), graph=Graph(db), documents=Documents(db), project=None,
                  project_id=None, query="what am I doing", settings=dict(SETTINGS),
                  global_system_prompt="You are a test.")

    system, used = build_context(**common, conv_settings={"useActivity": True}, activity=m)
    assert "Lives in Cursor" in system
    assert used["activity"] and "Lives in Cursor" in used["activity"]

    system, used = build_context(**common, conv_settings={"useActivity": False}, activity=m)
    assert "Lives in Cursor" not in system
    assert used["activity"] is None

    system, used = build_context(**common, conv_settings={}, activity=None)  # monitor absent
    assert used["activity"] is None


# ---------------------------------------------------------------- lifecycle


def test_pause_blocks_recording_and_expires_on_its_own() -> None:
    m = _monitor(Path(tempfile.mkdtemp()))
    m.running = True
    m.pause(minutes=30)
    assert m.status()["paused"] is True
    assert "paused" in m.now_line()

    m.pause_until = time.time() - 1          # pretend the pause ran out
    assert m.status()["paused"] is False     # status() expires it without anyone asking

    m.resume()
    m.last_focus = {"app": "Cursor", "title": "x", "since": time.time() - 10}
    with idle_for(2):
        assert "In Cursor" in m.now_line()


def test_now_line_reports_being_away_once_input_stops() -> None:
    m = _monitor(Path(tempfile.mkdtemp()))
    m.running = True
    m.last_focus = {"app": "Cursor", "title": "x", "since": time.time() - 600}
    with idle_for(900):                      # past the 120s default
        assert "Away from the machine" in m.now_line()
    with idle_for(5):
        assert "In Cursor" in m.now_line()


def test_restarting_does_not_orphan_the_previous_collectors() -> None:
    """Each generation watches the stop event it was started with, so a restart really stops the old
    threads instead of leaving two sets of collectors recording the same machine."""
    m = _monitor(Path(tempfile.mkdtemp()))
    first = activity.FocusCollector(m)
    old_event = m.stop_event

    m.stop_event = __import__("threading").Event()   # what start() does on the next generation
    second = activity.FocusCollector(m)

    assert first.halt is old_event
    assert second.halt is m.stop_event
    old_event.set()
    assert first.active is False                      # the old generation is done
    m.running = True
    assert second.active is True                      # the new one is unaffected


def test_config_patch_setting_enabled_false_stops_instead_of_restarting() -> None:
    m = _monitor(Path(tempfile.mkdtemp()))
    m.running = True                                  # pretend collectors are up
    m.set_config({"enabled": False})
    assert m.running is False
    assert m.config()["enabled"] is False


def test_status_reports_capabilities_without_asking_for_permissions() -> None:
    m = _monitor(Path(tempfile.mkdtemp()))
    st = m.status()
    ids = {c["id"] for c in st["capabilities"]}
    assert {"platform", "pyobjc", "accessibility", "ffmpeg", "loopback", "transcription"} <= ids
    for c in st["capabilities"]:
        assert isinstance(c["ok"], bool)
        assert c["fix"] or c["ok"]            # anything not ok explains how to fix it
    assert st["running"] is False
    assert st["md_path"].endswith("context/activity.md")


def test_markdown_is_written_even_with_nothing_recorded() -> None:
    m = _monitor(Path(tempfile.mkdtemp()))
    path = m.write_markdown()
    text = path.read_text()
    assert "# Activity context" in text
    assert "Stays on this machine" in text
    assert "No summaries yet" in text


def test_helpers() -> None:
    assert activity._fmt_minutes(30) == "30s"
    assert activity._fmt_minutes(600) == "10m"
    assert activity._fmt_minutes(5400) == "1.5h"
    assert activity.looks_like_loopback("BlackHole 2ch") is True
    assert activity.looks_like_loopback("MacBook Pro Microphone") is False
    assert activity._parse_json('noise {"a": 1} tail') == {"a": 1}
    assert activity._parse_json("not json at all") == {}
    assert activity.secure_input_active() in (True, False)   # must never raise


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"  ok  {fn.__name__}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"FAIL  {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
