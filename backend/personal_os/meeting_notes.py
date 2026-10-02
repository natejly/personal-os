"""Meeting notes: the note templates and the one-shot enhance pass that fills them in.

The user's typed notes are the outline and the transcript is only allowed to fill them in, so
everything here is a PROPOSAL: `enhance` returns markdown, the caller stores it as a
meeting_revisions row, and nothing in this module ever writes `meetings.notes`.

Like `activity.Monitor`, the LLM arrives as a `complete_fn` argument rather than an import of
llm (app.py:240). That is the whole reason this half of the subsystem is testable without a
network, a model or a microphone.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, Callable

# Per-kind hints in the shape of assist.KIND_HINTS: a label for the picker, the sections the
# enhanced notes should reach for, and one line of voice guidance appended to the system prompt.
TEMPLATES: dict[str, dict[str, Any]] = {
    "general": {
        "label": "General meeting",
        "sections": ["Summary", "Discussion", "Decisions", "Action items", "Open questions"],
        "hint": "An ordinary working meeting. Lead with what was settled, not with who spoke.",
    },
    "standup": {
        "label": "Standup",
        "sections": ["Updates", "Blockers", "Action items"],
        "hint": "A short status round. Group by person where the transcript makes the owner obvious, keep each update to a line or two, and surface blockers even when they were mentioned in passing.",
    },
    "one_on_one": {
        "label": "1:1",
        "sections": ["Topics", "Feedback", "Commitments", "Follow-ups"],
        "hint": "A private two-person conversation. Keep feedback in the words it was given in, and never soften or editorialise it.",
    },
    "user_interview": {
        "label": "User interview",
        "sections": ["Who they are", "Problems", "Current workaround", "Quotes", "Follow-ups"],
        "hint": "A research call. Quotes are the point: keep them verbatim and short, and never merge two speakers into one quote.",
    },
    "sales_call": {
        "label": "Sales call",
        "sections": ["Context", "Needs", "Objections", "Pricing", "Next steps"],
        "hint": "A prospect conversation. Record objections and pricing exactly as stated; an invented number here is worse than a missing one.",
    },
    "lecture": {
        "label": "Lecture or talk",
        "sections": ["Thesis", "Key points", "Examples", "Terms", "Questions"],
        "hint": "Mostly one person talking. Structure the material; do not pad it with commentary the speaker did not make.",
    },
}

ENHANCE_PROMPT = """You turn a person's rough meeting notes into the notes they would have written if they had been able to type fast enough.

You get what they typed during the meeting plus a transcript of what was said. The notes are the OUTLINE. The transcript exists only to fill in, correct and attribute what they already wrote.

Return ONLY a JSON object:
{
  "enhanced_markdown": "the full enhanced notes as markdown",
  "decisions": ["one line per decision the meeting actually reached"],
  "action_items": [{"text": "what to do", "owner": "attendee name or email, or empty", "due": "YYYY-MM-DD or empty"}],
  "topics": ["short topic or entity names the meeting touched"],
  "headline": "under 60 chars, concrete, e.g. 'Agreed to ship pricing v2 in October'"
}

Rules:
- Keep the user's headings, their ordering and their emphasis. If they wrote three bullets under a
  heading, those three bullets stay, in that order, expanded with what was actually said.
- Never reorder their points. Never pad a thin section: a section that got one line in the meeting
  gets one line here.
- Never invent a decision, an action item, a number or a commitment the transcript does not support.
  An empty "decisions" array is a correct answer.
- Skip an empty section rather than writing a placeholder.
- Speaker attribution is channel-level only: `[you]` is the user and `[them]` is everyone else on
  the call. Never attribute a quote to a named attendee, however obvious the voice seems.
