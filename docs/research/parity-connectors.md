# Parity: connectors, MCP, skills, commands, tool selection, agent loop

Date: 2026-10-04. Worktree: `harness-parity`. The Grain facts come from a code read, and each gap below was re-checked against the code by hand. The peer facts come from the vendors' public docs, cited below. This file lives under `docs/research/`, so it names other products; no file outside this folder should.

**Peers compared:**
- **Claude**: claude.ai / desktop connectors directory, custom remote MCP connectors, Agent Skills.
- **API**: Claude API tool search tool and MCP connector.
- **CC**: Claude Code (MCP, tool search, skills, hooks, plugins, permissions).
- **ChatGPT**: apps/plugins, developer mode (full MCP), Apps SDK.
- **Gemini**: Gemini app Connected Apps, plus Gemini CLI extensions, skills and policy engine.

| Peer | Sources |
|---|---|
| Claude | https://claude.com/docs/connectors/overview · https://claude.com/docs/connectors · https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-MCP · https://claude.com/blog/interactive-tools-in-claude · https://support.claude.com/en/articles/12512180-use-skills-in-claude · https://claude.com/docs/skills/how-to |
| API | https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-search-tool · https://platform.claude.com/docs/en/agents-and-tools/mcp-connector · https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview |
| CC | https://code.claude.com/docs/en/mcp · https://code.claude.com/docs/en/skills · https://code.claude.com/docs/en/hooks · https://code.claude.com/docs/en/plugins · https://code.claude.com/docs/en/permissions |
| ChatGPT | https://developers.openai.com/api/docs/guides/developer-mode · https://help.openai.com/en/articles/12584461-developer-mode-and-full-mcp-connectors-in-chatgpt · https://developers.openai.com/apps-sdk · https://developers.openai.com/apps-sdk/reference · https://help.openai.com/en/articles/11487775-connectors-in-chatgpt |
| Gemini | https://geminicli.com/docs/extensions/reference.md · https://geminicli.com/docs/extensions/writing-extensions · https://geminicli.com/docs/cli/using-agent-skills.md · https://geminicli.com/docs/reference/policy-engine.md · https://workspaceupdates.googleblog.com/2025/05/google-workspace-apps-are-now-generally-available-for-the-gemini-app.html · https://www.usecarly.com/blog/gemini-connectors/ |

## Feature table

