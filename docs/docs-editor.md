# Files

Writing of your own, kept in the app: markdown with LaTeX, an editor beside a
live preview, a revision history, and an assistant that may revise a file only by
*proposing* a diff you accept or reject.

## One name, three stores

The user-facing word is **file** for both the files you write and the files you
upload. The internal names below are unchanged.

| | what it is | where it lives |
| --- | --- | --- |
| **Uploaded files** | files you upload, chunked and indexed so replies can quote them | `repos.Documents`, `/documents` |
| **Editor files** | prose you write and keep editing | `docs.Docs`, `/docs` |
| **Sticky notes** | sticky notes on a space: a body and a colour, no history | `notes.Notes`, `/notes`; "Save to Files" on the widget copies one into Files |

The editor files took the `/docs` prefix, so FastAPI's own Swagger UI moved to `/api-docs`
(`docs_url` in `app.py`). Its OAuth redirect, which also defaults to a path
under `/docs`, is switched off.

## Reading first

A doc opens as its rendered page. Editing is a choice: the **Edit** button in the
toolbar, `⌘E`, or a double-click on the text; the same button (or `⌘E`) goes back to
reading. The choice is remembered per doc (`grain.docs.editing` in `localStorage`,
the newest 200 ids), while the editor-only / split / preview-only mode stays the one
global preference it always was and only applies while editing. A doc made by New or
Today's note opens in Edit with the caret at its end; dictation and an outline or
citation jump bring the editor up on their own.

## Type

The **Font** button (the `Aa` icon) sets the doc's face (Serif, Sans, Mono, Book),
size and line width; Book is a serif with a narrower default measure and more
leading. The choice is stored on the doc (`docs.typography`, JSON `{font, size,
measure}`, saved through `PATCH /docs/{id}`) and **Use default** clears it. Settings →
Behavior → Files holds the global default (`docTypography`) that docs without their own
choice follow; a doc's keys win over the global ones one by one. `typography.ts` turns
the result into `--doc-*` custom properties on the pane that holds both views: the
rendered page reads them directly and the editor reads them as the fallbacks of its own
`--ed-*` metrics, so an unset key leaves each view at its default.

## Comments

Select text in the rendered page and press the **Comment** bubble (in the editor, the
comment button in the toolbar takes the selection). The thread opens in the side
panel's **Comments** tab; replies, Resolve / Reopen, Edit (your own) and Delete live on
the card, and resolved threads are hidden behind a **Show resolved** toggle.

A thread's anchor is the quoted text plus up to 32 characters either side and the
offset it was made at, all taken from the rendered text (what the reader saw, not the
markdown). `comments.ts` finds it again by exact match first (several hits are told
apart by their context and distance from the hint), then by a context-scored fuzzy
match, so a thread follows its passage through rewording; nothing close enough marks
the thread **detached**, still listed, just not highlighted. Marks are painted with
the CSS highlight registry (`::highlight(doc-comment)`), which colours ranges without
wrapping the rendered DOM; a click on a mark opens its thread, and the quote on a card
scrolls the page to its passage.

The page agent sees the open-thread count in its context and has two tools:
`doc_comments` (safe) reads a file's threads and `doc_comment_reply` (writes) answers in
one as the assistant. It never resolves, edits or deletes a thread, and the assistant's
replies cannot be edited through the route either.

## The editing surface

A textarea sits on top of a highlighted mirror of the same text. The textarea
keeps the caret, the native undo stack, IME and spellcheck; the mirror behind it
paints markdown and maths. They must agree to the pixel, so both use the same
font metrics and padding and scroll together — the `--ed-*` custom properties in
`styles/docs.css` are the single source of those metrics.

The highlighter is line-oriented on purpose: every input line produces exactly
one output line, so the two layers cannot drift no matter what is typed.

Keys: `⇧⌘B` bold, `⇧⌘I` italic, `⌘K` link, `⌃⌘M` maths, `⇧⌘E` code, `Tab` /
`⇧Tab` indent, `⌘S` save now. These avoid the menu's own chords (`⌘B` sidebar,
`⌘I` page agent, `⇧⌘M` Meetings), which never reach the page. `Enter` continues
the list you are in and a second `Enter` on an empty item ends it with a blank line.

Editing autosaves 1.2 s after you stop typing. Consecutive saves by the same
author within three minutes fold into one revision, so the history reads as
sessions rather than as a keylogger.

