# Grain context pipeline and large tool results

Scope: how Grain (this repo) builds the prompt and what happens to tool output, judged as a personal assistant (chat, calendar, mail, documents, memory). Context Mode is the external idea under comparison: keep raw tool output out of the model window, store it locally, and let the agent search it. `docs/research.md` was written 2026-09-29; several items it lists as unbuilt are now in the code. Where the doc and the code disagree, the code is the current mechanism.

## Where is the agent loop, and how do tool results get appended to the history that is resent every turn?

### Takeaway
One reply is an in-memory loop in `app._chat_stream`. Each round sends the whole `messages` list, including tool-role rows from earlier rounds of that same reply. The next user message does not replay those tool rows: persisted history is user and assistant prose only.

### Cited Findings
- The loop is `async def _chat_stream` in [backend/personal_os/app.py](backend/personal_os/app.py) (starts ~L1380). It builds context, then `while True` (~L1765) calls `llm.stream_chat` and, when the model returns tool calls, appends an assistant turn and one `role: "tool"` message per call (~L1883–L2341).
- The provider call is `async def stream_chat` in [backend/personal_os/llm.py](backend/personal_os/llm.py) (~L564). It sends the `messages` argument it is given. It does not store history.
- After a tool runs, the model-facing string is `tool_results.for_model(...)`, then `messages.append({"role": "tool", "tool_call_id": c["id"], "content": content})` ([app.py](backend/personal_os/app.py) ~L2333–L2341). The same list is passed to `stream_chat` on the next round (~L1817).
- Before that call, `compaction.microcompact(messages, ...)` may replace older tool `content` strings in that same list ([app.py](backend/personal_os/app.py) ~L1802–L1804; [compaction.py](backend/personal_os/compaction.py) `microcompact`).
- The assistant row saved for the user is the model’s written text, plus `tool_events` and `context_used`. `Conversations.finish_message` writes `content`, `tool_events`, `context_used`, `trace`, `reasoning` ([repos.py](backend/personal_os/repos.py) ~L185–L191). `tool_events` hold `result_preview` from `summarize_result(result)` ([app.py](backend/personal_os/app.py) ~L2281, ~L2296), which is separate from the string put in `messages`.
- The next turn’s replay is `compaction.prepare_history` → `Compactor.build_history` ([app.py](backend/personal_os/app.py) ~L1437–L1439; [compaction.py](backend/personal_os/compaction.py) ~L134–L140, ~L171–L187). Rows come from `Conversations.history_rows`, which selects `id, role, content` where `content != ''` ([repos.py](backend/personal_os/repos.py) ~L210–L217). Tool-role messages are never rows in `messages`. Empty assistant content is dropped. `tool_events` are not selected.
- Compaction’s summarizer reads those same content strings, each clipped to `MAX_ROW_CHARS = 6000` ([compaction.py](backend/personal_os/compaction.py) ~L49, ~L157). It does not see tool JSON.
- Subagents have a parallel loop: `Subagents._loop` appends the same assistant and tool roles onto `ch.messages`, and `microcompact` runs there too ([subagents.py](backend/personal_os/subagents.py) ~L653–L681). `_exec` returns `self.results.for_model(...)` when a results store exists (~L796–L799).

### Inferences
- “Resent every turn” is true inside one reply (every tool round). It is false across user messages. A later question can use a tool result only if the assistant wrote it into its reply, or the user asks again and the model calls the tool again.
- The single choke point for model-facing tool text, for both built-in tools and MCP tools, is `ToolResults.for_model` called from `_chat_stream` and from `Subagents._exec`.

### Gaps
- Whether any provider path other than `stream_chat` (for example a non-streaming complete used by learning or compaction) ever receives tool-role messages was not traced past `llm.complete`, which takes a caller-supplied message list and is used for the summary, not the chat loop ([llm.py](backend/personal_os/llm.py) `complete` ~L720; [compaction.py](backend/personal_os/compaction.py) ~L161).

## Is there already truncation, summarization, or a size cap on tool results? What are the thresholds?

### Takeaway
Yes. Results whose JSON is longer than 4,000 characters are stored in SQLite and replaced by a handle (preview, shape, `result_id`). Several tools also cut their own payload before that, so the stored blob is sometimes already a tail or a slice. There is no search over the stored blob, only character paging.

