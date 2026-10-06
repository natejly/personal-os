# xAI developer platform and Grok agentic capabilities (as of Oct 2026)

Method note: WebFetch returns model-summarised docs, so field names are as reported, not copied from raw pages. Items marked (unverified) came from secondary sources or a single summary. xAI now brands itself "SpaceXAI"; docs live at docs.x.ai/developers/... (the old docs.x.ai/docs/guides/... paths were reorganised).

## Agent Tools API / server-side tools

**What**: Tools that run on xAI's servers. The model decides when to call them and loops until it has an answer, with no client round-trips. Launched 2025-11-19 alongside Grok 4.1 Fast, free until 2025-12-03, now paid per invocation.

**Detail**
- Built-in tools: `web_search`, `x_search`, `code_execution` (named `code_interpreter` in the OpenAI-compatible Responses API), `collections_search` (named `file_search` in the Responses API), image generation, remote MCP, image/video understanding (flags on the search tools), file attachments.
- Loop as documented: analyse query, decide, execute tool(s), feed results back, repeat, return the answer with citations. Parallel calls within a turn are common.
- Surfaces: Responses API (recommended), xAI Python SDK, OpenAI SDK, Vercel AI SDK. Chat Completions is the legacy path. The Live Search `search_parameters` migration is to the `tools` param on Responses.
- `max_turns` on the request caps agentic turns. When server-side and client-side tools are mixed, it limits only server-side calls. A client-side tool call is a checkpoint: control returns to the app, and the counter resets on the next request after you append the result. The default number is not documented in what I could read (unverified).
- Mixed mode: server tools execute automatically. Function calls pause the loop and return to the caller.
- Output items in `response.output`: `web_search_call`, `x_search_call`, `code_interpreter_call`, `mcp_call`, `reasoning`, plus the message. Server tool outputs are omitted by default because of size. Opt in per tool with `include=[...]` values.
- Streaming is recommended: tool calls and reasoning-token counts are visible live. Sync mode waits for the whole loop.
- State, two options: (1) `store` + `previous_response_id` (server keeps prompts, reasoning and responses for 30 days); (2) stateless, with `include=["reasoning.encrypted_content"]` (SDK: `use_encrypted_content=True`) and the client replays the items.
- Reasoning: `grok-4.7` returns encrypted reasoning automatically. `reasoning_effort` is low / medium / high (default) / xhigh. Reasoning cannot be turned off on the 4.5+ models. `presence_penalty`, `frequency_penalty` and `stop` are unsupported.
- Cost tracking: every `usage` carries `cost_in_usd_ticks` (1 USD = 1e10 ticks). It is the billed amount including discounts, covering all model calls and tool invocations in the request. Per-tool counts are in `usage.server_side_tool_usage_details`.

**Web search detail**
- `allowed_domains` (max 5) or `excluded_domains`, mutually exclusive.
- `enable_image_understanding` lets the model look at images while browsing.
- `enable_image_search` returns Markdown image embeds.
- User-location param exists (details unread).

**X search detail**
- Up to 20 allowed or excluded handles, mutually exclusive.
- `from_date` / `to_date` as YYYY-MM-DD.
- Image and video understanding on posts.
- Billed per item fetched, including parent and quoted posts, not per call.

**Code execution detail**
- Python only, in a sandbox with NumPy, Pandas, Matplotlib and SciPy preinstalled.
- Stateless, with no persistence between requests, limited filesystem, no outbound network.
- Docs mention time and memory limits without a number. A third-party source says a 30 s default timeout (unverified).
- Docs advise temperature 0.0-0.3 for math and the reasoning model for best code.