## Maths

Inline is `$x^2$`, display is `$$ … $$`. remark-math only reads `$$` as a block
when the delimiters are alone on their lines, and both `$$x^2$$` on one line and
`$$x^2` with the fence below are things people write constantly — the first
renders small and mid-paragraph, the second loses the formula entirely to the
block's "meta" slot. `lib/mathBlocks.ts` normalises both into the shape
remark-math wants before parsing, leaving fenced code alone. Chat replies go
through the same pipeline (`MarkdownPreview`), so a formula looks the same
wherever it appears.

## Note-taking

The editor gained a set of features that make it usable as a notebook. None of
them changes what is stored: a doc is still one markdown string, and every
feature below reads or edits that string.

**Slash menu.** Type `/` at the start of a line or after whitespace and a menu
opens at the caret. `and/or`, `https://` and `/usr/bin` do not open it, because a
`/` mid-word is not a command. Typing filters the list (label prefix, then word
prefix, then keyword, then substring); `↑` `↓` move, `Enter` or `Tab` picks, `Esc`
closes it for that `/` only. The built-ins are Heading 1-3, Bullet list,
Numbered list, To-do, Quote, Code block, Table, Divider, Math block, Today's date
and Current time. A block command opens with a line break when text already
precedes the caret on its line, so it never lands mid-sentence. Three more are
added by the Files view because they need app state: **Record and summarize**,
**Dictate into note** and **Daily note**.

**Wikilinks.** `[[Title]]` and `[[Title|alias]]` link to another doc by title.
Typing `[[` opens a picker of your other docs (prefix matches first, then
substrings, recents first within each); picking one inserts `[[Title]]` and
swallows a `]]` that was already typed after the caret. Links are resolved when
rendered, not when written, so renaming a doc never rewrites the docs that
mention it, and a link whose title no longer matches simply stops resolving. A
title matches ignoring case and runs of spaces. If two docs share a title the one
in the same project and folder scope wins, then the most recently updated. In the
preview a link to a doc that exists opens it; a link to one that does not is drawn
differently, and clicking it creates a doc with that title beside the current one.

The **Links** tab of the side panel is the other direction: docs that link here,
each with the line that holds the link (`GET /docs/{id}/backlinks`). It is a scan,
not an index: SQL narrows to live docs whose text contains `[[`, then the links are
parsed in Python, skipping fenced code, since a link in a code sample is an example
and not a reference. The 160-character snippet is that line.

**Task checkboxes.** `- [ ]` items in the preview are real checkboxes. Clicking one
flips `[ ]` to `[x]` on that line of the source, through the same `editDoc`
autosave path as typing, so it appears in history like any edit. The preview parses
a copy of the text that `lib/mathBlocks.ts` has reshaped, so line numbers can
differ from the source; `taskLineMap` maps the n-th task line of one to the n-th of
the other and, if the counts ever disagree, leaves those checkboxes read-only
rather than flip the wrong line. A click on a line that is no longer a task does
nothing.

**Status bar.** Word count as before, plus a reading time (230 words a minute;
"< 1 min read" for a short note, nothing for an empty one) and, while text is
selected, the words and characters in the selection.

**Smart paste.** With a single line selected, pasting an `http(s)` URL makes
`[selection](url)` instead of replacing the selection. Parentheses in the URL are
percent-encoded so a `)` cannot end the link early. Any other paste is untouched.

**Templates and the New menu.** The **New** button makes a blank doc; its chevron
opens *Today's note* and a list of templates: Meeting notes, Daily log, Project
brief, One-on-one, Lecture notes, To-do list. A template is a title and some plain
markdown, filled with today's date, and the result is an ordinary doc.

**Daily note.** `POST /docs/daily` finds or creates the note for a date (`{"date":
"YYYY-MM-DD"}`, default today on this machine; anything else is a 400) and returns
`{doc, created}`. It lives in the personal tree under a `Daily` folder and its
title is the date itself, so `[[2026-10-02]]` links to it. A trashed note with that
title does not count: a fresh one is made rather than reviving something you
threw away. The route is declared above `/docs/{id}`, or `daily` would be read as a
doc id.

**Export.** The download button offers *Download Markdown* (the source, named from
the title with path separators and reserved characters removed), *Copy Markdown*,
and *Print or save as PDF*, which renders the markdown to static HTML and opens the
system print dialog from a hidden frame. Printing needs no extra dependency, and
for the same reason it does not load KaTeX: **maths prints as its source**.

