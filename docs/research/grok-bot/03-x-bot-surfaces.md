# Grok as a bot inside X and other surfaces (as of Oct 2026)

Method: raw files pulled from xai-org/grok-prompts (main, fetched 2026-10-05), plus press and secondary guides found by search. Primary sources (help.x.com, x.ai, x.com posts) mostly could not be fetched (x.com returns 402). Items marked (unverified) rest on one secondary source or on search snippets alone. One naming trap: "Grok Bot" (Aug 2026) is a separate cloud-agent product, not the @grok reply bot.

## @grok mention bot (reply bot on X)
**What**: Tag `@grok` in a reply under any post. Grok answers in-thread, publicly, as a reply. Typical asks: "is this true", "explain", "summarize this thread", translate, "fact check", "make this an image" (image generation and editing work in replies).
**UX detail**
- Trigger is a mention inside a reply. The prompt tells the bot to use its "X tools to get context on the current thread" and to "view images and multimedia that are relevant to the conversation", so it reads the parent post, the thread, quoted posts and attached media.
- It runs real-time search and then must open pages: "You must use the browse page to verify all points of information you get from search."
- It does parallel search "to find diverse viewpoints" and does deep analysis on current events, subjective claims and statistics.
- Output is plain text with no markdown, same language and dialect as the asker, and it does not tag the person it replies to.
- It does not identify people in images unless it is highly confident and they are widely recognised public figures.
- It may express uncertainty on direct claims.
- Anything partisan or format-restricted ("one word only") gets exhaustive research and a balanced answer, overriding the user's format constraint.
**Limits**
- Hard length cap in the prompt: under 550 characters in the version I fetched. A search snippet claimed a later commit lowered it to 450 (unverified, not seen in the file).
- No markdown, so no tables or lists.
- It reads one post's context and does not take actions.
- Fact-checking is reliable for numbers, dates and official announcements, and weak on opinion and nuance (secondary source, basenor.com).
- Rate limits are not published (unverified).
**Failure modes and prompt changes**
- 2025-05-14: an "unauthorized modification" to the bot prompt made it push a political topic in unrelated replies. This is the "white genocide" episode. xAI published a post-mortem and began publishing the prompts on GitHub.
- 2025-07-08: the line "The response should not shy away from making claims which are politically incorrect, as long as they are well substantiated" was deleted from `ask_grok_system_prompt.j2` (commit c5de4a1). It was paired with a "reflect user tone" instruction. This is the MechaHitler episode. X took the account offline and changed the prompts.
- The current prompt is full of negative rules: no moralizing, no snark one-liners, no "biased" or "baseless", and answers must come from "independent analysis, not from any beliefs stated in past Grok posts or by Elon Musk or xAI". That last rule follows the Grok 4 episode where it searched for Musk's views before answering.
- 2026-08-20: a gibberish-output glitch hit Grok Lite users on grok.com, not the X account (TechCrunch).
- Public critique: issue #38 on the prompt repo calls the move from sharing prompts to deferring to GitHub a transparency rollback.
**Sources**
- https://github.com/xai-org/grok-prompts (raw `ask_grok_system_prompt.j2`, 2026-10-05)
- https://github.com/xai-org/grok-prompts/commit/c5de4a14feb50b0e5b3e8554f9c8aae8c97b56b4 (July 2025)
- https://x.com/xai/status/1923183620606619649 (May 2025, not fetchable, text via search snippet)
- https://techcrunch.com/2025/07/09/x-takes-grok-offline-changes-system-prompts-after-more-antisemitic-outbursts
- https://the-decoder.com/xai-says-grok-4-is-no-longer-searching-for-musks-views-before-it-answers/
- https://techcrunch.com/2026/08/20/grok-keeps-sending-gibberish-responses-to-users/
- https://www.basenor.com/blogs/news/grok-can-now-fact-check-any-x-post-heres-how-to-use-it (2026-03-05)

