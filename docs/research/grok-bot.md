# Grok Bot and the Grok assistant: what they do, what Grain lacks

Research date: 2026-10-05. Seven facet reports with sources live in [grok-bot/](grok-bot/):
[01 chat app](grok-bot/01-chat-app.md), [02 voice and companions](grok-bot/02-voice-companions.md),
[03 the X reply bot and other surfaces](grok-bot/03-x-bot-surfaces.md), [04 agent tools API](grok-bot/04-agent-api.md),
[05 memory and settings](grok-bot/05-memory-settings.md), [06 apps and presentation](grok-bot/06-apps-presentation.md),
[07 Grok Bot](grok-bot/07-grok-bot-agent.md). Most x.ai pages returned 403 to our fetcher, so many details rest on
press and hands-on guides; each report marks those items (unverified).

## Two products share the name

**Grok Bot** (SpaceXAI, which now owns Cursor; beta 2026-08-11 on macOS, Windows, iOS, later iPad, Android and
Linux) is an always-on *agent* app, not a chatbot. Every account gets one persistent cloud computer (a Firecracker
microVM with a browser, terminal, files and saved logins). Named "Bots" live there as teammates in an iMessage-style
chat and keep working with the laptop closed. They use connectors or MCP when a service has them and drive the cloud
screen with mouse and keyboard otherwise. Work is taught by recording a task once, saved as a skill, and scheduled as a
routine. Acting on the user's real machine is a separate per-device opt-in. Access widened on 2026-08-26 to every
SuperGrok and Cursor plan; the model is not named and there is no model picker.

**Grok** (grok.com and the mobile apps) is the chatbot: Auto/Fast/Expert/Heavy modes, DeepSearch, Projects, Studio,
Imagine, Tasks and Automations, Voice Mode, memory and a ghost-icon private chat. The `@grok` reply bot on X is a
third surface with its own published system prompt. There is still no official native macOS chat app.

## Grok Bot: key features

| Area | What it does | Grain today |
|---|---|---|
| Named Bots | A Bot is a name, label, boundaries and avatar. Focused roles beat a general assistant. Cmd+N makes one; `@` mentions Bots, groups, routines and connectors; `/` inserts skills. Groups of 2 to 6 Bots hand work to each other in visible group chats. | Agents library (hue, skills, prompt), chat as an agent, `agent_spawn` subagents, crews, workflows. Composer has `/`; no `@` mentions. |
| Cloud computer and computer use | One VM per account; Bots click and type on its screen when no API exists; one computer-use task per Bot at a time; egress can route through the user's own IP. | No cloud VM by design. Agent browser (accessibility-tree snapshots), shell sandbox, microVM sandboxes, Python sandbox. |
| Takeover | For passwords, 2FA, CAPTCHAs and payments the Bot hands the live screen to the user, who finishes the step and hands it back. Secrets never enter the transcript. | Agent browser has a sign-ins view and an approval card. No explicit "take over, then hand back" step that pauses the run. |
| Teach-a-task | Record up to 10 minutes of screen into a draft skill; the user then adds decision rules and failure handling. Skills are account-wide. | Skill induction from a chat exists (`skills/induce`, friction hints), but there is no one-click "save this chat as a skill" with the steps / decision rules / failure-handling shape. |
| Routines | One workflow bound to one Bot with a schedule or an event trigger (Slack, GitHub). Enable, pause, **test**, edit, delete. Max 50 per Bot; last 20 run records kept. Recommended ladder: one-off task, corrected task, saved skill, tested routine. Watcher pattern: run hourly, save each check, notify only on a threshold crossing. | Jobs (cron, once, folder watch, mail query) with retries, catch-up and an Agent inbox. No test run before enabling, no visible run history per job, no change-only notification, no calendar-relative trigger. |
| Approvals | Allow once / Always allow (saves a matching rule) / Deny. **Auto-review**: an independent review model judges shell, plugin, computer-use, automation-write and delegation calls against "Ask first" and "Allow automatically" rules; Ask-first wins on conflict. Always-ask categories: send, publish, money, delete, permission changes, production changes. | Approval card already offers allow once, allow in this chat, save a matching rule, allow everywhere; deny wins over allow; a fixed always-ask list. No independent reviewer model. |
| Local execution | "Execution on Local Computer": Ask every time (default) / Always / Never, per desktop. | Per-tool on/ask/off, home-scoped file tools with pre-image snapshots and undo. Covered. |
| Reports | Recommended report shape: verified facts, assumptions, actions completed, actions awaiting approval, unresolved questions, with evidence (URLs, timestamps, confirmation IDs). | Unattended runs deliver proposals to the inbox in free form. |
| Attention and notifications | Sidebar states "Needs attention" (question, approval, handoff) vs "Unread activity"; per-Bot OS notifications on finish or needs-input, suppressed while the app is focused. | Autonomous chats carry a status mark and a Needs-you count; chat, desk and job notifications fire only while unfocused. Covered. |
| Memory | Per-Bot preferences, role context and summaries of prior work; docs warn not to treat it as authoritative. | Memory rows with hybrid search, pinned rows, auto-learn, consolidation. Stronger. |
| Governance | Shared VM is not a security boundary; per-user audit log and spend caps were unshipped; prompt injection is the common worry. | Taint model (fetch after untrusted reads asks), proposal-only unattended runs, egress proxy, retention. |

## Grok chat: key features

