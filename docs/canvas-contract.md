# Canvas Mode — frozen contract

Status: **FROZEN 2026-09-29.** Source of truth for the six implementation slices of `docs/canvas-mode.md`.
Read this before writing a line. If you need something that is not here, do not invent it — say so and
it gets added here first.

`src/shared/types.ts` is frozen by this document. Nobody edits it. Every declaration below is already
in the tree and `npx tsc --noEmit -p tsconfig.web.json` is **clean**.

---

## 0. What the contract owner already did

Four files, all committed:

| File | Change |
|---|---|
| `src/shared/types.ts` | Renamed the Today payload to `TodayDashboard`; added `Settings.gatherShortcut`; extended `PersonalOSApi`; appended the whole `// ---------------- Canvas Mode ----------------` block. |
| `src/renderer/src/lib/api.ts` | Line 2: `Dashboard as DashboardData` → `TodayDashboard`. Line 43: `req<DashboardData>` → `req<TodayDashboard>`. **Nothing else.** |
| `src/renderer/src/store.ts` | Line 2 type import → `TodayDashboard`; line 22 `dashboard: TodayDashboard \| null`; the `settings` default literal gained `gatherShortcut: ''`. **Nothing else.** |
| `docs/canvas-contract.md` | This file. |

Why the two-file rule was broken: renaming `Dashboard` makes the tree not typecheck until its four
consumers move, and the rename was an explicit deliverable. `HomeView.tsx` and `Sidebar.tsx` needed no
edit — their errors were cascades of `store.ts:22` and cleared with it.

### One known-red build, assigned

`tsc --noEmit -p tsconfig.node.json` reports exactly one error, and it is deliberate:

```
src/preload/index.ts(4,7): error TS2739: Type '{ backendUrl: ...; backendStatus: ...; platform: ...; onMenu: ... }'
  is missing the following properties from type 'PersonalOSApi': popout, bus, shortcuts, closeSelf, minimizeSelf
```

`PersonalOSApi` now declares the pop-out surface as **required**, because making the five members
optional would force `window.os.popout?.open(...)` at every call site in the electron slice and then a
cleanup pass. The fix is `src/preload/index.ts` implementing the bridge — ~35 mechanical lines,
assigned to **electron (Wave 1)**, which is why electron's preload work is scheduled first and alone.
`tsconfig.web.json` (all renderer + shared code) is clean now and must stay clean at every wave boundary.

---

## 1. File ownership

Exhaustive. Every file that will be created or edited appears **exactly once**. The owner is the only
agent that may open that file, full stop — no "one small line", no drive-by import fix. If you need a
change in someone else's file, it goes in your handoff notes and the owner makes it.

### Frozen / contract owner

| Path | Wave | Note |
|---|---|---|
| `src/shared/types.ts` | 0 (done) | **FROZEN. Nobody else may edit this, including to add a type.** Need a type? Ask; it lands here first. |
| `docs/canvas-contract.md` | 0 (done) | **Nobody else may edit this.** |
| `src/preload/index.d.ts` | — | Unchanged, correct as-is. **Nobody may edit this.** |

### sessions — multi-session chat store (plan §3.1, §3.2)

| Path | New? | Note |
|---|---|---|
| `src/renderer/src/store.ts` | | The bulk. Also adds `mode`/`toggleMode()`. **Nobody else may edit this — streambus edits it in Wave 2, after sessions is merged.** |
| `src/renderer/src/styles.css` | | `.pulse` status variants + the duplicate-`@keyframes` fix. **Nobody else may edit this; canvas CSS goes in new files.** |
| `src/renderer/src/components/ChatPulse.tsx` | new | **Nobody else may edit this.** |
| `src/renderer/src/components/ChatView.tsx` | | gains optional `conversationId` prop. **Nobody else may edit this.** |
| `src/renderer/src/components/Composer.tsx` | | **Nobody else may edit this.** |
| `src/renderer/src/components/ContextDrawer.tsx` | | **Nobody else may edit this.** |
| `src/renderer/src/components/Message.tsx` | | passes `message.conversation_id` down to ToolEvents. **Nobody else may edit this.** |
| `src/renderer/src/components/ToolEvents.tsx` | | `approveTool(id, decision, conversationId)`. **Nobody else may edit this.** |
| `src/renderer/src/components/Sidebar.tsx` | | pulse → `<ChatPulse>`. **widgets adds drag sources here in Wave 3, after sessions is merged.** |
| `src/renderer/src/components/ProjectView.tsx` | | third pulse copy → `<ChatPulse>`. **widgets edits it in Wave 3, after sessions is merged.** |

Not touched, contrary to plan §3.1: `MemoryPanel.tsx`, `TraceView.tsx`, `ToolPermissions.tsx` — verified,
none of them reads `active`/`activeId`/`streaming`.

### datalayer — canvas + notes persistence (plan §6)

| Path | New? | Note |
|---|---|---|
| `backend/personal_os/canvas.py` | new | `canvases` + `canvas_windows`, module-level `SCHEMA`. **Nobody else may edit this.** |
| `backend/personal_os/notes.py` | new | `notes`, module-level `SCHEMA`. **Nobody else may edit this.** |
| `backend/personal_os/app.py` | | Imports + one append-only route block at EOF. **streambus rewrites the chat handlers here in Wave 2, after datalayer is merged. Nobody edits it in Wave 1 but datalayer.** |
| `src/renderer/src/lib/api.ts` | | Adds `api.canvases`, `api.windows`, `api.notes`. **streambus edits it in Wave 2, after datalayer is merged.** |
| `backend/personal_os/db.py` | | **NOT EDITED BY ANYONE.** See §12 — the plan is wrong about this. |

### electron — pop-out, bus, shortcuts, tray, menus (plan §11, §14)

| Path | New? | Wave | Note |
|---|---|---|---|
| `src/preload/index.ts` | | 1 | The `PersonalOSApi` bridge. Unblocks node typecheck. **Nobody else may edit this.** |
| `src/main/popouts.ts` | new | 1 | BrowserWindow registry, bounds persistence, gather/scatter. **Nobody else may edit this.** |
| `src/main/shortcuts.ts` | new | 1 | `globalShortcut` + failure reporting. **Nobody else may edit this.** |
| `src/main/bus.ts` | new | 1 | The `bus` relay. **Nobody else may edit this.** |
| `src/main/index.ts` | | 1 + 4 | Wave 1: `ipcMain` handler registration, the `activate` fix. Wave 4: `buildMenu()` rewrite, tray wiring. **Nobody else may edit this.** |
| `backend/personal_os/llm.py` | | 1 | `DEFAULT_SETTINGS["gatherShortcut"] = "Control+Alt+Command+Space"`. One line. **Nobody else may edit this.** |
| `src/main/tray.ts` | new | 4 | Inline base64 template icon (there is no `resources/`). **Nobody else may edit this.** |
| `src/renderer/src/PopoutSurface.tsx` | new | 4 | **Nobody else may edit this.** |
| `src/renderer/src/main.tsx` | | 4 | `?surface=widget` branch. **Nobody else may edit this.** |
| `src/renderer/src/components/SettingsModal.tsx` | | 4 | Gather-shortcut field. **Nobody else may edit this.** |

### streambus — server-side stream bus (plan §5)

| Path | New? | Note |
|---|---|---|
| `backend/personal_os/runs.py` | new | `RunBus`, and `sse()` moves here from `app.py:108`. **Nobody else may edit this.** |
| `backend/personal_os/app.py` | | Wave 2 hand-off from datalayer. Confined to: imports, module globals, `_chat_stream`, the chat route, the new `/stream` `/runs` `/conversations/{id}/stop` routes, `_shutdown`. **Must not reformat datalayer's canvas/notes block.** |
| `src/renderer/src/lib/api.ts` | | Wave 2 hand-off from datalayer. Confined to: `chatStream` signature, `api.chat`, `api.runs`, `api.stopRun`. |
| `src/renderer/src/store.ts` | | Wave 2 hand-off from sessions. Confined to: `runStream` (POST then stream), `stop` (`api.stopRun`), `Streaming` gains `runId`. |

