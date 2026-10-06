# Docs / Files codemap, for the notes + in-doc recording build

Read-only research, branch `worktree-docs-notes-dictation` (main = 11b1a54). Everything below was read from
source unless marked "(inferred)". Paths are relative to the repo root. Line numbers are from this checkout.

## 0. The one thing to know first: a notepad that listens already exists

`Meetings` (docs/meetings.md, `backend/personal_os/meetings.py`, `meeting_recorder.py`, `stt.py`, `meeting_notes.py`,
`src/renderer/src/components/MeetingsView.tsx`) is already "type notes while it records, afterwards a proposal
fills in the transcript, reviewed as a diff". It has native mic + system-audio capture, segmented STT, a stored
transcript, an LLM "enhance" pass that creates a pending revision, action items, and a per-meeting recorder bar.
Its revisions deliberately mirror `doc_revisions` so `DiffView` renders them unchanged.

What it is not: it is a separate table family (`meetings`, `meeting_segments`, `meeting_revisions`,
`meeting_action_items`), a separate view (`meetings`, hidden by default), and has no link to a `docs` row. A meeting's
`notes` column is the user's text and its `enhanced` column is the accepted model output; a doc has one `content`.

Consequence for the design: do NOT build a second recorder/STT stack. Either (a) attach a meeting row to a doc and
reuse `MeetingService` end to end (section 6A, recommended), or (b) extract the pieces. There is zero browser-side audio
code in the renderer (`getUserMedia` / `MediaRecorder`: no hits in `src/`); all capture is Python-native and macOS-only.

## 1. Data model

All doc tables are created in `backend/personal_os/docs.py` (`SCHEMA`, lines 23-97), not in `db.py`/`migrations.py`.
`docs` and the other tables are `CREATE TABLE IF NOT EXISTS` inside `Docs.__init__` (line 162). `db.py` and
`migrations.py` do not mention `docs` at all. Post-release columns are added by an additive `ALTER TABLE` loop in
`Docs.__init__` (lines 169-173, the `deleted_at` / `deleted_with` pair is the precedent). `_migrate_folder_scope`
(line 177) is the precedent for a rebuild migration.

### `docs`
| column | type | notes |
| --- | --- | --- |
| id | TEXT PK | `new_id()` |
| project_id | TEXT FK projects ON DELETE SET NULL | NULL = Personal. A doc's "scope" is exactly this |
| title | TEXT NOT NULL DEFAULT 'Untitled' | capped 200 chars on write |
| content | TEXT NOT NULL DEFAULT '' | markdown + `$..$` maths |
| folder | TEXT NOT NULL DEFAULT '' | path string "Work/Research", max depth 8, segment 60 (`folder_path`, line 118) |
| starred | INTEGER 0/1 | list sorts starred first |
| created_at, updated_at | REAL | epoch seconds |
| deleted_at, deleted_with | REAL, TEXT | added by ALTER; soft delete (trash.py). `deleted_with` is only set by project trash; docs are demoted not trashed, so effectively NULL |

Index: `idx_docs_updated(updated_at DESC)`. There are no tags, no `kind`, no `meta` JSON, no `pinned`, no `parent_id`.
Any new per-doc attribute needs an additive `ALTER` in `Docs.__init__`.

### `doc_revisions` (line 36)
`id, doc_id (FK CASCADE), before, after, title_before, title_after, summary, author ('user'|'assistant'), tool
(nullable; name of proposing tool), status ('applied'|'pending'|'rejected'), created_at, resolved_at`.
Indexes `idx_rev_doc(doc_id, created_at DESC)`, `idx_rev_pending(status, created_at DESC)`. `author` is a free TEXT
column (comment says two values) with no CHECK, so a third value is storable, but `DocRevision.author` in
`src/shared/types.ts:1608` is typed `'user' | 'assistant'`. A pending revision from recording should use
`author='assistant'` and a distinguishing `tool` string (e.g. `"doc_recording"`).

### `doc_folders` (line 60)
`(scope TEXT '', path TEXT, created_at REAL, PRIMARY KEY(scope, path))`. Scope '' = personal, else project id.
Folder rows exist so empty folders survive; `Docs.folders()` (line 409) also derives rows from `docs.folder` and ancestors.

### Search / retrieval tables
- `docs_fts` (fts5 `title, content, doc_id UNINDEXED`, porter unicode61): whole-doc FTS used by `Docs.search` (302) and the `doc_search` tool.
  Rebuilt by `_reindex` (195) on every create/save/update_meta/accept. No triggers anywhere; the cursor-taking `_reindex` is called by hand.
  It is NOT filtered by `deleted_at` in the FTS query, but the join to `docs` re-checks `deleted_at IS NULL`.
- `doc_chunks(id, doc_id, idx, heading, text)`, `doc_chunks_fts(text, chunk_id, doc_id)`, `doc_chunk_embeddings(chunk_id, doc_id, model, dim, vec)`,
  `doc_chunk_state(doc_id, hash)`: the retrieval pipeline over doc bodies. `_index_chunks` (200) hashes `title\0content`; same hash = skip.
  Chunks come from `chunk_blocks(markdown_blocks(content), title=title)` (`chunker.py`, `extract_text.py`). New chunks fire `self.on_chunks(doc_id)`,
  wired at `app.py:458` to `retriever.schedule_docs(settings)`, which embeds in the background (`retrieval.py:131`, `STORES["docs"]` at `retrieval.py:30`).
  `backfill_chunks()` runs at init and at `app.py:3861`.
  Hits come back via `Docs.chunk_search` (233) tagged `source:"doc"`; `search_documents` tool (`tools.py:704-718`) merges them with uploaded-file hits.
  Implication: anything you put in `docs.content` (including a pasted transcript) is chunked and embedded. A very long transcript inside the body bloats the index; keep it in a separate table (as meetings do) if you do not want that.
