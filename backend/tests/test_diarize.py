"""Speaker turns: the pure assignment, the service wiring with a fake backend, naming, and fallbacks.

No sherpa-onnx and no model: a FakeBackend returns fixed turns. The ffmpeg concat step is tested
on its own and skipped without ffmpeg.

Runs under pytest, or directly: python backend/tests/test_diarize.py
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os import audiocap, diarize, meeting_notes, meetings  # noqa: E402
from personal_os.db import Database  # noqa: E402

SETTINGS = {"baseUrl": "http://localhost:4000", "apiKey": "", "defaultModel": "test-model", "extractionModel": ""}


def _u(start: float, end: float, text: str = "x") -> dict:
    return {"start": start, "end": end, "text": text}


# ---------------------------------------------------------------- (a) pure assignment


def test_utterance_inside_one_turn() -> None:
    out = diarize.assign_speakers([_u(1, 3)], [(0, 10, "S1"), (10, 20, "S2")])
    assert out[0]["speaker"] == "S1"


def test_straddling_utterance_picks_the_larger_overlap() -> None:
    out = diarize.assign_speakers([_u(8, 14)], [(0, 10, "S1"), (10, 20, "S2")])
    assert out[0]["speaker"] == "S2"  # 4 s with S2 beats 2 s with S1


def test_overlapping_speech_picks_the_larger() -> None:
    out = diarize.assign_speakers([_u(0, 10)], [(0, 3, "S1"), (2, 9, "S2")])
    assert out[0]["speaker"] == "S2"


def test_gap_uses_the_nearest_turn_within_a_second() -> None:
    turns = [(0, 5, "S1"), (10, 15, "S2")]
    assert diarize.assign_speakers([_u(5.4, 5.9)], turns)[0]["speaker"] == "S1"
    assert diarize.assign_speakers([_u(9.2, 9.7)], turns)[0]["speaker"] == "S2"
    assert diarize.assign_speakers([_u(7, 8)], turns)[0]["speaker"] == ""


def test_no_turns_returns_utterances_unchanged() -> None:
    u = [_u(0, 1)]
    assert diarize.assign_speakers(u, []) == u


def test_turn_ids_are_renamed_in_order_of_first_appearance() -> None:
    out = diarize.rename_turns([(5, 6, "spk9"), (0, 1, "spk3"), (2, 3, "spk9")])
    assert out == [(0, 1, "S1"), (2, 3, "S2"), (5, 6, "S2")]


def test_merge_adjacent_honours_the_gap() -> None:
    utts = [{**_u(0, 2, "a"), "speaker": "S1"}, {**_u(3, 4, "b"), "speaker": "S1"},
            {**_u(8, 9, "c"), "speaker": "S1"}, {**_u(9.5, 10, "d"), "speaker": "S2"}]
    out = diarize.merge_adjacent(utts, gap=1.5)
    assert [(u["speaker"], u["text"]) for u in out] == [("S1", "a b"), ("S1", "c"), ("S2", "d")]


# ---------------------------------------------------------------- (b) service with a fake backend


class FakeBackend:
    def __init__(self, turns: list[diarize.Turn]):
        self.turns = turns
        self.calls = 0

    def diarize(self, wav: Path, cfg: dict) -> list[diarize.Turn]:
        self.calls += 1
        return list(self.turns)


def _concat_stub(wavs: list[Path], out: Path) -> bool:
    out.write_bytes(b"stub")
    return True


def _svc():
    tmp = Path(tempfile.mkdtemp(prefix="diar-"))

    async def complete(settings, model, messages, kind="learn"):
        return ""

    db = Database(tmp)
    repo = meetings.Meetings(db)
    return repo, meetings.MeetingService(db, lambda: dict(SETTINGS), complete, repo), tmp


def _seed(repo: meetings.Meetings, tmp: Path, with_wavs: bool = True) -> str:
    m = repo.create(title="Call")
    mid = m["id"]
    t0 = time.time()
    for k, (text, segs) in enumerate([
        ("hello there. sounds good", [_u(0, 4, "hello there."), _u(10, 14, "sounds good")]),
        ("let us ship", [_u(0, 6, "let us ship")]),
    ]):
        wav = tmp / f"import-{k:05d}.wav"
        wav.write_bytes(b"dummy")
        repo.add_segment(mid, "import", k, k * 20, k * 20 + 20, t0 + k * 20, str(wav) if with_wavs else "", 5,
                         duration_ms=20000, state="recorded")
        seg = repo.segment(mid, "import", k)
        repo.finish_segment(seg["id"], text=text, detail={"segments": segs}, backend="proxy", state="done",
                            wav_path=str(wav) if with_wavs else "", wav_bytes=5)
    mic = tmp / "mic.wav"
    repo.add_segment(mid, "mic", 0, 0, 20, t0, "", 0, duration_ms=20000, state="recorded")
    repo.finish_segment(repo.segment(mid, "mic", 0)["id"], text="my side", state="done")
    return mid


def test_service_assigns_speakers_and_rebuilds_the_transcript() -> None:
    repo, svc, tmp = _svc()
    mid = _seed(repo, tmp)
    repo.finalize(mid, repo.build_transcript(mid))
    before = repo.get(mid)["transcript"]
    assert "[them]" in before and "[S1]" not in before
    # concat time: segment 0 is 0-20, segment 1 is 20-40
    backend = FakeBackend([(0, 6, "a"), (9, 15, "b"), (20, 27, "a")])
    res = asyncio.run(_diarize(svc, mid, backend))
    assert res["ok"] and res["speakers"] == 2 and backend.calls == 1
    s0, s1 = repo.segment(mid, "import", 0), repo.segment(mid, "import", 1)
    assert [u["speaker"] for u in s0["detail"]["utterances"]] == ["S1", "S2"]
    assert [u["speaker"] for u in s1["detail"]["utterances"]] == ["S1"]
    assert s1["detail"]["utterances"][0]["start"] == 20.0  # meeting time, not file time
    assert s0["speaker"] in ("S1", "S2")
    assert repo.segment(mid, "mic", 0)["speaker"] == "", "the mic channel is never diarized"
    t = repo.get(mid)["transcript"]
    assert "[S1] hello there. let us ship" not in t  # the S2 line sits between them
    assert "00:00 [S1] hello there." in t and "00:10 [S2] sounds good" in t and "00:20 [S1] let us ship" in t
    assert "[you] my side" in t
    asyncio.run(_rename(svc, mid))
    t = repo.get(mid)["transcript"]
    assert "[Dana] hello there." in t and "[S2] sounds good" in t
    with repo.db.tx() as c:
        assert c.execute("SELECT 1 FROM meetings_fts WHERE meetings_fts MATCH 'Dana' AND meeting_id=?",
                         (mid,)).fetchone(), "FTS should reindex the renamed transcript"


async def _diarize(svc, mid, backend):
    # inject the stub concat through diarize_segments (the async wrapper does not take it)
    return await asyncio.to_thread(_run_and_settle, svc, mid, backend)


def _run_and_settle(svc, mid, backend):
    res = svc.diarize_segments(mid, backend, concat=_concat_stub)
    if res["ok"]:
        svc._settle_transcript(mid)
    return res


async def _rename(svc, mid):
    svc.set_speakers(mid, {"S1": "Dana"})


def test_naming_validates_ids_and_length() -> None:
    repo, svc, tmp = _svc()
    mid = _seed(repo, tmp)
    _run_and_settle(svc, mid, FakeBackend([(0, 6, "a"), (9, 15, "b"), (20, 27, "a")]))
    for bad in ({"S9": "Nobody"}, {"S1": "x" * 61}):
        try:
            svc.set_speakers(mid, bad)
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError for {bad}")
    assert svc.set_speakers(mid, {"S1": "  Dana  "})["speaker_names"] == {"S1": "Dana"}
    assert svc.set_speakers(mid, {"S1": ""})["speaker_names"] == {}


def test_no_retained_audio_or_backend_is_a_quiet_no_op() -> None:
    repo, svc, tmp = _svc()
    mid = _seed(repo, tmp, with_wavs=False)
    before = repo.build_transcript(mid)
    res = svc.diarize_segments(mid, FakeBackend([(0, 1, "a")]), concat=_concat_stub)
    assert not res["ok"] and "no retained audio" in res["note"]
    res = svc.diarize_segments(mid)  # default backend: sherpa is not installed here
    assert not res["ok"] and res["backend"] == "none"
    assert repo.build_transcript(mid) == before
    assert not repo.get(mid)["error"]


# ---------------------------------------------------------------- (c) backward compatibility


def test_a_transcript_without_speakers_is_byte_identical() -> None:
    repo, svc, tmp = _svc()
    m = repo.create(title="Plain")
    mid, t0 = m["id"], time.time()
    rows = [("mic", 0, 0, "hello"), ("mic", 1, 20, "again"), ("output", 0, 0, "hi back"), ("output", 1, 20, "more"),
            ("mic", 2, 40, "bye")]
    for ch, seq, ts, text in rows:
        repo.add_segment(mid, ch, seq, ts, ts + 20, t0 + ts, "", 0, state="recorded")
        repo.finish_segment(repo.segment(mid, ch, seq)["id"], text=text, state="done",
                            detail={"segments": [_u(0, 3, text)]})
    golden = ("00:00 [you] hello\n00:00 [them] hi back\n00:20 [you] again\n"
              "00:20 [them] more\n00:40 [you] bye")  # the pre-diarization output, channel runs by offset
    assert repo.build_transcript(mid) == golden


# ---------------------------------------------------------------- (d) sherpa absent


def test_sherpa_backend_degrades_without_the_package() -> None:
    cfg = {"diarize": True, "diarizeBackend": "sherpa"}
    assert diarize.SherpaBackend().diarize(Path("/nope.wav"), cfg) == []
    assert diarize.resolve_backend(cfg, Path(tempfile.mkdtemp())) == "none"
    assert diarize.resolve_backend({"diarizeBackend": "none"}) == "none"
    row = diarize.capabilities({}, Path(tempfile.mkdtemp()))
    assert row["id"] == "diarize" and row["ok"] is False
    assert "sherpa-onnx" in row["fix"] and "diarizeSegmentationModel" in row["fix"]
    assert "diarize" not in meetings.BLOCKING_CAPABILITIES


# ---------------------------------------------------------------- (e) enhance prompt


def test_enhance_only_relaxes_the_naming_rule_when_names_exist() -> None:
    seen: list[list[dict]] = []

    async def complete(settings, model, messages, kind="learn"):
        seen.append(messages)
        return json.dumps({"enhanced_markdown": "# n"})

    async def go(meeting: dict) -> None:
        await meeting_notes.enhance(complete_fn=complete, settings=SETTINGS, model="m", meeting=meeting,
                                    notes="n", transcript="00:00 [Dana] hi")

    asyncio.run(go({"title": "t", "attendees": []}))
    asyncio.run(go({"title": "t", "attendees": [], "speaker_names": {"S1": "Dana"}}))
    plain, named = seen
    assert "Never attribute a quote to a named attendee" in plain[0]["content"]
    assert "speakers" not in json.loads(plain[1]["content"])
    assert "ONLY when the line carries that name" in named[0]["content"]
    assert "Never attribute a quote to a named attendee" not in named[0]["content"]
    assert json.loads(named[1]["content"])["speakers"] == {"S1": "Dana"}


# ---------------------------------------------------------------- (f) concat


def test_concat_joins_wavs_in_order() -> None:
    if not audiocap.ffmpeg_path():
        print("  skip: ffmpeg missing")
        return
    d = Path(tempfile.mkdtemp())
    paths = []
    for i, secs in enumerate((1.0, 2.0)):
        p = d / f"{i}.wav"
        subprocess.run([audiocap.ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                        "sine=frequency=440:sample_rate=16000", "-t", str(secs), "-ac", "1", "-y", str(p)], check=True)
        paths.append(p)
    out = d / "all.wav"
    assert diarize.concat_wavs(paths, out)
    with wave.open(str(out), "rb") as w:
        assert abs(w.getnframes() / w.getframerate() - 3.0) < 0.1
    assert not diarize.concat_wavs([], d / "none.wav")


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
