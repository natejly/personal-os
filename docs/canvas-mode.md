# Canvas Mode — design & build plan

Status: proposal, nothing built yet. Written 2026-09-29 against the tree as it stands
(`src/renderer/src/App.tsx` view-router, single-session `store.ts`, FastAPI backend in `backend/personal_os/`).

---

## 1. What we're building

A second way to use Personal OS. Today the app is a single-pane router: the sidebar picks one
`view` and that view fills the window (`App.tsx:60-68`). Canvas Mode keeps the same sidebar but
turns the right-hand area into a **desktop**: clicking a chat, todo list, board, calendar or
dashboard widget opens it as a **floating window on a canvas** instead of replacing what's there.
Windows move, resize, snap, stack, minimize to a dock, and — for the ones you want on your real
desktop — **pop out into genuine macOS windows** that a global shortcut can summon back to the
centre of whatever screen you're looking at.

Five pillars:

1. **Canvas + window manager** — floating windows with Apple-style chrome, z-order, focus, minimize.
2. **Snapping** — a coordinate grid, alignment guides between windows, and macOS-style edge tiling.
3. **Spaces** — more than one canvas, each with its own window set, snap settings and optional project binding.
4. **Live widgets** — chat windows with a status ring (amber = working, green = just finished), todos,
   calendar, boards, notes, AI dashboard widgets — and drag-and-drop *between* them.
5. **Pop-out + Gather** — widgets detach into real Electron windows; one global shortcut centres them
   all on the current display, one more press puts them back.

---

## 2. Decisions taken (and what they'd cost to reverse)

| Decision | Choice | Why | Reversal cost |
|---|---|---|---|
| Canvas vs. classic | **Both.** A `mode: 'classic' \| 'canvas'` toggle (⌘⇧C), classic stays the default until canvas is solid | Every existing view keeps working while canvas is half-built; no big-bang cutover | Low — flipping the default is one line |
| Canvas geometry | **Unbounded canvas-space pixels**, viewport pans/zooms over it | Window positions survive resizing the app window; zoom-out doubles as an overview | Medium — coordinates are persisted |
| Zoom | CSS `transform: scale()` on the canvas layer, 50 %–200 %, ⌘-scroll | Live DOM stays live and crisp; no canvas/WebGL rewrite | Low |
| Window chrome | Real traffic lights (red/amber/green), centred 13 px semibold title, 30 px bar | This is the single strongest "it's a desktop" signal, and the ask is explicitly Apple design language | Low |
| Widget data flow | Widgets talk to the FastAPI backend directly, as they already do | Pop-out windows are separate renderers with no shared zustand — HTTP is the only state channel that works in both | High if we build shared-memory state first |
| Multi-chat | Refactor the store to N sessions **before** canvas | The current store has one `active` and one `streaming`; two live chats is impossible without it | High — it is load-bearing for pillar 4 |
| Pop-out streaming | Requires a **server-side stream bus** (§5) | Today the SSE *is* the generation; closing the window kills the reply | High for pop-out, optional before then |

---

## 3. The blocker: one chat at a time

`src/renderer/src/store.ts` models exactly one conversation:

```ts
activeId: string | null
active: Conversation | null
streaming: { conversationId, messageId, abort } | null
```

`runStream` mutates through `patchActive` (`store.ts:118-190`), which by construction only ever
touches `active`. Several chat windows side by side — the whole point of the amber/green rings —
cannot work until this is per-conversation.

### 3.1 Renderer refactor (Phase 0a)

```ts
export type SessionStatus = 'idle' | 'working' | 'done' | 'error' | 'needs-approval'

interface ChatSession {
  conversation: Conversation
  streaming: { messageId: string | null; abort: AbortController } | null
  status: SessionStatus
  /** epoch ms the last run finished; drives the 6 s green hold */
  finishedAt: number | null
  /** tool calls waiting on the approval card */
  pendingApprovals: number
  unread: number
}

sessions: Record<string, ChatSession>   // keyed by conversation id
focusedConversationId: string | null
```

- `patchActive(fn)` → `patchSession(convId, fn)`; `patchMessage(mid, fn)` → `patchMessage(convId, mid, fn)`.
- `send`, `regenerate`, `stop`, `setChatModel`, `setChatSettings`, `renameChat` take an explicit
  `conversationId` (defaulting to `focusedConversationId`, so classic mode is unchanged).
