"""Assist: one-shot LLM helpers behind the inline tab-complete and the email draft review."""
from __future__ import annotations

import json
import re
from typing import Any

from . import llm

COMPLETE_PROMPT = """You are an inline autocomplete engine (ghost text). Continue the user's text.

Rules:
- Return ONLY the continuation, nothing else: no quotes, no commentary, and never repeat text that is already there.
- If the text stops mid-sentence, finish it; if the last character is not whitespace and you start a new word, begin with a space.
- Keep it short: one sentence or about 25 words at most.
- Match the tone, language and formatting of the existing text.
- If nothing sensible can be added, return an empty string.
"""

REVIEW_PROMPT = """You review email drafts before they are sent.

Return ONLY a JSON object with this shape:
{"feedback": ["..."], "revised": "..."}

Rules:
- feedback: 2-5 short, specific suggestions (tone, clarity, missing information, structure, anything risky). If the draft is already good, say so in a single item.
- revised: the full improved draft body with the suggestions applied. Keep the author's voice, language and intent; do not add a subject line or invent a signature.
- Never invent facts, commitments or attachments the draft does not mention.
"""


def _parse_json(text: str) -> dict[str, Any]:
    m = re.search(r"\{.*\}", text.strip(), re.S)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except ValueError:
        return {}


async def complete_text(settings: dict[str, Any], kind: str, before: str, after: str = "", context: str = "") -> str:
    model = settings.get("extractionModel") or settings["defaultModel"]
    user = f"Kind of text: {kind}\n"
    if context.strip():
        user += f"Context:\n{context[:2000]}\n\n"
    user += f"Text before the cursor:\n---\n{before[-4000:]}\n---"
    if after.strip():
        user += f"\nText after the cursor (do not repeat it):\n---\n{after[:1000]}\n---"
    out = await llm.complete(settings, model, [{"role": "system", "content": COMPLETE_PROMPT}, {"role": "user", "content": user}], kind="assist")
    out = out.strip().strip('"')
    # Models love to restate the tail of the prompt; drop the longest echoed overlap.
    low_b, low_o = before.lower(), out.lower()
    for i in range(min(len(low_b), len(low_o), 200), 0, -1):
        if low_b.endswith(low_o[:i]):
            out = out[i:]
            break
    return out


async def review_email(settings: dict[str, Any], to: str, subject: str, body: str, reply_context: str = "") -> dict[str, Any]:
    user = f"To: {to}\nSubject: {subject}\n\nDraft body:\n---\n{body[:6000]}\n---"
    if reply_context.strip():
        user += f"\n\nIt replies to this message:\n---\n{reply_context[:3000]}\n---"
    raw = await llm.complete(settings, settings["defaultModel"], [{"role": "system", "content": REVIEW_PROMPT}, {"role": "user", "content": user}], kind="assist")
    data = _parse_json(raw)
    feedback = [str(x).strip() for x in (data.get("feedback") or []) if str(x).strip()]
    revised = str(data.get("revised") or "").strip()
    if not feedback and not revised:
        # The model ignored the JSON contract; surface whatever it said as feedback.
        feedback = [raw.strip()[:500]] if raw.strip() else ["The model returned no review."]
    return {"feedback": feedback, "revised": revised}