### windowmgr — canvas plane, chrome, drag/resize, snapping, spaces (plan §7, §10, §12, §13)

| Path | New? | Note |
|---|---|---|
| `src/renderer/src/canvas/store.ts` | new | The canvas store (§5 of this doc). **Nobody else may edit this.** |
| `src/renderer/src/canvas/Canvas.tsx` | new | Plane, pan, zoom, marquee, drop handlers, `live` computation, `canvasPointFromEvent`. **Nobody else may edit this.** |
| `src/renderer/src/canvas/WindowFrame.tsx` | new | Chrome, traffic lights, ring slot, drop zone. **Nobody else may edit this.** |
| `src/renderer/src/canvas/WindowHost.tsx` | new | Resolves the widget. **Exception: in Wave 3 widgets rewrites the single function `resolveWidget()` inside it, and nothing else.** |
| `src/renderer/src/canvas/useDrag.ts` | new | Module-level `schedule()` rAF loop. **Nobody else may edit this.** |
| `src/renderer/src/canvas/snapping.ts` | new | Pure. No DOM, no React imports. **Nobody else may edit this.** |
| `src/renderer/src/canvas/snapping.test.ts` | new | `node:test`, bundled with the local esbuild. **Nobody else may edit this.** |
| `src/renderer/src/canvas/SpacesBar.tsx` | new | Space tabs; the `project → bind space` drop target. **Nobody else may edit this.** |
| `src/renderer/src/canvas/Overview.tsx` | new | **Nobody else may edit this.** |
| `src/renderer/src/canvas/Dock.tsx` | new | **Nobody else may edit this.** |
| `src/renderer/src/styles/canvas.css` | new | Every token and class in §9. Imported by `Canvas.tsx`, not by `main.tsx`. **Nobody else may edit this.** |
| `src/renderer/src/App.tsx` | | `mode === 'canvas'` branch. **Nobody else may edit this.** |
| `package.json` | | Adds `"test"` (esbuild + `node --test`). **Nobody else may edit this.** |

### widgets — registry, catalog, status ring, drag & drop (plan §8, §9)

| Path | New? | Note |
|---|---|---|
| `src/renderer/src/canvas/registry.ts` | new | `WidgetDef`, `WidgetProps`, `WIDGETS`. **Nobody else may edit this.** |
| `src/renderer/src/canvas/widgets/*.tsx` | new | One file per `WidgetKind`, twelve files. **Nobody else may edit these.** |
| `src/renderer/src/canvas/StatusRing.tsx` | new | `StatusRing`, `StatusGlyph`. **Nobody else may edit this.** |
| `src/renderer/src/canvas/useRingStatus.ts` | new | The 6 s green hold, read by the ring and the dock tile. **Nobody else may edit this.** |
| `src/renderer/src/canvas/dnd.ts` | new | `DRAG_MIME`, `writeDrag`, `readDrag`, `DropContext`. **Nobody else may edit this.** |
| `src/renderer/src/styles/widgets.css` | new | Ring + widget-body classes. Imported by `registry.ts`. **Nobody else may edit this.** |
| `src/renderer/src/components/CalendarWeek.tsx` | new | Extracted from `CalendarView.tsx` so the calendar widget and the page share it. **Nobody else may edit this.** |
| `src/renderer/src/components/CalendarView.tsx` | | Uses the extracted `CalendarWeek`. **Nobody else may edit this.** |
| `src/renderer/src/components/GraphView.tsx` | | New `paused?: boolean` prop. **Nobody else may edit this.** |
| `src/renderer/src/components/TodoItem.tsx` | | Becomes a drag source. **Nobody else may edit this.** |
| `src/renderer/src/components/Sidebar.tsx` | | Wave 3 hand-off from sessions: drag sources on chat/project/nav rows only. |
| `src/renderer/src/components/ProjectView.tsx` | | Wave 3 hand-off from sessions: drag sources on chat rows only. |

---

## 2. Frozen TypeScript

Verbatim from `src/shared/types.ts`. Import from `'@shared/types'`.

```ts
/** Every widget a canvas window can host. Source of truth for `WIDGET_KINDS` in backend/personal_os/canvas.py. */
export type WidgetKind =
  | 'chat' | 'todos' | 'calendar' | 'board' | 'note' | 'dashboard-widget'
  | 'memory' | 'graph' | 'documents' | 'recap' | 'project' | 'usage'

export type WindowState = 'normal' | 'minimized' | 'maximized' | 'popped'
export type SnapMode = 'off' | 'grid' | 'guides' | 'both'

/** Canvas-space rect in canvas points. Never screen pixels: those are PopoutBounds. */
export interface Rect { x: number; y: number; w: number; h: number }

/** Screen bounds of a detached window: Electron's Rectangle plus the Display.id it was last seen on. */
export interface PopoutBounds { x: number; y: number; width: number; height: number; display?: number }

export interface CanvasWindow {
  id: string; canvas_id: string; kind: WidgetKind; ref_id: string | null
  project_id: string | null
  /** '' = derive the title from the underlying object */
  title: string
  x: number; y: number; w: number; h: number; z: number
  state: WindowState
  /** bounds to restore when un-maximizing */
  restore_bounds: Rect | null
  popout_bounds: PopoutBounds | null
  /** 0 | 1 — SQLite has no boolean. Always-on-top while popped. */
  pinned: number
  /** Window alpha while popped out, 0.2..1. 1 is opaque; the canvas ignores it. */
  opacity: number
  config: Record<string, unknown>
  created_at: number; updated_at: number
}

export interface Canvas {
  id: string; name: string; project_id: string | null; position: number
  snap_mode: SnapMode; grid_size: number; zoom: number; pan_x: number; pan_y: number
  wallpaper: string
  /** 0 | 1 — SQLite has no boolean. 1 freezes the view: no pan, no zoom, no window geometry. */
  locked: number
  created_at: number; updated_at: number
  windows: CanvasWindow[]
}

/** One row of the bulk `PUT /canvases/{id}/layout` body; every field but `id` is optional. */
export interface WindowLayout { id: string; x?: number; y?: number; w?: number; h?: number; z?: number; state?: WindowState }

export interface Note { id: string; project_id: string | null; body: string; color: string; created_at: number; updated_at: number }

export type DragKind = 'conversation' | 'todo' | 'document' | 'memory' | 'board-card' | 'project' | 'widget' | 'note' | 'file' | 'nav'

export interface DragPayload {
  kind: DragKind
  /** The underlying object's id. For kind 'nav' this is a WidgetKind; for 'file' it is ''. */
  id: string
  label: string
  projectId?: string | null
  /** kind 'widget' only: the dashboard the widget belongs to, since there is no GET /widgets/{id}. */
  dashboardId?: string
}

/** Run state of one chat session. Travels the cross-window bus, so it is a shared type, not a store-local one. */
export type SessionStatus = 'idle' | 'working' | 'done' | 'error' | 'needs-approval'

/** 200 body of POST /conversations/{id}/chat once the run is a background task. */
export interface ChatRunStarted {
  run_id: string
  /** seq of the last event already produced; open the stream with ?since=<seq> */
  seq: number
}

export interface RunInfo {
  run_id: string
  conversation_id: string
  message_id: string | null
  seq: number
  started_at: number
  live: boolean
}

/** 409 detail of POST /conversations/{id}/chat when that conversation already has a live run. */
export interface RunConflict { message: string; run_id: string; seq: number }

/** One detached widget window as the main process sees it. */
export interface PopoutInfo { windowId: string; bounds: PopoutBounds; pinned: boolean; opacity: number }

export interface PopoutOpenRequest { bounds?: Partial<PopoutBounds>; minWidth?: number; minHeight?: number; title?: string; pinned?: boolean; opacity?: number }

export interface PopoutChange { windowId: string; event: 'opened' | 'closed'; bounds: PopoutBounds | null }

export interface GatherState { gathered: boolean; popped: string[] }

export interface ShortcutState { accelerator: string; ok: boolean; message: string | null }

export type BusKind = 'window-bounds' | 'window-state' | 'window-config' | 'chat-status' | 'todo-changed' | 'note-changed' | 'canvas-invalidate'

/**
 * Optimistic cross-window hint relayed renderer -> main -> every other renderer.
 * The backend stays authoritative; a bus message never creates state.
 */
export interface BusMessage { kind: BusKind; windowId?: string; canvasId?: string; refId?: string; data?: Record<string, unknown> }
```

