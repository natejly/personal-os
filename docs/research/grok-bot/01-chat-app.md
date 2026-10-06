# Grok consumer chat app: core features (as of October 2026)

Source quality note: x.ai/news and x.ai/changelog returned 403 to the fetcher and docs.x.ai covers the API and Grok Build, not the consumer app. Almost everything below comes from secondary press and explainer sites (Engadget, TestingCatalog, Maginative, aggregator blogs). Primary-source gaps are marked (unverified). Dates are the article or event dates when known.

## Response modes (Auto / Fast / Expert / Heavy)

**What** The mode picker has four entries. Auto routes each query to Fast or Expert based on perceived complexity and is the default. Fast optimizes for latency and is meant for quick facts and summaries. Expert thinks longer, with step-by-step reasoning, for coding, document analysis and math. Heavy runs a team of agents behind the scenes on one problem (the Grok 4 Heavy multi-agent model).

**UX detail**
- The mode is chosen from a selector in the composer (exact widget placement unverified).
- "Think" and "Big Brain" were the 2025 labels. Expert is the 2026 successor, and it has no official xAI doc even though it is in the UI (per Suprmind).
- Auto does not explain its routing beyond the answer (unverified).
- Heavy takes visibly longer and returns one consolidated answer. The internal agents are not exposed.

**Tier/limits**
- Heavy requires SuperGrok Heavy, $300/month.
- Plans (per grok.com/plans, September 2026): SuperGrok Lite $10, SuperGrok $30, SuperGrok Plus $100, Heavy $300.
- Since June 2026, paid plans share one weekly usage pool across Chat, Imagine, Voice and Build. No quota numbers are published.
- Models seen in 2026: Grok 4.3, 4.5 (July 8), 4.6 (August 12). Consumer routing among them is unclear (unverified).

**Sources**
- https://mundobytes.com/en/What-are-the-heavy--expert--fast--and-auto-modes-in-Grok-used-for/ (2025-26)
- https://suprmind.ai/hub/grok/grok-features/ (2026)
- https://www.morphllm.com/comparisons/chatgpt-vs-grok (Sept 2026)
- https://www.ai-toolbox.co/grok-models/grok-pricing-plans-api-2026

## DeepSearch / DeeperSearch

**What** DeepSearch is an iterative research agent. It splits the query into sub-queries, searches the web and X in parallel, reads pages, summarizes into a scratchpad, and repeats up to about 10 steps or a time limit. DeeperSearch was the extended variant. Per one aggregator, the separate labels were folded into the modes around mid-2025, while Suprmind still lists both. Treat the current UI naming as unverified.

**UX detail**
- On launch (Grok 3, 2025) the UI showed a progress list on the left and a "Thoughts" panel on the right.
- Citations appear as links to the pages used.
- It could be triggered by the toggle or by prefixing "Use DeepSearch:".
- Citation accuracy is weak: the Columbia Journalism Review test found 94% wrong or hallucinated citations for Grok 3.

**Tier/limits** Free users got it with a daily cap. Current caps are unpublished (unverified).

**Sources**
- https://grokipedia.com/page/Grok_DeepSearch
- https://thedayafterai.squarespace.com/ai-academy/examining-grok-3s-deepsearch-and-think-features
- https://promptwatch.com/bots/grok-deepsearch

## Projects / Workspaces

**What** A Project (launched as "Workspaces" in April 2025, now "Projects") is a named container holding chats, uploaded files and custom instructions for one topic. Its contents are isolated from other projects.

**UX detail**
- Open the Projects section, choose New Project, name it, write Instructions, and upload files.
- New chats started inside a project are filed in it and inherit its instructions and files.
- The files are reference documents for the project's chats only.
- Memory is global, not per project (see Conversation features).

**Tier/limits** Available on grok.com. Per-project file caps are unpublished (unverified). A secondary source says custom instructions are 12,000 characters, cut to 4,000 in March 2026 and restored in April 2026 (unverified).

**Sources**
- https://www.maginative.com/article/xai-upgrades-grok-with-personalized-memory-and-custom-workspaces/ (Apr 2025)
- https://www.testingcatalog.com/workspace-folders-coming-to-grok-as-xai-builds-better-tools/
- https://www.eonmsk.com/2025/04/17/xai-grok-workspaces-are-now-available-widely/
- https://www.uniflow.kr/en/grok-custom-instructions-setup-guide-workspaces-agents/ (2026)

