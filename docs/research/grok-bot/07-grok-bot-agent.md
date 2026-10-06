# 07 - Grok Bot (SpaceXAI / Cursor agent app)

## Summary
Grok Bot is real: a beta app from SpaceXAI (xAI, which acquired Cursor) launched 2026-08-11 for macOS, Windows, iOS (Linux/Android/iPad followed). It is NOT the @grok X reply bot. The key architectural fact: it is a **cloud-computer agent**, not a local desktop-control agent. Each account gets one persistent cloud VM (Firecracker microVM) with browser, terminal, files and logins; named "Bots" (teammates, iMessage-style chat) run there 24/7, even with the laptop closed. Bots use connectors/MCP when available and computer use (mouse/keyboard on the cloud screen) otherwise; work is taught by recording a task once (skill) and scheduled as routines. Local-machine execution is an opt-in per-command-approval setting; human takeover handles passwords/2FA/CAPTCHAs.

## What it is / launch / pricing / access
**What**: Desktop + mobile client for always-on named agents. Auth is through Cursor accounts (SuperGrok can be linked).
**Detail**
- 2026-08-11 beta: Mac, iOS, Windows (Linux per docs listing); gated to SuperGrok Heavy ($300), Cursor Ultra ($200), Cursor Teams Premium ($120/seat). Enterprise waitlist.
- 2026-08-26: included with all SuperGrok, Cursor Pro/Pro+/Ultra, Teams plans. Own usage pool, separate from Grok/Cursor usage. Weekly limits, optional on-demand billed by tokens.
- 2026-09-04: iPad + Android, free trial with limited usage (9to5Mac).
- Install: x.ai/bot -> dmg (Apple silicon/Intel), Windows x64/Arm64, Linux deb/rpm/AppImage. Requires non-Legacy Privacy Mode (cloud storage needed).
- Model is not named anywhere in docs (Digital Applied); auto-routed, no model picker (Shumer criticism).
- WARNING: github.com/grok-bot-app/grok-bot and grok-bots.com are unofficial download mirrors (repo says "unofficial"); not sources.
**Limits**: 50 Bots+groups per account; one computer-use task per Bot at a time.
**Sources**: https://x.ai/news/introducing-grok-bot (2026-08-11); https://x.ai/news/grok-bot-more-plans (2026-08-26); https://docs.x.ai/grok-bot/get-started; https://docs.x.ai/grok-bot/faq; https://venturebeat.com/orchestration/spacexais-grok-bot-turns-agents-into-persistent-digital-coworkers-that-can-operate-your-apps-for-120-per-month; https://9to5mac.com/2026/09/04/spacexai-expands-grok-bot-to-ipad-as-access-expands-to-cheaper-plans/ ; https://9to5mac.com/2026/08/21/grok-bot-is-an-all-new-iphone-and-mac-app-from-spacexai-and-cursor/

## Computer use and browser
**What**: Bots operate apps on the cloud computer like a person (click/type), falling back to browser for services without connectors/API; docs recommend connectors first.
**Detail**
- Works on systems "without clean APIs or MCP".
- Sites may block automation/CAPTCHA; Bot should hand off, not bypass. Datacenter IPs get blocked: setting "Route egress through this desktop" sends traffic through the user's IP.
- Agent Computer window: user watches/takes over.
**Limits**: Layout changes break recorded skills; one screen per Bot.
**Sources**: https://docs.x.ai/grok-bot/get-started; https://flaviocopes.com/grok-bot/; https://docs.x.ai/grok-bot/settings-and-notifications

## Takeover (human handoff)
**What**: For passwords, passkeys, 2FA, CAPTCHAs, payment/identity checks, the Bot hands the machine over: open Agent Computer, take control, finish step, return control, tell the Bot to continue.
**Detail**: keeps secrets out of transcripts; masked "secure secret request" exists but is not a password manager; hardware security keys need per-use approval on macOS/Windows.
**Sources**: https://docs.x.ai/grok-bot/faq; https://docs.x.ai/grok-bot/approvals-security-and-privacy; https://www.dailydoseofds.com/p/grok-bot-masterclass/

