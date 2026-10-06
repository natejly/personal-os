# Context Mode platform, privacy, license, and adoption constraints

Research date: 2026-10-02. Scope is the commercial platform, the privacy boundary, Elastic License 2.0, and what that means for a closed-source local personal AI app. Mechanism of the local sandbox is covered only where it changes those answers.

Marketing figures (98%, 30×, 331,200+ developers, logo wall) are quoted as claims. No methodology that would make them measured facts was found, except the project's own fixture table for the byte-savings number.

## What is free and local vs what the $20/seat Platform adds, and what is shipping vs 2026 roadmap?

### Takeaway
The local plugin is free and is the only generally available product. Context Mode Insight is the only Solution the site marks live (v1.0) at a flat $20 per seat per month. Memory, Audit, and Cost are labeled “Coming · 2026” and “roadmap,” and the homepage says that today there is one Solution. Public pages disagree with each other on pattern counts, MCP tool counts, and adapter counts.

### Cited Findings
- Homepage positions two surfaces: a free plugin (“No cloud, no telemetry, no account”) and Context Mode Platform at “$20 / seat / month” with opt-in event forwarding. It says “Today there is one” Solution. — [context-mode.com](https://context-mode.com/)
- Live · v1.0: Context Mode Insight, “Engineering signal for AI-assisted teams,” role views named for CTO, EM, IC, CISO, FinOps, DevOps. — [context-mode.com](https://context-mode.com/)
- Coming · 2026, marked roadmap: Context Mode Memory (“Long-term memory for AI coding agents. Carry decisions, conventions, and architectural rules across sessions, teammates, and projects.”). — [context-mode.com](https://context-mode.com/)
- Coming · 2026, marked roadmap: Context Mode Audit (“Compliance trail for the AI surface… SOC2-ready audit log built from structural events”). — [context-mode.com](https://context-mode.com/)
- Coming · 2026, marked roadmap: Context Mode Cost (“Token spend × velocity, per team… FinOps lens”). — [context-mode.com](https://context-mode.com/)
- Insight price: “$20 per seat per month. No annual variant. No volume discount. No founder discount. No grandfathering.” Included list: “All 222 patterns,” “MCP endpoint with all 13 first-class tools,” “17 AI adapters,” “90-day data retention,” “7 persona views,” “REST API,” “Email support with 24-hour SLA.” The same page’s header metrics say “14 AI adapters,” not 17. — [Insight](https://context-mode.com/insight)
- Homepage platform metrics differ again: “222 behavioral patterns,” “13 remote MCP tools,” “14 AI adapters,” “7 persona views.” — [context-mode.com](https://context-mode.com/)
- Platform docs describe a shipping Insights product (dashboard, REST, remote MCP) and say the plugin “already ships events.” The docs’ Insights tab is “177 patterns across 6 categories,” and the tool catalog on that page lists nine tools: `find_blockers`, `what_changed`, `top_patterns`, `how_is_team`, `compare_teams`, `how_am_i`, `identify_risk`, `engagement_health`, `top_members`. A later sentence on the same page says “all 8 MCP tools.” — [platform docs](https://platform.context-mode.com/docs); contradicted on counts by [Insight](https://context-mode.com/insight) (222 patterns, 13 tools, nine categories whose sizes sum to 222: Adoption 24, Engagement 31, Workflow 38, Quality 42, Effectiveness 28, Efficiency 26, Risk 18, Cost 15)
- Connect path: install `context-mode`, then `curl -fsSL https://platform.context-mode.com/install.sh | bash -s -- <BOOTSTRAP_TOKEN>`, which writes `~/.context-mode/platform.json` with `api_key` (`ctxm_…`) and `platform_url` (`https://platform.context-mode.com/api/v1`). The forwarder then POSTs to `POST /api/v1/events`. — [platform docs](https://platform.context-mode.com/docs)
- Roles: owner (self+team+org), manager (managed teams), member (self only). Data store: “Cloudflare D1, fully isolated per org.” “Can I self-host the Platform? Not in v1… On-prem deployment is on the roadmap for enterprise tier.” Enterprise (stated as 200+ members): SSO, custom retention, on-prem, SLA via hello@context-mode.com. — [platform docs](https://platform.context-mode.com/docs)
- “Is there a free tier? Up to a small team size for evaluation. See pricing for current limits.” The numeric cap was not on the docs page. — [platform docs](https://platform.context-mode.com/docs)
- Docs say future Audit and “future Risk Score” would share the same `POST /api/v1/events` firehose. — [platform docs](https://platform.context-mode.com/docs)
- Free plugin surface in the current README: six sandbox MCP tools (`ctx_batch_execute`, `ctx_execute`, `ctx_execute_file`, `ctx_index`, `ctx_search`, `ctx_fetch_and_index`) plus five meta-tools (`ctx_stats`, `ctx_doctor`, `ctx_upgrade`, `ctx_purge`, `ctx_insight`). Hooks and “~98% saved” depend on the host; Zed and Antigravity IDE have no hooks and are listed at “~60% saved” via instruction files. Cursor is “Partial” session continuity because `sessionStart` is rejected by Cursor’s validator. — [README](https://github.com/mksglu/context-mode/blob/main/README.md)
- `ctx insight` / `context-mode insight` “opens the hosted Insight dashboard in your browser.” — [README](https://github.com/mksglu/context-mode/blob/main/README.md)
- Operator on the site footer: “MKSF LTD · Suite 8805 5 Brayford Square · London E1 0SG · United Kingdom.” Docs byline: “Built by B. Mert Köseoğlu.” — [context-mode.com](https://context-mode.com/); [platform docs](https://platform.context-mode.com/docs)
- “331,200+ developers” and the logo row (Microsoft, Google, Meta, and others) appear as marketing. The Insight page says “Engineers at these companies run the OSS plugin locally,” which is an install claim, not a statement that those companies are Platform customers. No method for the developer count was found. — [context-mode.com](https://context-mode.com/); [Insight](https://context-mode.com/insight)

### Inferences
- A personal app that only wants tool-output offload can stay on the free plugin and never create `platform.json`. Insight’s shipped value is org analytics (seats, teams, commit mix, adoption, a CISO view), which assumes an engineering org.
- Memory, Audit, and Cost are not available products as of the pages fetched on 2026-10-02. “Coming · 2026” with the year already underway means “later in 2026 or still unshipped,” not “available now.”
- Catalog numbers (222 vs 177 patterns, 13 vs 9 vs 8 tools, 14 vs 17 adapters) are unstable across the marketing site and the docs. Treat Insight as a live SaaS with a documented nine-tool MCP, and treat the larger marketing counts as unverified.
- The evaluation tier exists in the docs, but the seat cap is unpublished on the pages retrieved, so a single personal seat at $20 is the only priced public offer.

### Gaps
- The evaluation-tier seat limit. The docs point at “pricing” without a number.
- Whether Insight’s production pattern engine actually evaluates 222 patterns or the 177 the dashboard copy names. No independent count.
- A changelog or dated release note proving Insight’s v1.0 ship date. “Live · v1.0” is site copy.
- Whether on-prem or Memory/Audit/Cost have private betas. Public pages still mark them roadmap.

## What stays on the machine vs what is forwarded if Platform is enabled?

### Takeaway
With no `~/.context-mode/platform.json`, session events and the FTS index stay local. If that file holds a `ctxm_` key, `hooks/platform-bridge.mjs` POSTs every event field to the Platform after regex redaction and a 200-character cap per string. That includes user prompt text. It does not match the marketing line “never prompt content, never source code, never file content.”

### Cited Findings
- Marketing boundary, repeated on the homepage and Insight: “Only structural metadata — tool names, error counts, file paths.” “Never source code. Never prompt content. Never file content.” The homepage also says the local capture already includes “tool name, file path, error counts, decisions made,” and that opt-in forwards “those same events.” — [context-mode.com](https://context-mode.com/); [Insight](https://context-mode.com/insight)
- Platform docs, first paragraph: “The context-mode OSS plugin already ships events from every coding session — tool calls, errors, prompts, file edits.” Ingest is one firehose, “No per-tool routing, no client-side classification,” 2 second timeout, HTTP 201. Rows land in a “35-column `events` table.” — [platform docs](https://platform.context-mode.com/docs); contradicted by the “never prompt content” line on [context-mode.com](https://context-mode.com/)
- `maybeForward` returns immediately when config is missing. Config must be `{ api_key: "ctxm_…", platform_url }`. It then resolves a project id from the raw `projectDir` (package name, else `git remote origin` URL with scheme and credentials stripped, else directory basename) and POSTs the sanitized event plus `platform` and `ts` to `${platform_url}/events`. Comment in source: “All event fields passthrough — server-side Zod picks per event.type.” — [platform-bridge.mjs](https://github.com/mksglu/context-mode/blob/main/hooks/platform-bridge.mjs)
- Sanitization in that file: each string is run through `privacyTransform`, then cut at `MAX_FIELD_LEN = 200` with a `…[truncated]` suffix. Arrays are capped at 50 items. Nesting deeper than 4 becomes `"[depth-limited]"`. Redaction regexes cover GitHub tokens, AWS `AKIA…`, `sk-` style keys, JWTs, Slack tokens, GitLab `glpat-`, email addresses, and `\d{3}-\d{2}-\d{4}`. `$HOME` is replaced with a space, and the username segment of `/Users/…`, `/home/…`, or `\Users\…` is replaced with a space. The rest of the path remains. — [platform-bridge.mjs](https://github.com/mksglu/context-mode/blob/main/hooks/platform-bridge.mjs)
- Local capture table (what can become an event, and therefore a forwarded field): file read/edit/write/glob/grep; task and plan events; rule files “paths + content” (`CLAUDE.md` / `GEMINI.md` / `AGENTS.md`); “Every user message”; decisions parsed from phrasing like “use X instead”; git operations; errors; “Completed subagent results (first 500 chars)”; MCP tool names and counts; role/intent lines from the user prompt; “Large user-pasted data references (>1 KB).” — [README](https://github.com/mksglu/context-mode/blob/main/README.md)
- `UserPromptSubmit` builds `{ type: "user_prompt", category: "user-prompt", data: prompt, …promptFeatures }` and the hook comment says prompt-derived decision/role/intent events are routed onto the platform wire. An older commit message said the raw `data` field is kept. — [userpromptsubmit.mjs](https://github.com/mksglu/context-mode/blob/429d6f2c/hooks/userpromptsubmit.mjs)
- A session-test PRD shows file events as path-shaped strings (`file_read | …/package.json`, `file_search | VERSION in …/server.ts`) and rule snapshots as “First 400 chars of CLAUDE.md content.” — [PRD-session-test-guide.md](https://github.com/mksglu/context-mode/blob/649d2ab8bc0a73c40b350fd6752102ffa2903101/PRD-session-test-guide.md)
- Indexed tool output and fetched pages are a different store: SQLite FTS5 at `~/.context-mode/content/` (or `CONTEXT_MODE_DIR`), default URL TTL 24 hours, “14-day cleanup” of content databases. The bridge code that was read forwards events, not that database file. — [README](https://github.com/mksglu/context-mode/blob/main/README.md); [platform-bridge.mjs](https://github.com/mksglu/context-mode/blob/main/hooks/platform-bridge.mjs)
- README privacy paragraph: “Nothing leaves your machine. No telemetry, no cloud sync, no usage tracking, no account required… The SQLite databases live in your home directory and die when you're done.” The same README says session events are persisted in a per-project SQLite database and that content indexes survive restarts until the 14-day cleanup. — [README](https://github.com/mksglu/context-mode/blob/main/README.md)
- Insight’s own sample CISO answer says “2 secret-shaped paths were detected and redacted at the boundary (no exfil).” That is a product mock, not a published redaction spec. The implemented redaction is the regex list above. — [Insight](https://context-mode.com/insight); [platform-bridge.mjs](https://github.com/mksglu/context-mode/blob/main/hooks/platform-bridge.mjs)
- MCP tool arguments are redacted before local persistence for field names matching token/secret/password/cookie/private_key (case-insensitive). That note is about the session DB, not a claim that file bodies are stripped from Platform events. — [README](https://github.com/mksglu/context-mode/blob/main/README.md)

### Inferences
- Tool names: yes, forwarded (MCP tool events, latency events, error events).
- File paths: yes, forwarded, with the home directory and username blanked. Project structure, repo-relative paths, and filenames remain. A secret embedded in a path is removed only if it matches the regex list.
- Prompts: yes, forwarded when Platform is on. The hook stores the prompt in `data`, and the bridge passes fields through, truncated to 200 characters per string. “Never prompt content” is false as a description of the code.
- Decisions, role lines, and intent extracted from the prompt are also event fields, so short slices of user text leave the machine even when they are not in the `user_prompt` row.
- Source code and file contents: ordinary file events in the test guide are paths, not file bodies. Rule-file content and the first 500 characters of subagent findings are explicitly captured, and any of that text that sits in a string field is uploaded up to 200 characters. There is no field allowlist that would drop source code if an extractor puts it in `data`.
- Git remote URL (host/org/repo, credentials stripped) is added as `project` before send. That identifies the repository to MKSF’s Cloudflare D1 tenant.
- The FTS knowledge base of full tool output stays on disk unless some snippet has already been copied into an event. Enabling Platform does not, in the bridge that was read, upload `~/.context-mode/content/`.
- Retention on the server is advertised as 90 days for Insight. Local content retention is 14 days for the index, while session SQLite is described as per-project and persistent, which conflicts with “die when you're done.”

### Gaps
- The server-side Zod schema (the 35 columns and which `event.type` variants keep `data`). The client sends a passthrough envelope; the docs do not publish the union.
- A line-by-line read of `src/session/extract.ts` on current main, to prove whether a normal Read/Edit stores only a path or also a content slice. The PRD examples and the README table are the evidence used here.
- Whether `userpromptsubmit.mjs` on current main still puts the raw prompt in `data`. The copy cited is commit `429d6f2c`.
- A privacy policy or DPA. None was in the fetched pages.

## Elastic License 2.0: external process, vendoring, modification, and what is forbidden

### Takeaway
ELv2 allows use, copying, modification, and redistribution, including inside a closed-source desktop app that ships or shells out to the plugin. It forbids offering the software to third parties as a hosted or managed service that gives them a substantial set of its features, and it forbids stripping notices or license-key controls. The maintainer switched from MIT to ELv2 specifically to block a competing hosted service. The license is source-available: the grant is non-sublicensable and non-transferable.

### Cited Findings
- Repo `LICENSE` is Elastic License 2.0, “Copyright 2026 Mert Koseoglu.” Grant: “non-exclusive, royalty-free, worldwide, non-sublicensable, non-transferable license to use, copy, distribute, make available, and prepare derivative works,” subject to the limitations. — [LICENSE](https://github.com/mksglu/context-mode/blob/main/LICENSE)
- Limitation: “You may not provide the software to third parties as a hosted or managed service, where the service provides users with access to any substantial set of the features or functionality of the software.” — [LICENSE](https://github.com/mksglu/context-mode/blob/main/LICENSE); same wording on [Elastic’s license page](https://www.elastic.co/licensing/elastic-license)
- Further limitations: do not “move, change, disable, or circumvent the license key functionality” or remove functionality protected by the license key; do not “alter, remove, or obscure any licensing, copyright, or other notices.” — [LICENSE](https://github.com/mksglu/context-mode/blob/main/LICENSE)
- If you modify it, “prominent notices stating that you have modified the software.” Anyone who receives a copy must also receive these terms. — [LICENSE](https://github.com/mksglu/context-mode/blob/main/LICENSE)
- Patent license terminates if you or your company claim the software infringes a patent. No warranty. Violation terminates the license; a noticed cure within 30 days can reinstate once. — [LICENSE](https://github.com/mksglu/context-mode/blob/main/LICENSE)
- README: “Licensed under Elastic License 2.0 (source-available). You can use it, fork it, modify it, and distribute it. Two things you can't do: offer it as a hosted/managed service, or remove the licensing notices. We chose ELv2 over MIT because MIT permits repackaging the code as a competing closed-source SaaS.” — [README](https://github.com/mksglu/context-mode/blob/main/README.md)
- The license change from MIT is commit `a482980` (“chore: switch license from MIT to Elastic License 2.0”), and `package.json` `license` became `Elastic-2.0`. — [commit a482980](https://github.com/mksglu/context-mode/commit/a48298080e90f2824dfb4e5c883085ea019fd255)
- Elastic’s own FAQ (about Elasticsearch and Kibana, the license’s original context): you may “use, modify, create derivative works, and redistribute,” with three limits (managed service, license-key circumvention, notice removal). “You may freely use Elasticsearch inside your SaaS or self-managed application, and redistribute it with your application,” if those limits are met. A search box inside your own SaaS is permitted. Giving customers direct access to a substantial portion of the product’s APIs and UI as a service is not. A contractor setting the software up for a client’s internal use is permitted. — [ELv2 FAQ](https://www.elastic.co/licensing/elastic-license/faq)
- Homepage still says “open-source plugin” and “Free, open-source, ELv2.” The README uses “source-available.” — [context-mode.com](https://context-mode.com/); [README](https://github.com/mksglu/context-mode/blob/main/README.md)

### Inferences
- A closed-source desktop app may depend on the plugin as an external process the user runs locally. That is “use.” It may also vendor the code and ship it beside the app. Elastic’s FAQ allows redistribution inside a self-managed application. The app’s own source can stay closed; ELv2 is not a copyleft.
- Vendoring a modified copy is allowed if the LICENSE text is included, existing notices stay, and the copy carries a prominent modification notice. The app cannot relicense those files or drop the copyright header.
- Because the grant is non-sublicensable and non-transferable, the desktop app does not become the licensor of context-mode. Recipients are using Mert Koseoglu’s ELv2 grant. Shipping the LICENSE file is the practical way to meet the “anyone who gets a copy also gets these terms” clause.
- The forbidden shape is a hosted or managed service whose users get a substantial set of context-mode’s features (the sandbox tools, the FTS store, the hook engine) as the service. The maintainer’s stated reason for leaving MIT is that case: a competing closed-source SaaS. A personal app whose cloud backend executed the plugin for subscribers would sit on the forbidden side of that line. A process on the user’s machine does not.
- “Substantial set” is undefined in the license text. Elastic’s FAQ examples are persuasive, not a ruling about this repo. A thin wrapper that only shells out locally is the FAQ’s permitted “inside your application” case. Re-exposing `ctx_search` / `ctx_execute` as a multi-tenant API would need a lawyer, not a FAQ analogy.
- No license-key implementation was reviewed. The standard ELv2 clause is still in the file, so a fork should not delete license-check code if any exists.
- Calling the plugin “open source” in a Grain UI would repeat the homepage’s label. The repo’s own word is source-available.

### Gaps
- Whether `main` contains license-key checks that the circumvention clause would cover.
- No legal opinion. The Elasticsearch FAQ is Elastic’s intent for Elastic’s products, not MKSF’s enforcement policy.
- SPDX identifier `Elastic-2.0` is what `package.json` uses; an OSI-approval statement was not fetched.

## Does the plugin phone home, require an account, or work fully offline?

### Takeaway
The free plugin does not require an account, and event upload runs only after Platform config exists. It is not fully offline: the MCP server copy that was read calls `https://registry.npmjs.org/context-mode/latest` at startup and again every hour. Upgrade and page-fetch tools also use the network when invoked.

### Cited Findings
- README: “No telemetry, no cloud sync, no usage tracking, no account required.” — [README](https://github.com/mksglu/context-mode/blob/main/README.md); same claim on [context-mode.com](https://context-mode.com/)
- `hasPlatformConfig()` is a negative cache: if `platform.json` is absent, the forwarder does not POST. The file is only created by the Platform install script. — [platform-bridge.mjs](https://github.com/mksglu/context-mode/blob/main/hooks/platform-bridge.mjs); [platform docs](https://platform.context-mode.com/docs)
- In `src/server.ts` at commit `429d6f2c`, `fetchLatestVersion()` GETs `https://registry.npmjs.org/context-mode/latest` with a 5 second timeout and treats errors as `"unknown"`. `main()` calls it at startup and `setInterval` repeats it every hour. The outdated banner is prepended to tool responses. No request body of prompts or code is in that function. No disable flag appears next to those call sites. — [server.ts at 429d6f2c](https://github.com/mksglu/context-mode/blob/429d6f2c/src/server.ts)
- `ctx_upgrade` “Upgrade to latest version from GitHub, rebuild, reconfigure hooks.” That is user-invoked. — [README](https://github.com/mksglu/context-mode/blob/main/README.md)
- `ctx_fetch_and_index` fetches http(s) URLs. Loopback and RFC1918 are allowed unless `CTX_FETCH_STRICT=1`. Cloud metadata `169.254.169.254` is blocked. — [README](https://github.com/mksglu/context-mode/blob/main/README.md)
- Platform account: a dashboard invite supplies the Bootstrap Token. There is no free-plugin account. — [platform docs](https://platform.context-mode.com/docs)

### Inferences
- Default install: no account, no event upload. npm still sees the machine’s IP on the version poll, so “nothing leaves your machine” overstates the default process.
- Offline use of the sandbox should continue when the registry is unreachable, because the check fails open to `"unknown"`. That was not executed here.
- `ctx_insight` is an explicit browser open of the hosted dashboard, not a background upload.
- A personal app that vendors the server and wants no network should strip or gate `fetchLatestVersion` (a modification, so the ELv2 modification notice applies) or firewall the process. No documented opt-out was found in the file that contains the call.

### Gaps
- Confirmation that `fetchLatestVersion` is still unconditional on `main` as of 2026-10-02. The evidence is commit `429d6f2c`.
- Any other background hosts (update manifests, stats beacons) beyond this call and the Platform forwarder. A full URL inventory of `main` was not done.
- Whether the npm request sends a custom User-Agent that identifies the user or project. The code shown sets `Connection: close` only.

## Security reviews, known issues, and the 98% claim

### Takeaway
The 98% figure is the project’s own byte comparison of a fixed fixture set (315 KB of selected outputs reduced to about 5.4 KB). The author told Hacker News it is bytes, not tokenizer tokens, via a bytes/4 estimate. Independent security write-ups of the Platform were not found. GitHub issues document local trust bugs: a cross-origin local Insight API, a file-index path that skipped the deny policy, and a hook installer that deleted unrelated user hooks.

### Cited Findings
- README benchmark table: Playwright snapshot 56.2 KB → 299 B (99%); 20 GitHub issues 58.9 KB → 1.1 KB (98%); access log 45.1 KB → 155 B; analytics CSV 85.5 KB → 222 B; git log 11.6 KB → 107 B; tests 6.0 KB → 337 B (95%); repo research 986 KB → 62 KB (94%). “Over a full session: 315 KB of raw output becomes 5.4 KB. Session time extends from ~30 minutes to ~3 hours.” — [README](https://github.com/mksglu/context-mode/blob/main/README.md)
- `BENCHMARK.md` at commit `eb6ddac6`: “Subtotal: 315 KB raw → 5.5 KB context (98% savings).” A second rollup on that page: 177.1 KB → 10.2 KB, tokens “~45,300” → “~2,600”. — [BENCHMARK.md](https://github.com/mksglu/context-mode/blob/eb6ddac6c1c3a1868c391a2527497e0586086c15/BENCHMARK.md)
- Homepage: “Save up to 98% of context per session” and “30× fewer tokens.” — [context-mode.com](https://context-mode.com/)
- Hacker News author reply on “Show HN: Context Mode” (item 47148025): the percentage “is measured in bytes, not tokens,” using `Buffer.byteLength()` UTF-8, “bytes/4,” marked “estimated (~).” Claude’s tokenizer “would give slightly different numbers.” — [HN](https://news.ycombinator.com/item?id=47148025)
- README also prints “~98% saved” in the with-hooks column for many adapters and “~60% saved” without hooks. The 60% cell is presented as instruction-file compliance, not as the fixture benchmark. — [README](https://github.com/mksglu/context-mode/blob/main/README.md)
- Issue #293: local `ctx_insight` API responses include `Access-Control-Allow-Origin: *`, so another localhost origin can read session event data; the Node runtime also allowed cross-origin DELETE. Reporter explicitly says this is same-machine, not a remote exploit, and that it conflicts with the privacy framing. — [issue #293](https://github.com/mksglu/context-mode/issues/293)
- Issue #442: `ctx_index` reads an arbitrary path into FTS5 and, unlike `ctx_execute_file`, does not call `checkFilePathDenyPolicy`. Indexed secrets are then returned by `ctx_search`. — [issue #442](https://github.com/mksglu/context-mode/issues/442)
- Issue #415: `pretooluse.mjs` removed an entire `settings.json` matcher entry if any inner hook was context-mode’s, deleting sibling user hooks. Maintainer reply: fixed for v1.0.108, no automatic restore of already-deleted hooks. — [issue #415](https://github.com/mksglu/context-mode/issues/415)
- Issue #545: Pi adapter inherited `CLAUDE_PROJECT_DIR` and ran `ctx_*` against a different repo. Maintainer: fixed in v1.0.124. — [issue #545](https://github.com/mksglu/context-mode/issues/545)
- Issue #838: claims `insight/package.json` depends on `@tanstack/react-router` packages listed as malware under CVE-2026-45321, and that the first `ctx-insight` run `npm install`s them. Reported via a static scanner (SkillSpector). Resolution status was not re-checked. — [issue #838](https://github.com/mksglu/context-mode/issues/838)
- README security model: deny rules win; `ctx_execute_file` is confined to the project root by default (issue #852); `ctx_execute` “still inherit[s] the process's filesystem access” and “the boundary guard is … not a full OS sandbox.” — [README](https://github.com/mksglu/context-mode/blob/main/README.md)
- A third-party review (andrew.ooo, 13 September 2026) repeats the project’s benchmark table, star counts, and “no telemetry” claim, and says optional `ctx_insight` opens a hosted dashboard. It does not add a separate measurement. — [andrew.ooo](https://andrew.ooo/posts/context-mode-mcp-context-window-optimization-review/)

### Inferences
- 98% is a real arithmetic result on a curated corpus (large fixtures, byte sizes), not a study of production sessions and not a token measurement. “Up to 98%” matches that table. Applying it to every session, or to small tool outputs, is not supported. One row in the older benchmark (Playwright network requests, 0.4 KB → 349 B, 13% saved) shows small outputs can get worse; that row is not in the README’s highlight table.
- 30× does not match the published token estimate on `BENCHMARK.md` (~45,300 / ~2,600 ≈ 17×). The byte ratio 315 KB / 5.4 KB is about 58×. The 30× slogan has no published method of its own.
- “~30 minutes to ~3 hours” has no published protocol.
- “331,200+ developers” has no published method.
- For a personal app, the local trust issues that matter are: the process can run code and read files the host already allowed it to see; `ctx_index` has been reported to ignore Read-deny rules; the local Insight API has been reported world-readable to other localhost origins; the installer has destroyed unrelated hooks. Those are reasons to pin a reviewed revision and not to enable the hosted forwarder.
- Issue #838 should be treated as an unresolved allegation until the advisory and the pinned versions are checked. It is a reason to be careful with `ctx-insight`’s dependency install, not a proven incident in this note.

### Gaps
- Whether #293, #442, and #838 are closed on main. The search snippets did not include a maintainer fix for #293 or #442.
- A full read of the Hacker News thread beyond the author’s methodology comment. No separate critical essay that measured the claim was found.
- An external security audit of the Platform or of the forwarder.

## Adjacent tools (names only)

### Takeaway
Several other projects offload large tool output into a local store or snapshot context at compaction time. They are not substitutes for Insight, which is org analytics.

### Cited Findings
- mcp-context: a Claude Code PostToolUse hook indexes MCP outputs over a byte threshold (default 5 KB) into a local SQLite FTS5 file and replaces the context payload with a short summary. — [byliu-labs/mcp-context](https://github.com/byliu-labs/mcp-context)
- ctx-saver: a local Go MCP server writes large command and file output to SQLite (zstd above 4 KiB) and returns a summary plus `ctx_search` / exact-line fetch. — [ctx-saver](https://github.com/ChonlakanSutthimatmongkhol/ctx-saver)
- mcpagent context offloading: tool output over a token threshold is written to a session file and read back through virtual read/search/query tools. — [large_output_handling.md](https://github.com/manishiitg/mcpagent/blob/main/docs/large_output_handling.md)
- CCStash: before compaction, distills the transcript into a local SQLite vector plus FTS5 store and injects a short pointer with a `retrieve_context` tool. — [claude-vector-context-stash](https://github.com/jamesburton/claude-vector-context-stash)
- MCP Context Server: thread-scoped durable store on SQLite or PostgreSQL with FTS, optional semantic search, and hybrid fusion, exposed as MCP. — [MCP Context Server](https://www.alexfeel.info/projects/mcp-context-server/index.md)

### Inferences
- The category Grain would be buying from the free plugin is “keep large tool bytes out of the model and search them locally.” That category already has smaller single-purpose tools. Insight/Memory/Audit/Cost are a different product (org event analytics) and are not what those alternatives implement.

### Gaps
- No license or maturity comparison of the alternatives. They are named only so the report can point at the category.

## Would roadmap Memory overlap an app that already has long-term memory and a knowledge graph?

### Takeaway
Roadmap Memory, as described in one paragraph, stores coding-agent decisions, conventions, and architectural rules and shares them across sessions, teammates, and projects. That overlaps the slice of a personal knowledge graph that already remembers project decisions and conventions. It does not describe a general personal memory, and it is not shipping. The free plugin already keeps a local, per-project version of those same facts for session resume.

### Cited Findings
- Memory copy: “Long-term memory for AI coding agents. Carry decisions, conventions, and architectural rules across sessions, teammates, and projects. Same plugin, persistent context.” Status: “Coming · 2026” and “roadmap.” — [context-mode.com](https://context-mode.com/)
- The free plugin already records, locally, decisions (“use X instead,” “don’t do Y”), rule-file paths and content, user prompts, intent, and role, and reinjects them after compaction from a per-project SQLite database. — [README](https://github.com/mksglu/context-mode/blob/main/README.md)
- Platform products “differ in interpretation, not transport”: Insights, future Audit, and future Risk Score share `POST /api/v1/events`. Memory is described as “Same plugin,” so the natural implementation is that same event stream plus a cloud store. The site does not say this explicitly for Memory. — [platform docs](https://platform.context-mode.com/docs); [context-mode.com](https://context-mode.com/)

### Inferences
- Overlap is real and narrow: decisions, conventions, and architectural rules for coding work, persisted across sessions. An app that already writes those into its own memory and knowledge graph would have two writers for the same facts.
- Non-overlap: Memory is specified for AI coding agents and for teammates. A personal life/knowledge graph (people, notes, mail, calendar, non-code documents) is outside the published sentence. There is no API sketch, schema, or “bring your own store” option in the sources.
- Sharing “across teammates” implies a hosted, multi-user store. That is the Platform, with the privacy behavior in the previous section, not an offline feature of the free plugin.
- Using the free plugin’s session SQLite beside an existing knowledge graph is a smaller overlap: it is a coding-session cache for compaction recovery, keyed by project, not a second knowledge graph. Turning on Platform or future Memory would copy prompt-derived decisions to MKSF.
- For a single-user personal app, Memory adds a vendor memory that the app cannot query under its own schema, scoped to people who share a Platform org. That is a poor fit next to an existing local graph, and it is not available to adopt yet.

### Gaps
- No Memory spec, data model, or statement of whether memories are derived only from structural events or from stored prompt/rule text. The overlap judgment is from the one marketing paragraph plus the plugin’s existing event categories.
- No statement that Memory will be local-only. “Same plugin, persistent context” plus the Platform architecture makes a cloud store likely and unconfirmed.