**Programmatic inserts and undo.** The slash menu, the wikilink picker, smart
paste and dictation all edit through one handle (`MarkdownEditorHandle`:
`insertAtCaret`, `replaceRange`, `getSelection`, `getText`, `jumpToLine`). Assigning
`.value` or calling `setRangeText` would put the change behind the textarea's
native undo stack, so `⌘Z` would skip or discard it. The handle instead selects the
range and runs `document.execCommand('insertText')`, which Chromium records for
undo, and falls back to `setRangeText` only if the command is refused. That
reasoning follows documented Chromium behaviour and has not been exercised
against a running build; if a future Chromium drops `insertText`, inserts still
work but are no longer one `⌘Z` away. Whole-value edits (list continuation,
indent) are applied as the single smallest replacement that changes the text, for
the same reason.

## The side panel

One toggle in the title bar opens a panel in the right-hand column with five tabs.
Which tab is open, and whether the panel is open at all, is remembered per
browser profile (`grain.docs.panel` in `localStorage`); a malformed value falls
back to closed. Its width is the same resizable pane the History view used.

| tab | what it shows |
| --- | --- |
| **Outline** | the headings, indented by nesting (a jump from `#` to `###` indents once). Clicking one moves the caret there; from preview-only mode it switches to the split view first. While the editor is visible the heading holding the caret is marked. Headings inside fenced code and `$$` blocks are skipped |
| **Comments** | the doc's comment threads in document order, detached ones last. The badge counts open threads |
| **Recordings** | recordings made in this doc, with a Transcript and a Summary tab for the selected one. See below |
| **Links** | docs that link here |
| **History** | the revision list, unchanged. The count badge is the assistant edits waiting for review, whatever tab is open |

## Revisions and diffs

Every change is a row in `doc_revisions`, holding the text before and after.

- **You edit** → applied at once, recorded so you can walk back.
- **The assistant edits** → a `pending` revision. `docs.content` is untouched
  until you accept.

A pending revision is diffed against the document **as it stands now**, not
against the body the model saw. If you typed in between, that is the only
comparison that tells the truth about what accepting would do, and the card says
so (`stale`). Accepting rewrites the revision's `before` to the text it actually
replaced, so undoing afterwards restores what *you* had rather than what the
model was looking at.

`lib/diff.ts` is the diff engine: an LCS line diff with the common head and tail
trimmed first, then a word-level pass over del/add pairs that are similar enough
(≥ 0.4 shared tokens) to be a rewording rather than a replacement. That pass is
what lets a card mark only the words that moved. `DiffView` renders it unified
or side by side, and can copy the change out as a unified patch.

Restoring an old version is itself a revision, so it can be undone too.

**Append revisions.** A revision that only ever adds a section to the end of a doc
(`Docs.propose_append`) stores that section in its own `append` column instead of
freezing a whole new body. While it is pending, both the card and `accept` build
the result from the doc *as it stands*: the diff shows only the addition, typing
after the proposal does not make it stale, and accepting cannot overwrite what you
wrote in between. This is what a recording summary uses. `accept` still rewrites
`before` and `after` to what actually happened, so undo behaves as for any other
revision. Applied and rejected rows keep what was stored.

## Tools

| tool | danger | what it does |
| --- | --- | --- |
| `doc_list` | safe | titles, word counts, pending-edit counts |
| `doc_search` | safe | full-text over bodies, with snippets |
| `doc_read` | safe | the markdown, line-numbered, paginated |
| `doc_create` | writes | a new doc, applied directly |
| `doc_edit` | writes | **proposes** a revision for review |

`doc_edit` takes one of `edits` (find/replace), `append`, `content` or `title`.
A `find` must be copied verbatim from `doc_read` and match **exactly once**: zero
or several matches is refused with a hint rather than guessed at, which is what
keeps a model from silently editing the wrong paragraph. It always reports back
that the change is awaiting review, so the assistant tells you rather than
claiming it has written the doc.

`doc_create` applies directly — creating something new destroys nothing, and the
history still lets you undo it.

## Recording into a doc