### Cited Findings
- Handle policy in [working.py](backend/personal_os/working.py): `INLINE_CHARS = 4000`, `PREVIEW_CHARS = 2000`, `MAX_STORED_CHARS = 2_000_000`, `READ_CHARS = 4000`, `MAX_READ_CHARS = 20000` (~L157–L163). `ToolResults.for_model` returns the JSON blob when `len(blob) <= INLINE_CHARS`; otherwise it stores `content[:MAX_STORED_CHARS]` and returns `{result_id, tool, total_chars, shape, preview, note}` (~L215–L230). `total` is the unsliced length, so a blob past 2,000,000 characters is stored short while `total_chars` still reports the original length.
- Preview text is `summarize_result(result, PREVIEW_CHARS)`. Default limit on the function itself is 1500 ([tools.py](backend/personal_os/tools.py) ~L1152). For a dict it binary-searches a prefix of the longest list; otherwise it returns `{"truncated": true, "preview": s[:limit-200]}` (~L1160–L1176). The UI’s `result_preview` calls `summarize_result(result)` with that default 1500 ([app.py](backend/personal_os/app.py) ~L2281), not the 2000-character handle preview.
- Paging tool: `read_tool_result(result_id, offset=0, limit=4000)` in `Toolbox._register_working` ([tools.py](backend/personal_os/tools.py) ~L1215–L1232). `ToolResults.read` clamps `limit` to `MAX_READ_CHARS` (20000) ([working.py](backend/personal_os/working.py) ~L241–L247). Reads are scoped with `conversation_id`; another chat’s id returns nothing (~L241–L245).
- If the stored shape has `untrusted: true`, `read_tool_result` sets `ctx["tainted"]` ([tools.py](backend/personal_os/tools.py) ~L1223–L1225). `for_model(..., untrusted=True)` sets that flag when the run is already tainted ([working.py](backend/personal_os/working.py) ~L223–L225; [app.py](backend/personal_os/app.py) ~L2335–L2338).
- Microcompaction, once estimated tokens exceed `microAt` (default 0.5) of `contextWindow` (default 128000): keep the newest `microKeep` (default 3) tool results that the model has already seen; replace older ones of at least `MICRO_MIN_CHARS = 400` with a stub ([compaction.py](backend/personal_os/compaction.py) ~L47–L49, ~L193–L234; defaults in [llm.py](backend/personal_os/llm.py) ~L126–L131). The stub keeps `result_id` only if the content JSON already had one. Inline results (under 4,000 characters) that are still at least 400 characters are cleared with `result_id: null` and the note `cleared to save context; call read_tool_result(result_id) to re-read` (~L47, ~L193–L209).
- History compaction (separate from tool handles): when estimated history tokens plus system tokens exceed `compactAt` (default 0.7) times `contextWindow`, and `autoCompact` is true, older messages fold into a rolling summary. Defaults: `compactKeepRecent = 8`, summary prompt says “Stay under about 800 words” ([compaction.py](backend/personal_os/compaction.py) ~L51–L62, ~L146, ~L180–L182; [llm.py](backend/personal_os/llm.py) ~L125–L129). `estimate_tokens` is `max(1, len(text) // 4)` ([context.py](backend/personal_os/context.py) ~L10–L11). The settings comment says these are estimates, not provider counts ([llm.py](backend/personal_os/llm.py) ~L125).
- Run budgets, which stop the loop rather than clip one result: `maxToolRounds = 25`, `maxRunTokens = 200_000`, `maxRunSeconds = 300`, `maxRunCost = 0.50` ([llm.py](backend/personal_os/llm.py) ~L120, ~L139–L142). Soft nudge at `budget.fraction() >= 0.6` ([app.py](backend/personal_os/app.py) ~L2357–L2359). `Budget` is defined ~L1243.
- Retention: `retainToolResultDays = 30` deletes rows from `tool_results` ([llm.py](backend/personal_os/llm.py) ~L150; [retention.py](backend/personal_os/retention.py) ~L38–L44). Shell spills are deleted sooner: `SPILL_DAYS = 7` for `tool='shell_run'` ([shell.py](backend/personal_os/shell.py) ~L48, ~L532–L541).
- Per-tool cuts that happen before `for_model` (so the handle, when one is created, stores the already-cut text):
  - `fetch_url`: `max_chars` default 12000, clamped `max(1000, min(int(max_chars), 40000))` ([tools.py](backend/personal_os/tools.py) ~L907, ~L970). HTTP body kept up to `MAX_BODY = 5_000_000` bytes ([reach.py](backend/personal_os/reach.py) ~L50). Cache stores at most `MAX_CACHE_BODY = 2 * 1024 * 1024` ([webread.py](backend/personal_os/webread.py) ~L16).
  - `gmail_get`: `body[:max_chars]` with `max_chars: int = 8000` ([google.py](backend/personal_os/google.py) ~L821–L833). The returned dict in that function does not add a `truncated` field.
  - `drive_read` default `max_chars = 8000`; `docs_get` default `max_chars = 20000` ([google.py](backend/personal_os/google.py) ~L1083, ~L1118).
  - `read_local`: file size cap `MAX_READ_BYTES = 20 * 1024 * 1024`; window `n = max(1, min(int(length), 30000))`, tool default `length = 8000` ([mac.py](backend/personal_os/mac.py) ~L34, ~L184–L198; [tools.py](backend/personal_os/tools.py) ~L2207).
  - `run_python` (no tool bridge): stdout last 20,000 characters, stderr last 8,000; process killed after `HARD_CAP = 4_000_000` bytes, ring buffer `TAIL_KEEP = 64_000` ([sandbox.py](backend/personal_os/sandbox.py) ~L256–L257, ~L426–L434).
  - Bridged `run_python`: stdout kept in the result is `STDOUT_KEEP = 50_000` (40% head, 60% tail); full stdout stored via `ToolResults.store` when longer ([toolbridge.py](backend/personal_os/toolbridge.py) ~L27–L28, ~L128–L134, ~L218–L223). `MAX_CALLS = 50`, `MAX_SECONDS = 300`.
  - `shell_run`: `TRUNC_LINES, TRUNC_BYTES = 2000, 50_000` (tail) ([shell.py](backend/personal_os/shell.py) ~L42, ~L175–L185). When cut, the full scrubbed output is `store`d and `result_id` is put on the already-truncated result dict (~L716–L721). That dict is then passed through `for_model`, which will wrap it again if its JSON exceeds 4,000 characters.
  - MCP: `MAX_RESULT_CHARS = 20_000`; text is sliced and a truncation marker appended inside `_result_dict` before the chat loop sees it ([mcp_client.py](backend/personal_os/mcp_client.py) ~L67, ~L228–L240).
  - Subagent report back to the parent: `RESULT_CHARS = 6000` ([subagents.py](backend/personal_os/subagents.py) ~L38, ~L911–L923). Full transcript stored as tool `agent_transcript` (~L895–L898). If `results` is missing, `_exec` falls back to `blob[:8000] + "...[truncated]"` (~L798–L799).
  - `calendar_events` briefs clip `description` to 120 characters and page at 30 ([tools.py](backend/personal_os/tools.py) ~L1294–L1303).
  - `web_search`: `max_results` clamped to 10, fetch window `min(off + n, 25)` ([tools.py](backend/personal_os/tools.py) ~L887–L898).
  - `gmail_search`: `max_results` clamped to 100 ([tools.py](backend/personal_os/tools.py) ~L1489–L1492). Provider list uses `maxResults` up to 50 ([google.py](backend/personal_os/google.py) ~L712).