- `Docs.list(q=)` (249) uses `LIKE %q%` on title+content, NOT FTS. The tree search box (`DocTree` `query`) therefore does substring matching and scans every body.

### What a `Doc` looks like over the API (`src/shared/types.ts:1559`)
List row (`GET /docs`, from `Docs.list`, docs.py:249):
```
{ id, project_id|null, title, folder, starred:0|1, created_at, updated_at, size:int(chars), pending:int,
  preview:string(first 240 chars), words:int }            // no `content`
```
Full doc (`GET/PUT/PATCH /docs/{id}`, `Docs.get` line 277): every `docs` column (including `deleted_at`, `deleted_with` as null) plus
```
content:string, words:int, pending: Revision[]            // only status='pending', oldest first
```
Revision view (`Docs._rev_view`, line 514): every `doc_revisions` column plus
`stat:{added,removed}` (vs its own `before`), `stale:boolean` (pending and current != before) and `stat_vs_current:{added,removed}|null`.
`GET /docs/revisions/{id}` adds `patch` (unified diff string). `GET /docs/{id}/revisions` returns `before`/`after` bodies for each row (up to 100) so it is heavy for big docs.

`DocFolder` (types.ts:1579): `{scope, path, name, parent, docs, docs_deep}`.

## 2. Routes (all in `backend/personal_os/app.py`, block starts at line ~4880, "docs: long-form markdown notes")

Pydantic bodies: `DocIn` (4886: `title, content, folder, project_id`), `DocSave` (4893: `content?, title?, summary`),
`DocMetaPatch` (4899: `title?, folder?, starred?, project_id?, clear_project, scope?`), `FolderIn` (4925: `path, scope`), `FolderRename` (4932: `path, new_path, scope`).
Helpers: `sid()` (592) normalises '' / personal / null to None and 'all' to ALL; `fscope()` (582); `wsid()` (601) validates a writable project id.

| line | method path | handler | request | response / notes |
| --- | --- | --- | --- | --- |
| 4912 | GET /docs | `list_docs` | `?project_id=all\|<id>\|personal&q=` | `Doc[]` list rows |
| 4918 | GET /docs/pending | `docs_pending` | | `{pending:int}` sidebar badge (count of pending revisions on non-deleted docs) |
| 4938 | GET /docs/folders | `list_doc_folders` | | `DocFolder[]` |
| 4943 | POST /docs/folders | `create_doc_folder` | `{path, scope}` | `DocFolder[]`; 400 on ValueError |
| 4951 | PATCH /docs/folders | `rename_doc_folder` | `{path, new_path, scope}` | `DocFolder[]` (rename = move) |
| 4960 | DELETE /docs/folders | `delete_doc_folder` | `?path&delete_docs&scope` | `DocFolder[]`; delete_docs=true soft-deletes docs |
| 4968 | POST /docs | `create_doc` | `DocIn` | `FullDoc` (author is always 'user' via this route) |
| 4973 | GET /docs/{id} | `get_doc` | | `FullDoc` or 404 |
| 4981 | PUT /docs/{id} | `save_doc` | `DocSave` | `FullDoc`; autosave. Also banks a style sample if `learnStyle` (setting) is on |
| 4993 | PATCH /docs/{id} | `patch_doc` | `DocMetaPatch` | `FullDoc`; metadata only, no revision. `scope` wins over `project_id`/`clear_project` |
| 5008 | DELETE /docs/{id} | `delete_doc` | | `{ok:true}`; calls `trash.trash("doc", id)` (soft delete) |
| 5014 | GET /docs/{id}/revisions | `doc_revisions` | `?limit=100` | `DocRevision[]`, newest first |
| 5021 | GET /docs/revisions/{rev_id} | `doc_revision` | | revision + `patch` |
| 5029 | POST /docs/revisions/{rev_id}/accept | `accept_revision` | | `FullDoc`; 404 if not pending |
| 5037 | POST /docs/revisions/{rev_id}/reject | `reject_revision` | | `FullDoc` |
| 5045 | POST /docs/revisions/{rev_id}/restore | `restore_revision` | | `FullDoc`; writes a new applied revision |

Ordering rule: FastAPI matches in declaration order, so literal sub-paths (`/docs/pending`, `/docs/folders`) are declared above `/docs/{id}`
(comment at 4937). `GET /docs/revisions/{rev_id}` (5021) is declared after `GET /docs/{id}/revisions` (5014); that is safe only because
`/docs/{id}/revisions` needs a literal second segment (`GET /docs/revisions/revisions` would be ambiguous, a non-issue). Any new
`/docs/<literal>` route must go above line 4973. A new `/docs/{id}/<something>` route can go anywhere after it (e.g. `/docs/{id}/recordings`).
`app.py` sets `docs_url="/api-docs"` (line ~179) so Swagger does not own `/docs`.

Other app.py touch points: `docs = Docs(db)` (101), `trash = Trash(db, todos, docs)` (342), `docs.on_chunks = ...` (458),
`docs.backfill_chunks()` (3861), cowork promotion `dest == "doc"` / `"doc_append"` (6530-6550; this is the existing non-chat caller of `docs.create`/`docs.propose`, see section 6).
Deleting is soft: `Trash.trash("doc", id)` sets `docs.deleted_at`; `trash.py:124` hard-purges via `Docs.delete` (docs.py:402), which deletes the row (cascading revisions/chunks) and FTS rows by hand. `Docs.get/list/find/search/chunk_search/folders/pending_count` all filter `deleted_at IS NULL`. `Docs.save`/`update_meta`/`propose` do NOT check `deleted_at` (they call `get`, which does, so they return None for trashed docs).

