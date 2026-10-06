# Writing style

Grain learns *how* you write, not just what you said, so that when the assistant drafts something you
will send under your own name — an email, a message, a doc — it sounds like you rather than like a
language model.

Memory holds the facts. This holds the voice. They are separate on purpose: a fact about you is worth
keeping forever, while a voice drifts, and the evidence behind it has to stay reviewable.

Live in **Memory → Voice** (and in a project's Memory tab for that project's voice).

## How it works

Two objects, per scope (your personal voice, or one project's):

**Samples** — passages you actually wrote, stored verbatim:

| Source | When it is banked |
| --- | --- |
| `chat` | A message you send that reads as prose (see the filter below). |
| `doc` | Every time you save a doc, under a stable ref — editing one doc for a week refreshes one sample, it does not add seven. |
| `paste` | Writing you add by hand in the Voice panel, or that the assistant banks with `save_writing_sample` when you point at something as "how I write". |

The newest 80 per scope are kept, so the profile tracks how you write now.

**Profile** — one per scope, derived from the samples by the extraction model:

- a two-or-three sentence **summary**,
- up to ten imperative **guidelines** ("open with the ask, not a greeting"),
- measured **traits** (sentence length, formality, punctuation habits, emoji, sign-offs),
- **phrases** that are recognisably yours, and
- a **never** list — what is conspicuously absent from your writing.

Re-derivation is lazy: banking a sample is a row insert, and the model only re-reads your samples once
three new ones have piled up (or on the first one, so you see something immediately). One LLM call per
few messages, not per message.

## The prose filter

Most chat messages are instructions, not writing. Learning from "fix the bug in app.py" would teach the
app to draft like a ticket, so a message is only banked when it is at least ~220 characters, has two or
more sentences and forty or more words, is mostly letters, and carries no code fence, markup, log lines,
long paths or quoted reply text. The filter is deliberately strict: a thin profile built from real prose
beats a rich one built from commands.

Pasting a sample by hand skips the filter — a deliberate paste is already a decision.

## Where it goes

When a chat has **Writing style** on (Context panel, per chat), the profile is injected into the system
prompt under *"How the user writes (their voice)"*, and the block ends with the rule that makes the
feature usable:

> Use this voice when you draft text the user will send or publish as their own — email, messages,
> documents, posts. Do not imitate it when you are speaking to the user: your replies keep your own
> voice.

Without that line the assistant starts answering you in an impression of you, which nobody wants.

A chat uses **one** profile: the project's if that project has its own enabled voice, otherwise your
personal one. They are never merged — two style guides in one prompt is a contradiction, and the
narrower scope is the one you set deliberately (a work project that wants a flatter voice than yours).

The Context panel's *Last reply* and *Preview* tabs show which voice a reply drafted with.

## Staying in control

- **Every guideline is editable** in the Voice panel, and editing anything sets `edited`, which stops
  auto-relearn from overwriting your wording. "Re-read my writing" is then the only thing that replaces
  it, and it is a button you press.
- **Every sample is visible and deletable.** The profile is only as good as its evidence, so anything
  that is not your own writing should go.
- **Two off switches.** Settings → *Learn how you write* stops banking and relearning globally; the
  per-chat *Writing style* toggle stops injection for one chat. Turning a profile off keeps it.
- **Reset** deletes the profile, optionally with the samples it came from.
- A failed or unparseable model reply never blanks a working profile.

## Tools

| Tool | Does |
| --- | --- |
| `writing_style` | Reads the profile for the chat's scope. For drafting when the style block is not already in context. |
| `save_writing_sample` | Banks a passage **you** wrote. Writes in-app, so it is on by default; it is for text you point at, never the assistant's own prose or something from a web page. |

## API

| Route | Does |
| --- | --- |
| `GET /style?project_id=` | Profile, sample stats, and which voice a chat in that scope would use. |
| `PUT /style` | Hand edits (sets `edited`), or `enabled`. |
| `POST /style/learn` | Re-read the samples now. Forced, so it refreshes a hand-edited profile too. |
| `DELETE /style?with_samples=` | Reset. |
| `GET/POST /style/samples`, `DELETE /style/samples/{id}` | Review, add, remove samples. |

## Code

- `backend/personal_os/style.py` — schema, the prose filter, the repo, the analysis prompt.
- `backend/personal_os/context.py` — injection, and the per-chat `useStyle` switch.
- `backend/personal_os/app.py` — routes, the auto-learn hook at the end of a chat turn, and the doc-save hook.
- `src/renderer/src/components/StyleView.tsx` — the Voice panel.
- Tests: `backend/personal_os/tests/test_style.py` (unit), `backend/personal_os/tests/e2e_style.py` (HTTP + a real chat turn, LLM stubbed).
