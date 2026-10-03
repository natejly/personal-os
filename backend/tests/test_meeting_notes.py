"""Meeting notes: the templates, the transcript cap and the one-shot enhance pass.

No model answers /v1/chat/completions in a test run, so `complete_fn` is always a stub here.
The point of most of these is the degradation path: a meeting's notes are the user's own typing
and must survive a dead proxy, a model that ignores the JSON contract, and a model that chats.

Runs under pytest, or directly: python backend/tests/test_meeting_notes.py
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os import meeting_notes  # noqa: E402

SETTINGS = {"baseUrl": "http://localhost:4000", "apiKey": "", "defaultModel": "test-model", "extractionModel": ""}

MEETING = {
    "id": "m1",
    "title": "Pricing review",
    "started_at": 1_700_000_000.0,
    "duration_ms": 1_800_000,
    "attendees": json.dumps([{"email": "nate@example.com", "name": "Nate", "self": True},
                             {"email": "dana@example.com", "name": "Dana"}]),
}

NOTES = "## Agenda\n- pricing tiers\n- launch date"

GOOD_REPLY = json.dumps({
    "enhanced_markdown": "## Agenda\n- pricing tiers: settled on three\n- launch date: October 14",
    "decisions": ["Three tiers, not four", "  ", "Launch October 14"],
    "action_items": [{"text": "Write the pricing page", "owner": "Dana", "due": "2026-10-03"},
                     {"text": "Ping legal", "owner": "", "due": "next week"},
                     {"text": "   "}],
    "topics": ["pricing", "launch", ""],
    "headline": "Agreed three tiers and an October launch",
})


def _stub(reply: str):
    """A complete_fn that records its calls. `reply == "__raise__"` kills the proxy instead."""
    calls: list[dict] = []

    async def fake_complete(settings, model, messages, kind="learn"):
        calls.append({"model": model, "messages": messages, "kind": kind})
        if reply == "__raise__":
            raise RuntimeError("proxy down")
        return reply

    return fake_complete, calls


def _enhance(reply: str, **kw) -> tuple[dict, list[dict]]:
    fn, calls = _stub(reply)
    kw.setdefault("meeting", MEETING)
    kw.setdefault("notes", NOTES)
    kw.setdefault("transcript", "[you] what about pricing\n[them] three tiers works")
    res = asyncio.run(meeting_notes.enhance(complete_fn=fn, settings=dict(SETTINGS), model="test-model", **kw))
    return res, calls


# ---------------------------------------------------------------- templates


def test_every_template_is_fully_populated() -> None:
    assert set(meeting_notes.TEMPLATES) == {"general", "standup", "one_on_one", "user_interview", "sales_call", "lecture"}
    for key, tpl in meeting_notes.TEMPLATES.items():
        assert tpl["label"].strip(), key
        assert tpl["hint"].strip(), key
        assert tpl["sections"] and all(isinstance(s, str) and s.strip() for s in tpl["sections"]), key


def test_pick_model_prefers_the_meeting_override() -> None:
    assert meeting_notes.pick_model({"enhanceModel": "big"}, SETTINGS) == "big"
    assert meeting_notes.pick_model({}, {**SETTINGS, "extractionModel": "small"}) == "small"
    assert meeting_notes.pick_model({"enhanceModel": ""}, SETTINGS) == "test-model"


# ---------------------------------------------------------------- the transcript cap


def test_cap_transcript_keeps_both_ends() -> None:
    text = "HEAD" + ("x" * 5000) + "TAIL"
    out = meeting_notes.cap_transcript(text, 1000)
    assert out.startswith("HEAD")
    assert out.endswith("TAIL")          # the decision lands at the end; a head cut would lose it
    assert "characters omitted ...]" in out
    # 40/60 in favour of the tail.
    head, tail = out.split("\n\n[... ", 1)[0], out.rsplit(" ...]\n\n", 1)[1]
    assert len(head) == 400 and len(tail) == 600
    assert f"[... {len(text) - 1000} characters omitted ...]" in out


def test_cap_transcript_leaves_short_text_alone() -> None:
    assert meeting_notes.cap_transcript("short", 1000) == "short"
    assert meeting_notes.cap_transcript("", 1000) == ""


# ---------------------------------------------------------------- the enhance pass


def test_good_reply_maps_onto_every_output_key() -> None:
    res, calls = _enhance(GOOD_REPLY)
    assert res["degraded"] is False and res["error"] == ""
    assert res["model"] == "test-model"
    assert res["markdown"].startswith("## Agenda")
    assert res["headline"] == "Agreed three tiers and an October launch"
    assert res["decisions"] == ["Three tiers, not four", "Launch October 14"]   # blanks dropped
    assert res["topics"] == ["pricing", "launch"]
    assert res["action_items"] == [
        {"text": "Write the pricing page", "owner": "Dana", "due": "2026-10-03"},
        {"text": "Ping legal", "owner": "", "due": ""},   # "next week" is not YYYY-MM-DD
    ]
    assert len(calls) == 1                                                       # exactly one LLM call
    assert calls[0]["kind"] == "meeting"                                          # so llm.py attributes the usage


def test_the_prompt_carries_the_template_and_the_meeting_facts() -> None:
    res, calls = _enhance(GOOD_REPLY, template="user_interview")
    assert res["degraded"] is False
    system, user = calls[0]["messages"]
    assert system["role"] == "system" and user["role"] == "user"
    assert meeting_notes.ENHANCE_PROMPT in system["content"]
    assert "User interview" in system["content"] and "Quotes" in system["content"]
    payload = json.loads(user["content"])
    assert payload["title"] == "Pricing review"
    assert payload["notes"] == NOTES
    assert payload["duration"] == "30m"
    assert payload["attendees"] == ["Nate (you)", "Dana"]
    assert "three tiers works" in payload["transcript"]


def test_an_unknown_template_falls_back_to_general() -> None:
    res, calls = _enhance(GOOD_REPLY, template="nonsense")
    assert res["degraded"] is False
    assert "General meeting" in calls[0]["messages"][0]["content"]


def test_a_reply_wrapped_in_prose_still_parses() -> None:
    res, _ = _enhance("Sure! Here are your notes:\n```json\n" + GOOD_REPLY + "\n```\nHope that helps.")
    assert res["degraded"] is False
    assert res["headline"] == "Agreed three tiers and an October launch"


def test_a_reply_that_is_not_json_degrades_with_the_notes_intact() -> None:
    res, _ = _enhance("I'm sorry, I can't help with that.")
    assert res["degraded"] is True
    assert res["error"]
    assert NOTES in res["markdown"]
    assert "## Transcript" in res["markdown"]
    assert "three tiers works" in res["markdown"]
    assert res["decisions"] == [] and res["action_items"] == [] and res["topics"] == []


def test_json_without_enhanced_markdown_degrades() -> None:
    res, _ = _enhance(json.dumps({"decisions": ["something"], "enhanced_markdown": "   "}))
    assert res["degraded"] is True
    assert NOTES in res["markdown"]
    assert res["decisions"] == []


def test_a_dead_model_degrades_and_reports_the_error() -> None:
    res, calls = _enhance("__raise__")
    assert res["degraded"] is True
    assert res["error"] == "RuntimeError: proxy down"
    assert NOTES in res["markdown"]                 # the work is never lost over a bad LLM call
    assert "## Transcript" in res["markdown"]
    assert res["headline"] == "Pricing review"
    assert len(calls) == 1                          # one attempt, no retry storm


def test_degrading_with_no_notes_still_keeps_the_transcript() -> None:
    res, _ = _enhance("__raise__", notes="")
    assert res["markdown"].startswith("## Transcript")
    assert "three tiers works" in res["markdown"]


def test_degraded_markdown_caps_the_transcript_too() -> None:
    res, _ = _enhance("__raise__", transcript="A" * 5000 + "ZZZ", max_transcript_chars=1000)
    assert "characters omitted ...]" in res["markdown"]
    assert res["markdown"].rstrip().endswith("ZZZ")


# ---------------------------------------------------------------- the json scan


def test_parse_json_never_raises() -> None:
    assert meeting_notes._parse_json('noise {"a": 1} tail') == {"a": 1}
    assert meeting_notes._parse_json("not json at all") == {}
    assert meeting_notes._parse_json("") == {}
    assert meeting_notes._parse_json("{broken") == {}


# ---------------------------------------------------------------- amounts in a summary


def test_escape_currency_keeps_prices_out_of_inline_maths() -> None:
    esc = meeting_notes.escape_currency
    # two amounts in one paragraph are the shape the renderer reads as a formula
    assert esc("starter stays at $12 a month, team goes to $40.") == \
        "starter stays at \\$12 a month, team goes to \\$40."
    # already escaped, and a `$` that is not money, are left alone
    assert esc("costs \\$5 and $x^2$ stays maths") == "costs \\$5 and $x^2$ stays maths"
    # code keeps its bytes: a backslash would show there
    assert esc("run `echo $1` then pay $3") == "run `echo $1` then pay \\$3"
    assert esc("```\nprice=$9\n```\nowed $9") == "```\nprice=$9\n```\nowed \\$9"


# ---------------------------------------------------------------- evidence tags


def _summ(reply: str, **kw) -> dict:
    fn, _ = _stub(reply)
    return asyncio.run(meeting_notes.summarize_recording(
        complete_fn=fn, settings=dict(SETTINGS), model="m", meeting=MEETING, doc_title="Plan",
        doc_content="", transcript="x", **kw))


def test_numbered_transcript_skips_unspoken_segments_and_maps_tags_to_real_ids() -> None:
    text, ids = meeting_notes.numbered_transcript([
        {"id": "a", "state": "done", "text": "hello", "t_start": 5, "channel": "mic"},
        {"id": "b", "state": "failed", "text": "lost", "t_start": 9},
        {"id": "c", "state": "done", "text": "  ", "t_start": 10},
        {"id": "d", "state": "done", "text": "reply", "t_start": 65, "channel": "system"},
    ])
    assert ids == {"s1": "a", "s2": "d"}
    assert text == "[s1 00:05 you] hello\n[s2 01:05 them] reply"


def test_evidence_tags_become_real_segment_ids_and_leave_a_clean_body() -> None:
    reply = json.dumps({"summary_markdown": "- first point {s1,s2}\n- invented {s9}\n- plain", "headline": "h"})
    res = _summ(reply, sources={"s1": "seg-a", "s2": "seg-b"})
    assert "{" not in res["markdown"] and res["error"] == ""
    assert res["evidence"] == {"0": ["seg-a", "seg-b"]}      # the bogus s9 line has no entry


def test_an_untagged_reply_gives_a_summary_with_empty_evidence() -> None:
    res = _summ(json.dumps({"summary_markdown": "- a\n- b"}), sources={"s1": "seg-a"})
    assert res["markdown"] == "- a\n- b" and res["evidence"] == {}

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
