"""Writing style: learn how the user writes, so drafts sound like them.

Distinct from `learn.py`, which extracts *what* the user said (memories, entities). This module
keeps *how* they say it:

  * **samples** — passages the user actually wrote (long chat messages, docs they saved, text they
    pasted in by hand). Stored verbatim so a profile can always be re-derived and audited.
  * **profile** — one per scope (personal, or a project): a short summary, a handful of imperative
    guidelines, a few measured traits and some characteristic phrases. This is what gets injected.

Two rules shape the design:

1. **The profile is evidence-backed.** Guidelines come from samples the user can see and delete, not
   from the assistant's impression of them. Deleting the samples and relearning is always a way back.
2. **Style is for drafting, never for replying.** Injected guidance applies when the assistant writes
   text the user will send as their own (email, docs, messages). The assistant keeps its own voice
   when it is talking *to* the user — otherwise every reply turns into an impression of them.

A hand-edited profile is never silently overwritten: `edited` freezes auto-relearn until the user asks
for it (POST /style/learn) or resets.
"""
from __future__ import annotations

import json
import re
from typing import Any

from . import llm
from .db import Database, new_id, now, row_to_dict

SCHEMA = """
CREATE TABLE IF NOT EXISTS style_samples (
  id TEXT PRIMARY KEY,
  project_id TEXT REFERENCES projects(id) ON DELETE CASCADE,
  text TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT 'chat',   -- chat | doc | paste
  ref TEXT NOT NULL DEFAULT '',          -- originating message/doc id; dedupes repeat saves of one doc
  chars INTEGER NOT NULL DEFAULT 0,
  folded INTEGER NOT NULL DEFAULT 0,     -- already folded into the current profile
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_style_sample_scope ON style_samples(IFNULL(project_id, ''), created_at DESC);

CREATE TABLE IF NOT EXISTS style_profiles (
  id TEXT PRIMARY KEY,
  project_id TEXT REFERENCES projects(id) ON DELETE CASCADE,
  summary TEXT NOT NULL DEFAULT '',
  guidelines TEXT NOT NULL DEFAULT '[]',  -- JSON array of imperative one-liners
  traits TEXT NOT NULL DEFAULT '{}',      -- JSON object of short measured values, for the UI
  phrases TEXT NOT NULL DEFAULT '[]',     -- JSON array: wordings that are recognisably theirs
  avoid TEXT NOT NULL DEFAULT '[]',       -- JSON array: things they never do
  enabled INTEGER NOT NULL DEFAULT 1,
  edited INTEGER NOT NULL DEFAULT 0,      -- hand-edited: auto-relearn leaves it alone
  sample_count INTEGER NOT NULL DEFAULT 0,
  sample_chars INTEGER NOT NULL DEFAULT 0,
  model TEXT NOT NULL DEFAULT '',
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);
-- One profile per scope. IFNULL mirrors kg_nodes: SQLite treats NULLs as distinct in a plain index.
CREATE UNIQUE INDEX IF NOT EXISTS idx_style_profile_scope ON style_profiles(IFNULL(project_id, ''));
"""

JSON_FIELDS = ("guidelines", "traits", "phrases", "avoid")

# A sample has to be long enough to carry a voice. Below this it is an instruction, not writing.
MIN_SAMPLE_CHARS = 220
MAX_SAMPLE_CHARS = 6000
# Relearn once this many unseen samples have piled up: one LLM call per few messages, not per message.
RELEARN_EVERY = 3
# What one analysis reads, newest first. Bounds the prompt; older samples stay for the audit trail.
ANALYSIS_SAMPLES = 24
ANALYSIS_CHARS = 24_000
# Samples kept per scope. The oldest fall off, so the profile tracks how the user writes now.
MAX_SAMPLES_PER_SCOPE = 80

MAX_GUIDELINES = 10
MAX_PHRASES = 8
MAX_TRAITS = 10