- `active` / `activeId` survive as **derived selectors** over `focusedConversationId` so
  `ChatView`, `Composer`, `ContextDrawer`, `MemoryPanel` and `Sidebar` need only small edits.
- `ChatView` gains an optional `conversationId` prop; canvas mounts one per chat window, classic
  mounts one with no prop.
- New hook `useSession(convId)` and `useSessionStatus(convId)` for the ring.

Touched: `store.ts` (the bulk), `ChatView.tsx`, `Composer.tsx`, `Message.tsx`, `ContextDrawer.tsx`,
`Sidebar.tsx` (the `streaming?.conversationId === c.id` pulse becomes a status lookup).

### 3.2 Cleanup while we're in there

`src/shared/types.ts` declares `Dashboard` **twice** (line 209, the Today-dashboard payload, and
line 314, a widget dashboard). TypeScript silently merges them, and `api.ts` already imports it
under two names (`Dashboard as DashboardData` *and* `Dashboard`). Rename the Today one to
`TodayDashboard` before canvas adds a third consumer.

---

## 4. Where it lands in the tree

```
src/shared/types.ts                 + Canvas, CanvasWindow, WidgetKind, DragPayload, PopoutState
src/main/index.ts                   + mode menu items, window menu, global shortcut wiring
src/main/popouts.ts          (new)  BrowserWindow registry, bounds persistence, gather/scatter
src/main/shortcuts.ts        (new)  globalShortcut registration + failure reporting
src/preload/index.ts                + popout.*, bus.* bridges
src/renderer/src/store.ts           sessions refactor (§3.1)
src/renderer/src/canvas/            (new directory)
  store.ts                          canvas/space/window state, separate zustand store
  Canvas.tsx                        the plane: pan, zoom, drop target, marquee
  WindowFrame.tsx                   chrome, traffic lights, status ring, drag/resize handles
  useDrag.ts                        pointer-capture drag/resize with rAF, snapping hooks
  snapping.ts                       grid + guide + edge-zone maths (pure, unit-testable)
  SpacesBar.tsx                     space switcher
  Overview.tsx                      Mission-Control-style space overview
  Dock.tsx                          minimized-window strip
  widgets/                          one file per widget kind (§8)
  registry.ts                       WidgetKind -> { component, defaultSize, minSize, icon, title }
src/renderer/src/PopoutSurface.tsx  (new) renderer entry for detached windows
src/renderer/src/styles/canvas.css  (new) window materials, motion, dock
backend/personal_os/canvas.py       (new) canvases + canvas_windows repo
backend/personal_os/notes.py        (new) sticky notes (the only new content type)
backend/personal_os/runs.py         (new) stream bus (§5)
backend/personal_os/db.py           + three tables, migrations
backend/personal_os/app.py          + /canvases, /notes, /runs routes
```

---

## 5. Server-side stream bus (Phase 0b)

Today `POST /conversations/{id}/chat` returns a `StreamingResponse` whose generator *is* the model
run (`app.py:269-466`). Consequences: only one client can watch a reply, and if that client goes
away (pop-out, window close, app reload) the reply dies half-written.

Proposal — split *running* from *watching*:

- `runs.py` holds `RunBus`: one topic per conversation, an `asyncio.Queue` per subscriber, and a
  ring buffer of the last 500 events with a monotonic `seq`.
- `POST /conversations/{id}/chat` starts the generation as a background task and returns
  `{ run_id, seq }` immediately. `409` if that conversation already has a live run.
- `GET /conversations/{id}/stream?since=<seq>` is the SSE endpoint. Any number of clients attach;
  a late joiner replays from `since`. Sends `: keepalive` every 15 s.
- `GET /runs` lists conversations currently generating, so a freshly opened window (or a pop-out
  that just launched) paints its amber ring correctly on first render.
- `POST /messages/{mid}/stop` is unchanged; it already works by message id.

Roughly 150 lines in `runs.py` plus edits to two handlers. The existing SSE event shapes
(`ChatEvent` in `types.ts:277-289`) stay byte-identical, so `chatStream()` in `lib/api.ts` only
changes which URL it opens.

**Required for Phase 6** (pop-out). Phases 1-5 can ship without it: each chat window owns its own
`fetch` and its own `AbortController`, which works fine as long as only one view watches a given
chat and nothing detaches mid-reply. Build it early anyway — it also fixes "quit the app mid-reply
and lose the answer", which is a bug today.