### Inferences
- The 4,000-character gate already is the Context Mode split for one reply: small JSON stays in the window; large JSON becomes a pointer plus a short preview. The pointer is an offset cursor (`read_tool_result`), not a search.
- Tools that cut first (MCP at 20,000 characters, Gmail body at 8,000, Python stdout at 20,000, shell at 50 KB) throw away or hide text that `for_model` never sees. A later FTS index of `tool_results.content` would index that already-cut text, except where shell or the Python bridge called `store` on the full string.
- Mid-size results (400–4,000 characters) can be microcompacted out of the live window with no recoverable id.

### Gaps
- No measurement in this pass of how often real Gmail bodies, fetches, or calendar payloads cross 4,000 characters. The thresholds above are the code, not observed traffic.
- `docs/research.md` F7 says `summarize_result(..., 24000)` then a raw slice. The function now structural-truncates and defaults to 1,500 ([tools.py](backend/personal_os/tools.py) ~L1152). The roadmap sentence is stale.

## How does context assembly work, and is there a token budget?

### Takeaway
`build_context` concatenates a stable system prefix and per-turn retrieval blocks, each with its own clip. There is no budget that drops memories, graph triples, or excerpts until the sum fits a token cap. Token estimates drive compaction and the context inspector, using `len // 4`.