### Settings
`llm.DEFAULT_SETTINGS` (`llm.py:72`) is the whitelist: `put_settings` (`app.py:697`) only accepts keys present there and not in `PRIVATE_SETTINGS` / `SETTINGS_READ_ONLY` (`app.py:627`, currently `{"activity","googleTasksSync","googleTodoCalendar","meetings"}`). Doc-related keys today: `docEditMode` (`llm.py:116`, `'review'|'apply'`, typed in `types.ts:1069`) and `learnStyle`. A new key needs: an entry in `DEFAULT_SETTINGS`, the field in the `Settings` interface in `src/shared/types.ts`, and (if its shape is nested and deep-merged) a dedicated route instead of `PUT /settings`, which replaces whole keys.

## 3. Tools (backend/personal_os/tools.py, `_register_docs` at line 1783)

`doc_list` (1796), `doc_search` (1804), `doc_read` (1810, line-numbered, paged by `from_line/to_line`), `doc_create` (1821, applies directly, author "assistant"), `doc_edit` (1833).
`doc_edit` forms: `edits[{find,replace}]` (each `find` must match exactly once), `append`, `content`, `title`; it calls `self.docs.propose(d["id"], new, summary, tool="doc_edit", title_after=retitle)` (1868).
If `ctx.settings.docEditMode == "apply"` and not `ctx.proposal_only`, it immediately calls `self.docs.accept(rev["id"])` (1873-1879).
`doc_create` and `doc_edit` are in `PROMPT_WRITES` (`tools.py:65`), i.e. untrusted content in a run triggers asks. Tool group string is `"docs"`, danger `"writes"` for the two writers.
There is no tool that touches recordings or transcripts for docs; the meetings tools are `meeting_list`, `meeting_search`, `meeting_read` (read-only, `taints=True` because transcripts are third-party content). If a doc holds a transcript, its text reaches the model via `doc_read` untainted, unlike `meeting_read` (a design decision to make).

## 4. Frontend: store, API client, flows

### API client (`src/renderer/src/lib/api.ts:614-640`, `api.docs`)
`list(s='all', q='')`, `get(id)`, `create({title,content,folder,project_id})`, `save(id,{content,title,summary})` (PUT), `patch(id,{...})`,
`move(id, scope, folder)` (PATCH `{scope, folder}`), `delete(id)`, `pending()`, `folders()`, `createFolder(path, scope)`, `renameFolder(path,newPath,scope)`,
`deleteFolder(path, deleteDocs, scope)`, `revisions(id, limit=100)`, `revision(revId)`, `accept/reject/restore(revId)`. `api.meetings.*` is at line 643. Types imported at lines 10 (`Doc, DocFolder, FullDoc, DocRevision`).

### Store (`src/renderer/src/store.ts`, zustand)
State declarations (interface) / initial values:
| field | decl line | init line | meaning |
| --- | --- | --- | --- |
| `docs: Doc[]` | 197 | 1136 | list rows, all scopes (`refreshDocs` always asks scope `'all'`) |
| `activeDoc: FullDoc\|null` | 198 | 1137 | the open doc |
| `docTabs: string[]` | 200 | 1138 | open tab ids |
| `docRevisions: DocRevision[]` | 201 | 1139 | history of the active doc |
| `docFolders: DocFolder[]` | 203 | 1140 | |
| `docsPending: number` | 210 | 1142 | sidebar badge |
| `docMode: 'edit'\|'split'\|'preview'` | 211 | 1143 | default `'split'`; not persisted |
| `docDraft: string\|null` | 213 | 1144 | typed-but-unsaved body |
| `docTitleDraft: string\|null` | 215 | 1145 | |
| `docSaving: boolean` | 216 | 1146 | |
| `expandedFolders: string[]` | (near 203) | | tree fold state, `localStorage 'grain.docs.expandedFolders'` (store.ts:78) |
Module-level (not state): `SAVE_DEBOUNCE_MS = 1200` and `let saveTimer` (store.ts:528-529). `View` union at line 26 includes `'docs'`. `DocMode` at line 32.

Actions (decl lines 456-485, impls 1699-1912): `refreshDocs` (1699), `refreshDocsPending` (1700), `openDoc` (1705), `closeDocTab` (1717), `createDoc` (1728),
`editDoc` (1745), `editDocTitle` (1751), `flushDoc` (1757), `setDocStar` (1791), `moveDoc` (1795), `refreshDocFolders` (1806), `createDocFolder`, `renameDocFolder`,
`deleteDocFolder`, `toggleFolder`, `expandTo`, `deleteDoc` (1864), `setDocMode` (1872), `refreshDocRevisions` (1873), `acceptRevision` (1880), `rejectRevision` (1892), `restoreRevision` (1901).
Cross-references: `setView` (1276-1282) flushes the doc when leaving `docs` and refreshes docs + pending when entering; init refresh at 836, 869, 1239, 1359, 1399, 1409.

Autosave / flush flow:
1. `MarkdownEditor.onChange` -> `editDoc(content)` sets `docDraft` and (re)arms a 1.2 s timer; `editDocTitle` does the same for `docTitleDraft` (same timer, so title and body share one debounce).
2. `flushDoc` (1757) clears the timer, computes `content` = draft if it differs from `activeDoc.content` and `title` = trimmed draft if non-blank and different, returns early if nothing changed, sets `docSaving`, calls `api.docs.save`.
3. On success it keeps anything typed while the request was in flight (`newer` check compares current draft to the flushed draft) and merges server metadata; then `refreshDocs()` + `refreshDocRevisions()`.
4. Flush triggers: ⌘S in the editor, title input blur, `pagehide` and window `blur` and unmount in `DocsView` (56-65), `openDoc` of a different doc, `closeDocTab`, leaving the view via `setView`, and before `acceptRevision` / `restoreRevision`.
5. Server side `Docs.save` (346) no-ops on identical content/title, otherwise updates, reindexes, and either folds into the last `applied` revision (same author, < `COALESCE_SECONDS`=180 s, last summary not "Restored...") or inserts a new one with summary `summary or "Edited"`.

