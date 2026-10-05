# Spaces

A space is a desktop of live windows that sits beside the ordinary views. Open one from the
**Spaces** section of the sidebar, toggle in and out with ⌘⇧C, and jump between spaces with
⌃1 … ⌃9 or ⌥⌘← / ⌥⌘→ (⌥⌘↑ shows them all side by side). ⌃⌘N makes a new one. Spaces are stored
in the `canvases` and `canvas_windows` tables (`backend/personal_os/canvas.py`).

## Windows

Each window is a widget of one kind: `chat`, `todos`, `calendar`, `note`,
`dashboard-widget`, `memory`, `graph`, `documents`, `recap`, `project`, `usage`, `activity`, `web`,
`artifact`, `face` or `crew` (a `todos` window has a list and a board view; a `face` window is just the assistant's
creature, thinking while any chat answers and surprised while something waits on you; a `crew` window is a
desk or a workflow run with the agents under it, see below). Add one by dragging a row from the sidebar (a chat, a note, a nav row) onto the plane,
from the **+** on the Spaces bar, or by right-clicking the plane. Several chats can stream at once,
each with a status ring. A chat window can also shrink to a blob: the face button in its head (or
**Shrink to a face** in the right-click menu) folds the window to just the chat's creature with no
frame, drag the creature to move it, click it (or **Open chat** in the menu) to grow the chat back, and
Settings › Behavior › Spaces › **Compact chats** makes the blob the default for windows that have not
chosen. Sticky notes and the Web browser exist only here.

Windows move, resize, minimize and maximize. The Spaces bar holds the space tabs (drag to reorder),
the add-widget button, **Tidy up** (⌃⌘T), the snapping picker and the lock. Snapping is per space:
off, grid, alignment guides, or both, with a grid pitch in points; a guide match beats equal spacing,
which beats the grid.

A space can be bound to a project, so a chat opened in it belongs to that project.

## Crew

Drag a desk from the Cowork rail, or a saved workflow or one of its runs from the Library, onto a space
and it opens as a crew window: the desk or run at the head, and under it the agents working for it as a
small tree (a workflow nests each agent under the step that spawned it; a desk nests subagents under the
agent that asked for them). Every row is a face posed by its status and one line of what it is on right
now, which for a live subagent is the tool call in flight or "thinking". Clicking a row opens a card with
the task, the live call, rounds, calls and cost. When the root finishes while the window is open, a pill
appears at the head and stays until clicked. The head also carries the run's own controls (approve the
plan, cancel, resume, run again) and a link to the desk or the Library. A desk's status and every
workflow run or step write arrive over the app's event stream; a live tree also polls every two seconds,
since a child's "now" line moves without an announcement.

## Pop-outs

⌃⌘O pops the focused window out into its own OS window; ⌃⌘⇧O puts it back. A pop-out can be pinned
on top (⌃⌘P) and made see-through (⌃⌘[ / ⌃⌘]). **Gather Widgets** (⌥⌘G, and a global shortcut set in
Settings → Behavior) brings every pop-out to the front and sends them back again.

## Presets

A preset is a named snapshot of a space: its windows, snapping, zoom, pan and wallpaper. Save one from
the presets menu on the Spaces bar; picking a preset makes a new space from it. Presets live in
`canvas_presets` (`backend/personal_os/presets.py`).

## Lock

⌃⌘L, or the padlock on the Spaces bar, freezes a space. Widgets stay usable, but nothing on a locked
space can be moved, resized, added or closed, and its pan and zoom stay put. The assistant's tools
refuse a locked space too.

## Agent tools

The assistant can add to a space but never close or delete anything on one.

| Tool | Does |
|---|---|
| `space_list` | Lists the spaces and the windows on each. |
| `space_add_widget` | Puts a view, or an existing chat, note, page, dashboard widget or project, in the next free grid cell of a space. |
| `space_arrange` | Tiles (`grid`) or stacks (`cascade`) a space's open windows. Moves and resizes only. |
| `widget_create` | Builds a live chart, stat or table widget from a data source. The layout is generated once; refreshes re-read the source with no model call. |
| `widget_place` | Puts a widget from `widget_create` on a space (the first one when none is named). |

`space_*` live in `backend/personal_os/space_tools.py`, `widget_*` in `widget_tools.py`.