## Grok Studio (canvas)

**What** Studio is a side panel that opens beside the chat when Grok generates a document, code, report or browser game. Launched 2025-04-16 and available to free and paid users.

**UX detail**
- No activation step: the panel opens automatically when the output fits.
- Documents get basic rich-text editing (bold, italic, underline, headings, lists).
- Code runs and previews in place for HTML, Python, C++, JavaScript, TypeScript and bash.
- Google Drive attach lets you pull in Docs, Sheets and Slides, for example "build a report with charts from this spreadsheet".
- Edits are made together with Grok in the same pane.

**Tier/limits** Free and premium. No caps found.

**Sources**
- https://www.engadget.com/ai/xais-grok-launches-studio-interface-for-documents-and-code-123016714.html (Apr 2025)
- https://thetechportal.com/2025/04/16/xais-grok-rolls-out-grok-studio-a-canvas-like-feature/
- https://grokipedia.com/page/Grok_Studio

## Files and attachments

**What** Chat accepts documents, code, images, audio and video. Documents are read as text plus key page visuals.

**UX detail**
- Formats: PDF, DOCX, TXT, CSV, XLSX, PPTX, HTML, XML, JSON, Markdown, LaTeX, code files. Images: JPEG, PNG, WebP, HEIC, BMP. Audio: MP3, WAV, M4A, OGG, FLAC, AAC. Video: MP4, MOV.
- For PDFs over about 100 pages, xAI suggests naming page numbers in the question. Large documents may be processed in sections.
- Google Drive files can be attached through the Studio integration.

**Tier/limits**
- Web: about 100 files per message, 150 MB each. Android: up to 20 files per message. iOS: multiple files, 150 MB each.
- API Files limit is 512 MB. That is not a consumer limit.
- Per-file caps are from a secondary source (unverified).

**Sources**
- https://www.datastudios.org/post/grok-file-upload-limits-and-supported-formats-explained (2026)
- https://fast.io/resources/grok-file-upload-limit/

## Grok Imagine

**What** Imagine generates images and short videos, and edits both. Imagine 1.0 shipped Feb 1-2, 2026, with 10 s clips at 720p and native audio (music, dialogue, effects). It supports up to 7 reference images for character consistency and natural-language video editing (restyle, object changes, motion control). It is available as a mobile app (from August 2025), on grok.com, and inside chat.

**UX detail**
- Invoked from chat by asking for an image or video, or through the dedicated Imagine tab.
- Image-to-video: pick an image and animate it.
- Styled templates were added in March 2026 (for example "Chibi").
- Spicy mode is a toggle on video. It permits suggestive content for fictional adults only. Image-to-video with outside images does not support it, and editing real people's photos was disabled on 2026-01-15. Musk stated "R-rated" standards on 2026-03-12.
- Moderation was tightened with false positives, and xAI acknowledged this on 2026-03-27.
- Image understanding (vision) is part of normal chat: attach a picture and ask.

**Tier/limits**
- Free Imagine ended 2026-03-19.
- SuperGrok Lite: 480p, about 6 s clips.
- SuperGrok: 10 s clips, extendable to 30 s.
- Usage draws from the shared weekly pool (since June 2026).

**Sources**
- https://grokipedia.com/page/Grok_Imagine (March 2026)
- https://en.wikipedia.org/wiki/Grok_(chatbot)
- https://aiinsightsnews.net/grok-spicy-mode/
- https://www.tomsguide.com/ai/grok-launches-ai-image-generator-with-a-nsfw-spicy-mode-its-exactly-what-youd-expect (Aug 2025)

## Grok Tasks

**What** Tasks are scheduled prompts that run on a recurrence and deliver results by notification. Launched in 2025 (the exact date is unclear). Roughly: a standard scheduled query uses the knowledge base plus live X data, and an optional advanced task triggers the reasoning engine.

**UX detail**
- Open grok.com/tasks or the profile menu, then Create Task.
- Pick a frequency: once, daily, selected weekdays, weekly, monthly or yearly, with a time and date.
- Starter templates include "Daily Productivity Boost" and "Weekly News Digest".
- You can run a test first. Output lands in the Chat section.
- Delivery is by email (to the X account address) and push notification in the mobile app, each toggled independently.
- You can edit a task's prompt at any time.

