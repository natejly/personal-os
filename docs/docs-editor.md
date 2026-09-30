# Docs

Writing of your own, kept in the app: markdown with LaTeX, an editor beside a
live preview, a revision history, and an assistant that may revise a doc only by
*proposing* a diff you accept or reject.

## Three things that sound alike

| | what it is | where it lives |
| --- | --- | --- |
| **Documents** | files you upload, chunked and indexed so replies can quote them | `repos.Documents`, `/documents` |
| **Docs** | prose you write and keep editing | `docs.Docs`, `/docs` |
| **Notes** | canvas mode's sticky notes: a body and a colour, no history | `notes.Notes`, `/notes` |

Docs took the `/docs` prefix, so FastAPI's own Swagger UI moved to `/api-docs`
(`docs_url` in `app.py`). Its OAuth redirect, which also defaults to a path
under `/docs`, is switched off.

## The editing surface

A textarea sits on top of a highlighted mirror of the same text. The textarea
keeps the caret, the native undo stack, IME and spellcheck; the mirror behind it
paints markdown and maths. They must agree to the pixel, so both use the same
font metrics and padding and scroll together — the `--ed-*` custom properties in
`styles/docs.css` are the single source of those metrics.

The highlighter is line-oriented on purpose: every input line produces exactly
one output line, so the two layers cannot drift no matter what is typed.

Keys: `⌘B` bold, `⌘I` italic, `⌘K` link, `⇧⌘M` maths, `⇧⌘E` code, `Tab` /
`⇧Tab` indent, `⌘S` save now. `Enter` continues the list you are in and a second
`Enter` on an empty item ends it.

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

## Tests

```
backend/.venv/bin/python backend/tests/test_docs.py   # store, routes, tools
npm test                                              # diff engine, maths normaliser
```