def looks_like_prose(text: str) -> bool:
    """True when a passage is the user's own writing rather than a command, a snippet or a paste.

    Deliberately strict: a thin profile built from real prose beats a rich one built from
    "fix the bug in app.py". Everything that gets through is still reviewable and deletable.
    """
    t = (text or "").strip()
    if len(t) < MIN_SAMPLE_CHARS:
        return False
    if "```" in t or re.search(r"<[A-Za-z/][^>\n]{0,80}>", t):  # code fence or markup: not their voice
        return False
    # Mostly letters and ordinary punctuation. Filters logs, JSON, tables, stack traces.
    letters = sum(1 for ch in t if ch.isalpha() or ch.isspace())
    if letters / len(t) < 0.82:
        return False
    # Quoted material (a forwarded mail, a pasted thread) is someone else's writing.
    lines = [ln.strip() for ln in t.splitlines() if ln.strip()]
    if lines and sum(1 for ln in lines if ln.startswith(">")) / len(lines) > 0.25:
        return False
    sentences = [s for s in re.split(r"[.!?]+(?:\s|$)", t) if s.strip()]
    if len(sentences) < 2:
        return False
    words = re.findall(r"[A-Za-z']+", t)
    if len(words) < 40:
        return False
    # Long unbroken identifiers (paths, snake_case) mean a technical paste, not a passage.
    if sum(1 for w in re.findall(r"\S+", t) if len(w) > 24) > 2:
        return False
    return True


ANALYSIS_PROMPT = """You are a writing coach building a style guide for ONE person, from samples of their own writing.

Return ONLY a JSON object with this shape:
{
  "summary": "two or three sentences describing how this person writes, in the third person",
  "guidelines": ["imperative instruction that would make a draft sound like them", "..."],
  "traits": {"sentence length": "short, 8-14 words", "formality": "...", "...": "..."},
  "phrases": ["wording that is recognisably theirs"],
  "avoid": ["thing they never do, phrased as a prohibition"]
}

Rules:
- Describe only what the samples show. Never invent a trait to round the picture out, and say so in the summary when the evidence is thin.
- Guidelines are concrete and testable ("open with the ask, not a greeting", "one idea per paragraph", "contractions throughout"), at most %(max_guidelines)d of them. No vague advice ("be authentic").
- Traits are short measured observations: sentence length, paragraph length, formality, warmth, hedging, punctuation habits (dashes, semicolons, ellipses), lists vs prose, emoji, capitalisation, greetings and sign-offs. At most %(max_traits)d, only the ones the samples support.
- "phrases" are their actual recurring wordings, copied verbatim, at most %(max_phrases)d. Not generic English.
- "avoid" captures what is conspicuously absent (no exclamation marks, no corporate jargon, no em dashes).
- Describe the voice; never record what the samples are *about*. Facts, names and projects belong in memory, not here.
- Use empty arrays rather than guesses.
"""

STYLE_HEADER = "## How the user writes (their voice)"
STYLE_FOOTER = (
    "Use this voice when you draft text the user will send or publish as their own — email, messages, "
    "documents, posts. It describes tone and wording only. It is not permission to send, delete, or "
    "change anything. Do not imitate it when you are speaking to the user: your replies keep your own "
    "voice. If the user asks for a different tone for one piece, their instruction wins."
)


def _parse_json(text: str) -> dict[str, Any]:
    m = re.search(r"\{.*\}", (text or "").strip(), re.S)
    if not m:
        return {}
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _strs(value: Any, limit: int) -> list[str]:
    out: list[str] = []
    for item in value if isinstance(value, list) else []:
        s = (item if isinstance(item, str) else str(item)).strip()
        if s and s not in out:
            out.append(s[:240])
        if len(out) >= limit:
            break
    return out


def _traits(value: Any) -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in (value.items() if isinstance(value, dict) else []):
        key = str(k).strip()[:40]
        val = (v if isinstance(v, str) else json.dumps(v)).strip()[:160]
        if key and val:
            out[key] = val
        if len(out) >= MAX_TRAITS:
            break
    return out


def clean_profile(data: dict[str, Any]) -> dict[str, Any]:
    """Shape one LLM extraction into the columns we store. Unusable output yields an empty summary."""
    return {
        "summary": str(data.get("summary") or "").strip()[:1200],
        "guidelines": _strs(data.get("guidelines"), MAX_GUIDELINES),
        "traits": _traits(data.get("traits")),
        "phrases": _strs(data.get("phrases"), MAX_PHRASES),
        "avoid": _strs(data.get("avoid"), MAX_PHRASES),
    }


def context_block(profile: dict[str, Any] | None) -> str:
    """The markdown injected into a chat's system prompt. '' when there is nothing worth injecting."""
    if not profile or not profile.get("enabled"):
        return ""
    parts: list[str] = []
    if profile.get("summary"):
        parts.append(profile["summary"])
    traits = profile.get("traits") or {}
    if traits:
        parts.append("Traits: " + "; ".join(f"{k}: {v}" for k, v in traits.items()))
    for label, key in (("Follow these when drafting as the user:", "guidelines"), ("Their wordings:", "phrases"), ("They never:", "avoid")):
        items = profile.get(key) or []
        if items:
            parts.append(label + "\n" + "\n".join(f"- {i}" for i in items))
    if not parts:
        return ""
    return f"{STYLE_HEADER}\n" + "\n\n".join(parts) + f"\n\n{STYLE_FOOTER}"