Any doc can be recorded. The audio becomes a transcript you can read and search,
and a summary is proposed into the doc for you to accept. It reuses the Meetings
recorder end to end (consent, preflight, segmenting, transcription, redaction;
see [meetings.md](meetings.md)), so everything about *how* audio is captured and
transcribed is described there. This section covers what is different when the
recording belongs to a doc.

### The flow

The title bar has a **Record** control. Its main half starts *Record and
summarize*; the chevron adds *Dictate into note* and *Import audio file*. `/` in
the editor offers the first two as well. While a recording is live a slim bar under
the toolbar shows the state, elapsed time, how far behind the transcript is, any
capture warning, and Pause and Stop. The Recordings tab lists this doc's
recordings with the selected one's **Transcript** (timestamped, speaker-labelled,
follows the live edge until you scroll up, with find, copy and export as text or
markdown) and **Summary** (the proposal's state, a template and optional focus
line, and the action items, which can be added to your todos).

`POST /docs/{id}/recordings` creates a `meetings` row with `doc_id` and
`doc_mode`, then calls the same `MeetingService.start` as any meeting. If start is
refused (consent not given, a failing preflight blocker, or another recording
holding the microphone) the row it made is deleted, so a refusal leaves nothing
behind, and the 409 carries the same `blockers` list. The UI turns the first
blocker into a sentence and, for consent, opens the one-time modal. On a platform
that is not macOS the route is a 400 with the platform row's fix string.

**One recording at a time, app-wide.** The recorder pool holds one session. Record
is disabled on every other doc while one is running, with a line saying which,
and a start from anywhere else is a 409 naming the recording that holds the
microphone. The sidebar indicator reads "Recording doc" or
"Dictating" and clicking it opens the doc.

**Import audio file** creates a linked row (`POST /meetings` with `doc_id`) and
sends the file to `POST /meetings/{id}/import-audio`, which transcribes it on any
platform, under the same consent. When it finishes the doc gets a summary proposal
as a `record` recording would, provided `enhanceOnStop` is on (the default).

### Two modes

| | `record` | `dictate` |
| --- | --- | --- |
| Captures | the configured `sources` (mic by default, plus system audio if enabled) | microphone only, whatever `sources` says: it types what *you* say, never what the room says |
| Clip ceiling | `docSegmentSeconds`, default 10 | `dictationSegmentSeconds`, default 8 |
| On stop | a summary is proposed into the doc | nothing; the words were already typed |
| Transcript kept | yes, in the meetings tables | yes, in the meetings tables |

Both modes cut a clip at a pause rather than on a fixed grid. The recorder reads
the microphone in quarter-second steps and `meeting_vad.find_cut` closes the clip
once at least two seconds are in, speech was heard, and the last half second is
quiet (the same adaptive noise floor as the silence gate). The two config keys are
therefore ceilings: the longest a clip runs when nobody stops talking. A steady
tone or a clip that is silent throughout never reads as a pause, so it runs to
the ceiling, where the silence gate stores it as empty. Because clips have
variable length, the recorder measures each one's real offsets instead of
computing `seq * segmentSeconds`. This applies to native capture only; the ffmpeg
fallback keeps its fixed grid. An ordinary meeting is unchanged.

Dictation inserts each finished mic clip at the caret through the editor handle,
so it is one undo step and takes the normal autosave path. Spacing and capitals
follow the text before the caret: a clip that starts a sentence is capitalised, a
clip that continues one is left as the recogniser wrote it (lower-casing would
also lower-case names), and a clip that is exactly "new line" or "new paragraph"
becomes that break. Those words inside a sentence are words you said. Clips are
typed once each and in order, never past one that is still transcribing. What has
been typed is tracked per recording, outside the view, and a clip counts as typed
only once the insert ran: switch to another doc or leave the Files view and the
clips spoken meanwhile are held, then typed in order when that doc's editor is
back. A dictation only ever types into its own doc. Clips arriving up to two
minutes after Stop are still typed. A preview-only view has nowhere to put words,
so dictation switches it to split.

Dictation never takes focus. If you have clicked into another field (the search
box, the title, a chat), the clip is inserted at the editor's last selection
without focusing it, so your keystrokes stay where you put them. That insert is
not on the native undo stack; an insert made while the editor has focus is.

A spoken price arrives as `$12`, and two of them in one paragraph are the shape
the preview reads as inline maths. Dictated text and recording summaries escape a
`$` that is followed by a digit, so amounts stay amounts.

### What is stored where