Revision accept/reject flow:
- Pending revisions arrive embedded in `activeDoc.pending` (from `Docs.get`) and render as `<DiffView revision current onAccept onReject>` cards in `DocsView` (`.doc-review` block, lines 245-256, shown only while the history panel is closed) or inside the history aside (286-289).
- `acceptRevision` (1880): `flushDoc()` first, then `api.docs.accept` -> `Docs.accept` (554) which sets the revision `applied`, REWRITES `before` to the text actually replaced, writes `docs.content`/`title`, reindexes, returns the doc; the store sets `activeDoc`, clears `docDraft`, toasts, refreshes revisions/list/pending.
- `rejectRevision` (1892) -> `Docs.reject` (573) sets status `rejected`; no content change. Rejected rows stay in the history list but `DocsView` filters `status !== 'pending'` into `applied` (line 71), which includes `rejected` rows.
- Multiple pending revisions can coexist and each is diffed against the CURRENT body. Accepting one makes the others `stale` (their `before` no longer equals the body) but they remain acceptable; unlike meetings, docs do not supersede siblings.

### Views / registration
- `App.tsx:222` `{view === 'docs' && <DocsView />}`; `App.tsx:6` import.
- Sidebar entry `Sidebar.tsx:50` `{ view: 'docs', label: 'Files', icon: <Files/> }`, count at 160, pending badge at 172, project groups list docs at 115-132 and 216.
- `modules.ts` has `OPTIONAL_VIEWS` / `HOME_MODULES`; `docs` is not in `OPTIONAL_VIEWS` (always shown). `meetings` is in it and `llm.DEFAULT_SETTINGS.hiddenViews` hides it by default (`llm.py:110`).
- Menu / shortcuts: `src/main/index.ts:170-195` builds the View menu; each item `sendMenu('view:<x>')` and `store.ts:775-779` maps `view:<x>` to `setView(x)`. There is NO menu accelerator for Files/docs (⌘1-⌘9, ⌘0, ⇧⌘M meetings, ⇧⌘K cowork are taken; `src/main/shortcuts.ts` is global-hotkey code, not relevant). `menuShortcuts.test.ts` covers the wiring. In-editor chords (⌘B, ⇧⌘I, ⇧⌘E, ⇧⌘M, ⌘K, ⌘S) are handled in `MarkdownEditor.onKeyDown`, not by the menu. ⌘I is the app-wide "Ask About This Page" (menu-owned, never reaches the page), hence ⇧⌘I for italic.
- Page agent: `DocsView` calls `usePageContext` (lines 101-115) publishing the doc's current text (`body`, draft included) to the ⌘I panel, with `refs:[{kind:'doc',id,name}]`. Anything you add that the assistant should see about the doc (e.g. transcript) belongs in this `detail` string.
- Canvas: `canvas/widgets/documents.tsx` shows uploaded `Document`s, NOT docs; `canvas/widgets/note.tsx` is the sticky `notes` table. Docs have no canvas widget (only drag mime `DOC_MIME` in `DocTree`). Skip.

## 5. Editor internals

### Components
- `components/MarkdownEditor.tsx` (278 lines): textarea over highlighted `<pre>` mirror. Props `EditorHandleProps` (line 11): `value`, `onChange(next)`, `onSave?`, `placeholder?`, `readOnly?`, `wrap?` (default true), `onScrollFraction?(f)`. It is a function component with NO `forwardRef`, no imperative handle, no `onSelectionChange`, no ref to the textarea exposed. It also renders a line-number gutter (`.md-gutter`) and a status bar (`.md-status`: Ln/Col, line count, word count, key hints).
- It is shared by `DocsView` (263-269) and `MeetingsView` (321). Changes to props affect both.
- Highlight layer: `highlight()` (30) is line-oriented; each input line becomes exactly one output line (that invariant keeps mirror and textarea aligned, see docs/docs-editor.md). `inline()` (62) handles heading/quote/rule/list(with `[ ]` task)/table prefixes; `span()` (78) handles maths, code, links, strong, em, strike. Any new inline token must not change line count or width-affecting markup (no padding/margin, no font-size/weight change that alters advance width in the mirror, no extra DOM text).
- Metrics: both layers use the `--ed-*` CSS custom properties in `styles/docs.css` as the single source of truth; do not set font-size/padding on one layer only.
- `components/MarkdownPreview.tsx` (100 lines): react-markdown + remark-gfm + remark-math + rehype-katex + rehype-highlight; `normalizeMathBlocks` first; custom `pre` (chart/mermaid/interactive/html/svg fences) and `a`/`img` (`SAFE_MD`, external links via `window.open`, remote images downgraded to links, only `data:image/` rendered). Props `{source, streaming?}`. No `input` override, so GFM task-list checkboxes render as disabled inputs (inferred from react-markdown defaults + `SAFE_MD` containing only `a` and `img`). No heading ids/anchors, no outline.
- `components/DiffView.tsx` (187): props `revision: DocRevision`, optional `current` (diff base for pending), `onAccept`, `onReject`, `onRestore`; unified/split toggle, copy-as-patch. `lib/diff.ts`: `diffLines`, `diffStat`, `hunks`, `toUnified`, `wordDiff`. It renders any object satisfying `DocRevision`, which is why `MeetingRevision extends DocRevision` works (types.ts:2274).
- `components/DocTree.tsx` (394) + `lib/docTree.ts` (`buildGroups`, `flattenGroups`, `scopeOf`, `canDropDoc`, `canDropFolder`, ...): groups = Personal + one per project, then nested folders; drag/drop moves docs and folders (`DOC_MIME`/`FOLDER_MIME`); star toggle at DocTree:253; list sort is starred first then `updated_at DESC` (server). Tree search box feeds `refreshDocs(query)` (DocsView:54).
- `lib/mathBlocks.ts`: `normalizeMathBlocks(src)` (line 19).