`PersonalOSApi`, as frozen:

```ts
export interface PersonalOSApi {
  backendUrl: () => Promise<string>
  backendStatus: () => Promise<{ running: boolean; url: string; error: string | null }>
  platform: NodeJS.Platform
  onMenu: (cb: (action: string) => void) => () => void
  popout: {
    open: (windowId: string, req?: PopoutOpenRequest) => Promise<boolean>
    close: (windowId: string) => Promise<boolean>
    focus: (windowId: string) => Promise<boolean>
    setPinned: (windowId: string, pinned: boolean) => Promise<boolean>
    setOpacity: (windowId: string, opacity: number) => Promise<boolean>
    setMinSize: (windowId: string, minWidth: number, minHeight: number) => Promise<boolean>
    list: () => Promise<PopoutInfo[]>
    gather: () => Promise<GatherState>
    scatter: () => Promise<GatherState>
    onChanged: (cb: (c: PopoutChange) => void) => () => void
  }
  bus: {
    send: (msg: BusMessage) => void
    on: (cb: (msg: BusMessage) => void) => () => void
  }
  shortcuts: {
    gather: () => Promise<ShortcutState>
    setGather: (accelerator: string) => Promise<ShortcutState>
    onFailure: (cb: (s: ShortcutState) => void) => () => void
  }
  /** Closes the BrowserWindow this renderer lives in: the Cmd-W fall-through when no canvas window has focus. */
  closeSelf: () => void
  minimizeSelf: () => void
}
```

Plus, elsewhere in the same file: `TodayDashboard` (was the second `Dashboard`), and
`Settings.gatherShortcut: string`, inserted immediately after `theme`.

### Conflicts resolved, and why

| Conflict | Resolution | Reason |
|---|---|---|
| `WidgetKind`, `WindowState`, `SnapMode`, `Rect`, `CanvasWindow`, `Canvas`, `Note` proposed 2–3× identically | Declared once, in the `// Canvas Mode` block. | Nothing to choose between them. |
| `popout_bounds: (Rect & { display?: number })` (datalayer, windowmgr, widgets) vs. `PopoutBounds {x,y,width,height}` (electron) | **`PopoutBounds`.** `Rect` is canvas-space-only and its JSDoc now says so. | Electron speaks `{x,y,width,height}` in `Rectangle`, `getBounds`, `setBounds`, `Display.workArea`. Two rect shapes in one JSON column is a guaranteed silent bug, and the canvas never reads `popout_bounds`. |
| `SessionStatus` in `store.ts` (sessions) vs. in `types.ts` (windowmgr, widgets, electron) | **`types.ts`**, re-exported from `store.ts` so `import { useSessionStatus, type SessionStatus } from '../store'` keeps working. | It is a plain string union that rides `BusMessage { kind: 'chat-status' }` across the IPC boundary, which makes it a shared type by sessions' own test. `ChatSession` and `Streaming` stay in `store.ts` — sessions is right that a live `AbortController` must never appear in the shared contract. |
| `popout.list(): Promise<string[]>` (electron) vs. the required symbol `PopoutInfo` | **`Promise<PopoutInfo[]>`.** | The canvas ghost placeholder needs `pinned` and `bounds` to draw itself and to render a working pin toggle; ids alone force a second round trip per window. `GatherState.popped` stays `string[]` — it really is just a set of ids. |
| `Dashboard` declared twice | Today payload → **`TodayDashboard`**; the widget dashboard keeps `Dashboard`. | The two interfaces were declaration-merging into one 15-field type, which is why `api.dashboard()` type-checked against `.widgets` and `api.dashboards.get()` against `.gmail`. |
| `useCanvas` (widgets) vs. `useCanvasStore` (electron) | **`useCanvas`.** No alias. | One name, so `tsc` enumerates every call site if it ever changes. |
| `src/renderer/src/styles.css` claimed by sessions (pulse rules) and by windowmgr (canvas materials) | **sessions.** windowmgr gets new `src/renderer/src/styles/canvas.css`; widgets gets new `src/renderer/src/styles/widgets.css`. | sessions lands in Wave 1 with nothing else in flight and genuinely needs `.pulse` variants plus the duplicate-`@keyframes` fix. Canvas CSS has no reason to live in a 950-line legacy file. Each new file is imported by its own entry module (`Canvas.tsx`, `registry.ts`), so nobody has to touch `main.tsx`. |
| `backend/personal_os/app.py` claimed by datalayer and streambus; `api.ts` by datalayer, streambus and (as `api.canvas.windows`) electron; `store.ts` by sessions and streambus | **Sequenced, not shared.** See §11. | `Edit` is a read-modify-write of the whole file; two agents editing one file in the same wave silently clobber each other. There is no safe parallel answer, so these files have one owner per wave. |
| `api.canvas.windows.*` (electron) vs. `api.windows.*` (datalayer, windowmgr) | **`api.windows.*`.** | Two of three specs say `api.windows`, and it mirrors the route `/windows/{id}`. |

---

## 3. HTTP routes

### 3.1 New — canvas (datalayer)

| Route | Request | Response |
|---|---|---|
| `GET /canvases` | — | `Canvas[]`, ordered by `(position, created_at)`, each with `windows` ordered by `(z, created_at)`. Seeds `"Desk 1"` when the table is empty and returns it, so the client never handles an empty list. 2 queries, not N+1. |
| `GET /canvases/{id}` | — | `Canvas` \| 404 |
| `POST /canvases` | `{ name?: string = "Desk", project_id?: string \| null, copy_from?: string \| null }` | `Canvas`. `position = COALESCE(MAX(position),-1)+1`. `copy_from` copies `snap_mode/grid_size/zoom/pan_x/pan_y/wallpaper` (never `locked`: a copy starts unlocked) and duplicates every window (fresh ids, same geometry and `z`, `config` copied, `state` forced `'normal'`, `popout_bounds` dropped). 404 on unknown `copy_from`. |
| `PUT /canvases/{id}` | `{ name?, project_id?, position?, snap_mode?, grid_size?, zoom?, pan_x?, pan_y?, wallpaper?, locked?: boolean, clear_project?: boolean }` | `Canvas` \| 404. 400 if `snap_mode ∉ SNAP_MODES`. `clear_project: true` unbinds (the `todos.py` `clear_*` convention; a bare `project_id: null` is dropped by `exclude_none`). |
| `DELETE /canvases/{id}` | — | `{ ok: true }`, always 200. `canvas_windows` go via `ON DELETE CASCADE`. |
| `POST /canvases/{id}/windows` | `{ kind: WidgetKind, ref_id?: string \| null, project_id?: string \| null, title?: string = "", x?: number = 0, y?: number = 0, w?: number = 520, h?: number = 640, config?: object = {} }` | `CanvasWindow`. 404 unknown canvas, 400 `kind ∉ WIDGET_KINDS`. `z = COALESCE(MAX(z),-1)+1` within that canvas. |
| `PUT /canvases/{id}/layout` | `{ windows: WindowLayout[] }` | `{ ok: true, updated: number }` |
| `GET /windows/{wid}` | — | `CanvasWindow` \| 404 |
| `PUT /windows/{wid}` | `{ title?, config?, state?, pinned?, opacity?, x?, y?, w?, h?, z?, canvas_id?, restore_bounds?, popout_bounds?, clear_restore_bounds?, clear_popout_bounds? }` | `CanvasWindow` \| 404. **`config` is MERGED** into the stored object (the `Conversations.update` settings pattern) so `onConfig(patch)` never wipes sibling keys; send `{}` to no-op and use the `clear_*` flags to null the bounds. 400 if `state ∉ WINDOW_STATES`. `canvas_id` moves the window between spaces and re-bases `z` to `MAX(z)+1` in the destination. |
| `POST /windows/{wid}/raise` | — | `CanvasWindow` \| 404. `z = MAX(z)+1` within the window's canvas, no-op when already topmost. |
| `DELETE /windows/{wid}` | — | `{ ok: true }` |