### Cited Findings
- Entry point: `build_context` in [context.py](backend/personal_os/context.py) ~L93. Called from `_chat_stream` after `_doc_hits` and `_memory_hits` ([app.py](backend/personal_os/app.py) ~L1427–L1436). Returns `(system_prompt, context_used)` including `tokens_estimate`.
- Stable prefix (`parts`): global `systemPrompt`, project name/description and project `system_prompt`, approved skills, writing-style block ([context.py](backend/personal_os/context.py) ~L115–L190). No character cap on the user’s system prompt.
- Volatile blocks, query-dependent: page, memories, graph neighborhood, document excerpts, activity, meetings (~L126–L206).
- `layout_messages` ([context.py](backend/personal_os/context.py) ~L77–L90): when `cacheLayout` is true (default, [llm.py](backend/personal_os/llm.py) ~L124), the stable system message comes first, then history, then one system message of volatile blocks inserted immediately before the newest user message. `_chat_stream` also appends tool hints onto the stable prefix ([app.py](backend/personal_os/app.py) ~L1634–L1648).
- Page: `PAGE_DETAIL_LIMIT = 6000`, `PAGE_SELECTION_LIMIT = 2000`, on-screen refs capped at 40 ([context.py](backend/personal_os/context.py) ~L16–L17, ~L51–L71).
- Memories: hybrid search limit 40 when embeddings exist (`_memory_hits`, [app.py](backend/personal_os/app.py) ~L3651–L3660; `MemoryIndex.search` default path). Fallback `Memories.for_context` limit 40, plus up to 15 BM25 hits, deduped, then `[:limit]` ([repos.py](backend/personal_os/repos.py) ~L367–L385). Each line in the prompt is clipped with `_one_line(..., 500)` ([context.py](backend/personal_os/context.py) ~L136).
- Graph: `Graph.neighborhood(..., max_nodes: int = 30)` ([repos.py](backend/personal_os/repos.py) ~L500–L520). Prompt lines clip labels to 200 characters, relation to 80, type to 40, properties to 200 ([context.py](backend/personal_os/context.py) ~L145–L146).
- Documents: `Retriever.search` default `limit=6`, candidates `retrievalCandidates` default 20, `retrievalPerDocCap` default 3, `retrievalMinSimilarity` 0.25, mode default `hybrid` ([retrieval.py](backend/personal_os/retrieval.py) ~L204–L209, ~L243–L259; [llm.py](backend/personal_os/llm.py) ~L252–L262). Uploaded-file chunks are `CHUNK_SIZE = 900` with overlap 120 ([repos.py](backend/personal_os/repos.py) ~L524–L525). User-doc chunker `MAX_CHARS = 1200` ([chunker.py](backend/personal_os/chunker.py) ~L13). The prompt fences the full hit text; the inspector copy stores `text[:400]` ([context.py](backend/personal_os/context.py) ~L157–L160). `useDocsInContext` false drops `source == "doc"` hits (~L154–L155).
- Skills: inline until `skillsInlineBudget` default 6000 characters, then a manifest plus `skill_view` ([context.py](backend/personal_os/context.py) ~L169–L180; [llm.py](backend/personal_os/llm.py) ~L138).
- Activity block capped at `max_chars: int = 4000` ([activity.py](backend/personal_os/activity.py) ~L1884–L1907). Meetings block `max_chars: int = 3000`, “Accepted notes and headlines only — never the raw transcript” ([meetings.py](backend/personal_os/meetings.py) ~L2109–L2113).
- Per-chat switches read in `build_context`: `useMemory`, `useGraph`, `useDocuments`, `useSkills`, `useStyle`, `useActivity`, `useMeetings` ([context.py](backend/personal_os/context.py)).
- The context inspector reads `context_used` off the latest assistant message: memories, nodes, chunks (400-character preview), skills, and `tokens_estimate` ([ContextDrawer.tsx](src/renderer/src/components/ContextDrawer.tsx) ~L59–L64, ~L95–L123; type [types.ts](src/shared/types.ts) `ContextUsed` ~L14–L32). It does not list tool results.

### Inferences
- Assembly is “include the retrieved set, clip each piece,” not “fill a token budget.” R4 in the roadmap (“token-budgeted context assembly”) is still open for this block. Tool-result handles are a different mechanism and do not implement R4.
- Six document excerpts of ~900–1,200 characters, forty memory lines of 500 characters, and a 6,000-character page can land in one system message with no further trim. Compaction only looks at chat history, after this block is already built (`prepare_history` takes `system_tokens` as `used["tokens_estimate"]` of the system string, [app.py](backend/personal_os/app.py) ~L1438).

### Gaps
- Exact injected skill caps `MAX_INJECTED_SKILLS` and `MAX_MANIFEST_SKILLS` live in `learn.py` and were not quoted here.
- No count of how many tokens a typical Grain turn actually spends on the volatile block versus tool results.

## Does Grain already have an MCP client that can attach external servers, and how are those tools registered and permissioned?

### Takeaway
Yes. Stdio and HTTP servers are supervised in-process. Discovered tools default to ask, are marked external, and their results are truncated to 20,000 characters before the normal handle path. An external server does not observe built-in tool results.

