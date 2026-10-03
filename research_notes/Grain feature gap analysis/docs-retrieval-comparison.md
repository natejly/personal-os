# Documents, docs editor, and retrieval

Comparison for a builder. Sources are only the app inventory and the market note, both dated 2026-10-02. No new web research. The two notes do not disagree on a fact that needs an outside source.

Conflicts already inside the notes, kept as labeled:

- Reranker results disagree with each other. A 2024 Cohere reranker and a TREC 2025 LLM reranker helped on their setups. A 2026 SQLite hybrid system lost quality and added hundreds of milliseconds with small cross-encoders. Details in the gap table. This is not a top build.
- Gemini Notebook’s upgrade table was captured as a snippet. Plan names were not in the header. A third-party blog’s Plus/Pro names disagree with the middle columns. Those plan names are not used here.
- Click-through to a Notebook passage is reported by a university article and an unofficial gist. It is not a sentence on the Google FAQ that was fetched.
- Inside the app inventory, README and `docs/research/sota-retrieval.md` still describe keyword-only retrieval, weak extraction, and docs absent from ordinary chat. The code on disk has moved past that. Those two write-ups are not the backlog.
- Design tension, not a fact conflict: the market note’s measured local hybrid uses the sqlite-vec extension. Grain already fuses FTS5 with in-process numpy over blobs in core SQLite. New work in this comparison stays on core SQLite and FTS5.

Effort in the table: **S** is one module and an existing test file. **M** crosses backend and UI, or adds a tool, still on SQLite. **L** is a new viewer or a model call over every chunk.

---

## 1. What Grain ships now

From the app inventory only. Status there is shipped, partial, or absent. No runtime metrics were measured.

### Ingest and index

Any upload up to 20 MB is stored (`MAX_UPLOAD_BYTES`). SHA-256 dedup returns the existing live row in the same project, or in personal when `project_id IS NULL`. Parsing runs off the request thread.

Structured extraction covers PDF (per-page text, a page marker, blank-line paragraphs, a heading heuristic), DOCX (heading styles and pipe tables), and, via a markdown pass, xlsx/xlsm (200 rows × 30 columns per sheet), pptx (title, body, tables, speaker notes), odt/ods/odp, EPUB, RTF, and CSV/TSV (first 2,000 lines). A long text-extension list plus a UTF-8 sniff covers source-like files. `.tex` is indexed as text, not compiled.

OCR is partial. A PDF under 12 characters per page is OCRed only when `pdftoppm` and `tesseract` are on `PATH`, and only for the first 15 pages, with time limits. Images use `tesseract` if present. There is no in-process OCR library. Whether those binaries are installed was not checked.

Index caps are partial. Searchable text stops at 400,000 characters and PDFs at 80 pages. Oversized zip office files are stored as a no-text marker. The original bytes stay on disk. `.doc` and `.xls` have no reader. The library UI is a card list and a `<pre>` of extracted text, not a page viewer.

Chunks are structure-aware: 1,200 characters, peers under 300 merge, overlap 120, heading stack, table rows keep their header. The indexed string is `title > heading path` plus body. Paragraph packing at 900 characters is only the fallback. Uploads and notes use separate FTS5 tables (`porter unicode61`). A third index, `docs_fts(title, content)`, serves whole-document doc search. The query ORs up to 12 terms of 3+ characters. There is no stopword list and no prefix search. Order is SQLite `bm25()`.

`POST /documents/reindex` re-extracts when the file is still on disk, otherwise re-chunks stored text.

### What a reply sees

With documents enabled, a normal reply injects the top 6 fused chunks as fenced excerpts, labeled as data. A file header can include heading and page. A note header includes title and heading. Notes have no page. The recorded context copy keeps 400 characters of each chunk, plus `chunk_id`, heading, page, and source. The prompt gets the full chunk, already about 1,200 characters or less.

The open page is a second injection, not a search. The Docs screen sends the live editor, unsaved edits included, clipped at 4,000 characters. The Documents screen sends the open upload the same way. The backend caps page detail at 6,000 characters.

Tools can go further. `search_documents` is hybrid over files, notes, or both (default 8, hard max 20, offset paging) and returns section and page. `read_document` pages upload text (default 6,000 characters, max 20,000). `doc_read` returns line-numbered markdown. `doc_list` and `doc_search` exist. `doc_search` is whole-document FTS, one snippet per note.

If the hybrid call throws, the reply falls back to upload BM25 only. That path drops the user’s notes.

### Editor

A note is one markdown string. The UI is a highlighted editor beside a live preview with KaTeX (`$` / `$$`). Slash commands cover headings, lists, to-do, quote, code, table, divider, math, date, and time, plus record, dictate, and daily note on the Docs screen. `[[Title]]` resolves by title, creates a missing note beside the current one, and does not store a stable id. Renaming a note does not rewrite other notes. An outline skips fenced code and math blocks. Templates (Blank, Meeting notes, Daily log, Project brief, One-on-one, Lecture notes, To-do list) become ordinary notes. The daily note is a personal note titled `YYYY-MM-DD` in folder `Daily`, so `[[2026-10-02]]` resolves. A trashed note of that title is not revived.