**Tier/limits**
- Free: 2 daily tasks and up to 10 weekly or monthly tasks.
- SuperGrok or Premium+: unlimited (per the guides).
- One source claims 20 standard and 5 advanced tasks per day, and a 3-minute stop for advanced tasks (unverified).

**Sources**
- https://www.testingcatalog.com/grok-set-to-gain-tasks-feature-for-periodical-execution/
- https://app.therundown.ai/guides/how-to-use-grok-for-free-automated-research
- https://grokaimodel.com/tasks/ (secondary)

## Conversation features (history, share, edit, regenerate, private chat, memory, instructions)

**What** These are standard chat-app controls with a few distinctive ones.

**UX detail**
- Share: any thread can be shared by public link. In August 2025, shared links were indexed by Google Search, exposing thousands of chats.
- Regenerate keeps earlier variants. Grok on the web lets you go back to previous responses after regenerating (TestingCatalog, Dec 2024).
- On failure, the error reads "try again or use a different model", so switching model on retry is possible (exact UI unverified).
- Private Chat: tap the ghost icon at the top right. The chat is not saved to history and is deleted from xAI systems within 30 days. Whether it also bypasses memory is unverified.
- Memory (since April 2025): selective, rolls out unevenly across web, iOS and Android. You can ask Grok to forget something, view and delete memories, or reset all.
- Custom instructions are in Settings and apply only to new conversations. Persona/"Customize Grok" options exist (unverified: some sources claim four named personas).
- Search history: sidebar search over past chats (unverified detail).

**Tier/limits** Free and paid. Memory is not available in the API.

**Sources**
- https://www.ai-toolbox.co/grok-management-and-productivity/does-grok-remember-past-conversations-2026
- https://x.com/techdevnotes/status/1871626677387972889
- https://www.yahoo.com/news/articles/thousands-private-user-conversations-elon-173216941.html
- https://blog.memoryplugin.com/how-grok-memory-works/

## Distinctive surfaces: Grok Bot and macOS

**What**
- There is no official native Grok chat app for macOS (as of August-September 2026). Mac users use grok.com, the iOS app via iPhone Mirroring, or third-party wrappers.
- Grok Bot launched 2026-08-11 in early beta for macOS, Windows and iOS. Each bot has a persistent cloud computer, signs into your apps, clicks through the UI, runs files and the terminal, and reports back for approval.
- The Mac app is separate from the chat app. Version 0.66.0 landed 2026-10-02.

**UX detail**
- "Watch me once" records up to 10 minutes of browser actions and saves them as a reusable routine.
- Routines run on a schedule or on events, including while the laptop is closed.
- Limits: 50 routines per bot, 50 bots per account, group chats of 2-6 bots, one shared cloud computer per account.
- Teams: bots can message each other asynchronously.
- v0.66.0: your phone skips notifications while you are viewing the same Bot on your computer. Select text in a chat and press Cmd/Ctrl+L to quote it in your next message.
- Approvals are advisory: they cannot undo completed work, and there is no dry-run.

**Tier/limits** SuperGrok Heavy, or a Cursor Ultra/Teams plan. Token limits are unpublished.

**Sources**
- https://www.eesel.ai/blog/grok-bot
- https://www.digitalapplied.com/blog/grok-bot-ai-teammates-launch-cloud-computer-2026
- https://macdailynews.com/2026/08/29/how-to-use-grok-ai-on-your-mac-iphone-and-ipad/
- https://x.ai/changelog/bot (403 to fetcher; cited via search snippet, 2026-10-02)

## Not found

"Ask Grok" popovers (on X) and keyboard shortcuts in the chat app: I found nothing reliable on the consumer chat surface (unverified).

## Distinctive ideas worth copying

1. Test-run a scheduled task before saving it, with results landing in a normal chat (Tasks).
2. Record-once routines: "watch me once" captures actions and saves them as reusable, schedulable routines (Grok Bot).
3. Auto mode that routes between a fast and a thinking model, so users do not pick a model each time.
4. Studio panel that opens by itself when output is a document or runnable code, with in-place run/preview.
5. Projects as isolated containers: instructions, files and chats, with no cross-contamination.
6. Private chat as one ghost-icon toggle (history off).
7. Delivery of scheduled results to push and email, each toggled independently.
8. Regenerate that keeps earlier variants reachable.
9. Quote-selection-into-next-message shortcut (Cmd+L) from the Mac Bot app.
10. Suppress phone notifications while the user is active at the computer, resume on lock or idle.