| Capability | Grain | Claude | API | CC | ChatGPT | Gemini |
|---|---|---|---|---|---|---|
| Add a connector | Library → Connectors (Library ships hidden). The form is **stdio only** (`McpSettings.tsx` lines 13 and 227 hard-code `transport: 'stdio'`); pasted `mcpServers` JSON keeps only the first entry with a `command` | Directory (Verified / Community) with one-click OAuth; custom connector by URL | `mcp_servers` + `mcp_toolset` in the request | `claude mcp add`, `.mcp.json`, plugins; stdio, HTTP, SSE, WebSocket | Plugin directory; developer mode "Create app" by URL | App: Google-curated list, no user MCP. CLI: extensions from git or a path |
| Remote HTTP + OAuth | Backend complete (`mcp_client._open`, `mcp_oauth.py`: DCR, tokens in SQLite, silent refresh). **Only Health → Sources can add one or drive sign-in** | OAuth (DCR, published client, custom client) or static headers | Auth token | OAuth, headersHelper, scope limits | OAuth (DCR / client metadata), no auth, mixed | CLI: yes |
| SSE transport | Accepted by `POST /mcp/servers` (`app.py:914`), then always fails: "sse servers are not supported yet" (`mcp_client.py:142`) | n/a | n/a | Yes, deprecated | Yes | Yes |
| Pre-add trust check | Static eval of an unsaved draft (`mcp_eval.py`): schema shape, injection text, read-only claims that describe writes, shadowing, one-server toxic flow. **`McpProbeIn` (`app.py:890`) has no `url`/`headers`, so a remote draft can't be checked** | Verified badge, warning copy | none | Project `.mcp.json` servers need one-time approval | Elevated-risk banner; directory review | Consent at install |
| Per-tool permission | on / ask / off per tool, grant bound to the schema hash; chat → project → global; taint forces ask | Always allow / Needs approval / Blocked | per-tool `enabled` | allow / ask / deny rules, `mcp__server__.*` | Read vs write from `readOnlyHint`; remember per chat | Policy engine allow / deny / ask_user with arg regex |
| Read-only annotations | Recorded, never trusted to lower the tier (`mcp_client.py:70`) | Recommends Always allow for reads | n/a | Not used for gating | Drive the default confirm | Rules can match annotations |
| List and revoke grants | Global grants in the Library ToolRow. Project/chat grants: **no UI lists them, and `api.mcp.clearGrant` (`api.ts:342`) never sends `scope_id`**, so revoke misses the row | Connector page | n/a | `/permissions` | Per conversation only | Policy files |
| Tool definition drift | Version history, sentence/parameter diff, re-eval, quarantine on a new fail finding (`mcp_drift.py`) | none documented | none | list_changed refresh | Manual Refresh | none |
| Tool selection, MCP | BM25 `mcp_tool_search` past 12 ready tools; loaded set **resets every reply** (`app.py:1816`) | n/a | BM25 / regex search with `defer_loading`; discovered tools stay usable | ToolSearch on by default; discovered tools persist | n/a | none |
| Tool selection, built-ins | **None**: a fresh install sends ~113 schemas, ~78.6 KB JSON, every round (`Toolbox.schemas`, `tools.py:746`) | n/a | Guidance: search at ≥10 tools; accuracy drops past 30-50 | Built-ins small; MCP deferred | n/a | Denied tools are removed from the list |
| Non-text MCP results | Images and embedded resources become `[image content]` placeholders (`mcp_client.py:248`) | Images, MCP Apps inline UI | Images | Images; large results spill to a file path | Widgets via Apps SDK | Images |
| Result size | Cut at 20,000 chars (`MAX_RESULT_CHARS`) before Grain's own paged handles (`working.py`) ever see it | n/a | n/a | Warn 10k tokens, cap 25k, spill to disk | n/a | n/a |
| Server `instructions` | Captured in `server_info` and scanned; never reach the prompt | Used | Used | Used | Re-pulled by Refresh | n/a |
| Resources / prompts / elicitation | Not used | n/a | n/a | Resources @-mentionable; prompts as slash commands | n/a | Prompts as commands |
| Cancellation, long calls | One 45 s timeout; no cancel notice to the server | n/a | n/a | Auto-background after 2 min; idle timeouts | n/a | n/a |
| Skills | Human-approved only; inline to 6000 chars / 12 bodies, else index of 50 read via `skill_view`; `$name` forces | ZIP upload; progressive disclosure; per-skill toggle | Progressive disclosure | when_to_use, allowed-tools, fork, path gates, hot reload | Plugins bundle skills | `activate_skill`, consent per activation |
| Skill scripts | Prose only; `scripts/` and `assets/` dropped on import | Via code execution | Yes | Yes, plus `!cmd` injection | Yes | Yes |
| Skill authoring UI | Import / export / induce from a reply. `/skills/lint`, `/skills/draft`, `/skills/preview` **have no renderer caller**; lint flags MCP slugs as unknown (`_known_tools`, `app.py:7518`) | "+ Create skill" | n/a | Files, hot reload | n/a | `/skills reload` |
| Saved prompts / commands | `commands.py` templates with `$ARGUMENTS`; **no `/name` in the composer**, run only if the model calls `command_run` | n/a | n/a | `/name` slash commands | n/a | `/cmd`, extension-prefixed |
| Per-chat connector switch | Chat tool maps in the Context drawer; no per-server switch in the composer | "+" menu toggle per connector | Per request | `/mcp` enable / disable | Per-chat developer mode; @ mention | @App mention |
| Packaging and directory | none (nothing auto-installs) | `.mcpb` desktop extensions, directory | n/a | Plugins and marketplaces | Plugins | Extensions |
| Lifecycle hooks | none user-scriptable; fixed gates in `_chat_stream` | n/a | n/a | ~30 events; block, rewrite, inject | n/a | Hooks in extensions |
| Agent loop durability | Durable runs and resume, executed calls never replayed, stuck detector, budgets, overflow recovery, paged results | Cloud-hosted | Stateless | Session resume, compaction | Cloud-hosted | Session |

