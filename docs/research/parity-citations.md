# Parity: document retrieval and citations

Compared on 2026-10-04 against the Claude API and apps, ChatGPT and the OpenAI API, NotebookLM, Perplexity and Gemini. The Grain side was read from worktree `harness-parity`. Citation work that is in flight but uncommitted, in worktree `doc-citations`, is marked "(in flight)". This file sits under `docs/research/`, so naming products is allowed here.

## What I checked in the code

- `repos.fts_query` matches terms with the regex `[A-Za-z0-9_][A-Za-z0-9_'-]{2,}`, so it is ASCII only and every term needs 3+ characters. "Q3", "AI", accented words and CJK text all drop out of BM25 (backend/personal_os/repos.py:27).
- `app._chunk_span` uses a verbatim `text.find`. `tests/test_chunk_span.py` accepts `start == -1`, so a missed highlight never fails a test (app.py:4777). `chunker.split_text` re-joins pieces with `"\n"` (chunker.py:59).
- The retrieval query is `users[-1]["content"]` for both the doc hits and the memory hits (app.py:1680/1698 → `_doc_hits` at 1707).
- `contextualize_pending` is called only from `POST /documents/embed-backfill` (app.py:4916), and nothing in `src/renderer` calls embed-backfill or reindex. The Settings copy says "When you run the embedding backfill…" (SettingsModal.tsx:257).
- In SettingsModal, `contextualChunks` is the only retrieval-tuning key with a UI.
- `webread` numbers links as `[label][n]` (webread.py:56-74). In flight, `context.CITE_RULE` asks the model for `[1]`-style citations (doc-citations/backend/personal_os/context.py:86). Both use the same bracket syntax.
- web_search and fetch_url have no card of their own, only a generic Globe row (ToolEvents.tsx:29).

## Feature comparison

| Capability | Grain | Claude (API + apps) | ChatGPT / OpenAI API | NotebookLM | Perplexity | Gemini |
|---|---|---|---|---|---|---|
| Hybrid retrieval over the user's own files | BM25 + vector RRF, optional rerank, contextual blurbs, local | Projects RAG (not documented in detail); the API leaves RAG to you | file_search vector stores, max_num_results, metadata filters | Whole-notebook grounding | Spaces files, which sometimes miss uploads | Drive/Gmail via Sources |
| The user's own notes in retrieval | Yes, Docs are chunked into the same index | n/a | n/a | Notes can be saved, with their citations kept | n/a | n/a |
| Inline citations | (in flight) `[n]` chips for file and Docs chunks only | Structured `citations[]` per text block, with locations checked by the API | `url_citation` / `file_citation` annotations with start/end index | Numbered, each a direct quote | Numbered `[n]` on every answer | `groundingSupports` segment→chunk |
| Citations parsed by the system, not free-form markdown | No, the model writes `[n]` | Yes | Yes | Yes | Yes | Yes |
| Exact quote stored with each citation | No, only a 400-char chunk preview | `cited_text` | Index range | Quote on hover | No | Segment text |
| Locator granularity | Chunk (name, heading, page) | char / page / content-block / search-result-block | Text index, file id | Passage | URL | Text segment |
| Web search citations | None | Always on; url, title, cited_text | Required to be clickable inline; Sources panel | Deep Research imports sources | Core feature | Inline + source list |
| "Sources consulted" list separate from inline citations | Only the drawer, docs only | Research reports | Sources panel and the `sources` field | Source list | Sources list | Source list |
| Click to jump and highlight the passage | ChunkViewer modal; highlight fails on split chunks | n/a (API) | Hover preview card | Viewer scrolls to and highlights the passage | Opens the page | Opens the page |
| Memory / past-chat provenance | Drawer list of memories used; not cited | Past-chat search is a visible tool call that links back to the chat | Memory (not cited) | n/a | n/a | n/a |
| Citation validity check | None | Locations always valid | Annotations parsed by the system | Quote-based | n/a | Segments parsed by the system |
| Conversation-aware retrieval query | No, raw last message | Agentic multi-search in Research | Deep research plan | n/a | Sub-query decomposition | Editable plan |
| User-chosen source scope | Per-chat useDocuments, pinned docs, project scope | Domain allow/block, connectors | Allowed sources for deep research, domain filters | Per-source checkboxes | Focus modes, domain/recency filters | Sources dropdown |
| Domain / recency filters on web search | allowed/blocked domains, site:, time_range | allowed XOR blocked, max_uses | Up to 100 domains, context size | n/a | Domain + recency filter | n/a |
| Non-Latin queries | BM25 drops them; only the vector path works | Yes | Yes | Yes | Yes | Yes |
| Index maintenance UI | Index status line only; no rebuild button | n/a | Vector stores expire after 7 days | n/a | n/a | n/a |