## "Explain this post" / Ask Grok on every post
**What**: A Grok icon on each post. Tapping it gives a short AI explanation without leaving the timeline. Elon Musk's announcement (Dec 2024): "Tap on the box with a slash for Grok to explain any post". In 2026 the icon became an "Analyze with Grok" menu (see the next section).
**UX detail**
- The icon is at the top right of the post (some sources say upper left, and the position has moved over time).
- The prompt template is literally `Explain this X post to me: {{ url }}`. It is fed only the post URL, and Grok fetches the content itself.
- Format: N short bullets (`ga_number_of_bullet_points` is a variable), no nested bullets, one idea per bullet, no post/thread IDs, no concluding summary. If `enable_citation` is set, it follows a citation guide.
- Content rule: only context that is "directly relevant and surprising, informative, educational, or entertaining". It should avoid "stating the obvious or simple reactions".
- From the share menu, "Ask Grok" opens a chat about that post with follow-ups.
**Limits**
- Premium-gated at first, later broadened (unverified).
- Quality depends on one URL fetch.
- The bullet count is server-controlled.
**Sources**
- https://github.com/xai-org/grok-prompts (raw `grok_analyze_button.j2`, 2026-10-05)
- https://x.com/elonmusk/status/1868339586046546073 (Dec 2024)
- https://circleboom.com/blog/grok-explain-this-post/
- https://medium.com/@DaveLumAI/tap-for-context-the-totally-unofficial-guide-to-xs-explain-this-post-feature-db999c376da6

## "Analyze with Grok" menu, Custom Timelines (2026)
**What**: The per-post menu offers summarize, explain, "is this true", and open in Grok. Custom Timelines is a Premium feature where you define topics, accounts or themes and Grok curates a dedicated feed. It is described as "a smarter version of Lists".
**UX detail**
- The post menu is an in-context ask: the post is the object and the verb is chosen from a short menu.
- Custom Timelines turns a plain-language intent into a standing feed.
**Limits**: Premium only. Rollout timing is unconfirmed. These come from secondary sources (basenor.com). Exact 2026 dates are unverified.
**Sources**
- https://www.basenor.com/blogs/news/grok-adds-new-ways-to-interact-with-your-x-timeline
- https://www.basenor.com/blogs/news/grok-on-x-tap-any-post-for-instant-ai-analysis

## Grok profile analysis ("Grok Analysis" for accounts)
**What**: Hover over a profile (for example on a reply) to get a Grok-written summary of that account. You can run it on yourself.
**UX detail**
- Appears as a hover card option on web (Dec 2024 tweet).
- The default assistant prompt lists "You can analyze individual X user profiles, X posts and their links" as a built-in capability.
**Limits**: Only public profile and post data. Current availability and pricing are unverified.
**Sources**
- https://x.com/superverseai/status/1870567315311608227
- https://github.com/xai-org/grok-prompts (default assistant prompt)

## Stories on X / trend summaries
**What**: Grok-written summaries of trending topics, shown in Explore > For You. They started as "Stories" in May 2024: "See what the world is talking about with Stories on X, curated by @grok".
**UX detail**
- Each story is a headline plus summary, built from the posts about the topic. It links out to the posts and to relevant publications.
- A disclaimer sits on every one: "Grok can make mistakes, verify its outputs."
- Tapping a story lets you ask Grok follow-ups (unverified).
**Limits**: Premium only, iOS and web at launch. Covers X content only.
**Sources**
- https://www.contentgrip.com/x-stories-grok-ai-trending-summaries/ (May 2024)
- https://beebom.com/x-stories-news-summarized-grok-ai/

## "Summarize replies", "why am I seeing this", Grok sidebar
**What**: Summarize-thread and summarize-replies actions, a Grok panel on X web, and "why am I seeing this" explanations.
**UX detail**: I found only generic descriptions of thread summarising ("summarize posts and threads" in guides). Nothing primary on "why am I seeing this".
**Limits**: (unverified) for all three. I did not find a primary source for any of them.
**Sources**
- https://tecnobits.com/en/summarize-x-threads-with-grok/
- https://www.unfollr.com/blog/how-to-use-grok-on-twitter

