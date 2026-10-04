# Parity: memory, projects, context management, writing style

Grain (harness-parity worktree, read 2026-10-04) compared with ChatGPT (memory, Projects, personalities), Claude (memory, chat search, Projects, Styles), Gemini (personal context, Personal Intelligence) and Claude Code (CLAUDE.md, auto memory, compaction). Peer facts come from the sources listed at the end. help.openai.com returned 403 to the fetcher, so the ChatGPT details come from release-note mirrors and press coverage.

## Feature comparison

| Capability | Grain | ChatGPT | Claude (claude.ai) | Gemini | Claude Code |
|---|---|---|---|---|---|
| Explicit instructions kept apart from learned memory | Global system prompt and per-project instructions (`context.build_context`), separate from `memories` | Custom Instructions plus trait chips | Profile preferences and project instructions | "Saved info" / "Your instructions" | CLAUDE.md levels kept apart from auto memory |
| Learned memory on by default | Yes. `LearnWorker` runs after `done` (`learn.py`) | Yes. Background auto-updates since June 2026 | Yes on Free/Pro/Max, off by default on Team/Enterprise | Yes (personal context, Aug 2025) | Yes, locally |
| Contradictions resolved, history kept | `updates` lead to `Memories.supersede`, `forget` to `invalidate`, with restore and history (`repos.py`) | Automatic, and the history is not shown to the user | Edits only, no visible history | Not documented | The model rewrites files by hand |
| Consolidation | Approve-only Tidy-up proposals (`consolidate.py`) | Automatic pruning by recency and importance | Not documented | Not documented | A reminder to shorten MEMORY.md near the limit |
| A hand edit keeps history | **No.** `Memories.update` rewrites the row in place | n/a | n/a | n/a | Git, if the user commits |
| Memory summary the user can edit | No. Itemised list only | Memory Summary, editable | Memory summary and topics, editable | Saved info list | MEMORY.md index |
| Recall shown to the user | Per-message brain count and the `context_used` drawer (`Message.tsx`) | Memory citations | Visible chat-search tool call | Names the connected source | "Recalled N memories" |
| Saving shown to the user | Toast. The `/events` toast leaves out updates and forgets and has no Undo (`store.ts:1130`) | "Memory updated" | Not documented | Not documented | "Saved N memories" |
| Agent tool to save memory | `save_memory`, which **skips the credential scrub and date absolutizing** (`tools.py:935`) | Implicit | "Remember X" in chat | "Remember X" | "Remember X" |
| Agent can correct or forget a memory | **No tool.** It waits for auto-learn or a UI edit | Yes, conversationally | Yes, conversationally | Yes | Edits the file |
| Recall across past chats | **UI route only** (`GET /conversations/search`); the agent cannot reach it | Reference chat history toggle | Chat search tool, scoped to the project | Personal context | No, by design |
| One chat outside memory | Per-chat `useMemory` and `autoLearn` toggles in the drawer, set by hand; the chat still shows in history and search | Temporary Chat | Incognito, or memory off before the first message | Temporary chat (72 h) | n/a |
| Pause versus reset | Global `autoLearn` off; trash or delete | Off or Clear all | Pause and Reset are separate | Off | Env var or setting |
| Projects with their own instructions, files and memory | Yes: instructions, Knowledge, memory, graph, voice and tool overrides | Yes | Yes | No | Per repository |
| Project-only memory mode | **No.** Personal memories always flow into project chats (`_scope_clause` with `include_global=True`) | Chosen at creation, and forced on when the project is shared | Each project is isolated by default | n/a | n/a |
| Retrieval over memory | Hybrid BM25, cosine, recency and graph fused by RRF (`memory_index.py`) | Not documented | Topics | Not documented | 200-line index, with topic files read on demand |
| Knowledge graph | Yes, bi-temporal edges and agent graph tools | No | No | No | No |
| Writing style learned from samples | Yes: samples, a profile, an editable Voice panel (`style.py`, `StyleView.tsx`) | Personality presets only | Presets and custom styles built from samples | No | No |
| When the style applies | **Only when Draft mode is on for the chat** (`style.voice_wanted`) | Every chat, by personality | Every chat once a style is picked | n/a | n/a |
| Style updates in the background | The relearn runs **inside the chat generator after `done`** (`app.py:3117`) and holds the conversation lock | n/a | Built on demand | n/a | n/a |
| Automatic compaction | Rolling summary; the transcript is never edited (`compaction.py`) | Not exposed | Not exposed | Not exposed | /compact, auto-compact, then durable sources re-injected |
| Manual compact with a focus | Route accepts `focus`; **no UI passes it** | n/a | n/a | n/a | `/compact <instructions>` |
| Context threshold settings | Settings JSON only (`compactAt`, `contextWindow`, `contextBudget`, ...) | n/a | n/a | n/a | `/autocompact <tokens>` |
| Plan re-injected every round | Yes (`_reinject_plan`) | n/a | n/a | n/a | Plan re-injected after compaction |
| Old tool results cleared, large results paged | Yes (`microcompact`, `ToolResults` handles) | n/a | n/a | n/a | Subagents keep file reads out of the main context |

