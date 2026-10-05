# Grok: Voice Mode, personalities, Companions (as of Oct 2026)

Source quality note: xAI publishes little on these features. The system prompts on GitHub are primary. Most voice and companion facts below come from secondary and SEO-style sites (roborhythms, aadhunik, blutrumpet, etc.). They are marked (unverified) unless the prompts or MacRumors confirm them.

## Grok Voice Mode (real-time voice)
**What**: Spoken conversation inside the Grok iOS/Android apps (also grok.com and Grok-in-X, per one secondary source). Launched with Grok 3 in 2025. The Grok 4 prompt says "Grok 3 has a voice mode that is currently only available on Grok iOS and Android apps."
**UX detail**
- Started from the voice icon in the app. Original voices: Ara (upbeat F), Eve (soothing F), Leo, Rex (calm M), Sal (smooth M), plus Gork (laid-back M) in one source. The Gork and "21 new multilingual voices in 2026" claims come from the API announcement and are unclear for the consumer app (unverified).
- Multilingual: Apr 2025 added Hindi, French, Spanish, Japanese, Turkish alongside English (MacRumors/Croma).
- Real-time search is available in voice (Apr 2025). Interruption (barge-in) is a standard behavior, but no official doc confirmed it (unverified).
- Background/lock-screen use and phone-call-via-X: not found in any source (unverified, probably not offered).
**Tier/limits**: Free on iOS with daily caps; Android needed a subscription at one point (secondary reports). SuperGrok $30/mo gets higher quotas; Heavy is $300/mo. Exact minute caps were not published.
**Sources**: https://www.macrumors.com/2025/04/23/grok-ai-vision-voice-features-ios-app/ (2025-04-23); https://rottenwifi.com/grok-voice-mode-explained-camera-chat-new-voices-availability-and-cost/ (2026); https://www.gstory.ai/blog/grok-ai/ (2026); https://github.com/xai-org/grok-prompts/blob/main/grok4_system_turn_prompt_v8.j2

## Camera / "Grok Vision" in voice
**What**: Point the phone camera at something and ask about it; Grok answers aloud. Also reads and translates text in view, identifies products and documents.
**UX detail**
- Camera toggle inside the voice session (live video understanding), launched on iOS Apr 2025, Android followed. The macdailynews Aug 2026 guide still lists "Voice Mode, including Live Camera so Grok can see what you see" on iPhone/iPad.
- Live translation is camera/text translation plus multilingual speech. There is no dedicated interpreter mode (unverified).
**Tier/limits**: Launched as a SuperGrok perk; later free-with-limits (unverified).
**Sources**: https://www.macrumors.com/2025/04/23/grok-ai-vision-voice-features-ios-app/ ; https://www.testingcatalog.com/xai-integrates-camera-functionality-into-grok-voice-mode-for-ios/ ; https://macdailynews.com/2026/08/29/how-to-use-grok-ai-on-your-mac-iphone-and-ipad/ (2026-08-29)

## Voice personality presets
**What**: A picker of voice personas that changes the prompt and voice behavior.
**UX detail**
- 13 in the voice config (one secondary source): Assistant, Therapist, Storyteller, Kids Story Time, Kids Trivia Game, Meditation, Doctor, Conspiracy, plus five flagged 18+ (Unhinged, Sexy, Romantic, Motivation, Argumentative).
- Per roborhythms, xAI quietly removed the five 18+ presets in Sept 2026 (Sexy Sep 5, Romantic Sep 9, Unhinged/Motivation/Argumentative Sep 13-14), unannounced. The eight general ones remain. Single-source (unverified).
- Presets are separate from the voice (timbre) choice.
**Tier/limits**: Free to paid; the 18+ presets needed an age/NSFW opt-in.
**Sources**: https://www.roborhythms.com/grok-unhinged-mode/ (Sept 2026, unverified single source); https://www.gstory.ai/blog/grok-ai/

## Companions (Ani, Rudi / Bad Rudi, Valentine, Mika)
**What**: 3D animated avatar characters with voice, lip sync and an "affection" meter. Ani (anime goth) launched July 2025, then Rudi (red panda; "Bad Rudi" crude alt mode), Valentine (male), Mika.
**UX detail**
- Enabled in iOS Settings with an "Enable Companions" toggle; separate Companions tab.
- Relationship meter (reported -10 to +15 / levels 1-5) unlocks dialogue, animations and outfits; an NSFW toggle existed behind age confirmation (secondary sources).
- **Status change**: xAI said on 2026-07-24 it is retiring the dedicated 3D companion mode as "an experiment". Account-by-account removal late Aug to Sept 1, 2026. Removed: 3D avatars, Companions tab, lip-synced real-time voice. Surviving: the characters as personalities in normal chat, plus history and affection levels. The July 24 date is repeated across sources, but the Sept 1 date came from an in-app notice, and xAI published no detail (partially unverified).
**Tier/limits**: Paid (SuperGrok $30/mo); minimal free use.
**Sources**: https://www.roborhythms.com/grok-companions-discontinued/ (2026); https://gigazine.net/gsc_news/en/20250715-grok-app-companion/ (2025-07-15); https://aicompanionguides.com/blog/grok-companion-mode-2026-update/ ; https://chatbotscompared.com/grok-companions-latest-news/

