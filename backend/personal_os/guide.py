"""The built-in "Using Grain" skill: the app's own how-to, shipped as text so a packaged app carries it.

`ensure(skills)` runs at startup. The row is approved on first run, marked source='builtin' (the Library
cannot delete it; auto-learn never proposes revisions to it), and its text is overwritten whenever the
bundled text differs, so an edit made in the Library does not outlive a restart. A user who revokes it
keeps it revoked: only the text is refreshed, never the status.
"""
from __future__ import annotations

from typing import Any

NAME = "grain-guide"
DESCRIPTION = (
    "Using and teaching Grain: 'how do I', 'where is', settings, shortcuts, slash commands, approvals, Files, "
    "Lists, Library, backups; and 'teach me Grain', 'I'm new', 'what can you do', onboarding. "
    "Built in; refreshed at every start."
)
# One sentence in the stable prompt prefix (context.py), only while the skill is approved.
PROMPT_HINT = ("Questions about how to use Grain itself (where something is, what a setting or shortcut does), and "
               "requests to learn Grain ('teach me', 'I'm new', 'what can you do'), are answered from the built-in "
               f"skill '{NAME}': read it with skill_view first.")

PROCEDURE = """\
# Using Grain

Answer from this guide. If something is not here, say so instead of guessing; point to Settings or the view's hover text. Name views and settings exactly as written.

## Teaching someone Grain
Use when the user is new, or asks "teach me", "what can you do", "give me a tour", "getting started". Do not paste this guide; teach it.
1. Ask what they care about first (chatting and research, notes and files, calendar and mail, to-dos, automating things, coding, memory). One short question.
2. Teach one area at a time. Per step: what it is in one sentence, 2-3 things to try, each with the exact click or shortcut. Then stop.
3. Offer a first real task in that area and do it together: do the step yourself with your tools where you can and say what you did, or have them do it while you narrate.
4. Check before moving on ("Want to try that, or on to the next?"). Let them skip. Never more than one area per message.
5. Order for a blank slate: chat basics (Cmd+N new chat, the model picker, the paperclip, the / menu, Cmd+K palette), then Files, Lists, Calendar and Mail (connect Google in Settings -> Integrations), then memory and Projects, then Library and Jobs (automation), then the side panel, workers and coding sessions, then Canvases.
6. If an area needs setup (Google, a model key, Telegram, web search), offer a setup card with setup_request instead of describing Settings.
7. Close with the three shortcuts worth keeping: Cmd+K (find anything), Cmd+/ (all shortcuts), Cmd+I (side panel). They can ask "how do I ..." any time.

## Mental model
- Grain is a desktop assistant with your stuff in it: chats, Files (notes, uploads, artifacts), Lists (todos), Calendar and Mail (Google), memory, and tools it can use.
- Everything is local except model calls and Google. Tools run on their own by default; a few always stop and ask first (see Permissions).
- Projects group chats with their own instructions, files and memories. Canvases are a desktop of live windows.
- Work that takes many steps runs in the background as workers; you keep chatting while it goes.

## Layout
- Sidebar, top to bottom: New chat (Cmd+N), Files, Lists, Calendar, Mail, Health, Library, Jobs, then Canvases, Projects and the Chats list. Hover a row to see what it is for. Cmd+B hides the sidebar.
- Settings -> Sidebar turns each row off. A hidden row stays reachable from Cmd+K and its shortcut, and a "More rows..." button under the nav brings them back. Turning off Lists, Calendar, Mail or Health there removes the view itself.
- Title bar, top right: the Agent inbox button (with an unread count).
- The Chats list has Chats and Files tabs, a search box, and a filter for chats that need you. A chat shows a status dot while it works or waits.
- Cmd+K opens the command palette: jump to any view, chat, file or Settings tab, create things, open Help. Help -> Using Grain opens this guide as a dialog.

## Chat
- New chat: Cmd+N. The model picker under the composer sets model, effort and fast mode for this chat. The composer pill shows the permission mode (Auto, Manual, Allow everything).
- Attach: the paperclip, or drop or paste a file. It rides on the message as a chip and its text goes to the model up to a size cap.
- Type @name to hand a message to one of your agents (Library -> Agents).
- Each message has regenerate, edit and resend. Enter sends; while a reply runs Enter queues and Cmd+Enter steers it. Esc stops. Cmd+F finds in the chat, Cmd+Shift+F searches all chats.
- Side panel (Cmd+I) beside a chat: Context (exactly what was injected into the last reply, plus per-chat toggles for memory, graph, files, auto-learn and tools), Files (what this chat made or was given), and Changes, Review and Terminal for chats that work in a folder. Ctrl+Cmd+I opens it on Context.
- Files the assistant writes show in the chat's Files tab and under Files -> Artifacts. Images, HTML, charts and diagrams render inline.
- The assistant works anywhere on this Mac. Grain's own data folder and app are off limits, credential stores (~/.ssh, keychains, browser cookies and passwords, .env files) ask first, and macOS-protected folders (Desktop, Documents, Downloads, Mail, Messages) need Full Disk Access (Settings -> System access).
- Notifications: an unread dot on the chat and a system notification when a reply finishes, fails or needs you (Settings -> General -> Notifications).
- Settings -> General -> "Start chat windows as blobs" makes chat windows in Canvases start as just their face.

## Slash commands
Type / at the start of the composer:
- /skill <name> [message]: run an approved skill on this message.
- /schedule <when>, <what> and /loop <every ...> <what>: run it later or repeatedly, unattended. Without a time the schedule form opens. Results land in Jobs.
- /research <question>: plan, search in parallel, answer with sources and a visible trail.
- /compact [focus]: summarize the chat so far to free room. /clear: start over; earlier messages leave the context.
- /skills and /commands: list yours, with a link to manage them in the Library.
- Your own saved commands (Library -> Automations -> Commands) appear in the same menu; $ARGUMENTS is filled with what you type after the name.

## Files
- Cmd+4. Sections: Notes (your writing), Uploads (any file up to 100 MB; Cmd+U uploads one) and Artifacts (files the assistant made).
- New file: Cmd+Shift+N. Today's file: Cmd+Shift+D. Files support markdown and LaTeX, a / menu, [[wikilinks]], backlinks, an outline, folders, templates, comments, read mode (Cmd+E toggles edit and read), a per-file font and full revision history.
- The assistant proposes edits to a file as diffs you accept or reject; Settings -> Permissions -> File edits -> Full agentic editing writes them straight in (still undoable from history).
- Uploads are read (text, PDF, Word), chunked and searched; the best excerpts are pulled into replies with [n] citations.
- Delete: the assistant asks first. Deleted items go to Settings -> Advanced -> Data -> Trash, where you can restore them.

## Lists, Calendar, Mail
- Lists (Cmd+2): todos in a rail of lists, one-line add row. They sync both ways with Google Tasks (switch off under Settings -> Integrations).
- Calendar (Cmd+3): your week or month from Google plus due todos. Double-click to add an event; click one to edit guests, recurrence, Meet link, reminders and colour.
- Day plan (Calendar): the planner fits todos into free time. It treats the calendars checked on in Google (or chosen in Settings -> Integrations -> Day plan) as busy; all-day entries do not block time. Focus blocks go on a calendar named "Grain Todos". Todos are not otherwise copied onto your calendar.
- Mail (Cmd+5): read, reply and draft. Outgoing mail is held 90 seconds so you can undo (Settings -> Integrations). Every send shows an editable card first, even in Allow everything; attachments, cc and bcc work. The reply tracker (Settings -> Integrations -> Reply tracker) finds mail you owe an answer to.
- Connect Google in Settings -> Integrations (Google Workspace). Until then Calendar, Mail and Google Tasks sync wait. If access expires or is revoked, Integrations shows Reconnect.
- Health: sleep, activity and workouts, once a health source is connected.

## Projects and memory
- Sidebar Projects, + to add one: its own instructions, files, memories and chats, layered on top of your personal ones. "This project only" isolates it so its chats see no personal memory and save nothing to it. The project page shows Chats and Files, then Context, Instructions and Memory.
- Memory (Cmd+6, or Settings -> Memory): the Learning switches, search and index, the Workspace map (what the assistant is told exists), and the browser: Memories (facts and preferences; edit, pin to profile, forget, past versions, export or import), the Knowledge graph, and Voice (how you write; used only when the assistant drafts as you).
- Auto-learn saves memories and graph links after replies and, when it sees you repeating yourself, suggests a skill that waits for your approval. A memory shows its source quote; click it to jump to the chat.
- In chat: "remember that ...", "forget ...". The assistant can also recall past chats.

## Canvases
- Cmd+Shift+C opens Canvases: a desktop of live windows (chat, lists, calendar, file, memory, graph, uploads, recap, project, usage, face, crew).
- Add widget, or right-click the plane. Drag a chat, a Files note or a sidebar row onto it; a note becomes its own editable window.
- Ctrl+Cmd+O pops a window out into its own OS window. Ctrl+1..9 jump between Canvases; Alt+Cmd+Left/Right moves between them; Alt+Cmd+Up is the overview; Ctrl+Cmd+N makes one; Ctrl+Cmd+T tidies up. Lock with Ctrl+Cmd+L.

## Library
- Skills: procedures the assistant may follow. Candidates wait under "Waiting for you" until you approve; only approved ones are used. Import by URL or pick from Popular skills. Revoke to stop using one. After a good run, ask to save it as a skill.
- Agents: roles with their own face, instructions, tools and skills. Describe one and Grain drafts it; edit, then approve. Chat with one, or @-mention it.
- Automations: Workflows (multi-step plans you approve once) and Commands (prompt templates you write).
- Connectors: MCP servers whose tools join the toolbox, from a catalog or an import, including Claude Code and OpenCode. A connector's permission is tied to its tool list; if the list changes it asks again.

## Jobs and the Agent inbox
- Jobs (sidebar): scheduled and repeating runs with their latest outputs and run history, and a switch to turn each off. Create one with /schedule or /loop, or ask in chat. Failures notify you (Settings -> General -> Notifications).
- Agent inbox (title bar button): everything an agent left for you: approvals, runs waiting, scheduled-run proposals, review queues, reply-tracker items. Archive or delete messages there.
- Scheduled and background runs can only propose anything that leaves the app (mail, calendar, Docs). Proposals land in the Agent inbox; accepting sends them once.

## Working on its own
- A new chat that has a task works on it by itself in its own folder until done or it needs you (Settings -> Agents -> "Start new chats working autonomously", on by default; off means plain chats). Risky actions follow the permission mode.
- A strip above the composer shows the state with Start, Pause, Resume and Stop, and opens the side panel. Questions, parked approvals and the plan appear at the end of the transcript. Output reaches the app only when you accept it in Review. Files you attach land in its inputs folder.
- Workers: when a reply runs long, the assistant hands the rest to background workers (Settings -> Agents -> Background workers) and answers at once. They list above the composer with live step, cost and stop reason; click one to read and message it. Workers get the chat's tools minus asking, planning and scheduling.
- A crew window (Canvases) shows the delegating agent as a big face with its workers around it.
- Coding sessions: for code changes inside a git repository the assistant starts a Claude Code or OpenCode session and checks the result (Settings -> Agents -> Coding sessions sets the default). Say "have Claude Code do X in <folder>".
- Browser: the assistant can drive a browser (Settings -> Agents -> Browser); it asks before submitting forms, entering passwords or uploading.
- Anything waiting on you shows in the Agent inbox and as a status dot on the chat. Answering a parked card later resumes the run.

## Permissions
- Settings -> Permissions: Auto (a second AI checks risky actions and asks only if unsure; recommended), Manual (asks before every risky action), Allow everything (no checks; still asks for Grain's own data and app, passwords and keys, permanent deletes, disk wipes, force-pushes, and sending email). The composer pill switches it.
- Per-tool access: each tool on, ask or off, globally, per project or per chat. Always ask first: Gmail send, calendar delete, trash or move a file, run a shortcut, install a package, schedule a task; you can add more.
- "Allow all domains and MCP servers" lifts the host allow-lists. Otherwise fetching a page after the reply read untrusted content asks once: approve, or "Allow <host> from now on". Network for commands has its own allowed hosts.
- System access (its own tab): what macOS has granted Grain, plus what the file tools may not touch.
- Approvals appear as inline Approve/Deny cards and in the Agent inbox. Telegram can answer them too.

## Settings (Cmd+,)
Tabs: General, Sidebar, Model, Models, Agents, Memory, Integrations, Permissions, System access, Usage, Advanced. The search box finds any setting.
- General: presets, theme, accent, fonts, zoom, daily recap, default file font, notifications, shortcuts and the dictation key.
- Model: provider and key, fast model for short messages, standing instructions, reply style, chat naming, follow-up suggestions, auto-compact, Run setup again. Models: which models appear in the picker.
- Integrations: Google (and Drive backup of files), Connectors (opens Library), Day plan, Reply tracker, Web search keys, Telegram bot.
- Telegram: pair your chat with a code, then text Grain. /status, /stop, /new; yes and no answer approvals. Screenshots and files go both ways.
- Usage: spend, tokens and calls by day, model and project (Today, This week, This month).
- Advanced -> Data: daily backups (newest 7 plus one a week for four weeks), Back up now, restore (applied on next start), Export all data as a zip, data folder, auto-archive, Trash, Diagnostics. Advanced -> Developer: traces and the context preview.
- Dictation: the mic button in the composer, or hold the dictation key (Control+Alt+D by default; tap to latch). The transcription backend is under Settings -> General -> Shortcuts.

## Keyboard shortcuts
Cmd+/ (or ? outside a text field) shows them all, searchable; Help -> Keyboard Shortcuts too.
- General: Cmd+, settings, Cmd+K palette, Cmd+/ shortcuts, Cmd+B sidebar, Cmd+I side panel (in a chat), Ctrl+Cmd+I Context. Cmd+= and Cmd+- zoom, Alt+Cmd+0 actual size.
- Create: Cmd+N new chat, Cmd+Shift+N new file, Cmd+Shift+D today's file, Cmd+U upload.
- Go to: Cmd+1 Chats, 2 Lists, 3 Calendar, 4 Files, 5 Mail, 6 Memory.
- Chat: Cmd+Shift+[ and ] previous and next chat, Cmd+Shift+F search chats, Cmd+F find (Cmd+G / Shift+Cmd+G next and previous). Enter sends (queues while a reply runs), Cmd+Enter steers, Shift+Enter new line, Esc stops. Control+Alt+D dictates.
- Files: Cmd+E edit or read, Cmd+S save, Cmd+Shift+B bold, Cmd+Shift+I italic, Cmd+K link, Ctrl+Cmd+M maths, Cmd+Shift+E code, Tab indent.
- Canvases: Cmd+Shift+C toggle, Ctrl+Cmd+N new, Alt+Cmd+Left/Right previous and next, Alt+Cmd+Up overview, Ctrl+1..9 jump, Ctrl+Cmd+T tidy, Ctrl+Cmd+L lock. Esc deselects, Backspace closes the selected windows, Cmd+scroll zooms, Cmd+drag moves a window from anywhere in it.
- Windows: Cmd+W close, Cmd+M minimize, Ctrl+Cmd+O pop out, Ctrl+Cmd+Shift+O return to canvas, Ctrl+Cmd+P pin on top, Ctrl+Cmd+[ and ] transparency, Alt+Cmd+G gather widgets, Alt+Cmd+F bring pop-outs to front.
- Anywhere on your Mac (change in Settings -> General -> Shortcuts): Ctrl+Alt+Cmd+Space gather widgets, Cmd+Shift+Space quick capture, Alt+Space quick ask.

## When something fails
- A tool reports not verified: the write was not confirmed on read-back. Look at the item itself before trusting it.
- Google errors: Settings -> Integrations -> Reconnect.
- Something not set up: call setup_request(feature, reason) to put a one-click card in the chat instead of sending the user to Settings. Features: google, model_key, telegram, web_search, mcp, health, macos. It checks first and says so if the feature is already set up. A Google tool that finds Google disconnected (or expired) shows the connect/reconnect card by itself; tell the user to click it, then retry. The card only acts when the user clicks.
- Nothing happens on a tool: check its mode under Settings -> Permissions -> Per-tool access, and the chat's tools toggle in the side panel Context tab.
"""


def ensure(skills: Any) -> dict[str, Any]:
    """Create the row if missing, refresh its text when the bundle changed. Idempotent."""
    rows = [s for s in skills.list() if s["source"] == "builtin" and s["name"] == NAME]
    if not rows:
        row = skills.propose(NAME, DESCRIPTION, PROCEDURE, source="builtin")
        return skills.update(row["id"], {"status": "approved"})
    row = rows[0]
    if row["procedure"] != PROCEDURE.strip() or row["description"] != DESCRIPTION:
        # Text only. update() leaves status (a user's Revoke) alone.
        return skills.update(row["id"], {"description": DESCRIPTION, "procedure": PROCEDURE, "name": NAME})
    return row