History stores every saved change. User saves apply immediately and fold into one revision when the same author saves again within three minutes. Autosave is described as 1.2 seconds. Those two timings, the print path, and the backlinks route body were taken from the editor doc plus the existence of the UI, not re-read line by line in this inventory. Print-to-PDF does not load KaTeX, so math prints as source. Export is download markdown, copy markdown, or print. Preview checkboxes edit the source line. Programmatic inserts try Chromium undo and fall back to `setRangeText`. An insert while the editor is unfocused is not on the native undo stack. That path was not exercised against a running build.

### Assistant writes

`doc_create` writes immediately, indexes, and records an applied revision. `doc_edit` always starts as a pending revision (exact find/replace, append, full replace, or title). Zero or many matches return an error and do not guess. Default `docEditMode` is `review`. Settings can set Accept all, which applies in the same turn, except a scheduled `proposal_only` run, which stays pending. The chat shows the diff. Accept and reject routes exist. Undo after accept is another revision.

One residual inside that shipped flow: `doc_edit` append freezes the whole body, so typing afterward marks the proposal stale. Recording summaries use `propose_append`, which rebuilds from the live note. The edit tool does not. Pending proposals are not indexed until accept. Recording transcripts stay on meeting rows, not in `doc_chunks`. A summary enters the note index only after accept, and recording summaries are never auto-applied.

`doc_search` and `doc_list` are not project-scoped. They search every note. Chunk retrieval for the automatic excerpts limits a project chat to that project plus personal notes.

### Retrieval

Default retrieval is hybrid. FTS5 BM25 and, when an embedding model is configured and vectors exist, a numpy dot product over unit float32 blobs. Lists fuse with reciprocal rank fusion, k = 60, equal weights. Vector-only hits below similarity 0.25 are dropped. A hit that also matched BM25 is kept. At most 3 chunks per document. Candidate pool default is 20. `retrievalMode=bm25`, a missing model, a failed route, or no vectors skips the vector leg. Embed errors return no vector. The HTTP call is an OpenAI-shaped `POST /v1/embeddings`, default model name `qwen3-embedding-8b`, batches of 32, 10 second timeout. After a failure that model is skipped for 300 seconds. KNN loads every in-scope blob for the current model. A comment says this is fine to about 100,000 chunks. That figure is a comment, not a measurement. Whether the named embedding model is actually routed was not checked.

There is no LLM situating paragraph, no reranker, no query splitting, no neighbor expansion, and no pin-this-document-into-every-turn. Pinning exists for memories, which are a different store.

`GET /documents/index-status` returns chunk and embed counts. The renderer never calls it and has no `retrievalMode` control.

The model sees section and page on each excerpt. The Context drawer shows the file name, “chunk N”, and the 400-character preview. The shared `ContextUsed` type does not declare heading, page, or source, and the drawer does not read them. Nothing highlights a passage in a viewer. The reply text has no citation markers.

### Two stores, one ranker

Uploads (`chunks`, `chunk_embeddings`) and notes (`doc_chunks`, `doc_chunk_embeddings`) stay in separate tables. One retriever searches both and fuses them, so a file chunk and a note chunk compete for the same six slots. Whole-document `docs_fts` is only for `doc_search`. Canvas notes are in neither store. Memories use another vector table and another prompt block. Automatic retrieval scope matches on both stores: a project chat sees that project plus personal; a personal chat sees only personal. Deleting a note is excluded by `deleted_at IS NULL`. A purge cascade was inferred from the foreign key and not executed.

---

## 2. What leading apps ship that Grain does not

Named from the market note. Grain already has hybrid search, structure-aware chunks, note chunks in ordinary chat, and proposed diffs. Those are not listed here.

### Claude Projects