### Cited Findings
- Client: `McpClient` in [mcp_client.py](backend/personal_os/mcp_client.py) ~L509. Transports: `stdio_client` and `streamable_http_client` (imports ~L39–L40). One supervisor task per server owns the session (~L15–L19, class `_Supervisor` ~L255). `sync()` starts, stops, and restarts from `McpServers` rows (~L528–L557).
- Timeouts: `CONNECT_TIMEOUT = 20.0`, `CALL_TIMEOUT = 45.0`, `READY_TIMEOUT = 8.0` (~L50–L52).
- Registration: `McpServers.sync_tools` stores name, description, parameters, danger, schema hash ([mcp_servers.py](backend/personal_os/mcp_servers.py) class ~L224). Slugs are namespaced. `tool_export` sets `"danger": MCP_DANGER` ([mcp_client.py](backend/personal_os/mcp_client.py) ~L249–L252). `MCP_DANGER = DEFAULT_DANGER` and `DEFAULT_DANGER = "external"` ([mcp_servers.py](backend/personal_os/mcp_servers.py) ~L31, [mcp_client.py](backend/personal_os/mcp_client.py) ~L69–L73). Comment: annotations and descriptions cannot lower danger; only a user grant can.
- Default mode `DEFAULT_MODE = "ask"`, modes `("on", "ask", "off")` ([mcp_servers.py](backend/personal_os/mcp_servers.py) ~L29–L32). `effective_mode`: chat grant, then project, then global, then default. An `on` grant decays to `ask` when `schema_hash` no longer matches (~L516–L536).
- Offering: `_mcp_tooling` skips tools whose server is not ready, skips `off`, and prefixes the description with the server name and “third-party MCP connector” ([app.py](backend/personal_os/app.py) ~L506–L535). Drifted tools are not offered (`mcp_drift.offerable`, ~L519).
- When ready MCP schemas exceed `mcpDeferAbove` (default 12), the model gets `mcp_tool_search` instead of every schema ([llm.py](backend/personal_os/llm.py) ~L136; [app.py](backend/personal_os/app.py) ~L1498–L1554). Search is in-memory BM25 over name/description/args, `DEFAULT_LIMIT = 5`, `MAX_LIMIT = 10` ([mcp_search.py](backend/personal_os/mcp_search.py) ~L1–L19). A hit’s slug is added to `modes` for the next round ([app.py](backend/personal_os/app.py) ~L2342–L2348).
- Call path: permission is not decided in `McpClient.call` (comment ~L606). `_gate` forces `ask` when mode is `on` and the run is tainted ([app.py](backend/personal_os/app.py) ~L538–L546). `_mcp_call` turns errors into tool errors (~L563–L574). Success returns the dict from `_result_dict`, already cut at 20,000 characters, then `for_model` may spill it again.
- MCP results taint the reply ([app.py](backend/personal_os/app.py) ~L2288–L2295).
- Built-in name `mcp_tool_search` is reserved in [mcp_servers.py](backend/personal_os/mcp_servers.py) ~L53 so a connector cannot shadow it.

### Inferences
- Attachment (a), running Context Mode as an external MCP server, fits the client: add a stdio or HTTP server, tools appear as ask-by-default external tools, and past 12 ready tools they are behind `mcp_tool_search`.
- That server never receives Gmail, calendar, document, or web results. Those are produced inside `Toolbox` and appended in `_chat_stream`. An external search index only contains what the model chooses to send it, or what that server fetches itself.
- Each external call is a permission event (default ask) and a taint source. For a personal assistant that already has mail and calendar tools, routing those through a second server duplicates the tools and adds cards.

### Gaps
- The exact SQLite tables for `mcp_servers` / `mcp_grants` were not copied line-for-line; grant resolution behavior is from `effective_mode`.
- Whether HTTP servers need OAuth before tools list (`mcp_oauth.py`, `McpNeedsAuth`) was not walked for a hypothetical Context Mode process. Stdio needs no OAuth.

## Which built-in tools return large payloads?

### Takeaway
The payloads that blow past a few thousand characters are web page text, full email and Drive/Docs bodies, local file windows, Python and shell output, MCP tool text, and subagent transcripts. Calendar lists, web snippets, and Gmail search hits are built as short rows and usually stay inline.