---

## 6. Data model

### 6.1 SQLite (append to `SCHEMA` in `db.py`, plus `_migrate` entries)

```sql
CREATE TABLE IF NOT EXISTS canvases (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  project_id TEXT REFERENCES projects(id) ON DELETE SET NULL,  -- optional binding
  position INTEGER NOT NULL DEFAULT 0,
  snap_mode TEXT NOT NULL DEFAULT 'both',   -- off | grid | guides | both
  grid_size INTEGER NOT NULL DEFAULT 16,
  zoom REAL NOT NULL DEFAULT 1.0,
  pan_x REAL NOT NULL DEFAULT 0,
  pan_y REAL NOT NULL DEFAULT 0,
  wallpaper TEXT NOT NULL DEFAULT '',       -- '' | tint token | later: image path
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS canvas_windows (
  id TEXT PRIMARY KEY,
  canvas_id TEXT NOT NULL REFERENCES canvases(id) ON DELETE CASCADE,
  kind TEXT NOT NULL,                       -- WidgetKind, see §8
  ref_id TEXT,                              -- conversation / board / dashboard / widget / note id
  project_id TEXT REFERENCES projects(id) ON DELETE SET NULL,
  title TEXT NOT NULL DEFAULT '',           -- '' = derive from the underlying object
  x REAL NOT NULL, y REAL NOT NULL, w REAL NOT NULL, h REAL NOT NULL,
  z INTEGER NOT NULL DEFAULT 0,
  state TEXT NOT NULL DEFAULT 'normal',     -- normal | minimized | maximized | popped
  /** bounds to restore when un-maximizing */
  restore_bounds TEXT,
  /** screen bounds of the detached window, JSON {x,y,width,height,display} */
  popout_bounds TEXT,
  pinned INTEGER NOT NULL DEFAULT 0,        -- always-on-top when popped
  config TEXT NOT NULL DEFAULT '{}',        -- per-widget options (filters, scope, view mode…)
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cw_canvas ON canvas_windows(canvas_id, z);

CREATE TABLE IF NOT EXISTS notes (
  id TEXT PRIMARY KEY,
  project_id TEXT REFERENCES projects(id) ON DELETE CASCADE,
  body TEXT NOT NULL DEFAULT '',
  color TEXT NOT NULL DEFAULT 'yellow',
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);
```

`notes` is the only genuinely new content type — everything else on the canvas is a view onto data
that already exists (conversations, todos, boards, documents, memories, widgets).

### 6.2 REST

```
GET    /canvases                     -> Canvas[] (with windows)
POST   /canvases                     { name, project_id?, copy_from? }
PUT    /canvases/{id}                { name?, snap_mode?, grid_size?, zoom?, pan_x?, pan_y?, wallpaper?, position? }
DELETE /canvases/{id}
POST   /canvases/{id}/windows        { kind, ref_id?, x, y, w, h, config? } -> CanvasWindow
PUT    /canvases/{id}/layout         { windows: [{id, x, y, w, h, z, state}] }   # bulk, debounced 400 ms
PUT    /windows/{id}                 { title?, config?, state?, pinned?, popout_bounds?, restore_bounds? }
DELETE /windows/{id}
GET/POST/PUT/DELETE /notes[/{id}]
```

The bulk `PUT /canvases/{id}/layout` matters: dragging one window nudges z-order on others, and we
do not want a request per window per drag. Geometry is written on `pointerup`, not during the drag.

### 6.3 TypeScript (`src/shared/types.ts`)

```ts
export type WidgetKind =
  | 'chat' | 'todos' | 'calendar' | 'board' | 'note' | 'dashboard-widget'
  | 'memory' | 'graph' | 'documents' | 'recap' | 'project' | 'usage'

export type WindowState = 'normal' | 'minimized' | 'maximized' | 'popped'
export type SnapMode = 'off' | 'grid' | 'guides' | 'both'

export interface CanvasWindow {
  id: string; canvas_id: string; kind: WidgetKind; ref_id: string | null
  project_id: string | null; title: string
  x: number; y: number; w: number; h: number; z: number
  state: WindowState
  restore_bounds: Rect | null
  popout_bounds: (Rect & { display?: number }) | null
  pinned: number
  config: Record<string, unknown>
  created_at: number; updated_at: number
}
export interface Rect { x: number; y: number; w: number; h: number }
export interface Canvas {
  id: string; name: string; project_id: string | null; position: number
  snap_mode: SnapMode; grid_size: number; zoom: number; pan_x: number; pan_y: number
  wallpaper: string; created_at: number; updated_at: number
  windows: CanvasWindow[]
}
```