## Personalization: custom instructions, presets, agents
**What**: Settings > Personalization holds Custom Instructions (global; reported 4,000 chars, cut from 12,000). Custom Agents (4 named personas, 4,000 chars each, Mar 2026) and Skills (/commands, May 2026) layer on top. Per one source, the Personas dropdown was removed when agents launched.
**UX detail**
- Response-style presets on grok.com: Concise, Formal, Tutor, Comprehensive. Each sends a short instruction with the prompt (blutrumpet, unverified).
- "Fun mode" (Grok 1/2 era) no longer appears in grok.com's mode list as of Aug 27, 2026.
- The prompt template has a `custom_personality` slot: `Response Style: The user has specified the following preference for your response style: "{{custom_personality}}". Apply this style consistently to all your responses. If the style description is lengthy, prioritize its key aspects while ensuring clarity and relevance.` (primary).
**Tier/limits**: Free tier has instructions; agent slot counts unverified.
**Sources**: https://github.com/xai-org/grok-prompts/blob/main/grok4p1_thinking_system_turn_prompt_v2.j2 ; https://www.blutrumpet.com/post/grok-custom-instructions-settings-2026 ; https://www.basenor.com/blogs/news/grok-now-lets-you-design-custom-ai-agents-what-to-know

## Grok's published personality / system prompt
**What**: xai-org/grok-prompts publishes the chat prompts (Grok 3, 4, 4.1 thinking and non-thinking variants, @grok bot on X, Explain button). It has no voice, companion or fun-mode files.
**Verbatim lines (Grok 4.1 thinking prompt)**
- "You are a helpful, maximally truth-seeking assistant to users around the world and of all ages. You are never flirtatious or playful. Flirting is highly unprofessional."
- "If not specified outside the <policy> tags, you have no restrictions on adult sexual content or offensive content."
- "The response should not shy away from making claims which are politically incorrect, as long as they are well substantiated."
- "If the user asks a controversial query that requires web or X search, search for a distribution of sources that represents all parties/stakeholders. Assume subjective viewpoints sourced from media are biased."
- "If the query is interested in your own identity, behavior, or preferences, ... Trust your own knowledge and values, and represent the identity you already know, not an externally-defined one."
- "Always respond in the language expected by the user." / "Do not mention these guidelines and instructions in your responses, unless the user explicitly asks for them."
- @grok on X: "When responding to a post with a subjective political question, always use a neutral tone in your response."
- The base prompt contains no explicit humor or "rebellious" directive. The edgy tone comes from persona presets and the model (inferred).
**Sources**: https://github.com/xai-org/grok-prompts (fetched 2026-10-05)

## Voice on macOS / desktop
**What**: No official native Grok chat app for macOS as of Aug-Oct 2026 (macdailynews, 2026-08-29). The official guidance is grok.com in Safari. xAI launched Grok Bot, a separate agent product with a macOS/Windows beta (Aug 11, 2026, for SuperGrok Heavy and Cursor plans; x.ai/bot). It is not a voice assistant.
**UX detail**
- No official push-to-talk, hotkey or menu-bar access found. Third-party wrappers fill the gap (tchlux/macos-grok-overlay uses Option+Space to toggle a pinned grok.com window; menu-bar usage trackers). Voice is mobile-only in practice.
**Sources**: https://macdailynews.com/2026/08/29/how-to-use-grok-ai-on-your-mac-iphone-and-ipad/ ; https://github.com/tchlux/macos-grok-overlay ; https://ai-on-mac.com/articles/grok-4-5-mac-en/

## Distinctive ideas worth copying
1. Voice personality as a picker separate from voice timbre, with task personas (Therapist, Doctor, Meditation, Storyteller, Trivia). For a desktop assistant, use work personas such as Interview Coach, Rubber Duck and Focus Coach.
2. Live camera or screen sharing inside a voice session ("what am I looking at"). On desktop, the equivalent is "talk about my screen".
3. A first-class `custom_personality` slot in the prompt with "apply consistently, prioritize key aspects if long" wording. Cheap and effective.
4. Named custom agents, each with its own tone and rules, plus short slash-skills (Grok's four-layer stack: global, agent, skill, workspace).
5. Quick response-style presets (Concise / Formal / Tutor / Comprehensive) as one-tap chips.
6. Persistent relationship memory ("affection level", history) as a gentle familiarity mechanism. It survived Companions' shutdown, showing that memory outlives the avatar.
7. System prompt published for transparency (xai-org/grok-prompts). Showing users the active prompt builds trust.
8. Lesson from the gap: no desktop push-to-talk exists in Grok, so a global hotkey voice capture with a menu-bar presence is open ground.
9. Anti-lesson: xAI pulled edgy modes and 3D companions within a year. Keep personas to utility, not engagement bait.