- Write markdown, no preamble and no closing commentary.
"""

# Swapped in for the channel-level rule above, only when the user has named at least one diarized
# speaker. It is deliberately narrower than "name whoever you think it is".
ATTRIBUTION_RULE = """- Speaker attribution: `[you]` is the user. A transcript line may carry a person's name in its
  brackets, e.g. `[Dana]`; attribute to that person ONLY when the line carries that name, and the
  `speakers` field of the input lists the names the user assigned. Unnamed speaker labels such as
  `[S1]` stay unnamed (say "one participant", never guess who). `[them]` is channel-level: everyone
  else on the call, so never attribute it to a named attendee."""
_BAN_START = "- Speaker attribution is channel-level only"


def _speaker_names(meeting: dict[str, Any]) -> dict[str, str]:
    raw = meeting.get("speaker_names")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw or "{}")
        except ValueError:
            raw = {}
    return {str(k): str(v) for k, v in raw.items() if str(v).strip()} if isinstance(raw, dict) else {}


def _system_prompt(names: dict[str, str]) -> str:
    if not names:
        return ENHANCE_PROMPT
    start = ENHANCE_PROMPT.index(_BAN_START)
    end = ENHANCE_PROMPT.index("\n- Write markdown", start)
    return ENHANCE_PROMPT[:start] + ATTRIBUTION_RULE + ENHANCE_PROMPT[end:]


def _parse_json(text: str) -> dict[str, Any]:
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except ValueError:
        return {}


def _parse_list(raw: Any) -> list[Any]:
    """Attendees arrive either as the stored JSON column or already decoded; accept both."""
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            out = json.loads(raw)
        except ValueError:
            return []
        return out if isinstance(out, list) else []
    return []


def _attendees(meeting: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for a in _parse_list(meeting.get("attendees")):
        if isinstance(a, dict):
            label = str(a.get("name") or a.get("email") or "").strip()
            if label and a.get("self"):
                label = f"{label} (you)"
        else:
            label = str(a).strip()
        if label:
            out.append(label[:120])
    return out[:40]


def _fmt_duration(duration_ms: Any) -> str:
    ms = float(duration_ms or 0)
    m = ms / 60000.0
    if m < 1:
        return f"{int(ms / 1000)}s"
    if m < 60:
        return f"{m:.0f}m"
    return f"{m / 60:.1f}h"


def _when(meeting: dict[str, Any]) -> str:
    ts = meeting.get("started_at") or meeting.get("scheduled_start")
    if not ts:
        return ""
    return datetime.fromtimestamp(float(ts)).strftime("%A %Y-%m-%d %H:%M")


def _template_block(tpl: dict[str, Any]) -> str:
    sections = "\n".join(f"- {s}" for s in (tpl.get("sections") or []))
    return (f"\nMeeting kind: {tpl.get('label') or 'Meeting'}\n{tpl.get('hint') or ''}\n\n"
            f"Sections to reach for, in this order, but only where the user's notes or the transcript\n"
            f"support them -- and never in place of a heading the user wrote themselves:\n{sections}\n")


def cap_transcript(text: str, limit: int) -> str:
    """Fit a transcript into the prompt by keeping both ends of it.

    Head truncation is the obvious thing and the wrong one: the decision usually lands in the last
    few minutes of a meeting, so a naive cut drops the most valuable part. The budget goes 40/60 in
    favour of the tail, with an explicit marker so the model knows the middle is missing rather
    than assuming the two halves are adjacent.
    """
    text = text or ""
    if limit <= 0 or len(text) <= limit:
        return text
    head = int(limit * 0.4)
    tail = limit - head
    return f"{text[:head]}\n\n[... {len(text) - head - tail} characters omitted ...]\n\n{text[len(text) - tail:]}"


def pick_model(cfg: dict[str, Any], settings: dict[str, Any]) -> str:
    return cfg.get("enhanceModel") or settings.get("extractionModel") or settings["defaultModel"]


def _mechanical(notes: str, transcript: str) -> str:
    """The fallback notes when the model is unreachable: what they typed, then what was said.

    Unlike `rollup_once`, which marks its events `rolled_up` even when the LLM dies
    (activity.py:1201-1206), nothing is consumed or marked here. The transcript is the system of
    record for a meeting, so a degraded pass must stay re-runnable.
    """
    parts = [notes] if notes.strip() else []
    parts.append("## Transcript\n\n" + (transcript.strip() or "_(no transcript)_"))
    return "\n\n".join(parts)


async def enhance(
    *,
    complete_fn: Callable[..., Any],
    settings: dict[str, Any],
    model: str,
    meeting: dict[str, Any],
    notes: str,
    transcript: str,
    template: str = "general",
    max_transcript_chars: int = 48000,
) -> dict[str, Any]:
    """One LLM call per meeting. Never raises: a failure comes back as degraded markdown."""
    tpl = TEMPLATES.get(template) or TEMPLATES["general"]
    body = cap_transcript(transcript or "", max_transcript_chars)
    notes = notes or ""
    out: dict[str, Any] = {"markdown": "", "decisions": [], "action_items": [], "topics": [],
                           "headline": "", "degraded": False, "error": "", "model": model}
    names = _speaker_names(meeting)
    payload: dict[str, Any] = {
        "title": str(meeting.get("title") or ""),
        "when": _when(meeting),
        "duration": _fmt_duration(meeting.get("duration_ms")),
        "attendees": _attendees(meeting),
        "notes": notes,
        "transcript": body,
    }
    if names:
        payload["speakers"] = names
    user = json.dumps(payload, ensure_ascii=False)
    try:
        raw = await complete_fn(
            settings, model,
            [{"role": "system", "content": _system_prompt(names) + _template_block(tpl)},
             {"role": "user", "content": user}],
            kind="meeting",
        )
        data = _parse_json(raw)
        markdown = str(data.get("enhanced_markdown") or "").strip()
        if not markdown:
            raise ValueError("no enhanced_markdown in the reply")
        out["markdown"] = markdown
        out["decisions"] = [str(d).strip()[:300] for d in (data.get("decisions") or []) if str(d).strip()][:20]
        out["action_items"] = _action_items(data.get("action_items"))
        out["topics"] = [str(t).strip()[:60] for t in (data.get("topics") or []) if str(t).strip()][:12]
        out["headline"] = str(data.get("headline") or "").strip()[:120]
    except Exception as e:  # noqa: BLE001 - a dead model must not cost the user their notes
        out["degraded"] = True
        out["error"] = f"{type(e).__name__}: {e}"
        out["markdown"] = _mechanical(notes, body)
        out["decisions"], out["action_items"], out["topics"] = [], [], []
        out["headline"] = str(meeting.get("title") or "").strip()[:120]
    return out


def _action_items(raw: Any) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for it in raw if isinstance(raw, list) else []:
        if isinstance(it, dict):
            text, owner, due = it.get("text"), it.get("owner"), it.get("due")
        else:
            text, owner, due = it, "", ""
        text = str(text or "").strip()
        if not text:
            continue
        due = str(due or "").strip()
        out.append({"text": text[:300], "owner": str(owner or "").strip()[:120],
                    # meeting_action_items.due is a bare YYYY-MM-DD; anything else is dropped
                    # rather than handed to the todos date parser.
                    "due": due if re.fullmatch(r"\d{4}-\d{2}-\d{2}", due) else ""})
    return out[:30]