---

## 7. Snapping

All maths lives in `canvas/snapping.ts` as pure functions over rects, so it can be unit-tested
without a DOM. Thresholds are in *screen* pixels and divided by the zoom factor before use, so
snapping feels identical at every zoom level.

### 7.1 Grid

- Pitch: **16 pt default**, selectable 8 / 16 / 24 / 32, per space.
- Applies to both position and size. Moving snaps the top-left corner; resizing snaps the edge
  being dragged (so the opposite edge never drifts).
- A faint dot grid (1 px dots at 20 % opacity, only at ≥ 75 % zoom) fades in while dragging and
  fades out 200 ms after release.

### 7.2 Alignment guides

Candidates, computed once at drag start from the other windows in the space plus the viewport:

- vertical: each window's `left`, `centerX`, `right`; the viewport's `centerX`
- horizontal: each window's `top`, `centerY`, `bottom`; the viewport's `centerY`

Snap threshold **6 pt / zoom**. At most one guide per axis is applied and drawn (the nearest). The
guide is a 1 px accent line spanning from the dragged window to the window it matched, with 4 px
end caps — the Keynote/Sketch treatment.

**Equal-spacing guides**: if the gap to the neighbour on one side lands within 2 pt of an existing
gap elsewhere on that axis, snap to equality and draw the two gap pills.

### 7.3 Edge zones (macOS-style tiling)

Drag the pointer within **8 pt** of a viewport edge and hold **250 ms**:

| Zone | Result |
|---|---|
| left / right edge | half of the viewport |
| any corner | quarter |
| top edge | maximize to viewport |
| bottom centre | centre at the window's natural size |

A translucent preview (accent at 12 % alpha, 12 px radius, 1 px accent border) animates in; release
commits, `Esc` or dragging away cancels.

### 7.4 Modifiers and commands

- hold **⌘** while dragging: all snapping off (escape hatch, always available)
- hold **⇧**: constrain to one axis
- hold **⌥** while resizing: resize about the centre
- **⌃⌘T Tidy Up**: masonry-pack the space's windows on the grid pitch in reading order, animated
- Per-space menu: Snap → Off / Grid only / Guides only / Both

---

## 8. Widgets

### 8.1 Contract

`canvas/registry.ts` maps a `WidgetKind` to:

```ts
interface WidgetDef {
  kind: WidgetKind
  label: string
  icon: JSX.Element
  defaultSize: { w: number; h: number }
  minSize: { w: number; h: number }
  /** does this widget want a status ring? (chat does; a note doesn't) */
  statusful?: boolean
  /** what a drop of this payload onto the canvas creates */
  accepts?: DragKind[]
  Component: React.FC<WidgetProps>
}
interface WidgetProps {
  window: CanvasWindow
  focused: boolean
  /** false when the widget is off-screen or minimized: pause polling, unmount iframes */
  live: boolean
  onConfig: (patch: Record<string, unknown>) => void
  onTitle: (t: string) => void
}
```

`live` is how we keep 20 windows cheap (§12).

### 8.2 Catalog

| Kind | Default size | Backed by | Notes |
|---|---|---|---|
| `chat` | 520×640 | `conversations/{id}` | Full `ChatView` minus its header; status ring; ⌘↵ sends |
| `todos` | 380×520 | `/todos` | `config`: `{ scope, includeDone, q }`; rows are drag sources |
| `calendar` | 640×520 | `/integrations/google/calendar` | `config`: `{ mode: 'day' \| 'week', days }` |
| `board` | 760×560 | `/boards/{id}` | Whole board, or `config.column_id` for a single column |
| `note` | 300×300 | `notes` | Sticky note, markdown, 6 Apple-ish colours, no chrome beyond a close button |
| `dashboard-widget` | 420×340 | `/widgets/{id}/render` | Reuses the existing sandboxed iframe (`DashboardsView.tsx:127`) |
| `memory` | 400×520 | `/memories` | List, scope filter, drag a memory into a chat to pin it as context |
| `graph` | 640×560 | `/graph` | Existing d3-force view; heavy — proxy-render below 60 % zoom |
| `documents` | 400×480 | `/documents` | Drop files onto it to upload |
| `recap` | 420×360 | `/recap` | Today's recap, refresh button |
| `project` | 360×420 | `/projects/{id}` | Project card: instructions, files, chats, stats |
| `usage` | 560×420 | `/usage` | Cost/token charts |

