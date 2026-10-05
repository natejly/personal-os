# Grain reference

Generated from `GET /tools` on a fresh backend (147 tools). `default` is the mode before any Settings, project or chat override; `locked` marks a tool in the `alwaysAsk` setting (external/schedules danger only), whose mode tops out at `ask`. A tool also needs its backing service (Google connected, sandbox runtime, desktop bridge, activity monitor on) to be offered at all.

Danger tiers (`backend/personal_os/tools.py`): `safe` read-only in-app, `writes` in-app write, `network` reads the internet, `executes` sandboxed code, `external` writes to systems outside the app, `plan` the call is itself an approval card, `schedules` books future unattended work. Default mode is `on` for every tier except `plan` (`ask`); a few tools override it (shown below).

Always forced to ask in a reply that has read untrusted content (tainted): network tools, locked tools, and the lasting-text writes in `PROMPT_WRITES` (save_memory, graph_add, doc_create/edit/delete, todo_add/update/delete, skill_draft/revise/from_run, health_log, convert_document and a few more).

## Tools by group

### activity

| tool | danger | default | what it does |
|---|---|---|---|
| `activity_recent` | safe | on | What the user has actually been doing on their computer recently, from the local activity monitor: a live l... |
| `activity_access` | safe | on | Which macOS permissions the activity monitor currently has (Accessibility, Input Monitoring, Screen Recordi... |
| `activity_insights` | safe | on | The habits the activity monitor has noticed about how this person works, the patterns behind them, and the... |
| `activity_report` | safe | on | Where the user's focused computer time went by category (Work/Coding, Comms, Social/Media...) over the last... |
| `activity_pause` | external | on | Pause the activity monitor for a while, so nothing about the user's screen, typing or audio is recorded. |

### agents

| tool | danger | default | what it does |
|---|---|---|---|
| `agent_spawn` | executes | on | Hand a self-contained task to a subagent that works with its own context and returns a report. |
| `agent_wait` | safe | on | Wait for background subagents (all of yours that have not been collected, or the ids given) and return thei... |
| `agent_stop` | safe | on | Cancel a subagent and everything it started. |
| `desk_start` | plan | ask | Start a new desk: a separate autonomous work session with its own conversation and workspace that plans fir... |

### browser

| tool | danger | default | what it does |
|---|---|---|---|
| `browser_open` | network | on | Open a web page in your own browser (a real page with JavaScript; its own cookies, separate from the user's... |
| `browser_snapshot` | network | on | Read the current page again as a text snapshot with element refs. |
| `browser_scroll` | network | on | Scroll the page (or the element ref) by a number of screens. |
| `browser_click` | executes | on | Click the element with this ref from the last snapshot. |
| `browser_type` | executes | on | Type text into the input with this ref (clearing it first unless clear is false). |
| `browser_select` | executes | on | Choose option(s) in the dropdown with this ref, by label or value. |
| `browser_press` | executes | on | Press a key (Enter, Tab, Escape, ArrowDown, Control+a ...), on the element ref or the page. |
| `browser_manage` | network | on | Everything around the page itself. |

### code

| tool | danger | default | what it does |
|---|---|---|---|
| `run_python` | executes | on | Run a Python 3 script in an isolated sandbox and return stdout/stderr. |
| `python_install` | external | ask (locked) | Install Python packages into the shared work environment that run_python uses (wheels only, from the packag... |

### context

| tool | danger | default | what it does |
|---|---|---|---|
| `read_tool_result` | safe | on | Read part of a large tool result that was stored instead of put in your context. |
| `search_tool_results` | safe | on | Keyword search over the large tool results already stored in this chat. |

### deliver

| tool | danger | default | what it does |
|---|---|---|---|
| `convert_document` | writes | on | Convert a document from one format to another: markdown/html/docx/odt/rst/txt/epub between each other (pand... |
| `render_preview` | writes | on | Render pages of a PDF or office file (docx/xlsx/pptx/odt/ods/odp) to PNG images so you can look at them wit... |
| `doc_guide` | safe | on | A practical guide to producing one kind of deliverable with the Python libraries available to a desk: which... |

### desk

| tool | danger | default | what it does |
|---|---|---|---|
| `desk_list_files` | safe | on | List the files in this desk's workspace: outputs/ (the only place a deliverable can live), work/ (your scra... |
| `desk_read_file` | safe | on | Read a file from this desk's workspace. |
| `desk_write_file` | writes | on | Write a text file in this desk's workspace. |
| `desk_trash_file` | writes | on | Move a file in this desk's workspace to .trash/. |
| `desk_deliver` | writes | on | Nominate a file under outputs/ as a deliverable. |
| `desk_ask` | plan | ask | Ask the user one question and stop. |
| `desk_done` | safe | on | Declare this desk finished and say what you did. |
| `desk_import_sandbox` | writes | on | Copy a text file out of this chat's sandbox into the desk's workspace, overwriting the destination. |
| `desk_fetch_file` | network | on | Download a file from a public http(s) URL into this desk's workspace (default work/downloads/<name>), at mo... |

### docs

| tool | danger | default | what it does |
|---|---|---|---|
| `doc_list` | safe | on | List the files the user writes in the app's Files view (its markdown editor) — their notes, drafts and docu... |
| `doc_search` | safe | on | Full-text search across the bodies of the user's editor files, returning a snippet per hit. |
| `doc_read` | safe | on | Read an editor file's markdown with line numbers; the first page also carries linked_from, the titles of fi... |
| `doc_create` | writes | on | Create a new file for the user in Files, optionally with a starting markdown body. |
| `doc_edit` | writes | on | Revise one of the user's editor files. |
| `doc_delete` | writes | ask | Delete a file from Files by id or title. |

### files

| tool | danger | default | what it does |
|---|---|---|---|
| `find_files` | safe | on | Spotlight search of the user's files on this Mac (default scope: ~/Desktop and ~/Documents). |
| `read_local_file` | safe | on | Read the text of a file on this Mac (text, markdown, code, PDF or .docx), or list a folder. |
| `write_local_file` | external | on | Write a text file on this Mac (notes, markdown, CSV, code). |
| `move_local_file` | external | ask (locked) | Move or rename a file or folder on this Mac. |
| `trash_local_file` | external | ask (locked) | Move a file or folder on this Mac to the Trash. |
| `fs_glob` | safe | on | List files and folders under a root that match a glob (`**` crosses folders; a pattern with no slash matche... |
| `fs_grep` | safe | on | Search file contents under a root with a regular expression. |
| `fs_edit` | writes | on | Change a file by replacing exact text: old must match once (or pass replace_all). |
| `fs_copy` | writes | on | Copy a file or folder to a new path. |
| `fs_mkdir` | writes | on | Create a folder, with any missing parents. |

### google

| tool | danger | default | what it does |
|---|---|---|---|
| `mail_followups` | safe | on | Email threads waiting on someone: 'awaiting_reply' = the user sent last and is still waiting on a reply; 't... |
| `schedule_suggest` | safe | on | Suggest calendar time blocks for the user's open todos (uses their estimates, due dates, priorities, work h... |
| `calendar_events` | safe | on | List Google Calendar events (default: the next 2 days on the primary calendar). |
| `calendar_get` | safe | on | Full details of one event by id (from calendar_events): recurrence, reminders, guests and their RSVPs, colo... |
| `meeting_brief` | safe | on | Pre-meeting brief for one calendar event: for each guest, what long-term memory holds about them and the la... |
| `calendar_create` | external | on | Create ONE Google Calendar event. |
| `calendar_update` | external | on | Edit ONE Google Calendar event by id; for moving several events or rescheduling around conflicts use calend... |
| `calendar_delete` | external | ask (locked) | Delete a Google Calendar event by id. |
| `calendar_respond` | external | on | RSVP to an event the user was invited to: accepted, declined or tentative. |
| `calendar_free_busy` | safe | on | Busy ranges between two ISO datetimes from Google's free/busy service, across all the user's visible calend... |
| `calendar_find_time` | safe | on | Find free slots for a meeting: ranked candidates inside working hours (default 9-18 local, weekdays) that a... |
| `calendar_propose` | external | ask (locked) | Propose a batch of calendar changes the user reviews as a whole on a calendar view, can edit or switch off... |
| `gmail_search` | safe | on | Search Gmail with Gmail query syntax (e.g. |
| `gmail_read` | safe | on | Read the full body of an email by id (from gmail_search). |
| `gmail_draft` | external | on | Create a Gmail draft (never sends). |
| `propose_times_draft` | external | on | Find free meeting slots (calendar_find_time rules: 9-18 local weekdays unless working_hours is given) and w... |
| `gmail_send` | external | ask (locked) | Queue an email to send from the user's Gmail. |
| `gmail_outbox` | writes | on | The emails waiting out their undo hold before Gmail sends them: list them, or cancel one so it never goes out. |
| `gmail_modify` | external | on | Mark an email read/unread, star it, or archive it. |
| `google_tasks_list` | safe | on | List the user's Google Tasks (default list). |
| `google_tasks_add` | external | on | Add a task to Google Tasks (due as YYYY-MM-DD). |
| `google_tasks_complete` | external | on | Mark a Google Task complete. |
| `google_drive_search` | safe | on | Search the user's Google Drive by file name and content. |
| `google_drive_read` | safe | on | Read a Drive file's text by id (from google_drive_search). |
| `google_docs_search` | safe | on | Find Google Docs and Sheets in the user's Drive by name (newest first). |
| `google_docs_read` | safe | on | Read a Google Doc's text by id (from google_docs_search). |
| `google_docs_create` | external | on | Create a Google Doc in the user's Drive, optionally with initial text. |
| `google_docs_append` | external | on | Append text to the end of a Google Doc. |
| `google_sheets_read` | safe | on | Read a Google Sheet by id (from google_docs_search). |
| `google_sheets_write` | external | on | Write rows to a Google Sheet range (A1 notation). |
| `google_sheets_create` | external | on | Create a Google Sheet in the user's Drive, optionally with initial rows (first row as headers). |

### graph

| tool | danger | default | what it does |
|---|---|---|---|
| `graph_search` | safe | on | Find entities in the user's knowledge graph matching a query, with their direct relations (1 hop). |
| `graph_traverse` | safe | on | Walk the knowledge graph outward from a named entity up to `depth` hops and return everything connected. |
| `graph_add` | writes | on | Add a relation (and the entities if new) to the knowledge graph. |

### health

| tool | danger | default | what it does |
|---|---|---|---|
| `health_summary` | safe | on | Read the user's health log: each tracked metric (sleep, steps, water, exercise, weight, mood, meds, and any... |
| `health_log` | writes | on | Log a health reading for the user. |
| `health_delete_entry` | writes | on | Delete one logged health reading by id (from health_summary with `metric`). |

### knowledge

| tool | danger | default | what it does |
|---|---|---|---|
| `search_documents` | safe | on | Search (keywords and meaning) over the user's uploaded files AND the files they write in the Files editor (... |
| `read_document` | safe | on | Read a slice of an uploaded file's full text by id (ids come from search_documents or list_documents). |
| `list_documents` | safe | on | List the uploaded files available in this chat's scope. |

### mac

| tool | danger | default | what it does |
|---|---|---|---|
| `list_shortcuts` | safe | on | List the user's Apple Shortcuts by name, optionally only one Shortcuts folder. |
| `run_shortcut` | external | ask (locked) | Run one of the user's Apple Shortcuts by exact name, optionally passing text as its input, and return its o... |

### mcp

| tool | danger | default | what it does |
|---|---|---|---|
| `mcp_tool_search` | safe | on | Search the connected third-party (MCP) tools by keyword and load the best matches so you can call them. |

### meetings

| tool | danger | default | what it does |
|---|---|---|---|
| `meeting_list` | safe | on | List the user's meetings - the notes they took on calls, plus whatever was transcribed. |
| `meeting_search` | safe | on | Full-text search across meeting titles, the user's notes, the enhanced notes and the transcripts, one row p... |
| `meeting_read` | safe | on | Read one part of a meeting, as numbered lines. |

### memory

| tool | danger | default | what it does |
|---|---|---|---|
| `search_memory` | safe | on | Search what you remember about the user (long-term memory) for a topic. |
| `save_memory` | writes | on | Explicitly remember something durable about the user (a fact, preference or goal) for future chats. |

### plan

| tool | danger | default | what it does |
|---|---|---|---|
| `todo_write` | writes | on | Write this conversation's plan: the step list you are working from. |

### sandbox

| tool | danger | default | what it does |
|---|---|---|---|
| `sandbox_exec` | executes | on | Run a shell command in this chat's persistent Linux sandbox (a VM-isolated container; the host machine is u... |
| `sandbox_write_file` | executes | on | Write (or append to) a text file in the sandbox. |
| `sandbox_read_file` | executes | on | Read a file from the sandbox. |
| `sandbox_list_files` | executes | on | List files in the sandbox (default /workspace, up to 3 levels deep). |
| `sandbox_put_document` | executes | on | Copy an uploaded file's extracted text into the sandbox as a file, so you can edit, transform or analyse it... |
| `sandbox_export_file` | writes | on | Copy any file (binary included, up to 10 MB) from the sandbox's /workspace to the user so they can open it:... |
| `sandbox_reset` | executes | on | Destroy this chat's sandbox and its checkpoints and start the next call from a fresh container. |
| `sandbox_checkpoint` | executes | on | Save the sandbox's files and installed packages under a name so you can roll back to them with sandbox_rest... |
| `sandbox_restore` | executes | on | Replace the sandbox with a checkpoint made by sandbox_checkpoint. |

### schedule

| tool | danger | default | what it does |
|---|---|---|---|
| `schedule_task` | schedules | ask (locked) | Schedule work for YOU to do later, unattended — once at a given time, or repeatedly on a cron expression. |
| `scheduled_tasks` | safe | on | List the scheduled tasks: what runs on its own, when it next runs, and how the last run went. |
| `cancel_scheduled_task` | schedules | on | Switch off a scheduled task so it stops running. |

### shell

| tool | danger | default | what it does |
|---|---|---|---|
| `shell_run` | executes | ask | Run a shell command (zsh) on this Mac inside the working folder. |
| `shell_poll` | safe | on | Read the new output of a background shell job and whether it is still running (status running / exited / ti... |
| `shell_kill` | writes | on | Stop a background shell job: SIGTERM to its whole process group, SIGKILL if it has not exited after 3 seconds. |
| `opencode_run` | executes | ask | Hand a coding task to opencode, a terminal coding agent, inside the working folder (the desk workspace or a... |

### skills

| tool | danger | default | what it does |
|---|---|---|---|
| `skill_list` | safe | on | List the user's procedures (skills) — the step-by-step methods they keep for repeated tasks, with each one'... |
| `skill_draft` | writes | on | Write down a reusable procedure for the user — how a task you just carried out should be done next time — a... |
| `skill_revise` | writes | on | Revise one of the user's procedures. |
| `skill_view` | safe | on | Read the full steps of one approved procedure listed in the 'Approved procedures (index only)' section of y... |
| `skill_from_run` | writes | on | Turn a finished run in this chat into a candidate skill: the method, named from the tools that actually ran... |

### spaces

| tool | danger | default | what it does |
|---|---|---|---|
| `space_list` | safe | on | List the user's spaces (canvases) and the windows on each. |
| `space_add_widget` | writes | on | Put a window on a space: a todos/calendar/memory/etc. |
| `space_arrange` | writes | on | Tile ('grid') or stack ('cascade') the open windows of a space. |

### style

| tool | danger | default | what it does |
|---|---|---|---|
| `writing_style` | safe | on | The user's writing style profile: how they write, as guidelines, traits and characteristic phrasings. |
| `save_writing_sample` | writes | on | Bank a passage the USER wrote as a sample of their writing style, so future drafts can match their voice. |

### todos

| tool | danger | default | what it does |
|---|---|---|---|
| `todo_list` | safe | on | List the user's todos (open by default) in this chat's scope: the project's todos plus personal ones. |
| `todo_add` | writes | on | Add a todo for the user. |
| `todo_update` | writes | on | Update or complete a todo by id (from todo_list). |
| `todo_delete` | writes | on | Delete a todo by id (it goes to the trash, restorable for 30 days). |

### utility

| tool | danger | default | what it does |
|---|---|---|---|
| `current_time` | safe | on | Get the current local date and time. |
| `show` | safe | on | Open the side panel beside the chat on something to look at: a self-contained HTML page (inline CSS/JS, no... |
| `propose_plan` | plan | ask | Ask the user to approve several consequential actions at once, instead of one approval modal per call. |
| `ask_user` | plan | ask | Ask the user one question and wait for the answer. |
| `tool_search` | safe | on | Find and load more of your own tools by keyword. |

### vision

| tool | danger | default | what it does |
|---|---|---|---|
| `view_image` | safe | on | Look at a picture file and get text back: a description of it (layout, charts, tables), every visible word... |

### web

| tool | danger | default | what it does |
|---|---|---|---|
| `web_search` | network | on | Search the web for current information. |
| `fetch_url` | network | on | Fetch a web page, PDF or JSON document and return its main text as markdown. |
| `open_page` | network | on | Load a web page in an offscreen browser (its own cookies, separate from the user's) and return its title an... |
| `youtube_video` | network | on | Read a YouTube video: title, channel, description and the full transcript (subtitles, else auto captions),... |
| `youtube_search` | network | on | Search YouTube. |
| `github_search` | network | on | Search GitHub. |
| `github_read` | network | on | Read from a GitHub repository. |
| `read_feed` | network | on | Read an RSS or Atom feed: the latest items with titles, links, dates and summaries. |

### workflows

| tool | danger | default | what it does |
|---|---|---|---|
| `workflow_list` | safe | on | List the user's saved workflows (repeatable multi-step jobs) with their parameters. |
| `workflow_run` | writes | on | Propose running a saved workflow with the given parameters. |
| `workflow_resume` | plan | ask | Resume an interrupted or failed workflow run that the user already approved. |
| `command_list` | safe | on | List the user's saved commands (prompt templates they run by name). |
| `command_run` | executes | on | Run one of the user's saved commands by name (see command_list). |

## Keyboard shortcuts

From the app menu (`src/main/index.ts`); ⌘ is Cmd, ⌃ Control, ⌥ Option.

| Shortcut | Action |
|---|---|
| ⌘, | Settings |
| ⌘N | New chat |
| ⌘⇧N / ⌘⇧D | New file / today's file |
| ⌘U | Upload file |
| ⌘0 ... ⌘7 | Today, Chats, Lists, Calendar, Files, Mail, Memory, Activity |
| ⌘⇧M / ⌘⇧K | Meetings / Cowork |
| ⌘K | Command palette |
| ⌘⇧F | Search chats |
| ⌘⇧[ / ⌘⇧] | Previous / next chat |
| ⌘F, ⌘G, ⌘⇧G | Find in chat, next, previous |
| ⌘⇧P | Cycle plan mode (handled in the composer) |
| ⌘B | Toggle sidebar |
| ⌘I | Page agent panel |
| ⌃⌘I | Toggle context panel |
| ⌘⇧C | Toggle Spaces |
| ⌃⌘N | New space |
| ⌃1 ... ⌃9 | Go to space 1-9 |
| ⌥⌘← / ⌥⌘→ | Previous / next space |
| ⌥⌘↑ | Space overview |
| ⌃⌘T | Tidy up |
| ⌃⌘L | Lock / unlock space |
| ⌃⌘O / ⌃⌘⇧O | Pop a window out of its space / return it |
| ⌃⌘P | Pin window on top |
| ⌃⌘[ / ⌃⌘] | Window more / less transparent |
| ⌥⌘G | Gather widgets |
| ⌘W, ⌘M | Close, minimize window |

A view hidden in Settings → Modules keeps its shortcut, which offers to turn the view back on.

## Slash commands

From `src/renderer/src/lib/slashCommands.ts`:

| command | does |
|---|---|
| `/skill <name> <message>` | use one approved skill for this message |
| `/schedule <when>, <what>` | run it later, unattended |
| `/loop <every ...> <what>` | repeat on an interval |
| `/compact [focus]` | summarize the earlier messages |
| `/skills` | open Library → Skills |
| `/commands` | open Library → Automations |
| `/plan` | cycle plan mode: off, auto, always |