## Local computer, files, shell
**What**: The cloud VM has terminal/CLI and files (`/workspace`). Acting on the user's actual Mac is a separate opt-in.
**Detail**
- Settings > General > "Execution on Local Computer": Ask every time (default) / Always allow / Never allow; applies to that desktop only; docs advise Never unless a Bot needs local files. Mac/Windows only.
- Team admins can govern local execution.
- No documented AppleScript/Accessibility/Shortcuts control (unverified absence).
**Limits**: Local files are not the default surface; cloud files persist after Bot deletion.
**Sources**: https://docs.x.ai/grok-bot/settings-and-notifications; https://docs.x.ai/grok-bot/approvals-security-and-privacy; https://www.dailydoseofds.com/p/grok-bot-masterclass/

## Task input and UX
**What**: iMessage-style chat with named Bots; composer text, voice (Cmd/Ctrl+D) or voice chat.
**Detail**
- Cmd+N new Bot; Bot = name, label, description (boundaries), avatar. Focused-role Bots recommended over "General Assistant".
- Composer: `/` inserts saved skills; `@` mentions Bots, groups, routines, connectors.
- Settings Cmd/Ctrl+, ; Bot settings Cmd/Ctrl+Shift+,.
- Phone = quick delegation, not admin.
- Progress: conversation thread, Agent Computer view, sidebar attention states ("Needs attention" = question/approval/handoff; "Unread activity" = new result). No documented step list or undo.
- Notifications: per-Bot OS/mobile notification on finish or needs-input; suppressed while app focused.
- Stop: "stop or steer in the active conversation".
- Recommended report sections: verified facts / assumptions / actions completed / actions awaiting approval / unresolved questions; keep evidence (URLs, screenshots, timestamps, confirmation IDs).
- Menu-bar presence: not documented (iGeeksBlog: no details).
**Sources**: https://docs.x.ai/grok-bot/get-started; https://docs.x.ai/grok-bot/settings-and-notifications; https://www.dailydoseofds.com/p/grok-bot-masterclass/; https://flaviocopes.com/grok-bot/

## Skills, teach-a-task, routines, scheduling
**What**: Skill = reusable instructions (steps, decision rules, output, safety boundaries). Routine = one workflow bound to one Bot with a schedule or event trigger.
**Detail**
- Teach-a-task: record up to 10 min of visible screen interaction (no audio) -> draft skill; human must add decision rules and failure handling.
- Schedules: time, frequency, timezone; run with laptop closed. Event triggers: Slack messages, GitHub notifications; advice: narrow matching rule.
- Max 50 routines per Bot, last 20 run records kept. Enable/pause/test/edit/delete from conversation details. Test run does real work.
- Recommended ladder: one-time task -> corrected task -> saved skill -> tested routine.
- Watcher pattern: run hourly, save each check, notify only on threshold crossing.
- Failure policy: record missing source, continue, don't retry same step forever.
- Skills are account-wide across Bots.
**Sources**: https://docs.x.ai/grok-bot/skills-routines-and-automations; https://flaviocopes.com/grok-bot/; https://composio.dev/content/guide-to-frok-bot

## Multi-Bot, subagents, memory
**What**: Parallelism is multiple named Bots; groups of 2-6 coordinate in visible group chats and hand off to each other.
**Detail**
- Bot-to-group handoffs are text-only. Handoffs require same account.
- Cloud Agent and subagent launches exist and are covered by Auto-review.
- Memory: per-Bot preferences, role context, summaries of prior work; docs warn not to treat it as authoritative, re-check sources. Duplicating a Bot copies profile/settings, not history. Unofficial 3-layer split: user / Bot / shared project.
- Proactive: Bots "pick up work over time" and learn escalation boundaries (marketing; thin detail).
**Limits**: Weekly limits drain fast on swarms/long multi-Bot chats; error propagation between Bots unaddressed.
**Sources**: https://x.ai/news/introducing-grok-bot; https://www.dailydoseofds.com/p/grok-bot-masterclass/; https://www.datacamp.com/blog/grok-bot; https://composio.dev/content/guide-to-frok-bot

## Plugins / MCP / connectors
**What**: Connectors appear as Plugins (Marketplace sidebar), account-wide, individual tools can be toggled. Custom MCP servers allowed if publicly reachable or tunneled; team MCP policy applies.
**Sources**: https://docs.x.ai/grok-bot/settings-and-notifications; https://cursor.com/docs/grok-bot/settings (via search snippet only)

