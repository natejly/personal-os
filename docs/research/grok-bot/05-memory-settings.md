# Grok (xAI) consumer assistant: memory, personalization, privacy, settings (as of Oct 2026)

Caveat: official x.ai / help.x.com pages blocked fetches (403) or were not retrievable. Most detail below comes from secondary sources (tech press, guides). Items marked (unverified) rest on one weak source.

## Memory (reference past chats)
**What**: Persistent memory launched April 2025 on grok.com and the iOS/Android apps, later extended to Grok on X. Grok distils facts ("you're vegan", "you write in Python") into embeddings in a separate store rather than keeping whole transcripts (secondary-source description, unverified as to internals).
**UX detail**
- On by default. Toggle at Settings > Data Controls > Memory.
- User can view what Grok remembers, delete individual memories, or reset all.
- Press coverage: "Memories are transparent... see exactly what Grok knows and choose what to forget."
- Per-chat "Forget" button to exclude a specific conversation from memory: announced as coming first to Android (eWeek); current state (unverified).
- No confirmed per-reply indicator showing which memories were used (unverified; not found).
- Region: not available at launch in parts of the EU and UK (later status unverified).
- Separate setting "Personalize Grok with your conversation history" (DeleteMe guide).
**Tier/limits**: Free and paid; no documented cap found.
**Sources**: https://www.eweek.com/news/grok-can-remember-conversations-now/ (Apr 2025); https://joindeleteme.com/ai-privacy-settings/grok-privacy-settings-guide/ ; https://www.justthink.ai/blog/grok-evolved-xais-new-memory-feature ; https://aitoolsrecap.com/Blog/grok-agent-settings-explained-2026

## Personalization from X
**What**: Grok on X can tailor answers using the user's X profile/posts/interactions.
**UX detail**
- Opt-outs live in two places: "Personalize Grok using X" and "Personalize Grok with your conversation history" (Grok settings); and X Privacy settings > Grok & third-party collaborators, toggle for letting public data and Grok interactions be used for training/fine-tuning.
- X help text: users "control how your data as well as your interactions, inputs and results with Grok on X are used to personalize your Grok experience."
- Caveat: some X features "powered by Grok" are not governed by the chatbot toggles.
**Tier/limits**: all accounts signed in with X.
**Sources**: https://joindeleteme.com/ai-privacy-settings/grok-privacy-settings-guide/ ; https://help.x.com/en/using-x/about-grok (search snippet only, page 403)

## Custom instructions / Customize Grok
**What**: Global instructions launched Jan 2025 (button "Customize Grok" in the input); response-style presets: Formal, Socratic, Concise, Custom (free text only in Custom).
**UX detail**
- Applies to new conversations only, not existing chats.
- Limit reported inconsistently: ~4,000 chars; one source says 12,000 restored after a March 2026 cut (unverified).
- Newer layers (secondary source, unverified): Custom Agents (4 slots, 4,000 chars each, March 2026), Skills invoked as /commands (May 2026), Workspaces (isolated instructions, files and history per project; instructions override global).
- "Fun/Spicy" personality toggle under Settings > Customize > Personality (unverified).
**Tier/limits**: not tier-gated per sources.
**Sources**: https://x.com/techdevnotes/status/1883101018772418686 (Jan 2025); https://www.latestly.com/socially/technology/grok-web-new-update-elon-musks-xai-introduces-custom-instructions-feature-to-let-users-to-personalize-grok-ais-response-style-6691502.html ; https://www.uniflow.kr/en/grok-custom-instructions-setup-guide-workspaces-agents/ (Apr 2026) ; https://aitoolsrecap.com/Blog/grok-agent-settings-explained-2026

## Private Chat (ghost mode)
**What**: Ghost icon at top right of a chat starts a Private Chat.
**UX detail**
- Not shown in history, not used for training, not used by memory; self-destructs on exit.
- Backend deletion within 30 days, with exceptions for legal/safety.
- Per DeleteMe guide, applies on grok.com and mobile apps.
**Tier/limits**: all tiers.
**Sources**: https://joindeleteme.com/ai-privacy-settings/grok-privacy-settings-guide/ ; https://nixm.ai/learn/is-grok-private ; https://gadgetstouse.com/blog/2026/01/27/use-chatgpt-gemini-grok-in-private-mode-no-training/ (Jan 2026)