## Where Grain is ahead

- **Grants bound to the schema hash.** A changed tool decays from on to ask. No peer documents this. Drift review with a diff and quarantine (`mcp_drift.py`) goes further than any peer's Refresh button.
- **Taint.** Any MCP result forces the run's later "on" tools back to ask. Peers rely on warning banners.
- **A static trust check before trust** (`mcp_eval.py`): shadowing of built-ins, one-server toxic flow, read-only claims that describe writes. Peers show a Verified badge or nothing.
- **A durable agent loop.** Runs survive a restart, a `started` call is never replayed, approvals wait indefinitely, and the StuckDetector ends loops that peers leave to the round cap.
- **Programmatic tool calls** (`toolbridge.py`) pass through the same gates and real approval cards.

## Different by design

- **MCP annotations never lower the danger tier.** `readOnlyHint` is self-reported by the party that would gain from lying. ChatGPT uses it to skip confirmation; Grain asks until the user grants. Keep it.
- **Skills never auto-approve, and `allowed-tools` is ignored.** A skill cannot raise permissions. Claude Code lets a skill pre-approve tools for its turn; Grain will not (user anti-goal).
- **No directory, marketplace or auto-install.** These need a hosted catalog; out of scope for a local single-user app.
- **Subagents get no MCP tools, and jobs turn MCP calls into proposals.** External actions keep asking.
- **No user-scriptable hooks.** Deny/ask rules, plan mode and the fixed gates cover what peers use hooks for, without running arbitrary code at each tool call.
- **No skill scripts.** Peers run them in a code-execution sandbox; Grain's skills stay prose, and code runs through `run_python` with its own gates.

## Gaps, ranked

### P0: broken compared with peers

1. **Revoking a project or chat MCP grant does nothing, and those grants are not listed.** `api.mcp.clearGrant` sends `?scope=` without `scope_id`, so `DELETE /mcp/tools/{slug}/grant` (`app.py:1046`) targets a row that does not exist. `GET /mcp/servers` already returns `grants` (`app.py:1021`), but `McpSettings.tsx` never renders them. Every peer lists and revokes remembered approvals. Size S.

### P1: clear missing capability

2. **Tool selection for built-in tools.** About 20k tokens of schemas go to an open model every round. Peers defer and search past about 10 tools and report accuracy loss past 30-50. Generalise `mcp_tool_search` into one `tool_search` over built-ins and MCP, with a core set always loaded, the rest deferred by group, and the loaded set kept for the conversation. Size L.
3. **Add a remote MCP server from the Library.** The backend supports HTTP, headers and OAuth; the form is stdio only, pasted JSON drops `url` entries, `McpProbeIn` drops `url`/`headers`, and Sign in exists only in Health → Sources. Size M.
4. **MCP images and large results.** Images become placeholders, and text is cut at 20,000 characters before Grain's paging sees it. Save image blocks to a file `view_image` can read; let the paged-handle path take large text. Size M.
5. **`/command` in the composer.** Commands run only when the model chooses `command_run`. Size M.

### P2: nice to have

6. Put each ready server's `instructions` (fenced, scanned, capped) next to the connector catalog. Size S.
7. Wire the skill editor to the existing lint and preview endpoints; teach the lint MCP slugs. Size M.
8. Send `notifications/cancelled` on timeout and on Stop. Size S.
9. Remove the outside project name from `reach.py`'s module docstring (repo naming rule). Size S.