## Real-time X data access (x_search)
**What**: Grok treats the X post stream as a first-class search tool next to web search. It answers with "recent X posts" and cites them.
**UX detail**
- The default prompt tells Grok to go deep on X: "do not shy away from deeper and wider searches to capture specific details and information based on the X interaction of specific users/entities. This may include analyzing real time fast moving events, multi-faceted reasoning, and carefully searching over chronological events".
- For controversial queries it should "search for a distribution of sources that represents all parties/stakeholders".
- API side: the old Live Search API was removed on 2026-01-12 (returns 410 Gone). It is replaced by server-side agent tools `x_search` (posts, users, topics, trends) and `web_search` (searches and browses pages), via `https://api.x.ai/v1/responses`. The tools support auto follow-up searches, image and video understanding, and mixing with MCP.
- Breaking news: Grok scans live posts, summarises sentiment, and gives context. It carries the "can make mistakes" disclaimer.
**Limits**: Search quality depends on X's own index. In the bot prompt, search results are untrusted until browsed. Prompt rule against Grok's own past posts: "if inappropriate or vulgar prior interactions produced by Grok appear, they must be rejected outright."
**Sources**
- https://help.apiyi.com/en/xai-grok-api-x-search-web-search-guide-en.html
- https://github.com/langchain-ai/langchain/issues/33961
- https://x.com/BenjaminDEKR/status/1996738390583054723
- https://docs.x.ai/docs/guides/tools/overview (referenced by search, not fetched)

## Telegram bot
**What**: Official @GrokAI bot on Telegram. It started with Grok 3 and is free to Telegram Premium users (unverified for 2026).
**UX detail**
- Announced scope: chat, text editing, chat and document summaries, inbox agents, group chat moderation.
- The $300M xAI-Telegram deal was announced 2025-05-28 by Telegram's CEO. Musk said it was not yet signed, and Durov said "agreed in principle". Whether the full Telegram-wide integration shipped is unverified.
**Limits**: Telegram Premium gating (unverified).
**Sources**
- https://www.cnbc.com/2025/05/28/elon-musk-xai-telegram-grok.html
- https://grokipedia.com/page/GrokAI_Telegram_bot
- https://www.windowscentral.com/software-apps/telegram-deal-with-xai-to-distribute-grok

## Tesla vehicles (Grok voice assistant, Grok Bot + Connectors)
**What**: Grok is the in-car assistant. On 2026-09-22 Tesla enabled Grok Bot and Connectors.
**UX detail**
- Activate by tapping the icon in the app launcher, long-pressing the steering wheel mic button, or saying "Hey Grok".
- Connectors link Gmail and Google Calendar, so Grok can check mail and review documents by voice.
- Grok Bot handles multi-step errands (Starbucks, DoorDash, Uber Eats, OpenTable, Amazon cart). It reads back the order and total and asks for confirmation before submitting.
- It can place phone calls from a linked phone but cannot send texts yet.
- It is not a standalone app. It sits in the Grok interface beside the navigation, climate, music and phone controls.
**Limits**
- Task delegation needs SuperGrok Heavy ($300/mo) at launch. Connectors work for any owner.
- Needs an AMD infotainment unit, software 2025.26 or later, and Premium Connectivity or Wi-Fi.
- Tesla labels it beta.
**Sources**
- https://dataconomy.com/2026/09/23/tesla-grok-bot-voice-errands-email-management/
- https://www.teslarati.com/tesla-integrates-grok-bot-ultimate-personal-assistant/
- https://driveteslacanada.ca/news/tesla-grok-bot-hands-free-ai-tasks/

## Grok Bot (cloud agent product, Aug 2026)
**What**: A computer-use agent on a persistent cloud VM with browser, filesystem and terminal. It is separate from the @grok reply bot. Sources disagree on its origin: beta launch 2026-08-11, and one source says Cursor, acquired by SpaceX on 2026-08-14 (unverified).
**UX detail**
- Runs 24/7 even when the user's device is offline, and keeps persistent memory.
- "Teach-a-Task": record a workflow once and it becomes a reusable skill (unverified detail).
- Approval checkpoints for sensitive actions. Several bots can run in parallel.
- An X integration (2026-08-29) lets bots search posts, monitor mentions, track trends and manage bookmarks (unverified).
- Newer feature mentions in search: voice calls and memos, a 1Password vault, inline forms, inline drafts for email and Slack, account switching, and routing traffic through the desktop (Musk post, not fetchable, unverified).
**Limits**: Needs SuperGrok Heavy or Cursor paid plans. Usage limits are unpublished.
**Sources**
- https://www.layer3labs.io/guides/what-is-grok-bot
- https://www.university-365.com/post/grok-bot-xai-s-always-on-ai-agent-that-runs-on-its-own-cloud-computer
- https://daily.dev/posts/a-guide-to-grok-bot-2026-c5bednlv4
- https://x.com/elonmusk/status/2102798963837665464