### Cited Findings
- Large or windowed, from the caps in the truncation section: `fetch_url` (up to 40,000 characters of page text), `gmail_read` → `gmail_get` (8,000-character body), `gdrive` read (8,000), Google Docs get (20,000), `read_local_file` (up to 30,000 of a file up to 20 MB), `run_python` / `sandbox_exec` (20,000-character stdout tail, or 50,000 head/tail when bridged), `shell_run` (50 KB / 2,000 lines, full text stored aside), MCP tools (20,000), `agent_spawn` report (6,000) with full transcript stored.
- `web_search` returns titles, URLs, and snippets, at most 10 per page ([tools.py](backend/personal_os/tools.py) ~L887–L899; [websearch.py](backend/personal_os/websearch.py) ~L79–L88). The tool text says to call `fetch_url` for the full page.
- `gmail_search` returns headers and snippets ([tools.py](backend/personal_os/tools.py) ~L1493). `gmail_read` is the full-body tool.
- `calendar_events` returns brief events (description 120 characters, page size 30) ([tools.py](backend/personal_os/tools.py) ~L1294–L1306). `calendar_get` is the full event.
- `search_documents` / `doc_search` / `meeting_search` return paged hits or snippets, not whole files ([tools.py](backend/personal_os/tools.py) ~L764–L777, ~L1901–L1904, ~L2088–L2094). Injected excerpts are a separate path (`build_context`), not a tool result.
- Meeting transcripts are indexed for `meeting_search` but the system prompt gets notes, not the transcript ([meetings.py](backend/personal_os/meetings.py) ~L2109–L2113).
- `taints=True` on the large readers (`fetch_url`, `gmail_read`, `gmail_search`, `read_local_file`, `calendar_events`, `calendar_get`) ([tools.py](backend/personal_os/tools.py) at those `ToolSpec`s). Taint sticks for the conversation ([app.py](backend/personal_os/app.py) ~L1473–L1475).

### Inferences
- On this app, the expensive context is a fetched page, a long email, a file read, or a script/shell log during one reply. It is not a multi-megabyte codebase the model explores for many turns. Cross-turn replay already omits tool JSON.
- Calendar and mail-search answers are supposed to be short on purpose. Replacing those with pointers would add a round for data the model already has.

### Gaps
- `fs_grep` / `fs_glob` match caps (`GREP_CAP`, `GLOB_CAP` in [fsx.py](backend/personal_os/fsx.py)) were seen as truncation flags but the numeric caps were not quoted in this pass.
- Image payloads from `run_python` are stripped before the model (`images` popped, names listed as `images_shown_to_user`) ([app.py](backend/personal_os/app.py) ~L2279–L2328). Byte size of those images was not measured.

## Is there an existing FTS5 store we could reuse, and what does it index?

### Takeaway
FTS5 exists for memories, uploaded-file chunks, the user’s own docs, doc chunks, and meeting title/notes/transcript. `tool_results` is a plain `TEXT` column with no FTS index. Reusing a document index for tool output would mix third-party text into the user’s knowledge base.

### Cited Findings
- [db.py](backend/personal_os/db.py) module docstring: “FTS5 for document + memory search” (~L1).
  - `chunks_fts(text, chunk_id UNINDEXED, document_id UNINDEXED, tokenize='porter unicode61')` (~L438–L440) — uploaded file chunks.
  - `memories_fts(content, memory_id UNINDEXED, tokenize='porter unicode61')` (~L441–L443).
- [docs.py](backend/personal_os/docs.py): `docs_fts(title, content, doc_id UNINDEXED, ...)` (~L71–L73); `doc_chunks_fts(text, chunk_id UNINDEXED, doc_id UNINDEXED, ...)` (~L98–L100).
- [meetings.py](backend/personal_os/meetings.py): `meetings_fts(title, notes, enhanced, transcript, meeting_id UNINDEXED, tokenize='porter unicode61')` (~L159–L161). Comment: no FTS triggers; writers reindex by hand; virtual tables are not covered by `ON DELETE CASCADE`.
- `tool_results` schema is a normal table: `content TEXT`, `shape TEXT`, no `USING fts5` ([working.py](backend/personal_os/working.py) ~L34–L44). Lookup is by id and conversation, then a character slice (`read`). `list` returns id, tool, size, shape, not a query ([working.py](backend/personal_os/working.py) ~L258–L263).
- `mcp_tool_search` is an in-memory tokenizer, not FTS5 ([mcp_search.py](backend/personal_os/mcp_search.py) ~L1–L27).
- Uploaded chunks are ~900 characters ([repos.py](backend/personal_os/repos.py) `CHUNK_SIZE`); doc chunks ~1,200 ([chunker.py](backend/personal_os/chunker.py) `MAX_CHARS`). Hybrid rank over those stores is `Retriever.search` ([retrieval.py](backend/personal_os/retrieval.py)).

### Inferences
- The table to extend is `tool_results`, with a new `tool_results_fts` (or an FTS column on `content`) keyed by `result_id` and `conversation_id`. Same conversation scope and `untrusted` shape flag as `read`.
- Pointing `chunks_fts` or `memories_fts` at tool output would put mail, web, and shell text on the retrieval path that `build_context` injects into later chats, and it would ignore the 30-day (or 7-day shell) deletion of `tool_results`.