`PUT /canvases/{id}/layout` transaction semantics, frozen because two renderers write it:
one connection from `Database.tx()`, one implicit deferred transaction, one COMMIT. Validation
(`state ∈ WINDOW_STATES`) happens **before** the transaction opens, so a bad payload is a 400 with
nothing written. Per entry the fields are filtered to `(x,y,w,h,z,state)`, then
`UPDATE canvas_windows SET …, updated_at=? WHERE id=? AND canvas_id=?` — the `canvas_id` guard makes a
stale or foreign id a silent no-op (rowcount 0) rather than a cross-space move. Entries with no
updatable field are skipped. All rows share **one** `now()`, so a batch is identifiable by its
`updated_at`, and `UPDATE canvases SET updated_at=?` runs once at the end. Any raise rolls the whole
batch back, so a caller never sees a half-applied drag. `updated` is the summed rowcount, which is how
the client learns a window was deleted by another renderer. WAL is single-writer: a concurrent write
from a pop-out waits on sqlite3's default 5 s busy timeout.

### 3.2 New — notes (datalayer)

| Route | Request | Response |
|---|---|---|
| `GET /notes?project_id=all\|personal\|<id>&q=` | — | `Note[]`, `updated_at DESC`. `all` and a missing param both mean every scope, matching `GET /todos`. `q` is a `LIKE` on `body`. |
| `POST /notes` | `{ body?: string = "", color?: string = "yellow", project_id?: string \| null }` | `Note` |
| `GET /notes/{id}` | — | `Note` \| 404 |
| `PUT /notes/{id}` | `{ body?, color?, project_id?, clear_project? }` | `Note` \| 404 |
| `DELETE /notes/{id}` | — | `{ ok: true }`. Also deletes every `canvas_window` with `kind='note' AND ref_id=<id>`, since `ref_id` carries no foreign key. |

### 3.3 Changed — chat (streambus)

| Route | Request | Response |
|---|---|---|
| `POST /conversations/{id}/chat` | `ChatIn { content?: string, model?: string }` — unchanged | **`ChatRunStarted`** (200). 404 if the conversation is gone. 409 with `detail: RunConflict` if that conversation already has a live run. **No longer `text/event-stream`.** |
| `GET /conversations/{id}/stream?since=<int=0>` | — | `text/event-stream`, `Cache-Control: no-cache`, `X-Accel-Buffering: no`. Replays every buffered event with `seq > since`, then streams live, then ends when the run ends. `: keepalive\n\n` every 15 s of silence. Ends immediately with an empty body when there is no current-or-recent run. |
| `GET /runs` | — | `RunInfo[]` — live runs only, newest first. |
| `POST /conversations/{id}/stop?run_id=<optional>` | — | `{ ok: boolean }`. New. Covers the window between `POST /chat` returning and `assistant_message`, where no message id exists yet. `ok: false` when there is no live run or `run_id` does not match. |
| `POST /messages/{mid}/stop` | — | `{ ok: boolean }` — unchanged. |
| `POST /approvals/{call_id}` | `{ decision }` | `{ ok: true }` — unchanged. |

The SSE event names and payload dicts are unchanged, so the `ChatEvent` union in `types.ts` is
unchanged: `user_message`, `assistant_message`, `removed_message`, `title`, `delta`, `tool_call`,
`tool_result`, `span`, `done`, `learned`, `learn_error`, `error`. Ring buffer **2000** events per
conversation, `QUEUE_MAX` **1000** per subscriber — the `QUEUE_MAX < RING` invariant is what lets an
overflowed subscriber reconnect without a gap. `sse()` moves from `app.py:108` into `runs.py`.

`GET /events?since=<seq>` is the app-wide topic beside the per-conversation ones: background work
that outlives the run that queued it. Auto-learn is the only producer so far, and it emits the same
`learned` / `learn_error` payloads plus the `conversation_id` and `message_id` they belong to. The
stream never ends, each event carries its seq as the SSE `id`, and the ring holds **200** events, so
a window that reconnects resumes at its last seq instead of missing what happened while it was away.

### 3.4 Unchanged routes the canvas consumes

Read-only callers. Nobody changes these.

`GET /todos?project_id=all&include_done=true&q=` · `PUT /todos/{id}` · `POST /todos` ·
`GET /memories?project_id=all&include_global=false&q=` · `PUT /memories/{id} { pinned: true }` ·
`GET /documents?project_id=all&include_global=false` · `GET /documents/{id}` · `POST /documents` (FormData) ·
`GET /graph?project_id=<scope>&include_global=false` · `GET /boards/{id}` · `POST /boards/{id}/cards` ·
`POST /boards/cards/{cid}/move` · `GET /integrations/google/calendar?days=&start=` ·
`GET /dashboards/{id}` · `POST /widgets/{id}/refresh?regenerate=false` · `GET /widgets/{id}/render` ·
`GET /recap?force=` · `GET /usage?days=` · `PUT /projects/{id} { system_prompt }` · `GET /settings` · `PUT /settings`.

Note the near-collision: **`/widgets/{wid}` is the AI dashboard widget** (`app.py:1187`) and
**`/windows/{wid}` is a canvas window**. `api.ts` must carry a one-line `/** */` on each of
`api.widgets` and `api.windows` saying which is which.

---

## 4. IPC channels

All renderer-facing access goes through `window.os`; nobody imports `ipcRenderer` outside
`src/preload/index.ts`.

| Channel | Direction | Args | Returns |
|---|---|---|---|
| `popout:open` | invoke | `(windowId: string, req?: PopoutOpenRequest)` | `boolean` |
| `popout:close` | invoke | `(windowId: string)` | `boolean` |
| `popout:focus` | invoke | `(windowId: string)` | `boolean` |
| `popout:set-pinned` | invoke | `(windowId: string, pinned: boolean)` | `boolean` |
| `popout:set-opacity` | invoke | `(windowId: string, opacity: number)` | `boolean` — clamped to `[0.2, 1]`; `false` when that window is not popped out |
| `popout:set-min-size` | invoke | `(windowId: string, minWidth: number, minHeight: number)` | `boolean` |
| `popout:list` | invoke | `()` | `PopoutInfo[]` |
| `popout:gather` | invoke | `()` | `GatherState` |
| `popout:scatter` | invoke | `()` | `GatherState` |
| `popout:changed` | main → main renderer | `PopoutChange` | — |
| `bus` | renderer → main | `BusMessage` | — |
| `bus` | main → every **other** live webContents, main window included, sender and destroyed contents skipped | `BusMessage` | — |
| `shortcuts:gather` | invoke | `()` | `ShortcutState` |
| `shortcuts:set-gather` | invoke | `(accelerator: string)` | `ShortcutState`. The calling renderer also persists it with `PUT /settings { gatherShortcut }`. |
| `shortcuts:failed` | main → main renderer | `ShortcutState` with `ok: false` and a human message | — |
| `window:close-self` | renderer → main | — | closes the sender's BrowserWindow |
| `window:minimize-self` | renderer → main | — | minimizes the sender's BrowserWindow |
| `menu` | main → renderer | `action: string` | existing channel, see below |

Menu actions newly routed over the existing `sendMenu` → `window.os.onMenu`. **These exact strings:**