### Key handling (`MarkdownEditor.onKeyDown`, line 184)
⌘S -> `onSave`; ⌘B bold `**`; ⇧⌘I italic `*`; ⇧⌘E code `` ` ``; ⇧⌘M maths `$`; ⌘K link (`[sel]()` with caret inside the parens); Tab / ⇧Tab indent/outdent selected lines (two spaces); Enter on a list item continues it (bullets, numbers incremented, `[ ]` task reset to unchecked), second Enter on an empty item removes the marker. `readOnly` short-circuits everything except ⌘S.

### Where a toolbar / slash command / insert-at-caret hook attaches
- A formatting toolbar: `DocsView.tsx:168-243` (`.doc-toolbar`) holds title input, project select, folder select, save state, `.seg` mode switch, link/unlink, save, history. A new button group fits between the mode `.seg` (228-232) and the save button (238). Styles at `docs.css:159+`. For a toolbar that edits text it needs a handle into the editor, which does not exist yet (see next point).
- Slash command: needs `onKeyDown`/`onChange` interception inside `MarkdownEditor` (the textarea element is private to it) plus a popup positioned from caret coordinates; the mirror can be used to measure caret position (inferred; no helper exists today, `trackCaret` only yields line/col).
- Programmatic insert: the one existing mutation path is the local `apply(next:{value,start,end})` (line 175), which does `el.setRangeText(next.value, 0, el.value.length, 'preserve')` (replaces the WHOLE value), `el.setSelectionRange`, then `onChange(el.value)`. The code comment claims this keeps native undo. That is doubtful: in Chromium (Electron), programmatic `setRangeText`/`value` writes generally do not create undo entries, and writing the whole value as one range makes any later undo coarse (inferred from known Chromium behaviour; not verified in this repo, test in the running app before relying on it). The reliable way to insert at the caret while keeping the native undo stack is `el.focus(); document.execCommand('insertText', false, text)` (deprecated but supported in Chromium/Electron), optionally preceded by `el.setSelectionRange(pos,pos)`; that fires a real `input` event so the React `onChange` runs. To expose it, add `forwardRef` with `useImperativeHandle` returning `{ insertAtCaret(text), replaceRange(a,b,text), focus(), getSelection() }` and keep `apply` for the keyboard shortcuts. Also note the value is controlled (`value={value}` fed by `docDraft ?? activeDoc.content`): a server-side change to `activeDoc.content` while the user has no draft replaces the textarea value and drops its undo stack.
- Appending text to the END of the doc from a non-editor source (e.g. a live transcript) is easy through the store: `editDoc(body + chunk)`; it goes through the same debounce and revision coalescing. It would race with the user's caret, so prefer a separate pane or a section the user does not edit.
- Preview-only features (checkbox toggling, outline, wikilinks) go in `MarkdownPreview` components map, which is also the chat renderer, so gate behind a prop.

### CSS
`styles/docs.css` (358 lines): header comment says tokens come from `styles.css`. Layout is a CSS grid on `.docs-body` (lines 7-20). Variables: `--docs-tree-w`, `--docs-history-w` (written by `ResizeHandle`, persisted to localStorage), `--border`, `--hover`, `--active`, `--accent`, `--text`, `--text-faint`, `--ed-*`. Trap: the grid columns are switched by `:has(.docs-history)` and `.tree-hidden:has(.docs-history)`. A second right-hand panel needs its own grid-template rule(s) (four combinations: tree on/off x history on/off x new panel) or it must share the history column (tabs inside one aside). `.docs-body:has(.docs-history) .doc-panes.split` also stacks the editor and preview vertically whenever the history panel is open (lines 22-30); copy that for any new panel.

## 6. Seams for attaching recordings

### 6A. Reuse Meetings (recommended)
The recorder, STT, transcript and enhance pass are keyed by `meeting_id`. A doc-attached recording is "a meeting row linked to a doc id". Existing pieces and how they map:

| need | existing function | signature / notes |
| --- | --- | --- |
| create the container | `Meetings.create` (`meetings.py:573`) | `create(title="", project_id=None, template="general", ..., status="notes_only")`. No doc link param; add column `doc_id TEXT` through `ADDED_COLUMNS` (`meetings.py:~170`, `{"speaker_names": ...}` is the precedent) and a PATCH_FIELDS entry |
| start capture | `MeetingService.start(meeting_id)` (1299), route `POST /meetings/{id}/start` (`app.py:5603`) | blocking preflight; raises `MeetingBlocked` (409 with blockers) or `RecorderBusy` (409). Only ONE recording at a time app-wide (`RecorderPool.start`, `meeting_recorder.py:753/766`). Allowed only from status `scheduled` or `notes_only`; a meeting records once |
| consent | `MeetingService.consent` / `POST /meetings/consent`; store `meetingStatus.consented`, `startRecording` opens `MeetingConsentModal` when false (`store.ts:2268-2271`) | one-time modal naming the wav directory and STT endpoint; reuse as is |
| live bar | `MeetingRecorderBar.tsx` (113 lines) | reads `meetingStatus.active`; polls `pollMeetingLive` every 2 s and `refreshMeetings` every 5 s while live; Pause/Resume/Stop; this component can be mounted in `DocsView` unchanged |
| live transcript | `GET /meetings/{id}/segments?since=` (`app.py:5713`), `api.meetings.segments(id, since, limit)` (`api.ts:672`), store `meetingSegments`, `meetingCursor`, `lib/transcript.ts` (`mergeSegments`, `formatOffset`, `speakerLabel`, `recorderState`) | SSE route exists but nothing publishes; polling is the real transport |
| stop | `MeetingService.stop` (1397), `POST /meetings/{id}/stop` (5623) | drains STT (up to `drainSeconds`=90), `finalize`s a `transcript` column, then queues enhance if `enhanceOnStop` |
| audio import | `POST /meetings/{id}/import-audio` (5633), `meeting_import.py` | multipart; needs consent; refuses a meeting that has segments |
| summary / enhance | `MeetingService.enhance(meeting_id, force, template)` (1527) + `meeting_notes.enhance(...)` (`meeting_notes.py`, one LLM call returning `enhanced_markdown`, `decisions`, `action_items`, `topics`, `headline`; degraded mechanical fallback when the LLM fails) | takes `notes=m["notes"]` from the MEETING row, not from a doc. To enhance a doc's text, call `meeting_notes.enhance(complete_fn=..., settings=..., model=..., meeting=m, notes=<doc content>, transcript=m["transcript"], template=..., max_transcript_chars=...)` directly, or sync `docs.content` into `meetings.notes` before calling `enhance` |
| templates | `meeting_notes.TEMPLATES` (general, standup, one_on_one, user_interview, sales_call, lecture) | reusable for summary style |
| action items | `meeting_action_items` + `promote_action_item` (1025) -> todos | |

Redaction (`redact.scrub_secrets`, credential-only), VAD silence gate, hallucination filter and the privacy posture (no auto-learn from meetings, transcripts treated as untrusted, lifecycle never exposed as a tool) are in `docs/meetings.md`; an in-doc recording inherits all of it if it stays inside `MeetingService`. If transcripts get copied into `docs.content`, they LOSE these protections (they would feed `doc_read` untainted, the retrieval index, `learnStyle` samples (`save_doc` banks the doc body as a style sample, `app.py:4986-4988`), and context). Decide explicitly; the safe default is to keep the transcript in `meeting_segments`/`meetings.transcript` and keep only a reference + the accepted summary in the doc.

### 6B. Per-doc side panel mount point
- The existing right-hand panel is `<aside className="docs-history">` at `DocsView.tsx:280-297`, rendered when `activeDoc && historyOpen`; `historyOpen` is local `useState(false)` (line 41), toggled by the History button (`DocsView.tsx:239`, `History` icon with a `.dot-badge` pending count) and closed by its own X (282). Its width is the resizable `--docs-history-w` via `<ResizeHandle id="docs-history-w" ... grows="left">` (144-146).
- A Recording/Transcript panel can follow the same pattern: another local `useState`, a sibling `<aside>`, a `ResizeHandle` with its own id/CSS var, and CSS grid rules (section 5 CSS trap). Simplest: make the aside tabbed ("History" | "Recording") so there is one column, one handle and no new grid combinations.
- Meetings already does a "transcript beside the notes" pane: `MeetingsView.tsx:319-345` (`.mtg-panes.with-transcript`, `<aside className="mtg-transcript">`), styled in `styles/meetings.css`. It is a copyable reference.

### 6C. Record button
In `.doc-toolbar` (`DocsView.tsx:168-243`), next to the history button; Meetings' equivalent is the `Mic` "Record" `ghost-btn` at `MeetingsView.tsx:~268` (`startRecording(m.id)`), disabled with `OFF_TITLE` when `recorderOff`. The recorder bar goes above `.doc-toolbar` or above `.doc-panes` (the `.doc-review` card sits at 245). Store state to read: `meetingStatus` (`active.meeting_id`, `consented`, `config.enabled`), actions `startRecording`, `stopRecording`, `pauseMeeting`, `resumeMeeting`. Note `startRecording` hard-sets `view: 'meetings'` and `activeMeeting` (store.ts:2267-2295), so using it from the Docs view would navigate away; add a variant or a param that skips the view switch. Also `meetingStatus` is only polled by the Meetings view and by `MeetingRecorderBar`; the sidebar has a live indicator mounted everywhere.
- Recording is off by default: `settings.meetings.enabled` (`llm.py:221`) plus consent; the UI must handle "not enabled" (Meetings uses `recorderOff` + `OFF_TITLE`, and `MeetingSettings.tsx` hosts the checklist/preflight).

### 6D. Creating a pending revision from a non-chat source
`Docs.propose` (`docs.py:540`):
```python
def propose(self, doc_id: str, after: str, summary: str = "", tool: str | None = None,
            title_after: str | None = None) -> dict[str, Any] | None