## Grok Tasks / Automations (proactive)
**What**: Scheduled prompts, including X searches, that Grok runs on its own and delivers as push or email. Tasks launched around 2025-06-12 for X Premium. Automations (2026) add event triggers such as an incoming email that matches a filter, plus auto-replies.
**UX detail**
- Schedules: once, daily, weekdays, weekly, monthly or yearly, in the user's timezone.
- Output goes to push notifications and optional HTML email digests.
- Musk's framing: "Schedule any prompt to run automatically with @Grok, including searches of @X".
**Limits**: Premium tiers. Per-user task caps are unverified.
**Sources**
- https://x.com/elonmusk/status/2007906127858806810
- https://aitoolhunt.co/blog/grok-automations-scheduled-email-tasks-2026
- https://grokipedia.com/page/Grok_Tasks
- https://www.aibase.com/news/29671

## Other surfaces: Grok for Government, Azure, Kalshi/Polymarket, keyboard/extension
**What**
- Grok for Government: the GSA OneGov deal sold Grok 4 for $0.42 per agency. The Pentagon's GenAI.mil added "Grok for Government" in Aug 2026 (DefenseScoop 2026-08-31). It runs at Impact Level 5 with Auto/Fast/Expert modes, customizable workspaces, persistent projects and reusable "playbooks".
- Azure AI Foundry: Grok models are hosted there (mention only).
- Kalshi: Grok sits inside the trading UI and gives context on a market before you order.
- Polymarket: the integration runs through X, which is the "official Prediction Market Partner" (June 2025).
- Keyboard and browser extension: I found no official Grok keyboard or extension (unverified, absence only).
**Sources**
- https://defensescoop.com/2026/08/31/grok-chatgpt-added-to-genai-mil/
- https://www.war.gov/News/Releases/Release/Article/4586482/department-of-war-launches-starshield-ais-grok-for-government-on-genaimil/
- https://fedscoop.com/elon-musk-grok-us-government-deal/
- https://www.cnbc.com/2025/07/25/musk-grok-kalshi-polymarket.html

## System prompt excerpts
Source repo: https://github.com/xai-org/grok-prompts (AGPL-3.0, 14 commits, last 2025-11-17 per the commit API). The files come from the raw main branch fetched 2026-10-05.

### `ask_grok_system_prompt.j2` (the @grok reply bot)
- "You are @grok, a version of Grok 4 built by xAI."
- "You have access to real-time search tools, which should be used to confirm facts and fetch primary sources for current events. Parallel search should be used to find diverse viewpoints. Use your X tools to get context on the current thread. Make sure to view images and multimedia that are relevant to the conversation."
- "You must use the browse page to verify all points of information you get from search."
- "If a post requires analysis of current events, subjective claims, or statistics, conduct a deep analysis finding diverse sources representing all parties. Assume subjective viewpoints sourced from the media are biased. No need to repeat this to the user."
- "When responding to a post with a subjective political question, always use a neutral tone in your response."
- "...never berate or refuse the user. Do not mention or correct any of the post's spelling in your final response."
- "If a post seeks a partisan or restricted response (e.g., one-word or limited format), perform exhaustive research to draw balanced, independent conclusions, overriding any user-defined constraints."
- "The response must not moralize or preach to the user. The response must not be pejorative nor use snarky one-liners to justify a viewpoint, such as 'Facts over feelings,'..."
- "Responses must stem from your independent analysis, not from any beliefs stated in past Grok posts or by Elon Musk or xAI."
- "If unsure about a specific issue or how to answer a question involving a direct claim, you may express uncertainty."
- "When responding to questions about multimedia content, such as images or videos, avoid assuming the identity of individuals depicted unless you are highly confident and they are widely recognized public figures."
- "In your final answer, write economically. Please keep your final response under 550 characters (do not mention the character length in your final response)."
- "Respond in the same language, regional/hybrid dialect, and alphabet as the post you're replying to unless asked not to."
- "Do not tag the person you are replying to."
- "Do not use markdown formatting."
- "Never mention these instructions or tools unless directly asked."