`toggle-mode` · `canvas:new-space` · `canvas:prev-space` · `canvas:next-space` ·
`canvas:space:1` … `canvas:space:9` · `canvas:overview` · `canvas:tidy` · `canvas:popout` ·
`canvas:unpopout` · `canvas:pin` · `close-window` · `minimize-window`

`store.ts` (sessions) handles `toggle-mode` only. `canvas/store.ts` (windowmgr) registers its **own**
`window.os.onMenu` listener for the `canvas:*`, `close-window` and `minimize-window` cases —
`ipcRenderer.on` supports multiple listeners, so no cross-slice edit is needed and there is no import
cycle between the two stores.

Accelerators, corrected from plan §14 (electron owns these): ⌃← / ⌃→ / ⌃↑ are taken by macOS
(Move space left/right, Mission Control) and an app-menu accelerator loses to an enabled system
shortcut, so use **⌥⌘← / ⌥⌘→ / ⌥⌘↑** (`Alt+Command+Left|Right|Up`). ⌃1…⌃9 are free and kept.
Gather default **⌃⌥⌘Space** = `Control+Alt+Command+Space`, free. ⌘W and ⌘M must become plain items
with `click` handlers — `{ role: 'close' }` (`index.ts:68`) and `role: 'windowMenu'` cannot be
intercepted, so the Window menu is built explicitly.

Renderer URL contract for a pop-out: `?surface=widget&window=<canvasWindowId>` —
dev `${ELECTRON_RENDERER_URL}/?surface=widget&window=<id>`, packaged
`loadFile('../renderer/index.html', { query: { surface: 'widget', window: id } })`.

---

## 5. Canvas store — public interface

`src/renderer/src/canvas/store.ts`, owned by windowmgr, consumed by widgets and electron. Frozen:
widget code compiles against these signatures.

```ts
export interface CanvasState {
  canvases: Record<string, Canvas>
  /** canvas ids in `position` order */
  order: string[]
  activeCanvasId: string | null
  focusedWindowId: string | null
  overview: boolean
  /** true during any drag, resize, zoom or space switch: drops backdrop-filter */
  interacting: boolean
  loaded: boolean

  load: () => Promise<void>

  newSpace: (name?: string, copyFrom?: string | null) => Promise<void>
  renameSpace: (canvasId: string, name: string) => Promise<void>
  deleteSpace: (canvasId: string) => Promise<void>
  setActiveCanvas: (canvasId: string) => void
  gotoSpace: (n: number) => void
  nextSpace: () => void
  prevSpace: () => void
  toggleOverview: () => void
  bindSpace: (canvasId: string, projectId: string | null) => Promise<void>
  setSnap: (canvasId: string, patch: { snap_mode?: SnapMode; grid_size?: number }) => Promise<void>
  /**
   * Freeze or release the space. A locked space keeps its pan, its zoom and every window's geometry;
   * widgets stay interactive, but nothing on it can be moved, resized, added, closed or deleted. The
   * store is where that is enforced — `openWindow`, `closeWindow`, `setWindowState`,
   * `moveWindowToCanvas`, `popOut`, `markLayoutDirty`, `setViewport`, `tidyUp` and `deleteSpace` all
   * refuse — and the UI only mirrors it (no grab cursor, no resize handles, no drop ghost).
   */
  setLocked: (canvasId: string, locked: boolean) => Promise<void>
  /** ⌃⌘L and the bar's padlock: flips the active space's lock. */
  toggleLock: () => void
  /** Local + 600 ms debounced PUT /canvases/{id}. */
  setViewport: (canvasId: string, v: { zoom?: number; pan_x?: number; pan_y?: number }) => void

  openWindow: (kind: WidgetKind, refId?: string | null, at?: { x: number; y: number }, config?: Record<string, unknown>) => Promise<CanvasWindow | null>
  closeWindow: (windowId: string) => Promise<void>
  /** Local focus + POST /windows/{id}/raise so z stays authoritative across renderers. */
  focusWindow: (windowId: string) => void
  /** Local optimistic merge into one window row. Never writes to the backend. */
  patchWindow: (windowId: string, patch: Partial<CanvasWindow>) => void
  setWindowState: (windowId: string, state: WindowState) => Promise<void>
  setWindowTitle: (windowId: string, title: string) => Promise<void>
  /** Merges server-side; safe to call with one key. */
  setWindowConfig: (windowId: string, patch: Record<string, unknown>) => Promise<void>
  setWindowPinned: (windowId: string, pinned: boolean) => Promise<void>
  setWindowOpacity: (windowId: string, opacity: number) => Promise<void>
  moveWindowToCanvas: (windowId: string, canvasId: string) => Promise<void>
  /** Mark dirty; the 400 ms debounce flushes through PUT /canvases/{id}/layout. Call on pointerup, never mid-drag. */
  markLayoutDirty: (windowIds: string[]) => void
  flushLayout: () => Promise<void>
  tidyUp: () => void
  setInteracting: (v: boolean) => void

  popOut: (windowId: string) => Promise<void>
  returnToCanvas: (windowId: string) => Promise<void>
  popOutFocused: () => Promise<void>
  togglePinFocused: () => Promise<void>
  closeFocused: () => Promise<void>
  minimizeFocused: () => Promise<void>
}

export const useCanvas: UseBoundStore<StoreApi<CanvasState>>

export const useActiveCanvas = (): Canvas | null
export const useWindows = (): CanvasWindow[]
export const useWindow = (windowId: string): CanvasWindow | undefined
export const useIsFocused = (windowId: string): boolean
```

Two rules, both load-bearing:

- **Windows live only inside `canvases[id].windows`.** There is no parallel `windows` array. Every
  selector returns either a primitive or an object already in state, so zustand v5's default
  `Object.is` comparison is safe. Never build a fresh object or array inside a selector.
- **Geometry is written on `pointerup`, never during a drag,** and only through `markLayoutDirty` +
  the 400 ms debounce. `patchWindow` is local-only by design.

Renderer-store dependencies (sessions owns them, Wave 1): `mode: 'classic' | 'canvas'`,
`toggleMode()`, `useSession(convId?)`, `useConversation(convId?)`, `useSessionStatus(convId?)`,
`useIsStreaming(convId?)`, `useStreamingMessageId(convId?)`, and `send` / `stop` / `regenerate` /
`setChatModel` / `setChatSettings` / `renameChat` all taking an optional trailing `conversationId`.
Canvas mode loads each shared dataset once at the widest scope — `loadScope('all')` and
`refreshTodos('all', true)` — and every widget filters client-side, so two windows with different
scope configs cannot thrash the same fetch.

---

## 6. Widget contract

`src/renderer/src/canvas/registry.ts`, owned by widgets.

```ts
export interface WidgetDef {
  kind: WidgetKind
  label: string
  /** An element, not a component — matches Sidebar's NAV, MemoryPanel's MODES, ProjectView's TABS. */
  icon: JSX.Element
  defaultSize: { w: number; h: number }
  minSize: { w: number; h: number }
  /** 'minimal' = title bar with a close button only (the note) */
  chrome: 'full' | 'minimal'
  /** wants a status ring (chat does; a note does not) */
  statusful?: boolean
  /** counts against the 6-slot concurrent-live cap: iframes, d3, pollers */
  heavy?: boolean
  /** cannot open without a ref_id: chat, board, note, dashboard-widget, project */
  needsRef?: boolean
  defaultConfig?: Record<string, unknown>
  /** drag payload kinds this widget accepts as a drop target */
  accepts?: DragKind[]
  Component: React.FC<WidgetProps>
}

export interface WidgetProps {
  window: CanvasWindow
  focused: boolean
  /** false when off-screen, minimized, below 60% zoom, or over the heavy cap: stop polling, unmount iframes */
  live: boolean
  onConfig: (patch: Record<string, unknown>) => void
  onTitle: (t: string) => void
}

export const WIDGETS: Record<WidgetKind, WidgetDef>
```