Paid plans turn retrieval on automatically when project knowledge approaches the context window, and can switch back to loading the project in context once it fits again. A visual indicator shows retrieval is on. The user cannot choose the moment retrieval runs. The help center says this expands capacity by up to 10x and “maintains consistent response quality” with in-context processing. Neither claim has an eval on that page. In retrieval mode the model uses a project knowledge search tool and sees “only the most relevant information.” The help page does not document chunk size, hybrid search, reranking, or inline citations for project files. ([RAG for projects](https://support.claude.com/en/articles/11473015-retrieval-augmented-generation-rag-for-projects))

Projects are self-contained workspaces. Knowledge added to the project is what chats in that project share. ([What are projects?](https://support.claude.com/en/articles/9517075-what-are-projects))

Grain always injects six chunks when documents are on. It has no size threshold, no full-project injection, and no indicator that this turn used search rather than the whole set. Grain’s chat-level document toggles are a different control: they drop excerpts entirely. They do not choose full text versus search.

A separate Claude API can cite `search_result` blocks when citations are enabled (off by default on those blocks). The model cites the whole block, not a substring, and `cited_text` is that whole slice. That is a developer mechanism, not the Projects UI. ([Search results](https://platform.claude.com/docs/en/build-with-claude/search-results)) Enterprise “Ask your org” claims well-cited answers over connectors such as Slack and Microsoft 365. That is not project-file retrieval. ([Use enterprise search](https://support.claude.com/en/articles/12489464-use-enterprise-search))

The 2024 engineering write-up still states the threshold idea in numbers: under about 200,000 tokens (they say about 500 pages), put the knowledge base in the prompt and skip retrieval. Above that, chunk, embed, use BM25, and fuse ranks. ([Contextual Retrieval](https://www.anthropic.com/engineering/contextual-retrieval), 2024-09-19)

### Gemini Notebook

Official help says chat answers are grounded in the uploaded sources “with clear in-line citations.” Sources can be PDFs, websites, YouTube, audio, Google Docs, or Google Slides. ([Learn about Gemini Notebook](https://support.google.com/notebooklm/answer/16164461?hl=en))

If a source is too short, the product cites the entire document and does not cite a passage. When a notebook has many sources, it retrieves the most relevant information. The user can select which sources are in play. Notes enter the prompt only when selected. If the answer is not in the sources, it will not answer from outside them. Local uploads cap at 500,000 words or 200 MB, with no page limit. Copy-protected PDFs fail import. ([Gemini Notebook FAQ](https://support.google.com/notebooklm/answer/16269187?hl=en))

The FAQ does not say whether “retrieves” means embeddings, BM25, or a long-context selection. Click-to-scroll is not in that FAQ. A 2025-08-27 university article says numbered citations jump to the passage, and an unofficial gist describes hover quotes and scrolling a source viewer. Treat click-through as reported. ([FSU Service Center](https://servicecenter.fsu.edu/s/article/How-do-NotebookLM-s-inline-citations-work-and-why-are-they-important); [unofficial gist](https://gist.github.com/PauloMoekotte/f603ec3f6dbe946b7d44d302aa013f45))

Grain’s reply has no citation markers. The drawer does not show the heading and page the prompt already has. Nothing opens a passage. Grain does not ingest websites, YouTube, or Google Docs/Slides through the document pipeline described in the app note. Audio transcripts sit on meeting rows and are out of the document chunk tables until an accepted summary.

Deleted Gemini Notebook notes cannot be recovered. That is the opposite of Grain’s history. It is listed under section 3.

Free accounts: up to 50 sources per notebook, from the add-sources page. ([Add or discover sources](https://support.google.com/notebooklm/answer/16215270)) The upgrade snippet’s source caps run 50, 100, 300, 500, 600 per notebook, without verified plan names. ([Upgrade Gemini Notebook](https://support.google.com/notebooklm/answer/16206866?hl=en)) Grain’s inventory describes a per-file byte cap, not a source-count plan. Those quotas are not a build target.

### Open WebUI

Notes and knowledge are split. Notes inject their full content into every message. Knowledge uses retrieval so a large set does not have to fit. Per file, Focused Retrieval (default) injects the top chunks. Full Context injects the whole file with no chunking and no semantic search. A default upload mode can still be overridden per file. The chat docs call the same control “Using Entire Document.” ([Knowledge](https://github.com/open-webui/docs/blob/main/docs/features/workspace/knowledge.mdx); [RAG](https://docs.openwebui.com/features/chat-conversations/rag/))

With native function calling, attached knowledge is not auto-injected. The model calls tools. `query_knowledge_files` is semantic retrieval, plus BM25 and a reranker when hybrid search is on, and returns top K (default 5). `grep_knowledge_files` is exact or regex match and returns lines with 1-based line numbers, capped at 50. `view_file` reads a character page (default 10,000, hard cap 100,000) or a line range. The docs tell the model to use semantic search for “what does this say,” grep for identifiers, then a line-range read. An optional `kb_exec` shell layer is off by default and experimental. ([Knowledge](https://github.com/open-webui/docs/blob/main/docs/features/workspace/knowledge.mdx))

Grain’s model can search chunks, page through upload text by character, and read a note by line range. It has no exact or regex line grep over the knowledge files, and no per-file “entire document” switch. FTS drops terms shorter than 3 characters, so a short identifier never enters the OR query.

Open WebUI’s hybrid switch adds a cross-encoder and a relevance threshold. The docs publish no quality number. The RAG page says citations are added and does not describe hover text, quote offsets, or a source viewer. ([RAG](https://docs.openwebui.com/features/chat-conversations/rag/))

Open WebUI re-index re-chunks stored extracted text and does not re-parse the original file. Grain’s reindex does re-extract when the file is still on disk. That difference is in section 3.

Header-then-merge chunking in Open WebUI is the same family of idea as Grain’s shipped chunker. The docs claim a chunk-count drop “over 90%” with no dataset, metric, or date. That claim is not a reason to retune Grain. No 2025–2026 source opened in the market note names a best chunk size.

Open WebUI’s vector stores are Chroma and pgvector, not FTS5. Use it as a feature checklist only. ([Knowledge](https://github.com/open-webui/docs/blob/main/docs/features/workspace/knowledge.mdx))

### Obsidian and Logseq

Grain already ships wikilinks, a backlinks panel, and a dated daily note. The pieces those apps document that the Grain inventory does not:

- Obsidian backlinks include unlinked mentions: the note’s name as plain text, not only real `[[links]]`. ([Backlinks](https://obsidian.md/help/backlinks)) Grain’s described scan looks for `[[` and parses links, skipping fenced code. The route body was not re-read, so this gap is against that description.
- Obsidian can apply a template when the daily note is created, and can use a date format that creates folders. A date property in a note links to that daily note. ([Daily notes](https://obsidian.md/help/plugins/daily-notes)) Grain’s daily route is find-or-create by date title. The inventory does not describe a template body or a date-property link.
- Logseq journals resolve natural-language dates inside brackets (`[[Today]]`, `[[This Friday]]`, `[[Last Friday]]`) and move a day at a time. ([Logseq db-version docs](https://github.com/logseq/docs/blob/master/db-version.md)) Grain resolves the literal date title.

Logseq block references (`((uuid))`) and a block database are a different model. Grain’s note is one markdown string on purpose. A generated wiki says Logseq shows linked, unlinked, and block backlinks at the bottom of the page. That layout was not confirmed on the docs repo. ([DeepWiki summary](https://deepwiki.com/logseq/docs/4.3-linking-and-references))

### Notion and Craft

Both now let an agent propose edits for approval. Notion’s 2026-08-28 release notes: the user asks the agent to suggest edits and approves each change from top to bottom, including a line-level grammar pass. ([Notion releases](https://www.notion.com/releases)) Craft’s assistant has Explore (propose for approval) and Execute (apply as it goes). That Craft page was search-indexed, not fully fetched. ([Craft Assistant editing](https://craft-support.mintlify.app/en/ai-assistant/editing))

Grain already does this. Default is review. Accept all applies immediately, except scheduled runs. Do not rebuild proposed diffs.

Notion has had human suggested edits since 2024-07-29, grouped with comments. ([Notion 2.43](https://www.notion.com/releases/2024-07-29)) The app inventory describes one person’s notes and an assistant. It does not describe a second person proposing edits. This comparison does not treat a collaborator inbox as a build.

Notion restores a dated page version from the page menu. ([Notion help](https://www.notion.com/help/guides/tips-to-keep-your-teams-notion-pages-up-to-date)) Craft stores about one snapshot per continued hour of editing, and retention depends on the plan. A document idle for a full hour may get no new snapshot. The Craft history page was search-indexed, not re-fetched in full. ([Craft version history](https://support.craft.do/en/write-and-edit/version-history)) Grain’s per-save history is the finer of these. See section 3.

### Practice evidence that should not set the default build

These are papers and docs, not app features. They constrain the table.

Lexical plus dense retrieval, fused by rank, is the recipe with 2024–2026 measurements behind it. A weak extra path can pull fusion down. No hybrid layout won every dataset in the 2025 VLDB paper. ([Contextual Retrieval](https://www.anthropic.com/engineering/contextual-retrieval); [Balancing the Blend](https://arxiv.org/html/2508.01405v1); [vstash](https://arxiv.org/pdf/2604.15484)) Grain already fuses FTS5 and embeddings with RRF at k = 60, and already keeps a BM25 hit whose cosine is below the floor.

Reranking is mixed. Leave it out of the top three.

- 2024, Anthropic, top-20 failure rate on their contextualized stack: a Cohere reranker on the top 150 cut the failure rate from 5.7% to 1.9%. The same post’s contextual-embedding and contextual-BM25 gains (failure 5.7% to 3.7%, then to 2.9%) used a 50–100 token paragraph written by a model from the whole document. A generic document summary “saw very limited gains.” Hypothetical-document embeddings “saw low performance.” Passing 20 chunks beat 10 or 5 on that metric. No winning chunk size. The percentages are from the 2024 post, not a 2025 re-eval. ([Contextual Retrieval](https://www.anthropic.com/engineering/contextual-retrieval))
- TREC 2025: an LLM sliding-window reranker (GPT-4.1-mini) raised nDCG@5 from 0.486 to 0.778 on that track. The team had already fused BM25, SPLADE, BGE-small, and Qwen3-Embedding with RRF. ([TREC 2025 notebook](https://trec.nist.gov/pubs/trec34/papers/UTokyo.rag.pdf))
- 2026, the SQLite system: ms-marco-MiniLM and BGE-reranker-base changed NDCG by −0.3% to −3.1% and added 560–2100 ms. The authors say those cross-encoders were trained on web search and did not transfer to their technical and scientific sets. ([vstash](https://arxiv.org/pdf/2604.15484))

Neighbor expansion in that 2026 paper added 2.64× text at +0.12 ms around a ~250-token hit. The note reports no quality change for that expansion.

A vendor blog reports about +4.58% nDCG@10 for its own hybrid versus RRF. That is not an independent result, and it is not a reason to replace Grain’s RRF. ([TopK](https://www.topk.io/blog/20250724-beyond-rff-how-topk-improves-hybrid-search-quality)) A 2026 financial hybrid-RAG abstract was not opened. Its figures are unused.

FTS5 already exposes `bm25()` column weights and `snippet()` for a short lexical highlight. ([SQLite FTS5](https://www.sqlite.org/fts5.html)) The app inventory describes `bm25()` order and does not describe column weights or `snippet()`.

---

## 3. Where Grain is already ahead, or different on purpose

**History is finer than the page editors in the market note, and deleted notes are recoverable.** Every stored edit is a revision, same-author saves inside three minutes fold, and restore writes a new revision. Craft’s documented grain is about hourly, and only while editing continues, with plan-limited retention. Gemini Notebook’s fetched FAQ says a deleted note cannot be recovered. Notion’s dated restore is the comparable bar, and Grain already has a restore path.

**Reindex re-reads the file.** When `documents.path` is still on disk, reindex runs structured extraction again. Open WebUI’s re-index only re-chunks text it already stored.

**The 2026 propose-then-approve pattern is already the default.** Review shows a diff and waits. Accept all writes in the turn and stays undoable. Scheduled runs stay pending even in Accept all. Creating a note skips the queue on purpose. Do not spend a build recreating this. The append-stale residual in section 1 is a bug inside the feature, not a missing feature.

**Rank fusion already matches the part the 2026 SQLite paper said mattered.** Equal-weight RRF at k = 60, and a keyword hit survives a low cosine. That paper’s large ArguAna jump came mostly from relaxing a distance cutoff on long queries, not from the IDF weight tweak alone. Their adaptive weights beat fixed 0.6/0.4 weights by small margins on the other four reported BEIR sets (for example SciFact 0.7255 to 0.7263 nDCG@10). That is optional tuning later, not a gap to close first. Grain’s weights are equal, not 0.6/0.4. The inventory gives no recall number either way.

**Heading-path chunks are the local stand-in for “a chunk with a subject.”** Open WebUI’s header split plus min-size merge is the same family. The 2024 LLM paragraph is a further step with old numbers and no 2025–2026 remeasurement in the market note. It is not “chunking is missing.”

**The open page reaches the model before it is indexed.** Unsaved editor text is page context. Pending assistant text stays out of the index until accept. The market note does not describe that split.

**Project scope on the automatic path is already the right shape.** A project chat searches that project plus personal files and notes. Personal sees only personal. Claude’s project knowledge stays in the project. Grain’s hole is the two doc tools, which still search every note.

**Different on purpose, and worth keeping:**

- One markdown string, not a block database. Block ids and `((uuid))` refs would be a new editor.
- A general assistant that can answer with documents turned off, or with excerpts plus the rest of the product. Gemini Notebook’s FAQ says it will not answer from outside the sources, and notes count only when selected. Copying that refuse-outside-sources rule would turn Grain’s chat into a notebook.
- No source-count plans. The 20 MB, 80-page, and 400,000-character caps are safety bounds in the extractor, not billing tiers.
- Vectors stay blobs in core SQLite, scored in-process. The market note’s ANN design is a loadable extension. This comparison does not add one. The “~100k chunks” line is a comment, not a measured ceiling.
- Human track-changes for a second collaborator are outside the inventory.

---

## 4. Gap table

| Gap | User impact | Evidence | How to close it in this codebase | Effort |
| --- | --- | --- | --- | --- |
| Retrieved passages are not checkable. The drawer shows name, “chunk N”, and 400 characters. The reply has no citation marker. There is no jump to the passage. | The model is given heading and page. The user cannot confirm which section was used or open it. | App: prompt header in `context.py` `_excerpt_header`; drawer in `ContextDrawer.tsx`; `ContextUsed` in `types.ts` omits heading, page, and source; recorded `text` is 400 characters; full chunk is only in the prompt. Market: Gemini’s user-facing promise is in-line citations ([Learn about Gemini Notebook](https://support.google.com/notebooklm/answer/16164461?hl=en)). A short source should cite the whole document ([FAQ](https://support.google.com/notebooklm/answer/16269187?hl=en)). Claude’s API cites whole returned blocks when citations are on ([Search results](https://platform.claude.com/docs/en/build-with-claude/search-results)). Projects help does not document this UI. Open WebUI’s citation sentence does not describe a viewer ([RAG](https://docs.openwebui.com/features/chat-conversations/rag/)). Click-to-scroll is reported, not on the fetched FAQ. FTS5 `snippet()` is the lexical highlight available without a new index ([FTS5](https://www.sqlite.org/fts5.html)). | Data is already stored on `context_used.chunks` (`heading`, `page`, `source`, `chunk_id`, `doc_id` / `document_id`). Declare those fields on `ContextUsed` and render heading and page in `ContextDrawer.tsx`. Keep the 400-character preview. On activate, open the note in `DocsView` or the upload `<pre>` in `DocumentsView` and scroll to the chunk text or the stored page marker. Do not build a PDF renderer for this. Optional: FTS5 `snippet()` to mark query terms inside a lexical hit. Inline markers in the assistant prose need a citation token in the excerpt header from `build_context`. Test the token and the drawer. Do not require a live model to emit them. | M |
| Small libraries are always chopped into six chunks. Nothing loads a note or a small project whole, and nothing tells the user which mode ran. | A short daily note or a handful of files is retrieved as fragments. Leading apps keep the whole text while it fits. | App: `_doc_hits` always calls `Retriever.search` with limit 6. Page context is only the open page, clipped. Market: Claude switches by size and shows an indicator ([RAG for projects](https://support.claude.com/en/articles/11473015-retrieval-augmented-generation-rag-for-projects)); the 200,000-token line is 2024 guidance ([Contextual Retrieval](https://www.anthropic.com/engineering/contextual-retrieval)). Open WebUI injects an entire note every message, and can inject an entire file per attachment ([Knowledge](https://github.com/open-webui/docs/blob/main/docs/features/workspace/knowledge.mdx)). Gemini cites the whole short source ([FAQ](https://support.google.com/notebooklm/answer/16269187?hl=en)). The “10x” and “same quality” lines have no published eval. | In `_doc_hits` / `build_context`, sum in-scope `documents.text` and `docs.content` for the current project rule. Under a character budget in `DEFAULT_SETTINGS` (next to `retrievalCandidates`), inject those bodies and skip `Retriever.search`. Over the budget, keep the six fused excerpts. Record which mode ran on the context payload so the drawer can say so. Dedupe the open page when page context is the same body. Use a character budget because the extractor and the page clip are already in characters. Do not hard-code 200,000 tokens. The test supplies the budget. When the whole document is injected, cite the document, not a fake page. | M |
| `doc_search` and `doc_list` ignore the chat’s project. | The assistant can read and list notes from other projects. Automatic excerpts already cannot. | App: `tools.py` calls `Docs.search` / `Docs.list` with default `project_id="__all__"`. `Docs.chunk_search` and `Documents.search` use `_scope_clause` (this project plus personal, or personal only). Market: a Claude project’s knowledge stays in that project ([What are projects?](https://support.claude.com/en/articles/9517075-what-are-projects)). | Pass the chat project id into those two tools and use the same `_scope_clause` as `chunk_search`. Do not narrow it further. Personal notes must remain visible from a project chat. Cover `doc_search` and `doc_list` only. `search_documents` is already scoped. | S |
| No index or mode indicator in the UI. | The user cannot see whether embeddings caught up, or whether this reply was BM25-only because the embed route is down. A paraphrase then silently fails. Embed errors return no vector and do not raise. | App: `GET /documents/index-status` returns `{chunks, embedded, model, mode}` and, when notes are wired, doc chunk counts. `SettingsModal.tsx` knowledge tab does not call it. The renderer has no `retrievalMode` string. Market: Claude shows when project RAG is on ([RAG for projects](https://support.claude.com/en/articles/11473015-retrieval-augmented-generation-rag-for-projects)). | Read-only line on the knowledge tab from the existing route: chunk count, embedded count, model name, mode. After the size-threshold build, show full-text versus search for the last context build. Do not add a control that implies a reranker. | S |
| No per-document pin, and no per-file “use the whole file.” | With a large library, the user cannot force the open contract, or one note, into the prompt. Selection is chat-wide on/off, or whatever is on screen (clipped at 4,000 characters). | App: `Retriever.search` has no pin. Memory pin is a different store. Market: Open WebUI’s per-file Full Context ([Knowledge](https://github.com/open-webui/docs/blob/main/docs/features/workspace/knowledge.mdx)); Gemini uses the sources the user selects, and notes only when selected ([FAQ](https://support.google.com/notebooklm/answer/16269187?hl=en)). | A flag on one document or one note that `_doc_hits` always includes, capped by the same character budget, in addition to the six excerpts when the library is over budget. Follow the scope rule. This is the manual override. The automatic threshold is the row above. Do not pin by stuffing the system prompt with no cap. | M |
| No line-level exact or regex search. | Identifiers shorter than 3 characters never enter the FTS OR query. The model gets a ~1,200-character chunk or a character page, not the matching line. Notes can be read by line range only after the model already knows the range. | App: `fts_query` keeps tokens of 3+ characters, porter, no prefix. `read_document` pages characters. `doc_read` is line-numbered markdown. Market: Open WebUI grep returns lines and 1-based numbers, cap 50, then a line-range read ([Knowledge](https://github.com/open-webui/docs/blob/main/docs/features/workspace/knowledge.mdx)). The 2024 error-code example is what BM25 fusion was for; Grain already has that fusion. Grep is the remaining exact-line tool, not a second index. | Add a tool beside `search_documents` / `doc_read` that runs over in-scope file text and note bodies, returns 1-based line numbers and a short window, and caps matches (the market note’s cap is 50). Quote or parameterize the pattern. Do not pass the raw string through FTS5 query syntax. Keep it inside the project scope. `kb_exec` (shell over the corpus) stays out. It is experimental and off by default in the market note. | M |
| Upload viewer cannot show a cited page. Library UI is cards plus extracted text in a `<pre>`. | A page number in the prompt has nowhere to land visually. PDF headings are a short-line heuristic, not a layout model. | App: `DocumentsView.tsx`; no PDF viewer; `.doc` and `.xls` absent from `_more_parsed`. Market: Gemini’s click-through is reported, not confirmed on the FAQ. Official text promises in-line citations and accepts PDFs ([FAQ](https://support.google.com/notebooklm/answer/16269187?hl=en)). | The citation build above (scroll the extracted text to the chunk or page marker) is the M path. A real page renderer with highlight geometry is a separate UI over the stored bytes. Do not block citations on it. Legacy `.doc` / `.xls` need a new branch in `_more_parsed`. The current office readers are zip XML. Those two formats are not. | L for a page renderer. M for `.doc` / `.xls` if a reader is added later. |
| Long files are stored and only partly searchable. | Past 80 PDF pages or 400,000 extracted characters, `read_document` and search see the capped text. The bytes remain on disk. Gemini’s fetched cap is 500,000 words or 200 MB and no page limit. | App: `MAX_PDF_PAGES`, `MAX_INDEX_CHARS`, `MAX_UPLOAD_BYTES` 20 MB, zip-bomb checks. Market: [Gemini Notebook FAQ](https://support.google.com/notebooklm/answer/16269187?hl=en). | Raise the page and character caps only with the existing zip and time limits left in place. Reindex must re-extract files still on disk so older uploads pick up the new cap. Not a first build. A 20 MB local cap can stay. The cloud 200 MB figure is a different threat model. | M |
| Backlinks as described do not include unlinked mentions. Daily notes do not apply a template, and date aliases such as `[[Today]]` are not described. | The links panel misses plain-text names. A new daily note does not start from the Daily log template. `[[Today]]` does not resolve to today’s note. `[[2026-10-02]]` already does. | App: backlinks description scans `[[` (route body not re-read). Daily route sets folder and date title. Templates are a separate client list in `templates.ts`. Market: [Obsidian backlinks](https://obsidian.md/help/backlinks), [Obsidian daily notes](https://obsidian.md/help/plugins/daily-notes), [Logseq journals](https://github.com/logseq/docs/blob/master/db-version.md). | Unlinked mentions: when building the backlinks response, also match the current title as plain text outside fenced code, and label those rows differently from real wikilinks. Daily template: on create only, seed the Daily log body from the same markdown `templates.ts` uses. Date aliases: resolve `Today` and weekday phrases in `wikilinks.ts` / `resolveWikiDoc` to the date title the daily route already uses. Confirm the backlinks SQL before editing it. The inventory did not re-read that function. | S each |
| Neighbor expansion is absent. | A hit can start mid-argument. Grain already overlaps 120 characters and prefixes the heading path, so this is smaller than it would be for a bare splitter. | App: `Retriever.search` does not load adjacent `idx`. Market: ±1 chunk added 2.64× text at +0.12 ms on ~250-token chunks. No quality delta was reported ([vstash](https://arxiv.org/pdf/2604.15484)). | After RRF, optionally attach `idx±1` from the same `chunks` or `doc_chunks` row when the six-excerpt budget still has room. Cap added characters. Grain chunks are already up to 1,200 characters, so always adding both neighbors will bloat the prompt. Ship only if a local fixture shows a real miss that the heading path did not fix. | S, and not a default |
| LLM situating paragraph is absent. “Contextual” in Grain is the heading path. | Possibly better recall on chunks that omit the document’s subject. The only numbers are from 2024, on one failure metric, with a model call per chunk. A generic summary barely helped. | [Contextual Retrieval](https://www.anthropic.com/engineering/contextual-retrieval) (2024-09-19). No 2025–2026 remeasurement was opened. Their stated cost was $1.02 per million document tokens under 2024 prompt-cache assumptions. Do not treat that as a current price. | Not now. If it is tried later, store a short prefix beside `chunker.contextualize` and reindex. Do not replace the heading path. Do not use a summary of the whole corpus as the prefix. They reported limited gains for that. | L |
| Cross-encoder or other neural reranker is absent. | Unknown on Grain’s notes. On the one 2026 SQLite measurement, small web-trained cross-encoders hurt NDCG and added 560–2100 ms. | Mixed, see section 2. Positive: Cohere in 2024, LLM window at TREC 2025. Negative on this style of stack: [vstash](https://arxiv.org/pdf/2604.15484). Open WebUI ships one and publishes no quality number. A weak extra path can lower fusion ([Balancing the Blend](https://arxiv.org/html/2508.01405v1)). | Do not add one. If a later eval on a copy of this SQLite file shows a gain that beats the latency, the positive evidence is a stronger reranker or an LLM window, not ms-marco-MiniLM or BGE-reranker-base. Keep BM25 hits that fail the cosine floor. | Do not build |
| Query splitting and hypothetical-document embeddings are absent. | Extra latency and a second failure mode. The 2024 write-up saw low performance for hypothetical-document embeddings. | App: no query decomposition in `Retriever.search`. Market: [Contextual Retrieval](https://www.anthropic.com/engineering/contextual-retrieval). TREC 2025 used a HyDE mix inside a system that placed first. The mix was not isolated as the cause ([TREC 2025 notebook](https://trec.nist.gov/pubs/trec34/papers/UTokyo.rag.pdf)). | Do not build. | Do not build |
| ANN via a vector extension is absent. KNN is brute-force numpy. | No measured latency in this tree. The market note’s 20.9 ms median is for sqlite-vec at 50,000 chunks, not for Grain. | App: `retrieval.py` `_vector_rank`. Market: [vstash](https://arxiv.org/pdf/2604.15484); [Garcia, 2024-10-02](https://alexgarcia.xyz/blog/2024/sqlite-vec-hybrid-search/index.html). Core SQLite has no vector type. | Stay on FTS5 plus the current blobs. Revisit only if a measured personal library is slow. One FTS5 index still does not take metadata filters (Garcia, 2024). Grain already applies project scope in SQL around the query. Keep that. Do not add Chroma or pgvector. | Do not build |
| Web, YouTube, and Google Docs/Slides are not document sources. | A notebook-style answer cannot be grounded on a link or a video from this pipeline. | Market: [Learn about Gemini Notebook](https://support.google.com/notebooklm/answer/16164461?hl=en). App: `POST /documents` stores bytes. Meeting audio is a different store and stays out of `doc_chunks` until an accepted summary. | Not part of the retrieval builds. A URL or Google-file importer would be a new ingest path in front of `_store_upload`, not a change to FTS. Leave it to whatever owns capture. | L, elsewhere |

---

## 5. The top three builds

Order is trust, then checkability, then the size threshold. All three stay on core SQLite and FTS5. None of them add a reranker.

### 1. Scope `doc_search` and `doc_list` to the chat project

The automatic index already hides other projects. The two tools the model uses to list and snippet notes do not. That is incorrect behavior, and it is the smallest change.

Close it as in the table: same `_scope_clause` as `chunk_search`, in `tools.py` calling `docs.py`.

**Offline acceptance.** A backend test with no network and no embedder. Fixture: project A note titled `Alpha only`, project B note titled `Beta only`, personal note titled `Personal only`, each with a distinct body sentence. Invoking `doc_search` and `doc_list` as a project A chat returns Alpha and Personal, and returns neither Beta’s title nor Beta’s sentence. The same calls as a personal chat return Personal only. A project A `search_documents` / chunk search fixture still returns Alpha and Personal, so the tool change must not narrow the path that already worked.

### 2. Make a retrieved passage checkable

The prompt already names the section and, for uploads, the page. The user-facing copy throws that away.

Close the drawer and the type first, then navigation to extracted text or the note. Scroll to chunk text or the page marker. Do not wait for a PDF renderer. When build 3 injects a whole short document, the citation names the document and does not invent a page.

**Offline acceptance.** A renderer test, no backend and no model. A file hit `{name, heading: "Methods", page: 4, idx: 0, text}` renders a label that contains `Methods` and `4`. A note hit `{title, heading}` renders the title and the heading. A hit with both heading and page missing still shows the chunk number, which is today’s fallback. Activating the file citation passes `document_id` and the chunk text to the documents view, and the view’s text contains that chunk. Activating the note citation passes `doc_id`, and the editor string contains the heading. A second backend assertion: `build_context` puts the heading and page into the excerpt header and into `context_used.chunks`, and the recorded preview stays 400 characters. Run it with `retrievalMode=bm25` so embeddings are not required.

### 3. Full text under a budget, search above it

This is the pattern Claude, Open WebUI, and Gemini Notebook actually ship: whole text while it fits, search when it does not. Grain only has the search half, plus the open page.

Close it in `_doc_hits` and `build_context` with a settings character budget. Skip retrieval under the budget. Keep six fused chunks above it. Record the mode. Dedupe page context when it repeats a body just injected whole.

**Offline acceptance.** Backend tests, embeddings off. The test sets the budget.

- Under budget: one note whose only sentence is `The kiln ships Friday`. `build_context` contains that full sentence. The retriever is not called. The context mode is full text. The citation names the note and does not include a page.
- Over budget: one upload longer than the budget, with a unique tail sentence past the first chunk. The excerpt block has at most six fenced chunks. The full upload text is not in the prompt. The retriever is called. The context mode is search.
- Dedupe: the open page is that same short note, and the budget says full text. The unique sentence appears once.
- Scope: a project B note is outside a project A full-text build, same rule as build 1.

The production budget is the setting, not a number copied from the 2024 token line. The test’s budget is whatever makes the two fixtures fall on opposite sides.

After these three, the index-status line is the next small piece. It uses a route that already exists. It is not one of the three because it does not change what the model sees.

---

## 6. Already shipped, not gaps

Do not schedule these. The app inventory marks them present, and README plus `sota-retrieval.md` “Where we are” are stale on them.

- Hybrid retrieval: FTS5 BM25 plus optional embedding KNN, RRF k = 60, similarity floor that does not drop keyword hits, per-document cap, BM25 fallback.
- Structure-aware chunks and the heading-path prefix. The 900-character packer remains only as fallback.
- Note chunks in the same fused search as uploads, including `useDocsInContext` defaulting on, background embed, reindex, content-hash dedup, and `search_documents` scopes `all|files|docs`.
- Proposed diffs: pending `doc_edit`, accept, reject, restore, chat diff, review by default, Accept all, scheduled runs forced to stay pending.

Left in place on purpose, from section 3: one markdown string, no refuse-outside-sources mode, no vector extension, no collaborator suggestion inbox.