A recording is a `meetings` row with three extra columns (`doc_id`, `doc_mode`,
`summary_revision_id`) and its `meeting_segments`. The transcript lives there and
**not in the doc body, and not in the doc's search index**. That is deliberate:
a transcript is other people's speech, and anything in `docs.content` is
indistinguishable from what you wrote, is retrieved by `doc_search` and
`search_documents` as yours, and reaches every chat without the taint that
`meeting_search` and `meeting_read` put on a run. Keeping it in the meetings
tables keeps those tools as the only way in, and they stay tainted. What *does* go
into the doc is the summary, and only when you accept it.

- **Proposal, never applied.** On stop (with `enhanceOnStop` on, the default), `summarize_into_doc` makes one model call
  and proposes the result as an append revision with `tool="recording_summary"`.
  It is not applied whatever your doc edit mode is, for the reason above: a model's
  paraphrase of a call, once accepted, is your text. The section is headed
  `## Recording summary (2026-10-02 14:30, 12 min)`, and the model may use `###`
  inside it but never `#` or `##`. The prompt treats the transcript as quoted
  speech, forbids invented names, numbers and commitments, and attributes only to
  `[you]` and `[them]` unless speaker names exist.
- **Resolved at accept time.** See *Append revisions*. Text you typed or dictated
  after Stop survives the accept.
- **Credentials are scrubbed twice**: in the transcript as each clip is stored, and
  again in the summary, headline and action items, since a model can echo a secret
  in a paraphrase. Only credential rules; email and phone are left alone, as in
  Meetings.
- **A failure writes nothing.** If the model is down there is no revision and no
  fallback. Meetings' own enhance pass degrades to your notes plus the raw
  transcript, which is fine for a reviewable meeting revision and wrong for a doc,
  where verbatim third-party speech would land in untainted text. The error is
  shown on the Summary tab, `POST /meetings/{id}/summarize` answers 200 with
  `error` set so the UI can offer it again, and a recording with no spoken words
  says "Nothing was said" instead of calling the model.
- **One pending summary per recording.** Running it again (different template or a
  focus line) rejects the earlier pending proposal rather than stacking two in the
  History tab. An existing pending proposal is returned as is unless you ask for a
  new one.
- **Action items** from the summary are stored on the recording and promoted to
  todos from the Summary tab, the same as a meeting's.
- **Search and tools.** `meeting_search` and `meeting_read` find doc recordings,
  tainted as before; hits carry `doc_id`. `meeting_list` includes them with
  `doc_id` and `doc_title` so "what did I record in my Q3 plan" resolves. The
  summary is searchable as part of the doc once accepted.
