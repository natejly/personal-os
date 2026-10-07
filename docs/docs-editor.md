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

The editor files took the `/docs` prefix, so FastAPI's own Swagger UI moved to `/api-docs`
(`docs_url` in `app.py`). Its OAuth redirect, which also defaults to a path
under `/docs`, is switched off.

## Reading first

A doc opens as its rendered page. Editing is a choice: the **Edit** button in the
toolbar, `⌘E`, or a double-click on the text; the same button (or `⌘E`) goes back to
reading. The choice is remembered per doc (`grain.docs.editing` in `localStorage`,
the newest 200 ids), while the editor-only / split / preview-only mode stays the one
global preference it always was and only applies while editing. A doc made by New or
Today's note opens in Edit with the caret at its end; an outline or
citation jump brings the editor up on their own.

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
`⌘I` page agent), which never reach the page. `Enter` continues
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
added by the Files view because they need app state: **Daily note**.

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

**Programmatic inserts and undo.** The slash menu, the wikilink picker and smart
paste all edit through one handle (`MarkdownEditorHandle`:
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
wrote in between. This is what `doc_edit`'s `append` uses. `accept` still rewrites
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

## Routes

New in this feature. The rest of `/docs` is unchanged.

| Route | Purpose |
| --- | --- |
| `POST /docs/daily` | Find or create the daily note. `{date?}`; returns `{doc, created}` |
| `GET /docs/{id}/backlinks` | Live docs linking here with `[[Title]]`, newest first, each with a snippet |
| `GET /docs/{id}/comments` | The doc's comment rows, flat, by creation; `include_resolved=false` drops resolved threads and their replies |
| `POST /docs/{id}/comments` | A new thread. `{body, quote, prefix, suffix, offset_hint}` |
| `POST /docs/comments/{cid}/replies` | A reply in the thread `cid` belongs to. `{body}` |
| `PATCH /docs/comments/{cid}` | `{body}` edits (own comments only, 403 for the assistant's); `{resolved}` closes or reopens the thread |
| `DELETE /docs/comments/{cid}` | A thread with its replies, or one reply |
| `PATCH /docs/{id}` | Also takes `typography` ({font, size, measure}; `{}` clears) |

Migration 10 (`doc_comments_typography`) adds the `doc_comments` table and the
`docs.typography` column; comments go with the doc when the trash purges it.

## Tests

```
backend/.venv/bin/python backend/tests/test_docs.py             # store, routes, tools
backend/.venv/bin/python backend/tests/test_doc_notes.py        # daily note, backlinks
backend/.venv/bin/python backend/tests/test_doc_comments.py     # migration 10, comment routes, typography, the comment tools
npm test                                                        # diff engine, maths, editor features
```

`npm test` includes `features/notes/*.test.ts` (slash filtering, wikilinks, task
toggling, outline, templates, export, the undo-preserving edit helpers)
and `lib/docPanel.test.ts`. The undo behaviour of
programmatic inserts is not covered by a test, since it needs a real Chromium
textarea.