## Where Grain is ahead

- **Memory history that cannot be lost.** Supersession, invalidation and restore keep every version, and Tidy-up proposals change nothing until the user applies them. ChatGPT's 2026 background updater rewrites memories with no visible trail.
- **Hybrid retrieval and a temporal knowledge graph.** None of the chat assistants exposes a graph or explains how it ranks memories.
- **Taint-aware learning.** A chat that has read a web page or an email is never mined for memories or style samples, and `save_memory` is gated under taint. No peer documents an equivalent.
- **Context transparency.** The context drawer, live preview, token meter, trimmed-section notes and full system prompt are all visible. Claude Code's `/context` is the nearest peer equivalent.
- **In-run working memory.** The re-injected plan, microcompaction with recoverable handles, and paged tool results go beyond what the chat assistants expose.

## Differences by design

- **No automatic consolidation.** Grain proposes changes and the user approves them. Peers prune by themselves. This follows the approve-only stance and should stay.
- **The voice is for drafting only.** Peers apply a style to every reply. Grain keeps its own replies neutral and uses the user's voice only for text the user will send. The intent is right, but the trigger is too manual (gap G3).
- **Local and single-user.** There are no org controls, no sharing-forced isolation and no cloud import.
- **Rolling summary instead of a replacement.** Claude Code replaces history at compaction. Grain keeps the transcript and replays a summary plus the recent tail.

## Gaps

1. The style relearn holds the chat lock, and doc or tool samples never trigger a relearn (G1).
2. `save_memory` skips the credential scrub, date absolutizing and provenance, and the agent cannot correct or forget a memory (G2).
3. The voice needs Draft mode, so an ordinary "draft a reply to Sam" gets none (G3).
4. Background memory events are partly lost: the `proposals` event has no listener, and the toast leaves out updates and forgets and has no Undo (G4).
5. A hand edit of a memory destroys the previous version (G5).
6. The agent has no recall across chats (G6).
7. There is no one-click private chat (G7).
8. There is no project-only memory mode (G8).
9. Compaction and budget controls have no UI, and there is no focus for compact (G9).
10. Retrieval has scaling ceilings: an unordered LIMIT 5000 over vectors and a full-table substring scan for graph seeds (G10).

Gaps deliberately left out: a cloud memory import, a frontier-model summarizer, a generated memory summary (it would need a strong model to be trustworthy; the itemised list plus history covers it), and per-register style profiles (wait until G3 proves the voice is actually used).

## Sources

- ChatGPT memory, automatic updates: https://releases.sh/release/rel_9SyecKQN7LP70s8pMzJzj-chatgpt-upgrades-memory-with-automatic-updates-and-increased-capacity-for-plus
- https://www.thurrott.com/a-i/337052/openai-is-improving-how-chatgpts-memory-works
- Memory FAQ (mirror): https://help-lb.openai.com/en/articles/8590148
- https://eastleighvoice.co.ke/chatgpt%20plus/227223/chatgpt-can-now-automatically-manage-saved-memories
- Personalities: https://www.tomsguide.com/ai/chatgpt/chatgpt-can-act-like-different-assistants-heres-how-to-use-each-one , https://www.techradar.com/computing/artificial-intelligence/chatgpts-new-customization-options-are-exactly-what-ive-been-waiting-for-to-make-my-chats-more-personal
- ChatGPT Projects: https://openai.com/academy/projects , https://www.techradar.com/ai-platforms-assistants/chatgpt/chatgpt-project-only-memory-is-live-and-it-might-change-how-you-work-with-ai , https://fast.io/resources/chatgpt-projects-file-limit/ , https://www.memorylake.ai/blogs/chatgpt-project-memory-modes
- Claude memory and chat search: https://support.claude.com/en/articles/11817273-using-claude-s-chat-search-and-memory-to-build-on-previous-context , https://www.claude.com/blog/memory , https://support.claude.com/en/articles/10181068
- Claude Styles: https://claude.com/blog/styles
- Claude Projects: https://support.claude.com/en/articles/9517075-what-are-projects
- Gemini: https://support.google.com/gemini/answer/15637730 , https://www.techradar.com/ai-platforms-assistants/gemini/gemini-is-about-to-remember-everything-unless-you-tell-it-not-to , https://heise.de/-10522424 , https://www.pcworld.com/article/3032856/google-gemini-can-now-access-your-digital-life-for-smarter-answers.html , https://m.gsmarena.com/google_launches_personal_intelligence_for_gemini-amp-71110.php
- Claude Code: https://code.claude.com/docs/en/memory , https://code.claude.com/docs/en/context-window