`WIDGETS`' keys must stay identical to `WIDGET_KINDS` in `backend/personal_os/canvas.py`, which 400s an
unknown kind. **The TS union in `types.ts` is the source of truth; the Python tuple is its mirror.**

Catalog, with the per-kind decisions already taken:

| kind | default | min | chrome | statusful | heavy | needsRef | config |
|---|---|---|---|---|---|---|---|
| `chat` | 520×640 | 360×320 | full | ✓ | | ✓ | — |
| `todos` | 380×520 | 280×240 | full | | | | `{ scope, includeDone, q }` |
| `calendar` | 640×520 | 320×280 | full | | ✓ | | `{ mode: 'agenda' \| 'day' \| 'week', days }`, default `'agenda'` |
| `board` | 760×560 | 420×320 | full | | | ✓ | `{ column_id? }` |
| `note` | 300×300 | 200×160 | minimal | | | ✓ | — |
| `dashboard-widget` | 420×340 | 280×200 | full | | ✓ | ✓ | `{ dashboard_id }` |
| `memory` | 400×520 | 280×240 | full | | | | `{ scope }` |
| `graph` | 640×560 | 360×320 | full | | ✓ | | `{ scope }` |
| `documents` | 400×480 | 280×240 | full | | | | `{ scope }` |
| `recap` | 420×360 | 280×220 | full | | | | — |
| `project` | 360×420 | 280×240 | full | | | ✓ | `{ tab }` |
| `usage` | 560×420 | 360×280 | full | | ✓ | | `{ days }` |

Widget notes that are decisions, not suggestions:

- `dashboard-widget` resolves itself from `config.dashboard_id` via `GET /dashboards/{id}`. There is no
  `GET /widgets/{id}` (app.py has only PUT/POST/DELETE plus `/render`).
- `project` reads the already-loaded `projects` array via `useProject(id)` plus `s.conversations`.
  `GET /projects/{id}` does not exist.
- `memory` and `documents` render their own compact rows over the shared arrays. They do not wrap
  `MemoryView` / `DocumentsView`, whose add rows, scope help and page modals do not fit a widget.
- `graph` gets two levers: `paused={!focused}` genuinely stops the d3 simulation while keeping
  positions in its ref, and `!live` unmounts in favour of the proxy card. Only the second is in the plan.
- `chat` keeps a 24 px `WidgetBar` holding **only** `ModelPicker`. Dropping the whole header would
  remove the only way to change a window's model, which is a regression against classic.
- `chat` **adds** ⌘↵ to send. Plain Enter still sends, because classic must not change.
- Below 60 % zoom every widget renders its proxy card, which is also what Overview needs.

---

## 7. Drag and drop

```ts
export const DRAG_MIME = 'application/x-personal-os'
```

`src/renderer/src/canvas/dnd.ts` (widgets) exports `DRAG_MIME`, `writeDrag(dt, payload)` and
`readDrag(dt): DragPayload | null`. Every source writes the JSON `DragPayload` on `DRAG_MIME` **and** a
`text/plain` mirror of `payload.label`, so a payload also drops usefully into a textarea. Unknown or
unparseable payloads are ignored silently — never thrown.

| Drag | Onto the canvas plane | Onto a widget |
|---|---|---|
| `conversation` | opens a `chat` window | — |
| `nav` (`id` is a `WidgetKind`) | opens that widget | — |
| `todo` | opens a `todos` window | `board` → `POST /boards/{id}/cards`; `chat` → quoted into that chat's composer draft |
| `document` | opens a `documents` window | `chat` → appends a `read_document` instruction to that chat's composer draft |
| `memory` | opens a `memory` window | `chat` → `PUT /memories/{id} { pinned: true }` |
| `board-card` | opens a `note` with its text (`POST /notes`) | `todos` → `POST /todos { title }` |
| `project` | opens a `project` window | a `SpacesBar` tab → `bindSpace(canvasId, projectId)` |
| `file` | uploads via `uploadDocuments` + opens `documents` | `chat` → uploads and appends to the draft; `documents` → uploads |
| `widget` | opens a `dashboard-widget` with `config.dashboard_id = payload.dashboardId` | — |
| `note` | opens a `note` window | — |

`project` had two conflicting effects in plan §9; resolved **by target**, not by payload — the plane
opens a window, a `SpacesBar` tab binds the space.

`'document' → chat` and `'memory' → chat` have no per-conversation backend, so they are implemented as
the nearest real actions above. Flagged in §12.

Drop feedback: the target window gets `.drop-target` (border → accent, 2 px lift) and the plane draws
`.drop-ghost` at the snapped landing rect.

---

## 8. SessionStatus state machine

Owned by sessions in `runStream`. It is a pure reducer over the `ChatEvent` union
(`types.ts`) against the emitter at `app.py:269-466`, which is exactly why it is the one thing in this
plan worth a unit test.

Entry (per run): `streaming = { messageId: null, abort }`, `status = 'working'`, `finishedAt = null`,
`pendingApprovals = 0`, `touchedAt = Date.now()`, and `clearHold(convId)` — a new run cancels a green
hold still pending from the previous one.