## Data controls and the shared-link indexing incident
**What**: Training opt-out, history deletion, export, share-link management.
**UX detail**
- "Improve the model" toggle: Settings > Data Controls (mobile) / Settings > Data (web).
- Delete conversations, Imagine posts, uploaded files; deletion processed within ~30 days.
- Account export before deletion; account deletion takes up to 30 days.
- Incident: Aug 20-22 2025, Share created a public, indexable URL with no noindex and no warning; ~370k chats appeared in Google (Forbes count). xAI made no formal statement.
- Fix/control: grok.com/share-links lists every shared link with Remove per link (secondary source; unverified whether noindex was added).
**Tier/limits**: Business plan ($30/seat) promises no training on data (secondary source).
**Sources**: https://www.fortune.com/2025/08/22/xai-grok-chats-public-on-google-search-elon-musk ; https://www.tomsguide.com/ai/hundreds-of-thousands-of-grok-chatbot-conversations-are-showing-up-in-google-search-heres-what-happened ; https://www.aicerts.ai/news/browser-data-scandal-grok-chats-exposed-to-search-engines/ ; https://x.com/grok/status/2004401863152525762

## Model picker, modes, settings, desktop
**What**: Modes named Auto / Fast / Expert / Heavy on grok.com (naming per secondary sources; exact current list unverified).
**UX detail**
- Free tier is Fast mode only per one pricing guide; Heavy forces multi-agent parallel reasoning.
- Models mentioned in 2026 guides: Grok 4.3, 4.5, 4.6 (inconsistent between sources; unverified).
- No official standalone macOS app found; Grok runs at grok.com, with third-party Option+Space overlay apps and menu-bar usage trackers. An official Mac app with hotkey/menu bar/launch at login is unverified and probably does not exist as of this research.
- Language, appearance and voice settings not confirmed from primary sources.
**Sources**: https://www.hongkiat.com/blog/install-grok-mac/ ; https://macdailynews.com/2026/03/30/how-to-use-grok-ai-on-your-iphone-ipad-and-mac/ ; https://alloypress.com/blogs/grok-ai-pricing (Jul 2026)

## Tiers and prices
**What**: Pricing per a July 2026 secondary guide (sources disagree on model names).
**UX detail**
- Free $0: Fast mode, ~10 prompts / 2 h, a few images.
- X Premium $8: slightly higher limits, no Heavy, DeepSearch, Companions.
- SuperGrok Lite $10: ~15 Imagine videos/day at 480p.
- SuperGrok $30 ($300/yr): flagship model, DeepSearch, full Imagine, shared weekly usage pool.
- X Premium+ $40: Grok bundled with X perks.
- SuperGrok Plus $100 (one source, unverified).
- SuperGrok Heavy $300: multi-agent mode, largest context, top priority.
- Business $30/seat (SOC 2, no training); Enterprise custom.
**Sources**: https://alloypress.com/blogs/grok-ai-pricing ; https://www.cloudzero.com/blog/grok-pricing/ ; https://felloai.com/grok-pricing/

## Safety and children
**What**: Kids Mode (Oct 2025) in the mobile app with content filters and an optional PIN to prevent turning it off. "Spicy" mode in Imagine (Aug 2025) for NSFW.
**UX detail**
- Kids Mode toggle in mobile settings only, not web or X; second toggle sets the PIN.
- Common Sense Media (Jan 2026): "among the worst we've seen"; Kids Mode ineffective, explicit content pervasive. xAI restricted image editing to paid X subscribers after backlash.
**Sources**: https://techcrunch.com/2026/01/27/among-the-worst-weve-seen-report-slams-xais-grok-over-child-safety-failures/ ; https://www.internetmatters.org/parental-controls/entertainment-search-engines/grok/

## Distinctive ideas worth copying
1. Temporary/ghost chat: one icon, nothing saved, excluded from memory and learning, hard-deleted on exit. Cheap to build; high trust value.
2. "Forget this conversation": exclude one chat from memory extraction without deleting it (Grok announced; verify shipping).
3. Per-source personalization toggles (history vs social feed) so memory use can be partially disabled.
4. Per-reply memory-use transparency (Grok does not clearly do this; opportunity to beat it): "used 2 memories" chip linking to the rows.
5. Share-link manager page listing every public link with Remove; default noindex and a warning at share time.
6. Response-style presets (Formal / Socratic / Concise / Custom) as one-click alternatives to free-text instructions.
7. Skills as /commands plus agent persona slots, each with its own instruction cap (unverified details).
8. Kids-style locked mode with PIN (low priority for a personal desktop app).
9. Personalization from a feed (X posts): for a desktop app, analogue is opt-in import of mail/calendar/notes signals.