```
Behavior: reads the current doc (`self.get`; returns None if missing/trashed), inserts a `doc_revisions` row with `before = current content`, `after = <given>`, `author = 'assistant'` (hardcoded in the SQL), `status = 'pending'`, `tool = tool`, `summary = summary or "Assistant edit"`, `created_at = now()`; returns `self.revision(rid)` (with `stat`, `stale`, `stat_vs_current`). The doc is untouched until `accept`. Existing non-chat caller: cowork promotion `app.py:6543` `docs.propose(item.doc_id, after, f"Appended {rel} from a cowork desk", tool="cowork")`. Chat caller: `tools.py:1868`. There is no HTTP route that proposes (only accept/reject/restore). A recording summary job should call `docs.propose(doc_id, new_markdown, summary="Meeting notes from recording", tool="doc_recording")` server-side, then the existing UI (pending cards + `docsPending` badge) shows it with zero frontend work; the store must `refreshDocsPending()` / re-open the doc to see it (nothing pushes doc events: there is no doc SSE topic; `meeting_bus` and the app-wide `/events` exist but docs are not on them (inferred, `store.ts:869` only calls `refreshDocsPending` on some events)).
- Author values: `'user'` (autosave, restore) and `'assistant'` (propose). `tool` is free text (`'doc_edit'`, `'cowork'` today). `DocRevision.author` TypeScript union has only those two. If the UI should say "from recording", key off `tool`.
- `Docs.accept` rewrites `before` to the replaced text, so a stale accept (user kept typing during the recording) still undoes cleanly. That matters here: the user will type notes during the recording and the proposal will be computed from a snapshot taken at stop time. Compute `after` from the content at proposal time, not from an earlier read.
- Auto-apply semantics for meetings (accept automatically when `enhanced` is empty or unedited, never for degraded passes) live in `MeetingService.enhance` (`meetings.py:1527-1590`). Docs have no equivalent; the `docEditMode` setting governs only the `doc_edit` tool.
- Quality rule from the meeting prompt to preserve: the user's own notes are the outline, the transcript only fills in; never invent decisions (`meeting_notes.ENHANCE_PROMPT`).

## 7. Gaps for a note-taking surface (each verified in code)

Cheap = pure frontend or one additive column/route; medium = a new backend table/route + UI; expensive = touches the editor/mirror invariant or the index.

| gap | evidence | cost |
| --- | --- | --- |
| Quick capture / "New note" without choosing a place | only a "New doc" button (DocsView:127, 153) -> `createDoc({})` always Personal root, title "Untitled", opens in `docTabs`. No global hotkey, no menu accelerator for Files | cheap (add menu item + `view:docs`/new-doc action + accelerator; note main-process global shortcuts need `src/main`) |
| Daily note | no code references; `createDoc` accepts `title/content/project_id/folder` so "Daily/2026-10-02" is a client-side find-or-create | cheap (find by title via `docs`; create if absent) |
| Templates | none for docs. `meeting_notes.TEMPLATES` exists for meetings only | cheap (client-side seed strings) / medium (user-editable) |
| Pinned / recent | only `starred` (sorts first in tree; no "Starred" or "Recent" section; DocTree only builds project/folder groups) | cheap (derive from `docs` already in store, sorted by `updated_at`) |
| Tags | no column or UI | medium (column or table + FTS inclusion + UI); a `#tag` parse over `content` is cheap but un-indexed |
| Backlinks / wikilinks | none; no link parsing; preview has no internal link handler (`ExternalLink` only opens http(s)) | medium |
| Checklists in preview | rendered as disabled checkboxes (inferred); editor only continues `- [ ]` lists. Toggling needs a preview `input` component that rewrites the source line | cheap-to-medium (line index from remark position; edit `docDraft`) |
| Word count | already in the editor status bar (MarkdownEditor:273) and `words` on the API; reading time absent | done / trivial |
| Outline / TOC | none; headings are not extracted anywhere on the client; backend chunker (`markdown_blocks`) knows headings but does not expose them | cheap (client regex over fenced-code-aware lines + scroll to line) |
| Export | no `/docs/.../export` route, no download. Only "copy patch" in DiffView and copy buttons on code blocks. Drive reachable only through Google tools | cheap (client Blob download of `.md`) / medium (PDF/HTML) |
| Slash commands / insert menu | none (editor has fixed chords). Needs the imperative handle in section 5 | medium |
| Image paste/attach | preview renders only `data:image/` URIs; the editor has no paste handler for images (inferred: no `onPaste`) | medium |
| Real search | tree search = `LIKE` over title+content (`docs.py:258`); FTS `Docs.search` exists but is only used by the tool; no highlighting/snippets in the UI | cheap (route over `Docs.search`) |
| Persisted view prefs | `docMode` is store-only (not persisted, resets to split); `treeOpen` is persisted in localStorage; `historyOpen` and `linked` are local state | cheap |
| Attachments of any kind to a doc (audio, files) | no table | medium (the section 6 design) |
| Doc-level undo of an applied assistant edit | restore-to-revision exists (`restoreRevision`) | done |
| Collaboration / conflict | autosave is last-write-wins by whole-body PUT; no version/etag check. Two windows editing the same doc will clobber each other (inferred from `save_doc` having no precondition). Relevant because a recording summary accepted while the user types is safe (propose), but a background job writing `content` directly would not be | note as a trap |
| Large docs | no virtualisation; mirror re-highlights the whole text on every keystroke (`useMemo` on `value`, line-based regexes); `Docs.save` reindexes FTS + chunk hash on every autosave | note |
| Mobile-ish / empty states | empty state exists (DocsView:148) | n/a |