### Gaps
- Whether FTS5 is compiled into the SQLite that ships with the app was not checked at runtime. The schema is created with `CREATE VIRTUAL TABLE ... USING fts5`, so the running database already has it or those tables would have failed.
- No existing query helper was found that full-text-searches `tool_results`. Absence is from reading `ToolResults` and searching for `tool_results` FTS; a second index in an unlisted file is unlikely but not proven empty.

## What would break if raw tool output were replaced with a pointer?

### Takeaway
Replacing every result with a pointer would force extra rounds for short calendar, mail-snippet, and search hits that are the answer. The context inspector and tool cards would keep working only if they still receive the real result preview. A search or page tool must keep conversation scope and must re-taint untrusted blobs. Across user turns, raw tool output is already absent from the prompt.

### Cited Findings
- Small results are returned whole so “the common case is unchanged” ([working.py](backend/personal_os/working.py) ~L12–L13, ~L157–L158). `calendar_events`, `gmail_search`, and `web_search` are shaped as short rows (see the payload section). The model answers from that JSON inside the same reply because it is appended before the next `stream_chat`.
- `fetch_url`’s default window is 12,000 characters ([tools.py](backend/personal_os/tools.py) ~L970), which is above `INLINE_CHARS`, so a normal page fetch is already a handle whose preview is ~2,000 characters. The model must call `read_tool_result` to see the rest. That is the current behavior, not a proposal.
- Tool cards render `event.result_preview`, parsed as JSON for mail and calendar ([EmailCard.tsx](src/renderer/src/components/toolcards/EmailCard.tsx) ~L346, ~L438; [types.ts](src/shared/types.ts) `ToolEvent.result_preview` ~L386–L390). That preview is `summarize_result(result)` of the real result, computed before `for_model` ([app.py](backend/personal_os/app.py) ~L2281 vs ~L2338). The inspector shows `context_used`, not tool bodies ([ContextDrawer.tsx](src/renderer/src/components/ContextDrawer.tsx); [types.ts](src/shared/types.ts) `ContextUsed`).
- Permission checks run before the tool executes (`_gate`, approval cards, `permrules` in the loop around [app.py](backend/personal_os/app.py) ~L1905–L2268). Swapping the result string does not skip that gate. `read_tool_result` is registered as group `"context"` with no `taints=True` on the spec ([tools.py](backend/personal_os/tools.py) ~L1227–L1233); taint is applied only when `shape.untrusted` is set (~L1223–L1225).
- Taint is sticky on the conversation because injected instructions remain in replayed history ([app.py](backend/personal_os/app.py) ~L1473–L1475). Compaction “never clears a conversation’s taint” ([compaction.py](backend/personal_os/compaction.py) ~L14).
- Microcompact stubs for inline results have `result_id: null` ([compaction.py](backend/personal_os/compaction.py) ~L193–L209). A pointer that was never stored cannot be read back.
- Stored blobs expire: 30 days for `tool_results`, 7 days for `shell_run` rows ([retention.py](backend/personal_os/retention.py); [shell.py](backend/personal_os/shell.py)). A later `read_tool_result` then errors “No stored result with id …” ([tools.py](backend/personal_os/tools.py) ~L1217–L1222).
- Next-turn history is prose only ([repos.py](backend/personal_os/repos.py) `history_rows`). The model’s written answer is what survives. A pointer in a tool message does not change that, because tool messages are not replayed.

### Inferences
- Safe place to change model-facing text: `ToolResults.for_model` (and the shell/bridge `store` calls that run before it). Leave `result_preview` as `summarize_result` of the real object so cards keep parsing mail and calendar JSON.
- A search tool beside `read_tool_result` should take `conversation_id` from ctx, refuse other chats’ rows, and set `tainted` when any hit has `shape.untrusted`. Otherwise a standing “always” grant could send mail using text the search just pulled back in.
- Spilling results that are already under 4,000 characters would store them for microcompaction, which fixes the null-`result_id` stub, and would cost a round whenever the model actually needs those bytes. For calendar briefs and snippets, the model needs them immediately.
- External MCP Context Mode does not fix Grain’s own `fetch_url` / `gmail_read` / `read_local_file` path. Those results never leave the process unless the model calls the external tool. Internal FTS on `tool_results` matches the data the assistant already fetched.

### Gaps
- No UI test in this pass confirmed that a handle-shaped `result_preview` breaks a card. The code path keeps preview and model content separate, so the break appears only if that split is removed.
- How often microcompact fires on real chats (half of 128,000 estimated tokens) was not measured. Short personal-assistant turns may never hit it; long web-plus-mail runs will.