### 8.3 Status ring (the amber/green ask)

Applied to `statusful` widgets, drawn as a 2 px inset ring just inside the window border, plus a
glyph in the title bar so it never depends on colour alone.

| Status | Ring | Title-bar glyph | Clears when |
|---|---|---|---|
| `idle` | none (hairline border only) | — | — |
| `working` | amber `#febc2e`, 1.6 s breathing opacity 0.55↔1.0 | spinner | the run ends |
| `done` | green `#28c840`, solid | check | 6 s elapse, **or** the window is focused |
| `error` | red `#ff5f57`, solid | `!` | focused or dismissed |
| `needs-approval` | accent, 1 s pulse + count badge | lock | the approval card is answered |

`error` and `needs-approval` are extensions beyond the two colours asked for — `needs-approval`
in particular matters because the permission model (external actions must ask) means a chat can sit
blocked behind a card in a window you aren't looking at. Minimized windows carry the same ring on
their dock tile, which is what makes "run five chats at once" actually usable.

Honours `prefers-reduced-motion`: pulses become static fills.

---

## 9. Drag and drop between widgets

One payload type carried on a custom MIME (`application/x-personal-os`) so the browser's own
drag types never collide:

```ts
type DragKind = 'conversation' | 'todo' | 'document' | 'memory' | 'board-card' | 'project' | 'widget' | 'note' | 'file'
interface DragPayload { kind: DragKind; id: string; label: string; projectId?: string | null }
```

Drag sources: sidebar rows (`Sidebar.tsx` convo items, project items, nav items), and rows inside
widgets. Drop targets declare `accepts` in the registry.

| Drag | Onto canvas | Onto a widget |
|---|---|---|
| sidebar chat | opens a `chat` window | — |
| sidebar nav item (Todos, Calendar…) | opens that widget | — |
| todo | opens a focused `todos` window | onto `board` → becomes a card; onto `chat` → quoted into the composer |
| document | opens a `documents` window | onto `chat` → attached to context |
| memory | opens a `memory` window | onto `chat` → pinned into context |
| board card | opens a `note` with its text | onto `todos` → becomes a todo |
| project | opens a `project` window | onto a space → binds the space to that project |
| OS files | uploads + opens `documents` | onto `chat` → uploads and attaches |

Drop feedback: the target window's border brightens to accent and lifts 2 px; the canvas shows a
ghost rect at the snapped landing position.

---

## 10. Spaces

- A space = one row in `canvases`. Switcher lives as a segmented control under the sidebar's nav
  block, or as a slim bar along the top of the canvas (pick one in build; the sidebar keeps the
  canvas edge-to-edge, which I prefer).
- Create (⌃⌘N), rename, reorder by drag, duplicate ("copy_from"), delete (with confirm if non-empty).
- Switch: ⌃← / ⌃→, or ⌃1…⌃9. The transition is a horizontal slide at `--spring`, 320 ms —
  the macOS space swipe.
- **Overview** (⌃↑): all spaces scale to ~0.22 in a grid, each showing its live windows as static
  proxies; click to enter, drag a window between space thumbnails to move it. This is Mission
  Control and it is also the cheapest way to make many spaces navigable.
- Optional **project binding**: a space bound to a project opens new chats in that project, filters
  todos/memories/documents to it by default, and tints its wallpaper with the project colour. This
  keeps canvas inside the existing project model rather than inventing a parallel one.

---

## 11. Pop-out windows and Gather

### 11.1 Detaching

`src/main/popouts.ts` keeps `Map<canvasWindowId, BrowserWindow>`.

```ts
new BrowserWindow({
  width, height, x, y,
  frame: false,                 // custom slim title bar instead
  roundedCorners: true,
  hasShadow: true,
  backgroundColor: '#00000000',
  vibrancy: 'under-window',     // NOT transparent:true — on macOS the two fight
  visualEffectState: 'active',
  minWidth, minHeight,
  webPreferences: { preload, contextIsolation: true, sandbox: false }
})
```