## 8. Tests

- Backend files: `backend/tests/test_docs.py` (185 lines: store, routes, tools), `test_doc_folders.py` (112), `test_doc_scopes.py` (156), `test_docs_retrieval.py` (215), plus `test_meetings.py`, `test_meeting_notes.py`, `test_meeting_import.py`, `test_meeting_recorder.py`, `test_meeting_vad.py`.
- Style: SCRIPT-style, not pytest functions. Each file does `os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(...))` and `PERSONAL_OS_AUTH_TOKEN`, `sys.path.insert(0, backend)`, imports the app singleton (`from personal_os.app import AUTH_TOKEN, app, docs, toolbox`), builds `client = TestClient(app, headers={"X-Personal-OS-Token": AUTH_TOKEN})`, defines `check(cond, label)` (a counting assert) and `j(method, path, body, expect)`, runs top-level assertions, and prints `test_docs: N checks passed`. Tool calls use `asyncio.get_event_loop().run_until_complete(toolbox.call(name, args, ctx))`. Docstring gives the run line: `PERSONAL_OS_DATA_DIR=/tmp/x python backend/tests/test_docs.py` (docs/docs-editor.md says `backend/.venv/bin/python backend/tests/test_docs.py`; this worktree has no `backend/.venv`, so use the main checkout's interpreter or any venv with the backend deps). `test_meetings.py` is the other style: pytest-compatible, builds its own `Database` and stubs the LLM, also runnable directly.
- Runner: `backend/conftest.py` makes `pytest` collect one item per `test_*.py` and run each in a FRESH interpreter with a fresh `PERSONAL_OS_DATA_DIR` (`tempfile.mkdtemp`), `GRAIN_SECRETS_BACKEND=file`, `PYTHONPATH=backend`, 900 s timeout. Script-style files (no `test*` functions, a bare module-level `setup`, or opting out with `allow_module_level=True` plus `__main__`) are run as `python file`; pytest-style files run under an inner pytest. The parent sums the inner counts into a line `inner totals: ...`; read that line. Gotcha that caused this design: importing `personal_os.app` rebinds module globals (`llm.stream_chat`, `toolbox.call`, ...) and opens one DB per process, so two test files must never share a process or a data dir. New test files must therefore be named `test_*.py` in `backend/tests/` and must use `setdefault` for the data dir (an exported `PERSONAL_OS_DATA_DIR` makes every file share one DB).
- Frontend: `npm test` (package.json:14) is `esbuild <explicit list of .test.ts files> --bundle ... --outdir=out/test && node --test out/test`. The list is HAND-MAINTAINED; a new `*.test.ts` does nothing until its path is appended. Existing doc-related entries: `src/renderer/src/lib/docTree.test.ts`, `lib/diff.test.ts`, `lib/mathBlocks.test.ts`, `store.test.ts`, `menuShortcuts.test.ts`, `lib/transcript.test.ts`. Tests use `node:test` + `node:assert/strict`; component files can be bundled with `--jsx=automatic` and CSS/fonts loaded as empty. No DOM test harness: anything touching `textarea`/`document.execCommand` cannot be unit-tested here, so put logic in pure functions (like `wrapSelection`, `shiftLines`, which are module-private in `MarkdownEditor.tsx` today and not exported/tested). `npm run typecheck` runs both tsconfigs.
- No dedicated test exists for the `MarkdownEditor` key handling or highlight invariants.

## 9. Conventions

- Comments: heavy, explanatory "why" comments, often multi-line; module docstrings state the design decision (docs.py, meetings.py). Match that: describe constraints and failure modes, not what the line does.
- Python: `from __future__ import annotations`, type hints everywhere (`dict[str, Any]`), `with self.db.tx() as c:` for every write, `new_id()` / `now()` from `db.py`, `row_to_dict`. Repos are classes holding `db`; business logic (LLM, recorder) lives in a Service class that takes `complete_fn`/`settings_fn` as arguments so it is testable without a network. Routes live in the single `app.py` with Pydantic models declared next to the routes. Raise `HTTPException`; ValueError from repos becomes 400. Soft delete: always filter `deleted_at IS NULL`.
- FTS: no triggers; call the `_reindex(c, ...)` helper inside the same transaction from every write path, and delete FTS rows by hand on purge (virtual tables ignore ON DELETE CASCADE).
- Schema changes: additive only, via the class's own `SCHEMA` + `ALTER TABLE` loop in `__init__` (not `migrations.py`, which is for core tables).
- Settings: new keys go in `llm.DEFAULT_SETTINGS` (`llm.py:72`) with a comment; mirror in `src/shared/types.ts` `Settings`; nested/deep-merged config uses its own route (`/meetings/config`) and is added to `SETTINGS_READ_ONLY` (`app.py:627`) so `PUT /settings` cannot overwrite it. Bump `modulesDefault` (currently 3) only if the default-hidden view set changes.
- TypeScript: React function components with explicit `JSX.Element` return types, `useStore((s) => s.x)` selectors (one per field) plus a destructured `useStore()` for actions, lucide-react icons at `size={13..16}`, no semicolons, single quotes, 2-space indent. Types for API shapes live in `src/shared/types.ts` (shared with main); `api.ts` wrappers are thin `req<T>(path, {method, body: json(...)})` calls, with a JSDoc line when non-obvious. Buttons use the existing classes `primary-btn`, `ghost-btn`, `icon-btn` (+ `ghost`, `on`, `danger`), `seg`, `model-picker`, `muted`, `small`, `empty-hint`, `.dot-badge`.
- CSS: plain CSS files in `src/renderer/src/styles/` imported by the view (`import '../styles/docs.css'`); colours and spacing only through the theme tokens from `styles.css` (`--border`, `--hover`, `--active`, `--accent`, `--text`, `--text-faint`, `--focus-ring`), so both themes follow; one focus ring; empty-state pattern `.empty-state`. Pane widths are CSS variables written by `ResizeHandle` (`id` becomes the localStorage key and the `--<id>` var).
- Store: actions are async, toast errors with `get().toast(msg, 'error')`, optimistic merges guard against stale responses (see `openDoc`, `flushDoc`). Separate debounce timers per surface (doc: `saveTimer`; meeting notes: `meetingSaveTimer`) so typing in one is never cancelled by the other.
- Repo rule (`CLAUDE.md`): never name other apps/products a feature is modeled on in code, comments, UI copy, docs, tests, commits or PRs; `docs/research/` is exempt but this file avoids it anyway. (An existing code comment in `meetings.py` already names one; do not copy it.)
- Assistant-write safety invariants to keep: assistants never write `docs.content` directly (only `propose`), except `doc_create`; the user's typed text is never overwritten without a diff; recording lifecycle (start/stop/enhance/delete) is a click or an HTTP route, never a tool; transcripts are untrusted third-party text; nothing records without consent.

## 10. Traps (short list)

1. `/docs/<literal>` routes must be declared above `/docs/{id}` (`app.py:4973`).
2. `MarkdownEditor` is shared with Meetings, has no ref/imperative API, and its mirror depends on a one-output-line-per-input-line invariant. `apply` writes the whole value via `setRangeText` (undo behaviour unverified).
3. `startRecording` navigates to the Meetings view; only one recording can exist app-wide; a meeting records once; consent modal is global.
4. `docs.content` is chunked, embedded, style-sampled (`learnStyle`) and readable untainted via `doc_read`; transcripts pasted into it escape the meetings privacy posture and bloat the retrieval index.
5. `.docs-body` grid columns are driven by `:has(.docs-history)`; a second panel needs new grid rules.
6. `docRevisions` list in `DocsView` treats `rejected` as "applied" history (line 71); a `tool="doc_recording"` revision shows no special badge unless you add one.
7. No doc events are pushed to the client; a server-side `propose` appears only after the next `refreshDocsPending` / `openDoc` / `refreshDocRevisions`.
8. `npm test` file list is hand-maintained; backend tests must `setdefault` their data dir; the worktree has no `backend/.venv`.
9. Autosave is a whole-body last-write-wins PUT with no version check.