- **Not elsewhere.** Doc recordings are left out of the Meetings rail
  (`GET /meetings` hides them unless `include_docs=true`, or `doc_id=` selects one
  doc's), out of the "Recent meetings" block injected into chats, and out of the
  Meetings sidebar badge, which counts meeting revisions and not doc ones. A doc
  that has a `record` recording is not banked as a writing-style sample, because
  its accepted summaries are other people's words. A `dictate` recording does not
  block that, since the words are yours.

### Lifetime

Trashing a doc hides its recordings from every read, search and tool (the check is
a join on `docs.deleted_at`) and restoring the doc brings them back. Purging a doc
deletes them: row, segments, search entry and the audio directory, through
`Docs.on_delete`, because `doc_id` is plain text with no foreign key and neither
the FTS row nor the wavs would be covered by a cascade. Deleting a recording from
the panel deletes its transcript; a summary already accepted stays in the note.
Audio follows the Meetings rules: each wav is deleted once it transcribes, unless
Keep audio is on or the clip failed.

### Updates in the UI

A segment settling, a status change and a summary landing are published as
`recording` events on `GET /events` (each carries `meeting_id`, `doc_id` and
`doc_mode`, and a segment event carries the stored row). The recorder's worker
thread calls the publisher, so off-loop calls are handed to the event loop. A
stream can drop, so the panel also polls `/meetings/{id}/segments` every two
seconds, folding both sources by segment id and never letting a late poll move a
settled row back to unfinished. Because a clip's text arrives by `UPDATE`, which
does not change its rowid, the `?since=` cursor cannot deliver it; while any held
clip is unsettled the poll reloads the whole tail.

### Limits

- **Latency is a clip, not a word.** Text appears when a clip is transcribed, so
  the transcript runs at least one clip behind: up to the ceiling (10 s, or 8 s for
  dictation), usually less because clips close at a pause, plus transcription
  time. The bar says "about 10s behind", and "at least" when clips are queued,
  because the per-clip transcription time is not known. Dictation has the same
  latency: you speak a sentence, pause, and it appears.
- **macOS only for capture.** Import and everything after the transcript work
  anywhere.
- **One recording at a time, app-wide**, across docs and meetings together.
- **Pause discards audio** rather than holding it; it is not a way to keep a
  clip for later.
- **Speaker attribution is channel-level** (`[you]`, `[them]`), as in Meetings.
- **Cutting on silence uses an energy threshold.** In a noisy room the pause may
  never register and clips run to the ceiling. Nothing is lost; they are just
  longer.
- **Undo of dictated and other inserted text relies on Chromium's `insertText`**
  (see *Programmatic inserts and undo*).
- **Print renders maths as source.**

## Routes

New in this feature. The rest of `/docs` is unchanged.

| Route | Purpose |
| --- | --- |
| `POST /docs/daily` | Find or create the daily note. `{date?}`; returns `{doc, created}` |
| `GET /docs/{id}/backlinks` | Live docs linking here with `[[Title]]`, newest first, each with a snippet |
| `POST /docs/{id}/recordings` | Create a recording linked to the doc and start it. `{mode: "record" \| "dictate", template?, title?}`. 409 with `{blockers}` when consent or preflight fails or another recording is live |
| `GET /docs/{id}/recordings` | The doc's recordings, newest first, each with `summary_state` (`none`, `pending`, `applied`, `rejected`) |
| `POST /meetings/{id}/summarize` | Propose a summary of a doc recording into its doc. `{template?, focus?, force?}`. 400 for a recording with no doc. A model failure is a 200 with `error` |
| `GET /meetings?doc_id=&include_docs=` | Doc recordings are hidden unless asked for |
| `GET /events` | Carries `recording` events as well as the existing ones |
| `GET /docs/{id}/comments` | The doc's comment rows, flat, by creation; `include_resolved=false` drops resolved threads and their replies |
| `POST /docs/{id}/comments` | A new thread. `{body, quote, prefix, suffix, offset_hint}` |
| `POST /docs/comments/{cid}/replies` | A reply in the thread `cid` belongs to. `{body}` |
| `PATCH /docs/comments/{cid}` | `{body}` edits (own comments only, 403 for the assistant's); `{resolved}` closes or reopens the thread |
| `DELETE /docs/comments/{cid}` | A thread with its replies, or one reply |
| `PATCH /docs/{id}` | Also takes `typography` ({font, size, measure}; `{}` clears) |

Migration 10 (`doc_comments_typography`) adds the `doc_comments` table and the
`docs.typography` column; comments go with the doc when the trash purges it.

`POST /meetings/{id}/enhance` is a 400 for a doc recording: the doc is its notes,
and `summarize` is its pass. `POST /meetings` accepts `doc_id` and `doc_mode`, which
is how an audio import gets its target row. `GET /meetings/status` reports `doc_id`,
`doc_mode` and `segment_seconds` for the live recording.

## Tests

```
backend/.venv/bin/python backend/tests/test_docs.py             # store, routes, tools
backend/.venv/bin/python backend/tests/test_doc_notes.py        # daily note, backlinks
backend/.venv/bin/python backend/tests/test_doc_recordings.py   # the link, the proposed summary, trash and purge
backend/.venv/bin/python backend/tests/test_doc_comments.py     # migration 10, comment routes, typography, the comment tools
cd backend && uv run --with pytest pytest tests/test_meeting_recorder.py tests/test_meeting_vad.py tests/test_doc_recordings.py -q
npm test                                                        # diff engine, maths, editor features, recording logic
```

`npm test` includes `features/notes/*.test.ts` (slash filtering, wikilinks, task
toggling, outline, templates, export, the undo-preserving edit helpers),
`features/docrec/*.test.ts` (segment folding and settle rules, dictation spacing and
ordering, the lag label, blocker wording, transcript search and export) and
`lib/docPanel.test.ts`. The backend tests use a stub model and write segments by
hand through the recorder's own callbacks, so they need no microphone. Nothing
automated asserts that real audio becomes real words, and the undo behaviour of
programmatic inserts is not covered by a test, since it needs a real Chromium
textarea.