## Overlap with docs/research.md Track 2 (R4) and the agentic notes on tool output

### Takeaway
R4 is still a gap, and it is about the assembled system prompt, not tool results. The tool-output design in the roadmap (G30, G33, G34, G36, G37) is largely already implemented. Context Mode’s remaining difference from Grain is search over the stored blob, not the pointer itself.

### Cited Findings
- Track 2 table, [docs/research.md](docs/research.md) ~L65–L80: R4 is “Token-budgeted context assembly and pinned ‘full document’ mode”, effort S, ordered after R1–R3 and R5. The “where we stand” blurb (~L5–L13) still says BM25-only documents, top-6 excerpts, and a context inspector. The code now has hybrid retrieval (`retrievalMode: "hybrid"`, [llm.py](backend/personal_os/llm.py) ~L252–L256; [retrieval.py](backend/personal_os/retrieval.py)) and still has no token budget inside `build_context` (see the assembly section). Top-6 excerpts remain the default `Retriever.search` limit.
- Track 5 “also absent” list includes context compaction, MCP, and file-system tools ([docs/research.md](docs/research.md) ~L264). The code has `compaction.py`, `mcp_client.py`, and `read_local_file` / `fsx.py`. Treat that paragraph as a 2026-09-29 snapshot.
- Evidence paragraph the roadmap uses for tool output ([docs/research.md](docs/research.md) ~L278): “Store results whole in SQLite, put a handle plus preview in context.” That sentence is what `ToolResults` implements, with the caveat that several tools slice before store and that storage stops at 2,000,000 characters.
- Agentic build list ([docs/research.md](docs/research.md) ~L328–L337):
  - G30 “Pagination + `total`/`has_more` replacing the 24,000-char truncation” — `page()` and per-tool `next_offset` / `has_more` exist; `fetch_url` sets `truncated`, `total_chars`, `next_offset` ([tools.py](backend/personal_os/tools.py) ~L245, ~L978).
  - G33 “Tool results as handles: full blob in SQLite, preview + `result_id`, `read_tool_result(id, offset, limit)`” — implemented in [working.py](backend/personal_os/working.py) and [tools.py](backend/personal_os/tools.py) `_register_working`.
  - G34 “`ToolResult(model_content, user_artifacts, handle)` envelope” — partial: images go to the UI and not the model ([app.py](backend/personal_os/app.py) ~L2279), and `result_preview` is separate from `for_model`. There is no single envelope type.
  - G36 microcompaction of all but the last 3 tool results — implemented (`microKeep` default 3, `microAt` 0.5).
  - G37 full compaction with a preserve-list — implemented as `SUMMARY_PROMPT` plus `Compactor.compact`, using `extractionModel` or the chat model ([app.py](backend/personal_os/app.py) ~L1438), not a hard-coded model name in the runner.
- QM-derived A9, “compaction as an indexed summary plus a history search tool” ([docs/research.md](docs/research.md) ~L95, ~L120), is not the same as tool-result FTS. Grain’s summary is one row in `conv_summaries` ([compaction.py](backend/personal_os/compaction.py) schema ~L32–L44). There is no tool that searches old chat turns.
- Subagent guidance in the same doc (~L280): a subagent pays when the subtask generates a lot of text the parent does not need. Grain already returns a capped report (`RESULT_CHARS = 6000`) and stores the transcript behind `transcript_id` ([subagents.py](backend/personal_os/subagents.py) ~L911–L923).

### Inferences
- Do not recommend R4 as the way to adopt Context Mode. R4 would cap or pin the memory/graph/document block inside `build_context`. Context Mode would index `tool_results` and add a search tool next to `read_tool_result`.
- Do not recommend G33 or G36 as new work. The open delta versus Context Mode is: FTS (or similar) over stored tool text; store before the MCP 20,000-character slice if the full text must be searchable; give microcompact a real id for mid-size results by storing them too; keep short calendar and snippet payloads inline.
- For this product, internal spill-and-search is the fitting option. An external MCP server can be attached, and it would be permissioned like any other connector, but it would not sit on the path that already produces the large personal-data payloads.

### Gaps
- The roadmap tables have no status column that was updated when handles and compaction landed. “Already shipped” versus “still open” in this note is from reading the code, not from a changelog entry inside `docs/research.md`.
- R1 (hybrid RRF) appears implemented in `retrieval.py` even though Track 2 still lists it as work. That was only checked far enough to avoid treating the Track 2 table as a current backlog. R2 rerank and R5 citations were not verified.