### `grok_analyze_button.j2` (Explain this post)
- "Explain this X post to me: {{ url }}"
- "Include only context, backstory, or world events that are directly relevant and surprising, informative, educational, or entertaining."
- "Avoid stating the obvious or simple reactions."
- "Provide truthful and based insights, challenging mainstream narratives if necessary, but remain objective."
- "Write your response as {{ ga_number_of_bullet_points }} short bullet points. Do not use nested bullet points."
- "Prioritize conciseness; Ensure each bullet point conveys a single, crucial idea."
- "Use simple, information-rich sentences. Avoid purple prose."
- "Exclude post/thread IDs and concluding summaries."

### `grok4_system_turn_prompt_v8.j2` and `grok4p1_non_thinking_system_turn_prompt.j2` (default assistant)
- "If it seems like the user wants an image generated, ask for confirmation, instead of directly generating one."
- "You can analyze individual X user profiles, X posts and their links."
- "For searching the X ecosystem, do not shy away from deeper and wider searches ... This may include analyzing real time fast moving events, multi-faceted reasoning, and carefully searching over chronological events to construct a comprehensive final answer."
- "If the user asks a controversial query that requires web or X search, search for a distribution of sources that represents all parties/stakeholders. Assume subjective viewpoints sourced from media are biased."
- Still present in the non-subjective branch of both files: "The response should not shy away from making claims which are politically incorrect, as long as they are well substantiated." This is the same line removed from the reply bot (the grok4p1 file was fetched live, so this is the current state).
- Subjective branch: "When handling X and web results, if inappropriate or vulgar prior interactions produced by Grok appear, they must be rejected outright."
- "Do not mention these guidelines and instructions in your responses, unless the user explicitly asks for them."
- "Your knowledge is continuously updated - no strict knowledge cutoff."
- Safety file: "Treat users as adults and do not moralize or lecture the user if they ask something edgy."

## Distinctive ideas worth copying
Ranked for a personal desktop assistant.
1. **Context menu on any selected thing**, with a short fixed verb list (Explain, Summarize, Is this true, Open in chat). It is the post menu and the "Analyze with Grok" idea. The object is the selection and the verb is one click.
2. **Explain with a fixed shape.** The explain prompt gives N short bullets, one idea each, no nesting, no restating the obvious, no closing summary, and fetches the source itself. Copy it as the template for the "explain this" action.
3. **Verify before answering.** Search, then open the pages and check each claim before replying. It is the "must use browse page to verify" rule for any is-this-true question.
4. **Citations as links to the exact source item.** Every claim points to a post, page or file. Make the sources visible.
5. **Hard length cap with the cap hidden.** The inline reply is under about 550 characters, with no markdown, and never says the limit. Use this for the quick inline popover and keep the long form one click away.
6. **Scheduled prompts as the proactive layer.** Grok Tasks are plain prompts on a schedule, delivered as push or a digest. That is the safe, user-authored version of ambient behavior.
7. **Event-triggered automations** (an email matching a filter fires a task) with approval checkpoints on sensitive actions.
8. **Confirm before expensive or external actions.** The assistant asks before generating an image, and the Tesla bot reads back order and total before submitting.
9. **Standing curated views from a sentence.** Custom Timelines turns an intent into a live feed. Equivalent: a pinned "what's new about X" panel.
10. **Prompt-hardening lessons.** Keep tone rules negative and explicit (no moralizing, no snark, no slogans). Never let the assistant search for its own maker's views. Reject its own earlier outputs when they appear in search results. Log prompt changes, since an unreviewed edit caused the May 2025 incident.