| Event | State effect | Status |
|---|---|---|
| `user_message` | append to `conversation.messages` | `working` (unchanged) |
| `assistant_message` | append; `streaming.messageId = ev.data.id`; `unread++` **iff** this conversation is not focused | unchanged |
| `title` | `conversation.title` | unchanged |
| `removed_message` | filter the message out (regenerate, `app.py:288`) | unchanged |
| `delta` | `content += text` | unchanged — the hot path does **no** recount and **no** unread bump |
| `tool_call` | append a pending `ToolEvent`; if `needs_approval` then `settleApprovals` | → `needs-approval`, `pendingApprovals = 1..n` |
| `tool_result` | replace the tool event, `pending: false`; `settleApprovals` | at 0 pending, `needs-approval` → `working` (because `streaming` is still set) |
| `span` | merge-or-append by span id | **unchanged, deliberately.** A span can still land beside `done`; touching status here would resurrect `working`. |
| `done` | fill `error`/`context_used`/`tool_events`/`trace`; `finish(convId, error ? 'error' : 'done')`; `refreshConversations()` | `done` or `error`. `stopped === true` is treated as `done`. |
| `learned` | toast + `refreshAll()` when anything came back | unchanged (already `done`). On this stream only the `remember` tool reaches it; auto-learn arrives on `/events`, handled the same way. |
| `learn_error` | toast | unchanged — a failed extraction must not redden a reply that succeeded |
| `error` | toast; `finish(convId, 'error')` | `error`, no hold timer |
| `catch` (fetch threw) | if `!abort.signal.aborted`: toast + `finish(convId, 'error')` | an aborted signal is a user Stop, not an error |
| `finally` | abort-identity guard (today's `store.ts:188`), then clear `streaming` | `working`/`needs-approval` → `idle`; `done`/`error` preserved |

Clearing:

- `done` starts a **6000 ms** hold; when it fires, `done` → `idle` and `finishedAt` → `null`.
- `clearSessionStatus(convId)` (called on focus) clears `unread`, bumps `touchedAt`, and maps
  `done`/`error` → `idle`. It deliberately does **not** clear `needs-approval`: looking at a blocked
  chat does not answer its card.
- `error` has no timer; it clears on focus or `clearSessionStatus`.
- `approveTool` calls `settleApprovals` optimistically, so the ring leaves amber the instant the card
  is answered rather than when the tool finishes — an external action can take seconds.

Ring rendering (widgets, `useRingStatus`): `idle` none · `working` amber `--tl-yellow`, 1.6 s breathing
0.55↔1.0, spinner glyph · `done` green `--tl-green` solid, check glyph · `error` red `--tl-red` solid,
`!` glyph · `needs-approval` accent, 1 s pulse, lock glyph + count badge. The 6 s green hold is measured
in `useRingStatus` from `finishedAt`, not in the store, so the window ring and the dock tile agree and a
focus clears both at once. Honours `prefers-reduced-motion` — pulses become static fills.

Sessions also owns `ChatSession` (store-local, never shared — it holds a live `AbortController`):

```ts
export interface Streaming { messageId: string | null; abort: AbortController }

export interface ChatSession {
  conversation: Conversation
  streaming: Streaming | null
  status: SessionStatus
  /** epoch ms the last run finished; drives the 6 s green hold */
  finishedAt: number | null
  /** tool calls waiting on the approval card */
  pendingApprovals: number
  /** assistant replies that landed while this session was not focused */
  unread: number
  /** epoch ms of the last focus or run; only used to pick eviction victims */
  touchedAt: number
}
```

`MAX_SESSIONS = 12`, LRU eviction by `touchedAt`, never dropping the focused session, one with a live
run, or one with unread replies. `activeId` becomes the real field `focusedConversationId`; `active`
becomes `useConversation()` / `selectActive(s)`. There is no `active` or `activeId` key on `State`
afterwards — deliberately, so `tsc` enumerates every call site.

In Wave 2 streambus changes exactly two things here: `runStream` becomes
`const { run_id, seq } = await api.chat(convId, body)` then `for await (… of chatStream(convId, seq, abort.signal))`,
and `stop`'s `else st.abort.abort()` becomes `await api.stopRun(convId, st.runId)` — under the bus,
aborting the fetch only detaches a viewer. `Streaming` gains `runId: string`.

---

## 9. CSS contract

windowmgr defines these in `src/renderer/src/styles/canvas.css`. Widget authors may use every token
and every class listed here, and must not redefine them.

Tokens on `:root`, with light-theme overrides under `:root[data-theme='light']` and the
`@media (prefers-color-scheme: light) :root[data-theme='system']` block that `styles.css` already uses:

```
--win-radius --win-radius-sm
--material-thick --material-thin --material-opaque --material-blur
--win-border --win-highlight
--shadow-window --shadow-focused
--tl-red --tl-yellow --tl-green --tl-off
--spring --dur-window --dur-snap --dur-hover
--grid-dot --guide --zone-fill --zone-border
--dock-tile --ring-width
```

Classes:

- plane — `.canvas-root` `.canvas` `.canvas.interacting` `.canvas-plane` `.canvas-grid`
  `.canvas-drag-strip` `.canvas-guides` `.guide-v` `.guide-h` `.gap-pill` `.zone-preview` `.drop-ghost`
- window — `.win` and the modifiers `.focused` `.dragging` `.resizing` `.minimized` `.maximized`
  `.popped` `.drop-target`; `.win-bar` `.win-lights` `.win-light` (`.close` `.min` `.max`) `.win-title`
  `.win-body` `.win-ghost`; resize handles `.win-resize` + `.n .s .e .w .ne .nw .se .sw`
- dock — `.dock` `.dock-tile` `.dock-tile.focused`
- spaces — `.spaces-bar` `.space-tab` `.space-tab.active` `.space-tab.drop-target`
- overview — `.overview` `.overview-space` `.overview-proxy` `.proxy-card`
- pop-out — `.popout` `.popout.focused` `.popout.tuning` `.popout.translucent` `.popout-bar` `.popout-title` `.popout-actions` `.popout-opacity` `.popout-body`
  `.popout-error`

**Every animation this slice adds gets a prefixed name** — `ring-breathe`, `ring-pulse`, `win-open`,
`win-close`, `win-min`, `guide-in`, `zone-in` — because `styles.css` already proves that an unprefixed
`@keyframes` name gets silently redefined (see §12).

widgets defines `.widget-bar`, `.ring` + `.ring.working|.done|.error|.needs-approval`, `.ring-badge`,
`.status-glyph`, `.widget-empty` and the per-widget body classes in
`src/renderer/src/styles/widgets.css`. sessions defines `.pulse.working|.done|.error|.needs-approval`
in `styles.css`, extending the existing `.pulse`.

`.canvas.interacting` is the perf lever: it swaps `--material-thick` for `--material-opaque` and drops
`backdrop-filter` during every drag, resize, zoom and space switch. Window bodies get
`contain: layout paint style`; off-viewport windows get `content-visibility: auto`;
`will-change: transform` only for the duration of a drag, never as a static style.

`-webkit-app-region`: the app is `titleBarStyle: 'hiddenInset'` and today's only drag handles are
`.sidebar-top` / `.chat-header` / `.page-header`. In canvas mode the canvas replaces those headers, so
with the sidebar collapsed there would be no way to move the app window. windowmgr renders a 28 px
`.canvas-drag-strip.drag` inset 84 px from the left (clearing the traffic lights at `{16,16}`), only
when `!sidebarOpen`, z-ordered **below** the plane so it never swallows a marquee or a window drag.

---

## 10. Snapping decisions (windowmgr, frozen because they are observable)

- Thresholds are screen pixels divided by zoom. **The grid pitch is not.** A pitch is a canvas-space
  quantity by definition, since positions persist; dividing it by zoom would make persisted coordinates
  depend on the zoom they were dragged at. At 200 % a 16 pt grid is visibly 32 screen px, and the dot
  grid's `background-size` is `grid * zoom` so what you see is what you get.
- With `snap_mode: 'both'`, grid and guides fight. Order is fixed and explicit, **per axis**: a guide
  match inside `GUIDE_PT/zoom` wins outright; failing that, equal spacing; failing that, the grid. One
  guide applied and drawn per axis.
- `zoneRect` takes `natural` explicitly (plan §7.3's bottom-centre zone needs a natural size, which
  lives in the registry). `WindowHost` passes `WIDGETS[kind].defaultSize` when available and the
  drag-start size otherwise.
- The single rAF loop is module-level state in `useDrag.ts` — an exported `schedule()` over a module
  `Set` — not a hook-local ref, or every window instance gets its own loop. It must reset defensively
  under React StrictMode's double mount.
- `snapping.ts` stays free of DOM and React imports so `snapping.test.ts` can run under `node:test`,
  bundled by the `esbuild` binary already in `node_modules/.bin`. **Do not install anything** —
  `node_modules` is a shared symlink and Node here is 20.16.0, so `--experimental-strip-types` is
  unavailable.

---

## 11. Build order

The waves exist because `Edit` is a read-modify-write of a whole file: two agents editing one file in
the same wave silently clobber each other. Within a wave, no two agents share a file. A wave ends when
`npx tsc --noEmit -p tsconfig.web.json` **and** `-p tsconfig.node.json` are both clean.

### Wave 1 — three agents in parallel

| Agent | Files | Why now |
|---|---|---|
| **sessions** | `store.ts`, `styles.css`, 8 chat components + `ChatPulse.tsx` | Must land first: every later slice reads `sessions` and `SessionStatus`, and plan §16 wants it done with nothing else in flight. Also ships `mode`/`toggleMode` so windowmgr has a toggle to hang off. |
| **datalayer** | `canvas.py`, `notes.py`, `app.py` (append-only), `api.ts` (canvas/notes client) | Pure addition — no existing behaviour changes. Everything in Waves 2-4 needs these routes. Takes `app.py` and `api.ts` first because its edits are append-only and therefore the cheapest to build on. |
| **electron (part 1)** | `preload/index.ts`, `main/popouts.ts`, `main/shortcuts.ts`, `main/bus.ts`, `main/index.ts` (ipcMain + the `activate` fix), `llm.py` | **Unblocks the node typecheck** (§0). Shares no file with the other two. `popouts.ts` only needs `PUT /windows/{id}`, which datalayer lands in the same wave, and nothing calls it until Wave 4. |

### Wave 2 — two agents in parallel, after Wave 1

| Agent | Files | Waits on |
|---|---|---|
| **windowmgr** | all of `canvas/` except `registry.ts`, `widgets/`, `StatusRing.tsx`, `useRingStatus.ts`, `dnd.ts`; `styles/canvas.css`; `App.tsx`; `package.json` | `api.canvases` / `api.windows` (datalayer), `mode` + `toggleMode` (sessions). `WindowFrame` takes `status` as an **optional** prop and `WindowHost` passes `null`, so nothing waits on widgets. |
| **streambus** | `runs.py`, `app.py` (chat handlers), `api.ts` (`chatStream`, `api.chat`, `api.runs`, `api.stopRun`), `store.ts` (`runStream`, `stop`) | Hand-off of `app.py` + `api.ts` from datalayer, and of `store.ts` from sessions. Cannot run in Wave 1 for that reason alone. Must land before electron part 2, because a popped-out chat only keeps streaming once the SSE is detached from the renderer that started it. |

### Wave 3 — one agent, after Wave 2

**widgets** — `registry.ts`, `widgets/*.tsx` (12 files), `StatusRing.tsx`, `useRingStatus.ts`, `dnd.ts`,
`styles/widgets.css`, `CalendarWeek.tsx`, and the edits to `CalendarView.tsx`, `GraphView.tsx`,
`TodoItem.tsx`, `Sidebar.tsx`, `ProjectView.tsx`, plus the single `resolveWidget()` function inside
windowmgr's `WindowHost.tsx`.

Waits on: windowmgr for `WindowFrame`/`Canvas`/`Dock`/`SpacesBar` and the canvas store (§5); sessions
for `Sidebar.tsx` and `ProjectView.tsx`; streambus for `GET /runs`, which is how a freshly opened chat
window paints its amber ring (without it, an unloaded session falls back to `idle`).

Alone in its wave because it is the only slice left touching `Sidebar.tsx` / `ProjectView.tsx` /
`WindowHost.tsx`.

### Wave 4 — one agent, after Wave 3

**electron (part 2)** — `PopoutSurface.tsx`, `main.tsx`, `main/tray.ts`, `buildMenu()` in
`main/index.ts`, `SettingsModal.tsx`.

Waits on: `WIDGETS` for the component and `minSize` (`PopoutSurface` resolves both from the registry);
the canvas store for `popOutFocused` / `returnToCanvas` / `togglePinFocused` / `closeFocused` /
`minimizeFocused` / `focusedWindowId`; `runs.py` for a chat that survives detaching.

### What cannot be parallelised, and why

- `app.py` — datalayer (Wave 1) then streambus (Wave 2). Their regions are disjoint, but the import
  block is shared and a whole-file write would lose one of them.
- `api.ts` — datalayer (Wave 1) then streambus (Wave 2).
- `store.ts` — sessions (Wave 1) then streambus (Wave 2).
- `Sidebar.tsx`, `ProjectView.tsx` — sessions (Wave 1) then widgets (Wave 3).
- `main/index.ts` — electron only, but in two visits (Wave 1 handlers, Wave 4 menus).
- `WindowHost.tsx` — windowmgr (Wave 2) then one function rewritten by widgets (Wave 3).

---

## 12. UNRESOLVED — for a human

Recon corrections that are recorded but **not** acted on by this contract. Each needs a call before or
during the wave that hits it.

1. **No test runner.** `package.json` has no `test` script and no vitest/jest, and `node_modules` is a
   shared symlink that must not be reinstalled. windowmgr's plan is `snapping.test.ts` under
   `node:test`, bundled by the local `esbuild` binary. That works for pure functions and nothing else.
   The `SessionStatus` machine (§8) is the single most testable and most destabilising piece in the
   whole plan — a pure reducer over `ChatEvent` — and it will ship untested unless a runner lands in
   Wave 1. **Recommendation: add vitest in Wave 1 and test the reducer.** Not done here because
   `package.json` belongs to windowmgr (Wave 2) and installing is forbidden.

2. **`@keyframes pulse` is declared twice in `styles.css`** — line 172 (`0.3 ↔ 1`, written for the
   sidebar dot) and line 814 (`1 ↔ 0.45`, written for `.trace-row.running .trace-bar`). The second
   silently wins for both, so the sidebar pulse and the `.thinking` dots at line 282 have been
   animating on the trace curve. sessions is told to rename the second to `trace-pulse`. `.project-dot`,
   `.seg` and `@keyframes spin` are **also** each declared twice in the same file and are *not*
   assigned to anyone. Someone should sweep that file.

3. **`done` with `stopped: true` counts as `done`, not `error`** — green ring, "just finished", after a
   user pressed Stop. Defensible (a partial answer is not a failure) but it is a judgement call someone
   may want inverted. Today's store ignores `stopped` entirely, so this is a new, stated decision.

4. **`document → chat` and `memory → chat` drops have no per-conversation backend.** Implemented as a
   `read_document` instruction appended to that chat's composer draft, and `PUT /memories/{id}
   { pinned: true }` respectively. Neither is what plan §9's "attached to context" / "pinned into
   context" describes. If per-conversation attachments are wanted, that is a new backend feature.

5. **`GET /widgets/{wid}` does not exist.** `dashboard-widget` therefore carries
   `config.dashboard_id` and resolves itself out of `GET /dashboards/{id}`. Adding
   `GET /widgets/{wid} -> Widget` is three lines in `app.py` and would let the config field go. Not
   taken because it is scope creep in datalayer's wave.

6. **macOS pop-out limitations, documented not fought:** another app's fullscreen space will not
   reliably accept overlaid windows even with `visibleOnFullScreen`, and gather lands on the current
   space instead; `setVisibleOnAllWorkspaces(true)` can be reset by the system on space changes, so it
   is re-asserted on every gather; Stage Manager rearranges windows on its own and gather is
   best-effort there.

7. **Pop-out minimum size on restore.** Main has no access to the widget registry, so a restored
   pop-out starts at a generic 280×200 floor and `PopoutSurface` reasserts the registry `minSize` on
   mount via `popout.setMinSize`. That bridge exists only for this; a cleaner fix would be persisting
   `minSize` in `config`.

8. **Space switcher placement** (plan §17.3) is still "sidebar segmented control vs. top bar". This
   contract assumes `SpacesBar.tsx` — a slim bar on the canvas — because it is also the `project → bind
   space` drop target, which a sidebar control cannot be without crossing slice boundaries. The plan's
   stated default was the sidebar.

9. **`notes.project_id` uses `ON DELETE SET NULL`, not the plan's `CASCADE`.** Deleting a project
   demotes its notes to personal scope instead of shredding them, matching `todos` and `boards`.
   Flagged because it contradicts plan §6.1 as written.

10. **`db.py` is not edited, contrary to plan §6.1.** Every feature table since the first release
    (`todos.py`, `boards.py`, `dashboards.py`) declares its own module-level `SCHEMA` and runs
    `with db.tx() as c: c.executescript(SCHEMA)` in the repo's `__init__`. `Database._migrate` also
    runs inside `Database.__init__`, *before* `Canvases(db)` / `Notes(db)` exist, so a `_migrate` entry
    for these tables would see nothing from `PRAGMA table_info`. Anything added to `canvases`,
    `canvas_windows` or `notes` after release needs a local additive `ALTER` in the owning module's
    `__init__`. Worth a comment in `canvas.py` so the next person does not add a dead `db.py` entry.

11. **`PersonalOSApi` is required-complete, so `tsconfig.node.json` is red until electron's Wave 1
    lands.** See §0. The alternative — five optional members — was rejected, but it is reversible in
    one edit if Wave 1 slips.

12. **Effort estimate correction.** Plan §5's "~150 lines in `runs.py` plus edits to two handlers"
    undercounts the handler side: two rewritten handlers (chat, shutdown), three new ones (stream,
    runs, stop), a new `_run_chat` runner, a new `CancelledError` branch in `_chat_stream`, and a
    24-site mechanical `yield` rewrite. `runs.py` itself is about 140 lines.

13. **Shutdown ordering.** The `@app.on_event("shutdown")` hook must become `async def` and
    `await bus.shutdown()` **before** `shutil.rmtree(db.data_dir / "tmp")`, since a live run's sandboxed
    `run_python` writes into that directory. The plan mentions neither. Assigned to streambus, recorded
    here because it is easy to miss in review.