## Permissions, approvals, safety
**What**: Approval cards plus a model-based "Auto-review".
**Detail**
- Approval choices: Allow once / Always allow (saves a matching rule) / Deny (iPhone: approve once / deny). Approvals don't reverse completed work.
- Settings > General > Auto-review: "Ask first" and "Allow automatically" rules; Ask-first wins on conflict. Independent review model evaluates shell, plugin calls, computer use, automation writes, delegation. Docs: complements, not replaces, least privilege.
- Always-ask categories: send, publish, financial transfers, delete, permission changes, production changes.
- Enterprise: Firecracker microVM per user, org kill-switch, network egress policies (4 modes), team secrets (100, encrypted), action recording (metadata only, secrets scrubbed, URLs stripped of query strings), SCIM, MCP allowlist, enforce Auto-review, SIEM/OpenTelemetry export.
- Privacy follows Cursor settings; Privacy Mode = no training.
**Limits**: All Bots share one VM, files, sessions; docs say "Do not use separate Bots as a security boundary". Deleting a Bot leaves files/logins. Per-user audit log and Grok-Bot spend caps "coming, not shipped" (DataCamp). Prompt injection is the HN worry.
**Sources**: https://docs.x.ai/grok-bot/approvals-security-and-privacy; https://docs.x.ai/grok-bot/teams-and-enterprises; https://docs.x.ai/grok-bot/faq; https://www.digitalapplied.com/blog/grok-bot-ai-teammates-launch-cloud-computer-2026

## Reception
**Praise**: low-latency cloud UX, teach-a-task, Slack-triggered routines worked (note.com hands-on); Lenny Rachitsky "haven't been this excited..."; HN: async work, domain-separated Bots.
**Criticism**: price gate (later relaxed); model opaque; shared-VM security vs. "own computer" marketing; fragile recorded skills; token burn; prompt-injection worry; spam/scale concerns; "treat as a first-week new hire" (Layer3Labs).
**Comparisons**: Claude Cowork (cheaper via Claude plans), ChatGPT Agent, OpenClaw/Hermes (control vs convenience), Gemini Spark (stops before spending).
**Sources**: https://www.layer3labs.io/guides/grok-bot-review; https://news.ycombinator.com/item?id=49261514; https://note.com/masa_wunder/n/na9744c486976; VentureBeat (above)

## System prompt / design principles
No leaked or published system prompt found. Published principles (docs/guides): narrow roles; code over agent for deterministic work; connectors over computer use; evidence-linked reports; test before scheduling.

## Confidence
- Primary (x.ai news, docs.x.ai pages fetched): launch dates, plans, platforms, approvals/Auto-review, takeover, local execution, routines/skills limits, enterprise controls, settings/notifications, shortcuts.
- Press (VentureBeat, 9to5Mac, Techmeme, DataCamp, Digital Applied): pricing tiers, Cursor acquisition, model undocumented, governance gaps. Tier naming is inconsistent across sources.
- Single blogs (Composio, Daily Dose of DS, Flavio Copes, note.com, Layer3Labs): groups 2-6, memory layers, hands-on quality, failure modes.
- Unverified: menu-bar presence, undo, step-by-step progress UI, Linux desktop status. Page contents came through a small summarizing model; wording may be paraphrased.

## Distinctive ideas worth copying
1. Takeover handoff: user steps into the live session for passwords/2FA/CAPTCHA and hands it back, secrets never in transcript (needs a visible browser session; fit unverified).
2. Teach-a-task: record up to 10 min of screen, produce a draft skill the user then annotates with decision rules and failure handling.
3. Approval trio Allow once / Always allow (saves a matching rule) / Deny, with Ask-first beating Allow-automatically on conflict.
4. Model-based Auto-review as an independent gate over shell/plugin/computer-use/automation-write/delegation calls, layered on rules.
5. Routine lifecycle: test run, enable/pause, last 20 run records, one-task -> skill -> routine promotion ladder.
6. Event-triggered routines (Slack/GitHub) alongside cron, with a narrow-match-rule warning.
7. Watcher pattern: save every check, notify only on threshold crossing.
8. Standard report template: verified facts / assumptions / actions done / awaiting approval / unresolved.
9. Sidebar attention states (Needs attention vs Unread) plus per-agent OS notifications suppressed while focused.
10. `/skill` and `@agent/@routine/@connector` composer mentions.
11. "Execution on Local Computer" as a per-device Ask/Always/Never switch (per-tool permissions exist; per-device scope is the new bit).
12. Route browser egress through the user's own IP (only relevant with a cloud browser; unverified need).