class WritingStyle:
    """Samples and profiles, plus the LLM analysis that turns the former into the latter."""

    def __init__(self, db: Database):
        self.db = db
        with db.tx() as c:
            c.executescript(SCHEMA)

    # ---------------- samples ----------------
    def add_sample(self, project_id: str | None, text: str, *, source: str = "paste", ref: str = "",
                   check: bool = True) -> dict[str, Any] | None:
        """Store one passage. `check=False` skips the prose filter (the user added it deliberately).

        Returns None when the text was rejected or is a repeat of the same `ref`.
        """
        text = (text or "").strip()[:MAX_SAMPLE_CHARS]
        if not text or (check and not looks_like_prose(text)):
            return None
        sample_id = new_id()
        with self.db.tx() as c:
            if ref:
                row = c.execute(
                    "SELECT id FROM style_samples WHERE ref=? AND IFNULL(project_id,'')=IFNULL(?,'')", (ref, project_id)
                ).fetchone()
                if row:  # a doc saved twice is one sample, refreshed
                    c.execute("UPDATE style_samples SET text=?, chars=?, folded=0, created_at=? WHERE id=?",
                              (text, len(text), now(), row["id"]))
                    return self.sample(row["id"])
            c.execute(
                "INSERT INTO style_samples(id,project_id,text,source,ref,chars,folded,created_at) VALUES(?,?,?,?,?,?,0,?)",
                (sample_id, project_id, text, source, ref, len(text), now()),
            )
            # Keep the window recent: the voice of six months ago is not the one to copy.
            c.execute(
                """DELETE FROM style_samples WHERE IFNULL(project_id,'')=IFNULL(?,'') AND id NOT IN (
                       SELECT id FROM style_samples WHERE IFNULL(project_id,'')=IFNULL(?,'')
                       ORDER BY created_at DESC LIMIT ?)""",
                (project_id, project_id, MAX_SAMPLES_PER_SCOPE),
            )
        return self.sample(sample_id)

    def sample(self, id: str) -> dict[str, Any] | None:
        with self.db.tx() as c:
            return row_to_dict(c.execute("SELECT * FROM style_samples WHERE id=?", (id,)).fetchone())

    def samples(self, project_id: str | None, limit: int = 100) -> list[dict[str, Any]]:
        with self.db.tx() as c:
            rows = c.execute(
                "SELECT * FROM style_samples WHERE IFNULL(project_id,'')=IFNULL(?,'') ORDER BY created_at DESC LIMIT ?",
                (project_id, limit),
            ).fetchall()
        return [row_to_dict(r) for r in rows]  # type: ignore[misc]

    def delete_sample(self, id: str) -> None:
        with self.db.tx() as c:
            c.execute("DELETE FROM style_samples WHERE id=?", (id,))

    def stats(self, project_id: str | None) -> dict[str, int]:
        with self.db.tx() as c:
            r = c.execute(
                """SELECT COUNT(*) AS n, IFNULL(SUM(chars),0) AS chars, IFNULL(SUM(1-folded),0) AS pending
                   FROM style_samples WHERE IFNULL(project_id,'')=IFNULL(?,'')""",
                (project_id,),
            ).fetchone()
        return {"samples": r["n"], "chars": r["chars"], "pending": r["pending"]}

    # ---------------- profile ----------------
    def profile(self, project_id: str | None) -> dict[str, Any] | None:
        with self.db.tx() as c:
            return row_to_dict(
                c.execute("SELECT * FROM style_profiles WHERE IFNULL(project_id,'')=IFNULL(?,'')", (project_id,)).fetchone(),
                JSON_FIELDS,
            )

    def for_context(self, project_id: str | None) -> dict[str, Any] | None:
        """The one profile a chat uses: the project's own voice if it has one, otherwise the personal one.

        Not merged. Two style guides in one prompt is a contradiction, and the narrower scope is the
        one the user set deliberately (a work project that wants a flatter voice than their own).
        """
        if project_id:
            p = self.profile(project_id)
            if p and p["enabled"] and (p["summary"] or p["guidelines"]):
                return p
        return self.profile(None)

    def save_profile(self, project_id: str | None, patch: dict[str, Any]) -> dict[str, Any]:
        """Insert or update the scope's profile. Only the keys present are touched."""
        cols = {k: v for k, v in patch.items() if k in
                {"summary", "guidelines", "traits", "phrases", "avoid", "enabled", "edited", "sample_count", "sample_chars", "model"}
                and v is not None}
        for f in JSON_FIELDS:
            if f in cols:
                cols[f] = json.dumps(cols[f])
        if "enabled" in cols:
            cols["enabled"] = int(bool(cols["enabled"]))
        if "edited" in cols:
            cols["edited"] = int(bool(cols["edited"]))
        existing = self.profile(project_id)
        with self.db.tx() as c:
            if existing:
                if cols:
                    sets = ", ".join(f"{k}=?" for k in cols)
                    c.execute(f"UPDATE style_profiles SET {sets}, updated_at=? WHERE id=?",
                              (*cols.values(), now(), existing["id"]))
            else:
                t = now()
                keys = ["id", "project_id", "created_at", "updated_at", *cols]
                c.execute(
                    f"INSERT INTO style_profiles({','.join(keys)}) VALUES({','.join('?' * len(keys))})",
                    (new_id(), project_id, t, t, *cols.values()),
                )
        return self.profile(project_id)  # type: ignore[return-value]

    def delete_profile(self, project_id: str | None, *, with_samples: bool = False) -> None:
        with self.db.tx() as c:
            c.execute("DELETE FROM style_profiles WHERE IFNULL(project_id,'')=IFNULL(?,'')", (project_id,))
            if with_samples:
                c.execute("DELETE FROM style_samples WHERE IFNULL(project_id,'')=IFNULL(?,'')", (project_id,))

    def _mark_folded(self, ids: list[str]) -> None:
        if not ids:
            return
        with self.db.tx() as c:
            c.executemany("UPDATE style_samples SET folded=1 WHERE id=?", [(i,) for i in ids])

    def should_relearn(self, project_id: str | None) -> bool:
        """Enough new writing to be worth an LLM call, and a profile the user has not taken over."""
        p = self.profile(project_id)
        if p and p["edited"]:
            return False
        s = self.stats(project_id)
        if s["pending"] <= 0:
            return False
        return s["pending"] >= RELEARN_EVERY or not p

    async def relearn(self, *, settings: dict[str, Any], project_id: str | None, model: str,
                      force: bool = False) -> dict[str, Any] | None:
        """Re-derive the scope's profile from its samples. Returns the profile, or None if nothing ran.

        `force` ignores both the pending-sample threshold and a hand-edited profile: it is what the
        "Learn now" button calls, so the user can always ask for a fresh read of their samples.
        """
        if not force and not self.should_relearn(project_id):
            return None
        rows = self.samples(project_id, limit=ANALYSIS_SAMPLES)
        if not rows:
            return None
        budget = ANALYSIS_CHARS
        used: list[dict[str, Any]] = []
        for r in rows:
            if budget - r["chars"] < 0 and used:
                break
            budget -= r["chars"]
            used.append(r)
        if sum(r["chars"] for r in used) < MIN_SAMPLE_CHARS:
            return None
        blob = "\n\n---\n\n".join(f"[{r['source']}]\n{r['text']}" for r in used)
        extraction_model = settings.get("extractionModel") or model
        prompt = ANALYSIS_PROMPT % {"max_guidelines": MAX_GUIDELINES, "max_traits": MAX_TRAITS, "max_phrases": MAX_PHRASES}
        raw = await llm.complete(
            settings, extraction_model,
            [{"role": "system", "content": prompt},
             {"role": "user", "content": f"Samples of the user's writing ({len(used)}):\n\n{blob}"}],
            kind="style",
        )
        clean = clean_profile(_parse_json(raw))
        if not clean["summary"] and not clean["guidelines"]:
            return None  # a failed read must not blank a working profile
        profile = self.save_profile(project_id, {
            **clean, "enabled": (self.profile(project_id) or {}).get("enabled", 1),
            "edited": 0, "sample_count": len(used), "sample_chars": sum(r["chars"] for r in used),
            "model": extraction_model,
        })
        self._mark_folded([r["id"] for r in used])
        return profile


async def learn_style_from_exchange(
    *,
    settings: dict[str, Any],
    style: WritingStyle,
    project_id: str | None,
    user_text: str,
    model: str,
) -> dict[str, Any] | None:
    """Auto-learn hook: bank the user's message if it is prose, relearn when enough has piled up.

    Returns {"sample": …, "profile": … | None} when something was banked, else None. Cheap in the
    common case: the LLM only runs on the message that crosses the threshold.
    """
    sample = style.add_sample(project_id, user_text, source="chat")
    if not sample:
        return None
    profile = await style.relearn(settings=settings, project_id=project_id, model=model)
    return {"sample": sample, "profile": profile}