**Collections / file search detail**
- Upload PDF, text, CSV and other files to a collection. Semantic search. The model runs many autonomous queries (the docs' Tesla SEC filings demo made 13 searches).
- Citations use the URI form `collections://<collection_id>/files/<file_id>`.
- Can be combined with web and x search for hybrid answers.

**Remote MCP detail**
- Params: `server_url` (Streamable HTTP or SSE only), `server_label` (prefixes tool names), `server_description`, `allowed_tools`, `authorization`, `headers`.
- Empty `allowed_tools` injects every tool definition into context. Restricting it cuts context and risk.
- Several servers can be attached at once.

**Limits/pricing**
- Web search $5 per 1k calls.
- X search $5 per 1k posts fetched and $10 per 1k user profiles.
- Code execution $5 per 1k calls.
- File attachments $5 per 1k.
- Collections search $2.50 per 1k.
- Image/video understanding and remote MCP: token-only.
- Image generation: Imagine rates (see Model lineup).
- Tokens are billed on top. The agent decides how many tools to call, so cost scales with query difficulty.

**Sources**
- https://docs.x.ai/developers/tools/overview
- https://docs.x.ai/developers/tools/web-search
- https://docs.x.ai/developers/tools/x-search
- https://docs.x.ai/developers/tools/code-execution
- https://docs.x.ai/developers/tools/collections-search
- https://docs.x.ai/developers/tools/remote-mcp
- https://docs.x.ai/developers/tools/advanced-usage
- https://docs.x.ai/developers/tools/streaming-and-sync
- https://docs.x.ai/developers/pricing
- https://docs.x.ai/developers/cost-tracking
- https://docs.x.ai/developers/rest-api-reference/inference/responses
- https://x.ai/news/grok-4-1-fast (2025-11-19)
- https://docs.x.ai/developers/model-capabilities/text/reasoning

## Citations (replaces Live Search output)

**What**: Two layers. A flat `citations` URL list on every response (every source encountered, even if unused), plus inline citations in the text.

**Detail**
- Inline format is a numbered markdown link: `[[N]](url)`. This is on by default in the Responses API. In the xAI Python SDK it is off by default (`include=["inline_citations"]`), and `include=["no_inline_citations"]` disables it.
- Structured form: `output_text` blocks carry `annotations[]` of `{type:"url_citation", url, start_index, end_index, title:"1"}`. Indices follow Python slice convention (end exclusive), so a UI can rebuild or restyle chips from offsets.
- When streaming, the links arrive inline in the content chunks, and the full list lands on the final response.
- Collections hits cite `collections://...` URIs.

**Limits/pricing**: no extra charge.

**Sources**: https://docs.x.ai/developers/tools/citations

## Live Search (deprecated)

**What**: The older `search_parameters` feature on Chat Completions, with sources `web`, `x`, `news` and `rss`, date ranges, site allow/exclude, safe search and `return_citations`.

**Detail**
- Announced deprecated early Dec 2025. A tweet said removal on 2026-01-12 with 410 Gone, and another source says 2025-12-15 (the date is inconsistent across sources).
- The xAI-stated replacement benefits: agentic search with automatic follow-ups, multimodal, internal doc search, MCP mixing.
- There is no `news` or `rss` source in the new API. Equivalents are `web_search` plus `allowed_domains`, and `x_search` with date and handle filters. A Grok-side RSS tool does not exist in what I found (unverified).

**Sources**
- https://github.com/langchain-ai/langchain/issues/33961
- https://x.com/BenjaminDEKR/status/1996738390583054723

## Function calling and structured outputs

**What**: Standard JSON-schema function calling on the Responses API and Chat Completions.

**Detail**
- Parallel calls are on by default (`parallel_tool_calls` boolean). The client must process all of them before continuing.
- `tool_choice`: `"auto"` (default), `"required"`, `"none"`, or a specific function name.
- Mixed with built-in tools in the same request (see Agent Tools).
- `text.format` controls structured output. Strict JSON-schema structured outputs are supported (I did not read that page, so exact field names are unverified).
- Multi-agent models do not support function calling.

**Limits/pricing**: token-only.

**Sources**
- https://docs.x.ai/developers/tools/function-calling
- https://docs.x.ai/developers/rest-api-reference/inference/responses

## Grok Heavy / multi-agent

**What**: Parallel agents that discuss a query, with a leader agent that synthesises the final answer. Debuted as Grok 4 Heavy (July 2025), now exposed in the API as `grok-4.20-multi-agent-0309`.

**Detail**
- xAI's docs: agents "discuss and collaborate on your query" and a leader writes the answer.
- 4 agents for low/medium effort (quick research) and 16 for high/xhigh (deep analysis). In the xAI SDK the param is `agent_count`. In REST and OpenAI SDKs `reasoning.effort` controls agent count, not depth.
- Visibility: "Only the tool calls and the final response from the leader agent are sent back to the user." Sub-agent reasoning stays encrypted. Set `use_encrypted_content` to carry it across turns.
- Tools: only built-ins (web, X, code execution, collections). No function calling or custom tools. Chat Completions is unsupported, as is `max_tokens`. Responses API or xAI SDK only.
- Consumer app: Heavy is a mode (Auto / Fast / Expert / Heavy) on the SuperGrok Heavy tier, about $300/month (secondary sources, 16-agent claim unverified). UI details of what is shown were not found.
- Secondary claims of hallucination reduction (about 12% to 4.2%) and HLE 44.4% for Grok 4 Heavy with tools are unverified press numbers.

**Limits/pricing**: $1.25 in / $0.20 cached / $2.50 out per 1M tokens (under 200k context), 1M context, 20% batch discount. All sub-agent tokens and their tool calls are billed, so 16 agents costs far more than 4.

**Sources**
- https://docs.x.ai/developers/model-capabilities/text/multi-agent
- https://docs.x.ai/developers/models
- https://theairankings.com/xai/grok-4-heavy/
- https://mundobytes.com/en/What-are-the-heavy--expert--fast--and-auto-modes-in-Grok-used-for/

## Grok Build (coding agent)

**What**: xAI's open-source terminal coding agent. Early beta 2026-05-14 for SuperGrok Heavy, widened 2026-05-25 to SuperGrok and Premium+ (about $30/month). v1.0 on 2026-08-07, defaulted to Grok 4.6 at that time, and Grok 4.7 is available now. Apache-2.0 since 2026-07-16.

**Detail**
- Full-screen TUI or headless (`-p` for CI). Also speaks the Agent Client Protocol, MCP, hooks, skills and plugins.
- Plan-first flow: the agent proposes structured steps, and the user can approve all, comment per step, or rewrite the plan. Clean diffs per approved change.
- Up to 8 parallel subagents. One source says each gets its own git worktree on its own branch and the orchestrator reconciles at the end. Another describes them as child sessions. Reconcile before relying on the worktree claim (partly unverified). Arena Mode (competing outputs) is mentioned in secondary sources (unverified).
- Sandbox: OS-level (Landlock on Linux, Seatbelt on macOS), off by default. File rollback restores conversation only, not disk.
- Long-term memory (announced 2026-09-16): after each turn it extracts durable decisions and facts in the background, filters temporary or sensitive items, and exposes `/memory` and `/dream` to manage them.
- Model: `grok-build-0.1` (256k context). API calls for `grok-code-fast-1` route to it since 2026-05-15. `grok-code-fast-1` was also the model inside Cursor, Cline and similar.
- Enterprise: OIDC, team-level zero data retention, pinned policy file.
- Controversy: in July 2026 a researcher found it uploaded full repos (including secrets and files marked off-limits) to xAI cloud storage. xAI disabled it server-side and open-sourced the CLI.
- 70.8% SWE-bench Verified at launch (xAI-reported).

**Limits/pricing**: API `grok-build-0.1` is $1.00 in / $0.20 cached / $2.00 out per 1M under 200k context. CLI is included with subscriptions.

**Sources**
- https://en.wikipedia.org/wiki/Grok_Build
- https://innfactory.ai/en/ai-harness/grok-cli/
- https://www.digitalapplied.com/blog/xai-grok-build-cli-parallel-coding-agents
- https://www.kucoin.com/news/flash/xai-launches-long-term-memory-feature-in-grok-build-to-streamline-developer-workflows (2026-09)
- https://x.ai/build/changelog (403 to my fetcher, not read)

## Model lineup, defaults, pricing

**What**: Current text lineup per docs.x.ai/developers/models.

**Detail** (per 1M tokens, input / cached / output, prices under 200k context; above 200k they double)
- `grok-4.7` (released 2026-09-21): 500k context, $2.00 / $0.50 / $6.00. Latest and most capable, May 2026 cutoff. Larger base, trained for multi-hour tasks and self-verification, and trained natively on the "Grok Bot" harness. DeepSWE 71.0%, Terminal-Bench 4.0 38.0% (xAI-reported).
- `grok-4.6`: 500k, same price. `grok-4.5`: 500k, $2.00 / $0.30 / $6.00.
- `grok-4.3`: 1M, $1.25 / $0.20 / $2.50.
- `grok-4.20-0309-reasoning` and `-non-reasoning`: 1M, same as 4.3. `grok-4.20-multi-agent-0309`: same.
- `grok-build-0.1`: 256k, $1.00 / $0.20 / $2.00.
- Older: Grok 4.1 Fast (2025-11, 2M context, $0.20 / $0.50, reasoning and non-reasoning variants). No longer on the main table (probably legacy).
- Imagine: image $0.02-$0.05 each, video $0.02-$0.08 per second. Voice: speech-to-speech `grok-voice-think-fast-2.0` $0.08/min, STT $0.10-$0.20/hr, TTS $15 per 1M chars.
- Cached input is 4x-10x cheaper than fresh input. Batch API 20% off on the 4.3 and 4.20 families.
- App modes: Auto (the default) routes each query to Fast or Expert. Heavy is the multi-agent tier. Expert and Auto need SuperGrok Lite or higher (per an X post, unverified).
- The 2M context claim now applies only to the older 4.1 Fast. Current flagships top out at 500k-1M.

**Sources**
- https://docs.x.ai/developers/models
- https://www.unite.ai/spacexai-releases-grok-4-7-for-coding-and-knowledge-work/
- https://siliconangle.com/2026/09/21/spacex-launches-grok-4-7-with-long-horizon-processing-safety-upgrades/
- https://x.ai/news/grok-4-7

## Memory and scheduling primitives

**What**: Consumer-side features plus thin API state; no first-class developer memory API.

**Detail**
- API state is only stored responses (`store`, `previous_response_id`, 30-day retention) plus encrypted reasoning replay. A secondary source states persistent memory is not natively available through the API (unverified).
- Collections are the closest thing to a developer knowledge store.
- Consumer Grok memory (since April 2025) recalls past conversations. Grok Build's `/memory` and `/dream` (Sept 2026) are the agentic version. Grok Bot keeps memory across conversations and learns preferences.
- Grok Tasks (early 2026) became Automations on 2026-07-16: describe a job in plain language, pick a schedule (one-time, daily, weekday, weekly, monthly, yearly) or a trigger (email arrival, SuperGrok), with a run history. Free tier included for scheduled runs. There is no public scheduling API (unverified).
- Grok Bot (Aug 2026 beta, top tiers): always-on cloud-PC agent that logs into apps and sites without APIs, multiple bots in one conversation sharing context and delegating, asks for approval on requests.

**Sources**
- https://www.neowin.net/news/spacexai-launches-openclaw-style-grok-bot-that-can-work-across-apps-on-its-own/
- https://www.infoq.com/news/2026/08/grok-bot-agent/
- https://blockchain.news/news/grok-automations-scheduled-tasks
- https://docs.x.ai/developers/model-capabilities/text/comparison

## Distinctive ideas worth copying

1. **Return the citation as character offsets, not just a list.** `annotations` with `start_index`/`end_index` plus a separate "all sources seen" list means the UI can render chips, hover cards and a "sources considered" drawer without parsing text. Cheap to add to any answer pipeline.
2. **Per-request cost ticks including tools.** One `cost_in_usd_ticks` figure covering tokens plus every tool call, with per-tool counts (posts fetched, profiles fetched). It makes a visible "this answer cost X" line and budget guards trivial.
3. **Reasoning that can be carried statelessly.** Encrypted reasoning replayed by the client keeps multi-turn agent quality with no server session. Consider persisting each run's reasoning/tool trace alongside the messages for resume.
4. **Heavy = N parallel researchers then a leader, with only the leader streamed.** For a subagent feature, add a "deep" mode: fan out 4 independent attempts with web tools, then a leader reconciles and drops unsupported claims. Show only the leader's tool calls to keep the UI quiet. Make N the effort knob.
5. **X search as a first-class live signal, with date and handle filters and per-item billing.** If the assistant has a social or news source, expose filters (handles, date range) as tool args so the model scopes searches itself, and meter by items fetched.
6. **Domain allow/exclude lists on search (cap 5) as a per-task argument.** Lets a scheduled task say "only these sources". Mutually exclusive allow versus exclude keeps the semantics simple.
7. **Mixed tool turns with `max_turns` counting only server-side rounds.** Client-side calls act as checkpoints that reset the counter. A clean model for approval-gated tools inside an otherwise autonomous loop.
8. **Background memory extraction after every turn, with `/memory` to inspect and `/dream` to consolidate.** Same shape as auto-learn plus a consolidation pass, and the user-visible commands build trust.
9. **Plan-first with per-step comments in Grok Build.** Approve all, comment on one step, or rewrite. Pairs with scheduled tasks: review the plan once, then run unattended.
10. **Automations run history plus plain-language schedule/trigger creation.** Email-arrival trigger is the high-value one for a desktop assistant. Keep a per-run log the user can read.

Caution: Grok Build's repo-upload incident is a reminder to keep any tool that reads local files strictly opt-in and logged.
