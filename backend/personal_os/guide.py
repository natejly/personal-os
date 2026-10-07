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
    "Using Grain: how to do things in this app. Use for 'how do I', 'where is', 'what does X do in Grain', "
    "settings, shortcuts, slash commands, approvals, Spaces, Lists, Files, Library, backups. "
    "Built in; refreshed at every start, so edits here do not last."
)
# One sentence in the stable prompt prefix (context.py), only while the skill is approved.
PROMPT_HINT = ("Questions about how to use Grain itself (where something is, what a setting or shortcut does) are "
               f"answered from the built-in skill '{NAME}': read it with skill_view first.")

PROCEDURE = """\
# Using Grain

Answer from this guide. If something is not here, say so instead of guessing; point to Settings or the view's hover text. Name views and settings exactly as written.

## Mental model
- Grain is a desktop assistant with your stuff in it: chats, Files (notes and uploads), Lists (todos), Calendar and Mail (Google), memory, and tools it can use.
- Everything is local except model calls and Google. Tools run on their own by default; a few always stop and ask first.
- Projects group chats with their own instructions, files and memories. Spaces are a desktop of live windows.

## Views and the title bar
- Sidebar: New chat, Today, Files, then Spaces, Projects and Recents. Library is a sidebar row too.
- Title bar, top right of every view: Lists, Calendar, Mail and Health. Hover any of them to see what it is for.
- Settings -> Modules moves each view to the sidebar, the title bar, or hides it (nav placement). If a view seems missing, look there.
- Today: the day's recap, calendar, unread mail, todos, and the Agent inbox (everything an agent left for you: approvals, desks waiting, scheduled-run proposals, review queues).

## Chat
- New chat: Cmd+N. Model and effort pickers sit under the composer.
- Attach: drop or paste a file. It rides on the message as a chip, and its text goes to the model up to a size cap.
- Files: the assistant's file and shell tools work anywhere on this Mac. Grain's own data folder and app are off limits, credential stores (~/.ssh, keychains, browser cookies and passwords, .env files) ask first, and macOS-protected folders (Desktop, Documents, Downloads, Mail, Messages) need Full Disk Access in System Settings.
- Per-chat context toggles (memory, graph, files, auto-learn, tools) are in the Context panel (Ctrl+Cmd+I). It also shows exactly what was injected into the last reply.
- Regenerate, stop, edit and resend are on each message. Cmd+F finds in the chat, Cmd+Shift+F searches all chats.
- A face by each chat blinks while it works. Compact chats in Settings folds chat windows in Spaces to the face and one line.

## Slash commands
Type / in the composer:
- /skill <name> [message]: run an approved skill on this message.
- /schedule: book a run for later. /loop: repeat something on an interval.
- /research <question>: plan, search in parallel, and answer with sources and a visible trail.
- /compact: summarize the chat so far to free room.
- /skills and /commands: list what you have.
- Your own saved commands (Library -> Automations) appear in the same menu.

## Files
- Cmd+4. Two sections: Notes (your own writing) and Uploads (any file up to 50 MB; Cmd+U uploads one).
- New note: Cmd+Shift+N. Today's note: Cmd+Shift+D. Notes support markdown and LaTeX, / menu, [[wikilinks]], backlinks, an outline, folders per project, templates and full revision history.
- Each note has its own chat in the Page agent panel (Cmd+I). The assistant proposes edits as diffs you accept or reject; Settings -> Tools -> File edits -> Accept all writes them straight in (still undoable from history).
- Delete: the assistant asks first. Deleted notes go to Settings -> Trash, where you can restore them.
- Uploads are read (text, PDF, Word), chunked and searched; the best excerpts are pulled into replies with [n] citations.

## Lists, Calendar, Mail, Tasks
- Lists (Cmd+2): todos in a rail of lists, with a one-line add row.
- Calendar (Cmd+3): your week from Google plus due todos. Double-click to add an event; click one to edit guests, recurrence, Meet link, reminders and colour.
- Mail (Cmd+5): read, reply and draft. Sends have a 90 second undo.
- Connect Google in Settings -> Integrations. Until then Calendar, Mail and the first-prompt suggestions wait. If access expires or is revoked, Integrations shows Reconnect.
- Once connected, todos sync both ways with Google Tasks (switch it off under Integrations). Todos are not copied onto your calendar; the planner's Focus blocks go on a calendar named "Grain Todos".

## Projects and memory
- Sidebar Projects, + to add one: its own instructions, knowledge files and memories, layered on top of your personal ones. A project can be isolated so its chats see no personal memory.
- Memory (Settings -> Memory, Cmd+6): Memories (facts and preferences; edit, pin, forget, see past versions, export or import), the Knowledge graph, and Voice (how you write; used only when the assistant drafts as you).
- Auto-learn saves memories and graph links after replies. When it sees you repeating yourself it suggests a skill, which waits for your approval.

## Spaces
- Cmd+Shift+C opens Spaces: a desktop of live windows (chat, lists, calendar, doc, memory, graph, uploads, recap, project, usage, face, crew).
- Add widget, or right-click the plane. Drag a chat, a Files note or a sidebar row onto it; a note becomes its own editable window.
- Ctrl+Cmd+O pops a window out into its own OS window. Ctrl+1..9 jump between Spaces; Alt+Cmd+Left/Right moves between them; Alt+Cmd+Up is the overview; Ctrl+Cmd+N makes a Space; Ctrl+Cmd+T tidies up.
- Lock a Space with Ctrl+Cmd+L so nothing can be rearranged. Save a layout as a preset from the Spaces bar.

## Library
- Skills: procedures the assistant may follow. Candidates wait under "Waiting for you" until you approve; only approved ones are used. Import by URL or pick from Popular skills. Revoke to stop using one.
- Agents: roles with their own face, instructions, tools and skills. Describe one and Grain drafts it; edit, then approve.
- Automations: workflows (multi-step jobs you approve once) and commands (prompt templates you write).
- Connectors: MCP servers whose tools join the toolbox. A connector's permission is tied to its tool list; if the list changes it asks again.

## Autonomy: working on its own
- A chat can do several steps by itself. It stops for you at approval cards, plans and questions.
- Plan first (Mode button): the assistant writes a plan first, you approve it once, and its steps run without a card each. Changing the plan needs approval again.
- Approvals: an inline Approve/Deny card. A card nobody answers waits; answering later resumes the run.
- Needs you: anything waiting on you shows in the Agent inbox on Today, with a count on the sidebar Today row.
- Scheduled and background runs can only propose anything that leaves the app (mail, calendar, Docs). Proposals land in the Agent inbox; accepting sends them once.
- Work autonomously (Mode button in any chat): the chat keeps working on its task in its own folder until it is done or needs you. Pick Plan first (you approve a plan once), Ask as it goes (no plan up front; risky actions follow the permission mode) or Work and propose (it only proposes anything external).
- A strip above the composer shows the state, turns used and what needs you, with Start, Pause, Resume and Stop; it opens a side panel with Files, Changes and Review. Questions, parked cards and the plan appear at the end of the transcript. Output reaches the app only when you accept it in Review. Files you attach land in its inputs folder.
- Autonomous chats list with other chats, with a status dot; the Chats header counts what needs you. Turn the control off to stop; the chat answers normally again. Caps live in Settings -> Autonomy.

## Subagents and crews
- A reply can delegate to subagents. They show as indented rows under that reply with live status; click one to open and message it.
- A crew window (in Spaces) shows the delegating agent as a big face with its subagents around it.
- Subagents get the chat's tools minus asking, planning and scheduling.

## Settings people touch (Cmd+,)
- Tools: each tool is on, ask or off, set globally, per project or per chat. Always-ask, whatever the mode: Gmail send, calendar delete, trash or move a file, run a shortcut, install a Python package, schedule a task.
- Allowed hosts (under Tools): fetching a page after the reply read untrusted content asks once. Approve the card, or click "Allow <host> from now on".
- System access (under Permissions): what macOS has granted Grain, plus what the file tools may not touch and what asks first.
- Autonomous (on by default): a new chat runs as a task that works through its steps, and a quick question is simply answered.
- Data: daily backups (newest 7 plus one a week for four weeks), Back up now, restore (applied on next start), Export all data as a zip.
- Modules: which views appear and where. Voice and Memory live under Memory. Usage shows spend, tokens and calls. Integrations holds Google and connectors. Compact chats and Trash are here too.

## Keyboard shortcuts
Cmd+/ (or ? outside a text field) shows them all, searchable; Help -> Keyboard Shortcuts in the menu bar too.
- General: Cmd+, settings, Cmd+K command palette, Cmd+/ shortcuts, Cmd+B sidebar, Cmd+I Page agent, Ctrl+Cmd+I Context panel. Cmd+= and Cmd+- zoom, Alt+Cmd+0 actual size.
- Create: Cmd+N new chat, Cmd+Shift+N new file, Cmd+Shift+D today's file, Cmd+U upload.
- Go to: Cmd+0 Today, 1 Chats, 2 Lists, 3 Calendar, 4 Files, 5 Mail, 6 Memory.
- Chat: Cmd+Shift+[ and ] previous and next chat, Cmd+Shift+F search chats, Cmd+F find (Cmd+G / Shift+Cmd+G next and previous). Enter sends (queues while a reply runs), Cmd+Enter steers the running reply, Shift+Enter new line, Esc stops the reply. The mic button dictates into the message; the dictation chord (hold to talk, tap to latch) and the transcription backend are in Settings -> Behavior -> Voice input.
- Files (note editor): Cmd+S save, Cmd+Shift+B bold, Cmd+Shift+I italic, Cmd+K link, Ctrl+Cmd+M maths, Cmd+Shift+E code, Tab indent.
- Spaces: Cmd+Shift+C toggle Spaces, Ctrl+Cmd+N new Space, Alt+Cmd+Left/Right previous and next, Alt+Cmd+Up overview, Ctrl+1..9 jump, Ctrl+Cmd+T tidy up, Ctrl+Cmd+L lock. Esc deselects, Delete closes the selected windows, Cmd+scroll zooms, Cmd+drag moves a window from anywhere in it.
- Windows: Cmd+W close, Cmd+M minimize, Ctrl+Cmd+O pop out, Ctrl+Cmd+Shift+O return to Space, Ctrl+Cmd+P pin on top, Ctrl+Cmd+[ and ] transparency, Alt+Cmd+G gather widgets, Alt+Cmd+F bring pop-outs to front.
- Anywhere on your Mac (change them in Settings -> Behavior -> Shortcuts -> Advanced): Ctrl+Alt+Cmd+Space gather widgets, Cmd+Shift+Space quick capture, Alt+Space quick ask.

## When something fails
- A tool reports not verified: the write was not confirmed on read-back. Look at the item itself before trusting it.
- Google errors: Settings -> Integrations -> Reconnect.
- Nothing happens on a tool: check its mode under Settings -> Tools, and the chat's tools toggle.
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