Loads the same renderer bundle with `?surface=widget&window=<id>`; `main.tsx` reads the query and
mounts `PopoutSurface` (just the widget body + a 28 px `-webkit-app-region: drag` bar with a close
and a pin button) instead of `App`.

Because each pop-out is its own renderer with its own zustand store, **the backend is the only
shared state** — which is already true of every widget, so nothing extra is needed for correctness.
For snappy cross-window updates, add a thin relay: `ipcRenderer.send('bus', msg)` → main →
`webContents.send('bus', msg)` to every other window. The bus carries optimistic hints only
(a todo was toggled, a window moved, a chat's status changed); the backend stays authoritative.

Lifecycle:
- Pop out: window `state` → `popped`, canvas shows a dashed ghost placeholder in its slot.
- `move` / `resize` on the BrowserWindow → debounced 400 ms → `PUT /windows/{id} { popout_bounds }`.
- Close the pop-out → `state` → `normal`, the ghost fills back in.
- Pin (⌃⌘P) → `setAlwaysOnTop(true, 'floating')`, persisted in `pinned`.
- Transparency (⌃⌘[ / ⌃⌘], the pop-out's own slider, or the tray for all of them at once) →
  `win.setOpacity(o)`, persisted in `opacity` and clamped to `[0.2, 1]` so a pop-out can never
  fade out of reach. Only pop-outs wear it; a window back on the canvas just remembers the level.
- Quitting the app closes all pop-outs; relaunching restores them if `state === 'popped'`.

**Pop-out requires the stream bus (§5)** — without it, detaching a chat mid-reply kills the reply,
because the SSE belongs to the renderer that opened it.

### 11.2 Gather

Global shortcut, default **⌃⌥⌘Space**, configurable in Settings. `globalShortcut.register()`
returns `false` on conflict and fails silently otherwise, so the return value is checked and a
warning surfaces in Settings when it fails.

On trigger, if any windows are popped out:

1. Target display = `screen.getDisplayNearestPoint(screen.getCursorScreenPoint())`, use `workArea`.
2. Save each window's current bounds to `previousBounds` (guarded so a double-trigger doesn't
   overwrite the scattered layout with the gathered one).
3. Lay out a centred grid: `cols = ceil(sqrt(n))`, 20 pt gaps, 10 % margins inside `workArea`,
   cells scaled down uniformly if the natural sizes don't fit, each window keeping its aspect ratio.
4. Per window: `setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true })`, `showInactive()`,
   `setBounds(target, true)` (animated), `moveTop()`, temporary `setAlwaysOnTop(true, 'floating')`.
5. `app.focus({ steal: true })`.

Pressing it again while gathered **scatters**: restore `previousBounds`, drop the temporary
always-on-top (keeping it for `pinned` windows), clear `visibleOnAllWorkspaces`.

If nothing is popped out, the shortcut just brings the main window forward — predictable beats clever.

Also exposed as a menubar **Tray** item ("Gather Widgets", "Scatter", "Pin all on top", "Open
Personal OS"), which doubles as the discovery path when the shortcut is taken by something else.

Known macOS limitations to document, not fight:
- Another app's **fullscreen space** will not reliably accept overlaid windows even with
  `visibleOnFullScreen`; gather lands on the current space instead.
- `setVisibleOnAllWorkspaces(true)` can be reset by the system on space changes; re-assert it on
  each gather rather than trusting it to stick.
- Stage Manager rearranges windows on its own; treat gather as best-effort there.

---

## 12. Apple design language

### 12.1 Tokens (append to `styles.css`, both themes)

```css
--win-radius: 12px;
--win-radius-sm: 10px;
--material-thick: rgba(30, 29, 27, 0.72);
--material-thin:  rgba(30, 29, 27, 0.55);
--material-opaque: #232220;              /* swapped in during drag, no blur */
--material-blur: saturate(180%) blur(30px);
--win-border: rgba(255, 255, 255, 0.12);
--win-highlight: rgba(255, 255, 255, 0.22);   /* 1px inner top edge */
--shadow-window:  0 0 0 0.5px rgba(0,0,0,.35), 0 8px 24px rgba(0,0,0,.28), 0 24px 60px rgba(0,0,0,.30);
--shadow-focused: 0 0 0 0.5px rgba(0,0,0,.45), 0 12px 32px rgba(0,0,0,.34), 0 36px 90px rgba(0,0,0,.42);
--tl-red: #ff5f57; --tl-yellow: #febc2e; --tl-green: #28c840; --tl-off: #565654;
--spring: cubic-bezier(0.32, 0.72, 0, 1);
--dur-window: 320ms; --dur-snap: 180ms; --dur-hover: 120ms;
```

### 12.2 Chrome

- Title bar 30 px; title 13 px / 600 weight, **centred**, truncating with a fade not an ellipsis.
- Traffic lights left at 8 px, 12 px diameter, 8 px gap; glyphs (×, −, ⤢) appear only on
  `:hover` of the trio; all three go `--tl-off` when the window is unfocused.
- Unfocused windows: shadow drops to `--shadow-window`, content opacity 0.92, blur unchanged.
- Body inherits the existing `--bg-elev`/`--border` language so widgets look native to the app.

### 12.3 Motion

| Event | Animation |
|---|---|
| Open | scale 0.94 → 1, opacity 0 → 1, 320 ms `--spring` |
| Close | scale 1 → 0.96, opacity → 0, 180 ms |
| Minimize | scale + translate toward its dock tile, 280 ms `--spring` |
| Snap settle | 180 ms `--spring` on `transform` |
| Focus raise | shadow + 1 px lift, 120 ms |
| Space switch | translateX slide, 320 ms `--spring` |
| Overview | scale to 0.22 + fade the wallpaper, 320 ms |

Everything collapses to instant under `prefers-reduced-motion: reduce`.

### 12.4 Dock

Minimized windows collapse to a centred, floating strip along the bottom of the canvas: 44 px
tiles, 12 px gaps, the same material as windows, subtle magnification on hover (scale 1.0 → 1.35
with neighbour falloff). Tiles carry the status ring, which is how an amber chat you minimized
still tells you it's working.

---

## 13. Performance

Targets: 60 fps dragging with 12 open windows; a space with 20 windows opens in < 400 ms.

- **Dragging bypasses React.** `pointerdown` → `setPointerCapture`, deltas held in a ref, written
  straight to the node as `transform: translate3d(...)`. The store and the backend learn about it
  on `pointerup`. One `requestAnimationFrame` loop for the whole canvas, not one per window.
- **Blur is the expensive part.** `.canvas.interacting` (set during any drag, resize, zoom or space
  switch) swaps `--material-thick` for `--material-opaque` and drops `backdrop-filter`. This is the
  single biggest win and it's invisible in motion.
- `contain: layout paint style` on window bodies; `content-visibility: auto` for off-viewport windows.
- The `live` prop (§8.1): off-screen and minimized widgets stop polling, unmount iframes, and pause
  the graph simulation. Cap concurrently-live heavy widgets (iframe, graph, calendar) at 6; beyond
  that render a static proxy card until focused.
- Below 60 % zoom, every widget renders its proxy card (icon + title + one line of summary) — which
  is also exactly what Overview needs, so it's one implementation serving two features.
- `will-change: transform` only for the duration of a drag; never as a static style.

---

## 14. Menus and shortcuts

Added to `buildMenu()` in `src/main/index.ts` and routed through the existing `sendMenu` → `onMenu`
channel (`store.ts:246`), which already handles this pattern.

| Shortcut | Action |
|---|---|
| ⌘⇧C | Toggle Canvas / Classic |
| ⌃⌘N | New space |
| ⌃← / ⌃→ | Previous / next space |
| ⌃1…⌃9 | Jump to space *n* |
| ⌃↑ | Space overview |
| ⌃⌘T | Tidy up |
| ⌃⌘O | Pop out focused window |
| ⌃⌘⇧O | Return popped window to canvas |
| ⌃⌘P | Pin popped window on top |
| ⌃⌘[ / ⌃⌘] | More / less transparent (pop-out) |
| ⌥⌘G | Gather (in-app equivalent) |
| **⌃⌥⌘Space** | **Gather / scatter (global, works from any app)** |
| ⌘W | In canvas mode, closes the **focused canvas window** — not the app window |
| ⌘M | Minimize focused canvas window to the dock |

⌘W is the trap worth calling out: it must be intercepted in canvas mode and fall through to the
app window only when no canvas window has focus.

---

## 15. Phases

Each phase is independently shippable and leaves the app working.

**Phase 0 — foundations (no visible change)**
0a. Sessions refactor (§3.1) + the duplicate `Dashboard` rename. 0b. Stream bus (§5).
*Done when:* two chats can stream simultaneously in classic mode (open one, switch away, come back
and find it finished), and quitting mid-reply no longer loses the answer.

**Phase 1 — canvas shell**
`mode` toggle, canvas store, `Canvas.tsx` pan/zoom, `WindowFrame` chrome + traffic lights, open
from a sidebar click, drag, resize, z-order, focus, minimize to dock, persistence via `/canvases`.
Widget kinds: `chat` and `note` only.
*Done when:* click three chats in the sidebar, arrange them, quit, relaunch, layout is identical.

**Phase 2 — snapping**
`snapping.ts` + guides overlay + edge zones + tidy up + per-space settings.
*Done when:* unit tests cover grid/guide/zone maths; dragging at 50 % and 200 % zoom feels the same.

**Phase 3 — widget catalog**
The rest of §8.2, the registry, the `live` contract, and the status ring (§8.3).
*Done when:* five chat windows run at once and their rings tell the truth — amber while working,
green for six seconds after, and the dock tile agrees.

**Phase 4 — drag and drop**
The payload type, sources, targets, drop feedback (§9).
*Done when:* a todo dragged onto a board becomes a card and survives a reload.

**Phase 5 — spaces**
Multiple canvases, switcher, transitions, overview, project binding.
*Done when:* three spaces with different window sets, ⌃→ switches at 60 fps, overview drag moves a
window between spaces.

**Phase 6 — pop-out**
`popouts.ts`, `PopoutSurface`, IPC bridge, bounds persistence, the bus relay, restore on relaunch.
*Done when:* a chat popped out mid-reply keeps streaming (this is what Phase 0b bought), and the
canvas ghost placeholder reflects it.

**Phase 7 — gather**
Global shortcut, grid layout, scatter, tray, Settings field with conflict warning.
*Done when:* from Safari fullscreen-adjacent, one keystroke centres every popped widget on the
display under the cursor, and a second press puts them back exactly where they were.

**Phase 8 — polish**
Motion pass, dock magnification, reduced-motion, perf budget verification, ⌘W interception, README
+ keyboard-shortcut docs, wallpaper tints.

Rough sizing: Phase 0 is the riskiest and least visible (2 substantial sessions). Phases 1-3 are the
bulk of the work. Phases 6-7 are small in code but fiddly in macOS behaviour and want real
on-device testing.

---

## 16. Risks

| Risk | Mitigation |
|---|---|
| Sessions refactor destabilises classic mode | Keep `active`/`activeId` as derived selectors so existing components change as little as possible; do it as its own phase with nothing else in flight |
| Streaming multiplexing bugs (dropped/duplicated events) | Monotonic `seq` + replay-from-`since` makes reattach idempotent; ring buffer bounds memory |
| `backdrop-filter` on 15 windows tanks the frame rate | Blur is dropped during every interaction; measured against a 12-window budget before Phase 3 ships |
| Sandboxed widget iframes multiply cost | `live` unmounts them off-screen; hard cap of 6 concurrent |
| `transparent` + `vibrancy` flicker on macOS | Use `vibrancy` with a transparent `backgroundColor`, never `transparent: true`; verify on this machine early in Phase 6 |
| Global shortcut already taken | `register()` return value checked, Settings shows a warning, Tray is the fallback path |
| Gather can't overlay another app's fullscreen space | Documented as a limitation; gather targets the current space |
| Coordinate persistence churn | Bulk layout endpoint, debounced 400 ms, written on `pointerup` only |
| Canvas becomes a second half-maintained UI | Classic and canvas share every widget component; the only canvas-only code is the frame, the plane and the snapping |

---

## 17. Forks worth your call (defaults assumed if you say nothing)

1. **Does canvas eventually replace classic, or stay a toggle?**
   Default taken: toggle, classic remains the launch default. Flipping it later is one line.
2. **Sticky notes need a new `notes` table** — the only new content type in the whole plan.
   Default taken: yes, build it. Say so and the `note` widget drops out cleanly.
3. **Space switcher placement**: sidebar segmented control vs. a top bar on the canvas.
   Default taken: sidebar, so the canvas stays edge-to-edge.
4. **Gather shortcut default** ⌃⌥⌘Space. Say a different one and it changes a constant.
5. **Build the stream bus in Phase 0, or defer it to Phase 6?**
   Default taken: Phase 0 — it also fixes losing a reply when you quit mid-generation, which is a
   live bug today.
