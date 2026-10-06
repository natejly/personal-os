"""Enhanced notes stay pending, vocabulary rides on the STT prompt, identity is scrubbed from segments."""
from __future__ import annotations

import asyncio
import json
import math
import struct
import sys
import tempfile
import threading
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os import meeting_recorder, meetings, stt  # noqa: E402
from personal_os.db import Database  # noqa: E402

SETTINGS = {"baseUrl": "http://localhost:4000", "apiKey": "", "defaultModel": "test-model", "extractionModel": ""}
NOTES = "## Agenda\n- pricing tiers"


def _reply(markdown: str) -> str:
    return json.dumps({"enhanced_markdown": markdown, "decisions": [], "action_items": [], "topics": [],
                       "headline": "h"})


def _svc(reply: str):
    async def fake_complete(settings, model, messages, kind="learn"):
        if reply == "__raise__":
            raise RuntimeError("proxy down")
        return reply

    db = Database(Path(tempfile.mkdtemp()))
    repo = meetings.Meetings(db)
    return repo, meetings.MeetingService(db, lambda: dict(SETTINGS), fake_complete, repo)


def _with_transcript(repo, mid: str, text: str, detail=None) -> None:
    seg = repo.add_segment(mid, "output", 0, 0.0, 20.0, 1_700_000_000.0, "/nowhere/0.wav", 4096)
    repo.finish_segment(seg["id"], text=text, detail=detail, backend="proxy")
    repo.finalize(mid, repo.build_transcript(mid), status="ready")


def test_the_queued_pass_stays_pending_and_notes_never_change() -> None:
    repo, svc = _svc(_reply("# Pricing\n\n- [00:42] ship the tiers\n"))
    mid = repo.create(title="Pricing call")["id"]
    repo.patch(mid, {"notes": NOTES})
    _with_transcript(repo, mid, "ship the tiers")
    asyncio.run(svc._enhance_quietly(mid))           # what stop queues
    m = repo.get(mid)
    assert m["notes"] == NOTES and m["enhanced"] == "" and m["has_pending"] is True
    rev = m["pending"]
    assert rev["status"] == "pending"
    repo.accept(rev["id"])
    m = repo.get(mid)
    assert m["notes"] == NOTES
    assert "[00:42]" in m["enhanced"]                # the point still carries its timestamp


def test_a_second_pass_does_not_replace_a_hand_edited_enhanced() -> None:
    repo, svc = _svc(_reply("# Pricing\n\n- ship\n"))
    mid = repo.create(title="Pricing call")["id"]
    repo.patch(mid, {"notes": NOTES, "enhanced": "mine"})
    asyncio.run(svc.enhance(mid, force=True))
    assert repo.get(mid)["enhanced"] == "mine"


def test_a_degraded_pass_is_still_not_accepted() -> None:
    repo, svc = _svc("__raise__")
    mid = repo.create(title="Pricing call")["id"]
    repo.patch(mid, {"notes": NOTES})
    _with_transcript(repo, mid, "ship the tiers")
    rev = asyncio.run(svc.enhance(mid))
    assert rev["degraded"] is True and rev["status"] == "pending"
    assert repo.get(mid)["enhanced"] == ""


def test_a_speaker_rename_proposes_but_does_not_apply() -> None:
    repo, svc = _svc(_reply("# Pricing\n\n- Dana will ship\n"))
    mid = repo.create(title="Pricing call")["id"]
    repo.patch(mid, {"notes": NOTES, "enhanced": "old accepted text"})
    _with_transcript(repo, mid, "we ship", {"utterances": [{"speaker": "S1", "text": "we ship"}]})

    async def go():
        svc.set_speakers(mid, {"S1": "Dana"})
        for _ in range(40):
            await asyncio.sleep(0.05)
            if repo.get(mid)["has_pending"]:
                break

    asyncio.run(go())
    m = repo.get(mid)
    assert m["has_pending"] is True and m["enhanced"] == "old accepted text" and m["notes"] == NOTES


def test_vocab_has_terms_and_attendee_names() -> None:
    repo, svc = _svc("")
    mid = repo.create(title="Sync", attendees=[{"email": "d@x.com", "name": "Dana Whitfield"}])["id"]
    v = svc._vocab(mid, {"terms": ["Kubernetes", " Grain "]})
    assert "Kubernetes" in v and "Grain" in v and "Dana Whitfield" in v
    assert meetings.DEFAULT_CONFIG["terms"] == []


def test_terms_persist_through_the_config_route_model() -> None:
    from personal_os.app import MeetingConfigIn
    repo, svc = _svc("")
    patch = MeetingConfigIn(terms=["Kubernetes"]).model_dump(exclude_none=True)
    assert patch == {"terms": ["Kubernetes"]}
    svc.set_config(patch)
    assert svc.config()["terms"] == ["Kubernetes"]
    mid = repo.create(title="Sync")["id"]
    assert "Kubernetes" in svc._vocab(mid, svc.config())


def _tone(path: Path) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(i / 8))) for i in range(16000)))


def test_the_whisper_prompt_has_the_vocab_and_the_previous_clip_tail() -> None:
    prompts: list[str] = []
    texts = iter(["first clip words", "second clip"])
    real = stt.transcribe

    def fake(path, *, settings, cfg, data_dir, prompt="", vocab=""):
        prompts.append(prompt)
        return {"text": next(texts), "detail": {}, "backend": "proxy", "error": "", "ms": 1}

    stt.transcribe = fake
    try:
        tmp = Path(tempfile.mkdtemp())
        w = meeting_recorder.TranscribeWorker(
            "m1", None, threading.Event(), out_dir=tmp, settings_fn=lambda: {},
            config_fn=lambda: {"vadGate": False, "hallucinationFilter": False}, data_dir=tmp,
            on_result=lambda *a: None, vocab="Kubernetes, Dana Whitfield")
        for seq in (0, 1):
            p = tmp / f"mic-{seq}.wav"
            _tone(p)
            w._transcribe_one("mic", seq, p, False)
    finally:
        stt.transcribe = real
    assert "Kubernetes" in prompts[0] and "Dana Whitfield" in prompts[0]
    assert "Kubernetes" in prompts[1] and "first clip words" in prompts[1]


def test_a_segment_is_stored_without_email_or_phone_but_ordinary_words_stay() -> None:
    repo, _ = _svc("")
    mid = repo.create(title="Call")["id"]
    seg = repo.add_segment(mid, "output", 0, 0.0, 20.0, 1_700_000_000.0, "/nowhere/0.wav", 4096)
    row = repo.finish_segment(
        seg["id"], text="mail ada@example.com or call 415 555 0134 tomorrow",
        detail={"segments": [{"text": "mail ada@example.com or call 415 555 0134 tomorrow"}]}, backend="proxy")
    blob = row["text"] + json.dumps(row["detail"])
    assert "ada@example.com" not in blob and "555 0134" not in blob
    assert "[email]" in row["text"] and "[phone]" in row["text"]
    assert "mail" in row["text"] and "tomorrow" in row["text"]


if __name__ == "__main__":
    for k, v in sorted(globals().items()):
        if k.startswith("test_"):
            v()
            print("ok", k)
