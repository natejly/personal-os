# Parity: user interaction (chat, composer, steering, plan mode, questions, approvals, notifications, branching, voice, artifacts, keyboard, onboarding, Today)

Date: 2026-10-04. Grain worktree `harness-parity`, HEAD `e287a76`.
Peers: Claude Code, the Claude apps (claude.ai / desktop / Cowork), ChatGPT, the Gemini app, and Cursor.
Grain facts come from the code, and the ones the gaps rest on were re-checked by grep (file:line given). Peer facts come from the sources at the end. The OpenAI help pages returned 403, so the ChatGPT rows rely on search snippets and secondary coverage. Some Gemini release-note dates look a year off.

## Spot-check of the inventory

| Claim | Checked at | Result |
|---|---|---|
| No ask-the-user tool in ordinary chat | `backend/personal_os/app.py:1925-1927` drops every `group == "desk"` spec when `desk_id` is unset; `tools.py:3029-3065` `desk_ask` is the only question tool | Confirmed |
| Ask card renders a textarea only, not the options | `src/renderer/src/components/ToolEvents.tsx:217-260` (`AskAnswer` takes question and context only) | Confirmed |
| Steering always interrupts; a steer during an approval is a deny | `app.py:3432-3458` (`steer_run`), `app.py:2712-2723` (`by="steer"`) | Confirmed |
| No fork into a new chat | No fork route in `app.py`; `repos.py` has `begin_variant` (290), `activate_message` (347) and `supersede_from` (389) only | Confirmed |
| No mic in the composer | `Composer.tsx` (193 lines) has no mic; `getUserMedia` appears nowhere in `src/`; audio is captured natively by `meeting_recorder.py`, which needs a doc (`_start_doc_recording`, `app.py:6176`) | Confirmed |
| No slash menu and no Up-arrow recall | No `/` or `ArrowUp` handling in `Composer.tsx`; `/commands` exists (`app.py:3639`) with `Command{name,description,body}` (`src/shared/types.ts:2830`) | Confirmed |
| No conversation export | Export routes exist only for canvas presets (`app.py:7324`) and skills (`app.py:7544`) | Confirmed |
| GET `/conversations/{id}/plan` is declared twice | `app.py:7413` (work plan) and `app.py:8239` (proposed plan). The first registration wins, so 8239 is dead code. The renderer calls only the work-plan shape (`lib/api.ts:439`) | Confirmed. Harmless, but delete 8239 |
| Context size is known per reply but not shown | `used["tokens_estimate"]` (`app.py:1725, 1974`) goes out on `assistant_message.context_used` | Confirmed |
| A denied OS notification permission fails silently | `lib/notify.ts:7` returns when `Notification.permission === 'denied'` | Confirmed |

## Feature-by-feature comparison

Legend: **=** parity, **+** Grain ahead, **-** Grain behind, **~** different by design.