Sources: [Claude citations](https://platform.claude.com/docs/en/build-with-claude/citations), [Claude search results](https://platform.claude.com/docs/en/build-with-claude/search-results), [Claude web search tool](https://platform.claude.com/docs/en/agents-and-tools/tool-use/web-search-tool), [Claude Research](https://support.claude.com/en/articles/11088861-use-research-on-claude.md), [Claude chat search and memory](https://support.claude.com/en/articles/11817273-use-claude-s-chat-search-and-memory-to-build-on-previous-context), [ChatGPT deep research](https://help.openai.com/en/articles/10500283-deep-research-in-chatgpt), [ChatGPT file uploads](https://help.openai.com/en/articles/8555545-file-uploads-faq), [OpenAI web search](https://developers.openai.com/api/docs/guides/tools-web-search), [OpenAI file search](https://developers.openai.com/api/docs/guides/tools-file-search.md), [ChatGPT Sources visibility](https://seroundtable.com/openai-chatgpt-sources-less-visible-41864.html), [ChatGPT citation rate](https://geotoolbox.ai/blog/chatgpt-citations), [NotebookLM help](https://support.google.com/notebooklm/answer/14276569?hl=en), [NotebookLM Deep Research](https://blog.google/technology/google-labs/notebooklm-deep-research-file-types/), [NotebookLM limits](https://fast.io/resources/notebooklm-limits/), [NotebookLM grounded Q&A](https://freeacademy.ai/lessons/notebooklm-grounded-qa-citations), [Perplexity API](https://docs.perplexity.ai/guides/chat-completions-guide), [Perplexity Deep Research](https://hub-prod.perplexity.ai/hub/blog/introducing-perplexity-deep-research), [Perplexity upload limits](https://fast.io/resources/perplexity-file-upload-limit/), [Perplexity Spaces recall](https://www.memorylake.ai/blogs/perplexity-forgets-spaces-content), [Gemini Deep Research](https://support.google.com/gemini/answer/15719111), [Gemini Workspace sources](https://workspaceupdates.googleblog.com/2025/11/gemini-deep-research-integrates-workspace-content.html), [Gemini grounding](https://ai.google.dev/gemini-api/docs/google-search).

## Where Grain is ahead

- **Local and inspectable retrieval.** Every stage can be tuned and is visible: BM25, vectors, RRF, rerank, the floor and the per-document cap. `POST /context/preview` shows what a query would retrieve. No peer exposes its retrieval pipeline to the end user.
- **The user's own notes and uploaded files share one index.** Docs are re-chunked on every save. Among the peers, only NotebookLM's notes come close.
- **Extraction breadth offline.** PDF with OCR fallback, DOCX tables, XLSX, PPTX, EPUB and RTF, all without a cloud call.
- **Web search engines are pluggable**: Brave, Tavily, SearXNG+Exa fused, then DuckDuckGo. Domain and recency filters match the peer APIs, and the allowed/blocked lists are exclusive, as in the Claude API.
- **Taint-aware web reading.** web_search results are allow-listed for follow-up fetches, while links found inside a page are not. Peers do not surface this.

## Where Grain differs by design

- **Citations are prompted, not structured.** Grain's models are Fireworks open models behind LiteLLM, so there is no citations API. The design can still get closer to the peers by checking the model's `[n]` against a ledger the system owns and extracting the quote itself, instead of trusting the model's markers.
- **No Deep Research mode.** The agent loop, approved plans and desks already do multi-step work. A separate research product is out of scope, and so is raising tool rounds, which is an anti-goal.
- **No cloud vector store and no file expiry.** Everything stays in SQLite.

## Gaps

| # | Gap | Priority | Size |
|---|---|---|---|
| 1 | Highlighting the passage fails for split and merged chunks (`_chunk_span` looks for the text verbatim; `split_text` re-joins with `\n`) | P0 | S |
| 2 | The "Contextual chunks" toggle and reindex cannot be reached from the UI (the only caller of `contextualize_pending` is a route nothing calls) | P0 | S |
| 3 | No web citations, and fetch_url's `[text][n]` link numbers use the same `[n]` syntax as doc citations | P1 | M |
| 4 | No "Sources" footer listing what was consulted, and nothing to fall back on when the model writes no `[n]` | P1 | S |
| 5 | No check that a citation supports its sentence, and no exact quote stored with a citation | P1 | S |
| 6 | Retrieval query is the raw last message, so follow-ups retrieve badly | P1 | S |
| 7 | BM25 drops non-Latin and 2-character terms | P1 | S |
| 8 | read_document, doc_read, meeting_read and pinned docs cannot be cited | P2 | M |
| 9 | Retrieval settings without UI | P2 | S |
| 10 | A cited Docs chunk opens a read-only modal, not the editor at the passage | P2 | M |

Not proposed: indexing fetched web pages (a 1 h cache is enough for a single user), a Research mode (it leans on more tool rounds, which is an anti-goal), memory `[m]` chips (the drawer already lists the memories used, so it adds little).