| Feature | What it does | Grain today |
|---|---|---|
| Auto mode | Routes each prompt between a fast model and a reasoning model; Heavy runs several agents in parallel and a leader synthesises (only the leader's tool calls stream). | Per-chat model and effort pickers; no routing. Workflow fan-out and subagents exist; no "N researchers then a leader" mode. |
| DeepSearch | Up to about ten search and browse steps over web and X, a progress panel with a Thoughts view, linked sources. Citation accuracy is weak. | Five search engines merged, `fetch_url` with a reader fallback, shared `[n]` citation numbering, collapsed activity line. No research mode and no query-plan or sources-considered view. |
| Studio | A side panel that opens by itself for documents and code; runs HTML, Python, JS, TS, C++ and bash; imports from Google Drive. | `show` side panel (HTML, SVG, diagrams, charts, markdown, files). No run button on code blocks. |
| Projects, Files | Isolated containers with instructions, files and chats; about 100 files per message at 150 MB. | Projects with instructions, files, memories and a graph. Covered. |
| Imagine | Image and 10 s video generation with native audio, up to seven reference images, editing. | None. Images are understood through a vision tool only. |
| Tasks and Automations | Scheduled prompts (once, daily, weekly, monthly, cron-like) and an email-arrival trigger; delivery by push, email, in-app or none; run history; test run before saving. | Jobs and `schedule_task`; delivery to the inbox; no per-job delivery choice or test run. |
| Voice Mode | Real-time voice with interruption, several voices, a personality picker separate from the voice, live camera. Mobile only; no desktop push-to-talk. | Click-to-record dictation into the composer. No read-aloud and no hands-free loop. |
| Memory and privacy | Viewable, deletable memories; per-source personalization toggles; ghost-icon private chat deleted within 30 days; "Forget" per chat announced; a share-link manager after the indexing incident. | Editable memory panel, per-chat Private. Memory use is visible only in the Context drawer, not on the reply. No "stop learning from this chat". |
| Customize | Response-style presets (Concise, Formal, Socratic/Tutor, Comprehensive) plus a custom personality slot applied consistently. | Global system prompt and project instructions; no one-tap style presets. |
| Follow-ups | Suggested follow-up chips under replies (reported). | First-prompt suggestions only. |
| The X reply bot | A short fixed-shape answer (under about 550 characters, no markdown, in the asker's language), "Explain this post" as N short bullets with no closing summary, verify by opening pages before answering an "is this true". | No "explain / summarize / verify this selection" verbs on selected text. |
| Desktop presence | Nothing official; community overlays add Option+Space, a menu-bar icon and a floating window. | Tray, two global hotkeys (gather pop-outs, quick capture). No global ask-the-assistant bar. |

## What was chosen to build

Ranked by value for a personal desktop assistant and by fit with Grain's existing design (durable runs, proposal-only
unattended work, approve-once rules). Nothing below adds pixel-level desktop control, a cloud VM, or auto-enabled
skills; those stay anti-goals.

1. **Deep research mode** (`/research`, `deep_research` tool). Plans sub-queries, fans out independent researcher
   subagents that search and read, and a leader reconciles and drops unsupported claims. Only the leader streams; the
   reply shows a research trail (query plan, sources considered, steps) above the answer, reusing the activity line.
2. **Follow-up chips** under replies, produced by the extraction model after `done`, inserted into the composer on click.
3. **Promotion ladder**: "Save as skill…" and "Schedule as routine…" on any reply, a **Test run** before a job is
   enabled, and the last 20 runs visible per job.
4. **Independent review gate** (off by default): a cheaper reviewer model judges risky calls that would otherwise run
   on their own and turns doubtful ones into approval cards with its reason. Ask rules always win; the always-ask
   list stays untouchable.
5. **Quick-ask bar**: a global hotkey opens a floating composer; the answer streams in place, with "Open in chat" and
   an optional "include clipboard" chip.
6. **Selection verbs**: Explain, Summarize, Verify and Ask about this on selected text in chats and notes, with the
   fixed-shape explain template and search-then-open verification.
7. **Memory transparency and forgetting**: a "used N memories" chip on the reply that opens those rows, and a per-chat
   "Don't learn from this chat" switch that also removes what was learned from it.
8. **Watchers and triggers**: change-only notification for repeating jobs, an event-relative calendar trigger, and a
   fixed report shape (verified facts, assumptions, actions, awaiting approval, open questions) for unattended runs.
9. **Response style**: one-tap presets and a custom personality slot, per chat, applied consistently.
10. **Image generation** tool through the configured provider, shown in the side panel and saved to uploads.
11. **Voice**: read a reply aloud, and a hands-free loop (listen, send, speak, listen again).
12. **Auto model routing**: an "Auto" entry that picks the fast or the reasoning model per prompt and says why in the
    activity line.
13. **Takeover in the agent browser**: a card that pauses the run, brings up the live browser for a login or 2FA, and a
    "Hand back" that resumes; nothing typed by the user enters the transcript.
14. **Agents as scoped teammates** (asked for during the build): an agent definition gains a label, boundaries, its own
    tool-mode overrides and notes; routines (jobs) bind to an agent and run with its prompt, skills and boundaries; an
    agent home in the Library lists its chats, routines (with Run now and New routine), skills, notes and last runs;
    `@name` in the composer addresses an agent. No cloud computer: delegation stays with subagents and crews.
15. **Default zoom** in Settings → Appearance (80–160 %), with View menu zoom items, so the UI is easier to read without
    a hard-coded size change.

Left out on purpose: cloud computers and screen-recording teach-a-task (pixel control is an anti-goal), the approval
trio (already shipped), X search (no social feed in Grain), companions and adult modes, share links (local app),
spend caps (removed earlier by decision).