| Area | Grain | Claude Code | Claude apps / Cowork | ChatGPT | Gemini | Cursor |
|---|---|---|---|---|---|---|
| Durable reply that survives a closed window or restart | **+** The run lives on a bus with a tape (`runs.py`); any window can tail `?since=`; a restart salvages text and offers Resume | Session resume (`--resume`), with no tape replay to other windows | Remote Cowork sessions save automatically | Server-side | Server-side | Agents Window (local, cloud) |
| Stop | **=** Esc or the square button; partial output kept, outcome `stopped` | Esc keeps the work done | Stop | Stop | Stop | Stop |
| Mid-reply steer | **=** `/steer` cuts the provider read and answers in a new segment | Queued message injected after the current tool calls | n/a | "Update" while thinking | Live voice barge-in | Cmd+Enter steers at the next tool call |
| Queue a follow-up | **-** Every mid-reply send steers and interrupts | Enter queues (grey), Up pulls it back, Ctrl+Enter sends it now | n/a | n/a | n/a | Enter queues below the task; queued items can be dragged |
| Steer while an approval card is open | **~ / -** Counts as a silent deny with the text as the note (`app.py:2712`) | Typing at a prompt is the answer's comment (Tab) | Allow/Deny per action | Agent pauses for confirmation | Confirms first | n/a |
| Plan mode | **=** off, auto or always (⌘⇧P); `propose_plan` card with per-step checkboxes, JSON edits, args-digest binding and single-use claims | Shift+Tab cycle; ExitPlanMode dialog picks the execution mode; plan editable in $EDITOR; saved as markdown | n/a | n/a | n/a | Shift+Tab; editable markdown plan with to-dos; Build |
| Plan binding strength | **+** Each approved step is bound to its argument digest, and drifted arguments ask again (`plans.py`) | Approval covers the plan as prose; execution mode decides the rest | | | | Plan as prose |
| Clarifying questions | **-** Only `desk_ask`, filtered out of chat (`app.py:1927`); the card shows a free-text box only | AskUserQuestion: single/multi-select, Other, notes; optional timeout | n/a | Agent asks inline | n/a | AskQuestion card (multiple choice / select all) |
| Live to-do panel | **=** `todo_write` → PlanPanel; user can tick steps | TaskCreate/Update; Ctrl+T | Live progress in Cowork | n/a | n/a | Plan to-dos |
| Approval tiers | **+** One-shot, always in this chat, always, session, saved rule, deny with a note, edited arguments (8 tools); external actions never become standing grants; taint forces cards | Manual / acceptEdits / plan / auto (classifier) / dontAsk / bypass; deny wins | Manual / auto (safety review) / skip; per-connector Allow/Ask/Block | Confirms high-impact actions; watch mode | Asks before send or spend | Per-mode |
| Edit approval arguments before running | **+** Email, calendar, Tasks, write_local_file, schema-validated, with the digest re-bound (`approval_edits.py`) | Comment on a prompt; no argument edit | No | No | No | No |
| Undo of side effects | **=** File snapshots with Undo/Redo per reply; Gmail outbox held 60-120 s with undo | Checkpoints plus /rewind (code and/or conversation; not Bash) | n/a | n/a | n/a | Checkpoints in the timeline |
| Edit and regenerate | **~** Hidden rows plus ‹i/n› for the trailing answer only; an edit hides the old tail | Rewind restores the original prompt into the input | Sibling branches with ‹ › arrows on any edited message | Inline versions ‹ › | Regenerate | Revert to a checkpoint |
| Branch into a new chat | **-** None | /branch, --fork-session | No | "Branch in new chat" from any message | No | n/a |
| Side question that stays out of the transcript | **-** None (the page agent ⌘I is a separate thread, with page context only) | /btw (no tools, one answer, f forks it) | No | No | Temporary chat | /side, /btw |
| Voice input | **-** Docs dictation only (`stt.py`, `meeting_recorder.py`) | n/a | Mobile voice mode | Advanced Voice; dictation | Gemini Live inside the main app | n/a |
| Spoken replies (TTS) | **-** None | n/a | Mobile | Yes | Yes | n/a |
| Composer history and recall | **-** Drafts per chat (`lib/drafts.ts`); no recall | Ctrl+R search, Up arrow, Esc Esc stashes into history | Up arrow edits the last message | Up arrow edits the last message | n/a | Up arrow |
| Slash commands and @-mentions | **-** Saved commands exist (`/commands`) but only the model can invoke them | Slash commands, @ files, ! shell | Slash (connectors) | / tools | @ apps | Slash, @ files/docs |
| Context gauge | **-** `tokens_estimate` is computed and never shown | Context indicator, /context | n/a | n/a | n/a | Context-usage report |
| Ghost text / next-prompt suggestion | **=** SmartTextarea ghost text (Tab), but no off switch | Predicted next prompt after each reply | n/a | Homepage suggestions | n/a | Tab completions |
| Notifications | **=** Once per run and kind (reply, approval, failed); body contains no text; desk and job notices | Desktop notification, bell or hooks; done, permission, idle | Cowork progress | Push on mobile | Push | Agent done |
| Unread across restarts | **-** Kept in memory only (`store.ts:91`) | n/a | Server state | Server state | Server state | n/a |
| Artifacts | **~** HTML only, strict CSP (no network or storage), versions, search/replace edits, can be placed on a canvas | Publish to claude.ai | Side pane, select-to-edit, share; documents and decks | Canvas (docs and code, ~25k words) | Canvas | Interactive UIs in chat |
| Inline rich blocks | **+** chart, interactive (sliders), mermaid, html/svg, math | Markdown | Artifacts | Charts, quizzes | Timelines, graphics | Interactive UIs |
| Conversation export | **-** None per chat | Session files on disk; /export | Account export | Account export; share link | Takeout | Transcript files |
| Find and search | **=** ⇧⌘F full-text over bodies (FTS); ⌘F in the chat | Ctrl+R, transcript search | Search | Search | Search | Cmd+K over transcripts |
| Morning surface | **+** Today view: recap, calendar, inbox, waiting mail, day plan (tick to apply), job proposals; nothing acts on its own | n/a | Scheduled tasks | Pulse → scheduled tasks | Daily Brief (US, paid) | n/a |
| Onboarding | **=** Provider → key → mandatory test → Google → about-you memory → first prompts | /init, login | Account | Account | Account | Account |
| Global entry | **~** Quick capture (⌘⇧Space) appends to the daily note; no global "ask" window | Terminal | Desktop shortcut opens a quick chat | Option+Space launcher | Gemini overlay | n/a |
| Keyboard cheatsheet | **-** Shortcuts scattered across components; none for regenerate or edit-last | `?` help, keybindings.json | Shortcut sheet | Ctrl+/ sheet | n/a | Settings |

## Where Grain is ahead

- **Durability.** A reply is a run with a tape, not a socket. Closing the window, opening a second one, or restarting the backend loses nothing: `_recover_runs` salvages the text and Resume rebuilds from executed calls without replaying `started` calls. None of the consumer apps expose that, and Claude Code's resume is per terminal.
- **Plan binding.** An approved step is bound to its argument digest and claimed once. The coding harnesses approve prose and then let the execution mode govern.
- **Approval granularity.** Per-chat, session and rule grants, deny with a note, and **edited arguments** on outward tools (re-bound into the journal, verification and outbox) go further than any peer. External actions never become standing grants, and taint forces a card. That matches the peers' "send/spend always asks" rule.
- **Inline rich blocks** (charts with transforms, slider-driven interactive blocks, mermaid) appear in the reply itself, with no separate artifact.
- **Today.** A local, read-only morning surface built from the user's own Gmail, Calendar and Tasks. Gemini's Daily Brief is the closest peer, and it is paid and US-only.

## Different by design

- **Notification bodies are generic** ("Reply ready"), so mail content stays off the lock screen. There are no repeat reminders. This is an anti-goal, and no peer nags either.
- **Artifacts are sandboxed HTML** with no network or storage. Peers allow richer documents and sharing; Grain is local and single-user, so sharing is out of scope.
- **Edit hides the tail and shows no sibling tree.** That is cheaper than a full branch tree, and a fork into a new chat (gap 3) covers "keep both".
- **Nothing on Today acts on its own**, and jobs only propose. Gemini Spark and Cowork scheduled tasks act; Grain deliberately doesn't send.
- **Skip permissions is not a blanket bypass.** Deny/ask rules, external actions, shell, taint, doom loops, plans and questions still ask, unlike bypassPermissions or "Skip all approvals".

## Gaps

1. **Clarifying questions in chat (P1, M).** Peers ask a structured multiple-choice question mid-task, especially while planning. Grain's chat model can only ask in prose and end the turn, and the desk card ignores its own `options`.
2. **Queue instead of steer (P1, M).** All three agentic peers separate "queue for after this" from "steer now". Grain always interrupts, and a message typed while a card is open silently denies it.
3. **Branch in new chat (P1, M).** ChatGPT and Claude Code fork a conversation at any message. Grain cannot keep the pre-edit line.
4. **Voice input in the composer (P1, M).** Every consumer peer has a mic in the chat box. Grain already has `stt.transcribe` (Apple Speech, whisper.cpp or proxy) but uses it only in Docs.
5. **Slash menu for saved commands (P2, S).** The commands exist, but the user cannot pick one from the composer.
6. **Up-arrow recall of earlier prompts (P2, S).**
7. **Context gauge (P2, S).** The number is computed and thrown away.
8. **Export a conversation to Markdown (P2, S).**

Smaller rough edges, not packaged as gaps:
- The dead duplicate `GET /conversations/{id}/plan` (`app.py:8239`).
- There is no off switch for ghost text.
- There is no in-app hint when OS notifications are denied.
- Unread state is not persisted.

Skipped on purpose:
- TTS and spoken conversation: a speech-native model is needed for the peer experience.
- A global "ask" launcher: quick capture already owns the hotkey. This could come later.
- /btw side questions: the page agent already covers most of it.
- Auto mode with a classifier reviewer: a frontier-grade judge, and close to the blanket-bypass anti-goal.

## Sources

- Claude Code: https://code.claude.com/docs/en/interactive-mode, https://code.claude.com/docs/en/permission-modes, https://code.claude.com/docs/en/checkpointing, https://code.claude.com/docs/en/tools-reference, https://code.claude.com/docs/en/claude_code_docs_map, https://vibeisland.app/guides/claude-code-notifications-mac/
- Claude apps / Cowork: https://support.claude.com/en/articles/12138966-release-notes, https://support.claude.com/en/articles/13364135-using-cowork, https://support.claude.com/en/articles/13345190-get-started-with-cowork, https://www.nodea.ai/blog/branching-ai-chat-guide, https://www.gend.co/blog/claude-ai-thinking-partner
- ChatGPT: https://help.openai.com/en/articles/6825453-chatgpt-release-notes, https://itdaily.com/news/software/chatgpt-branch-conversations, https://bgr.com/1959993/chatgpt-branch-conversations-new-feature-now-available/, https://www.techradar.com/ai-platforms-assistants/chatgpt/you-can-now-interrupt-chatgpt-as-it-learns-to-take-feedback-on-the-fly, https://intercom.help/openai/en/articles/10169521-projects-in-chatgpt, https://fast.io/resources/chatgpt-canvas-limit/, https://help.openai.com/en/articles/11752874-chatgpt-agent, https://help.openai.com/en/articles/12293630-chatgpt-pulse, https://learnprompting.org/blog/how-to-use-openai-chatgpt-advanced-voice-mode
- Gemini: https://gemini.google/release-notes/, https://blog.google/innovation-and-ai/products/gemini-app/next-evolution-gemini-app/, https://blog.google/products/gemini/scheduled-actions-gemini-app/, https://techcrunch.com/2026/05/19/google-updates-its-gemini-app-to-take-on-chatgpt-and-claude/, https://thenextweb.com/news/google-gemini-app-daily-brief-redesign-io-2026
- Cursor: https://cursor.com/docs/agent/overview, https://cursor.com/docs/agent/plan-mode, https://cursor.com/docs/agent/chat/history, https://cursor.com/changelog, https://forum.cursor.com/t/plan-mode-queued-messages-not-sent-automatically-after-plan-generation/147795, https://forum.cursor.com/t/askquestion-submitted-answers-not-reliably-delivered-to-the-agent-false-interrupted-wrong-free-text-relayed/163435, https://www.morphllm.com/cursor-agent-mode
