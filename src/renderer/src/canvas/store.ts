import { create } from 'zustand'
import type { Canvas, CanvasWindow, SnapMode, WidgetKind, WindowLayout, WindowState } from '@shared/types'
import { api, getBase, getToken } from '../lib/api'
import { useStore } from '../store'
import { tidyLayout, visibleRect, zoneRect, type Point, type Size, type Viewport } from './snapping'

/**
 * Canvas, space and window state. A separate store from the app store: pop-out renderers mount
 * widgets without a canvas, and the two must stay independently mountable. The dependency is one
 * way — canvas/store.ts may read the app store, never the reverse.
 */
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
  /** The last `load()` threw and nothing has loaded yet: the sidebar offers a retry instead of a blank list. */
  loadFailed: boolean

  load: () => Promise<void>

  /** Creates a space, activates it and navigates into the canvas view. */
  newSpace: (name?: string, copyFrom?: string | null) => Promise<void>
  /** Activate `canvasId` (stale or omitted: the active space, then the first) and navigate to the canvas view. Creates a space when there are none. */
  enterSpace: (canvasId?: string) => Promise<void>
  /** ⌘⇧C: into the canvas from a classic view, back to `lastClassicView` from the canvas. */
  toggleCanvas: () => Promise<void>
  /** Put a canvas the caller already created into the store; `navigate` (default) also enters it. */
  adoptSpace: (c: Canvas, navigate?: boolean) => void
  /** The first space (in `order`) bound to this project, or null. */
  spaceForProject: (projectId: string) => string | null
  renameSpace: (canvasId: string, name: string) => Promise<void>
  deleteSpace: (canvasId: string) => Promise<void>
  setActiveCanvas: (canvasId: string) => void
  gotoSpace: (n: number) => void
  nextSpace: () => void
  prevSpace: () => void
  toggleOverview: () => void
  bindSpace: (canvasId: string, projectId: string | null) => Promise<void>
  setSnap: (canvasId: string, patch: { snap_mode?: SnapMode; grid_size?: number }) => Promise<void>
  /** Local + 600 ms debounced PUT /canvases/{id}. */
  setViewport: (canvasId: string, v: { zoom?: number; pan_x?: number; pan_y?: number }) => void

  /**
   * `canvasId` defaults to the active space. A window opened in another space does not take focus,
   * and nothing navigates.
   */
  openWindow: (kind: WidgetKind, refId?: string | null, at?: { x: number; y: number }, config?: Record<string, unknown>, canvasId?: string) => Promise<CanvasWindow | null>
  /** A new conversation (project: `projectId`, or the active space's binding when undefined) in a chat window in the active space. */
  newChatWindow: (projectId?: string | null, at?: { x: number; y: number }) => Promise<CanvasWindow | null>
  /** Surface the window of `kind` on `refId` in `canvasId` — restoring it if minimized — or open one. Never navigates. */
  ensureWindow: (canvasId: string, kind: WidgetKind, refId?: string | null, config?: Record<string, unknown>, at?: { x: number; y: number }) => Promise<{ win: CanvasWindow | null; existed: boolean }>
  /** Focus the chat window holding this conversation — restoring or crossing spaces if needed — or open one. */
  openChat: (conversationId: string) => Promise<void>
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

const LAYOUT_MS = 400
const VIEWPORT_MS = 600
/** How long `interacting` stays set for a transition nobody releases, like a space switch. */
const TRANSITION_MS = 340

const EMPTY: CanvasWindow[] = []
const dirty = new Set<string>()
let layoutTimer: ReturnType<typeof setTimeout> | null = null
let transitionTimer: ReturnType<typeof setTimeout> | null = null
const viewportTimers = new Map<string, ReturnType<typeof setTimeout>>()
let listening = false

let viewportEl: HTMLElement | null = null
/** Canvas.tsx registers its plane viewport on mount; snapping needs its screen size and offset. */
export const setViewportEl = (el: HTMLElement | null): void => {
  viewportEl = el
}

let liveView: { zoom: number; pan_x: number; pan_y: number } | null = null
/**
 * Canvas.tsx drives a pan or a zoom straight to the plane node and only commits on settle, so for the
 * length of the gesture this — not the store row — is what `viewport()` reports. `null` releases it.
 */
export const setLiveViewport = (v: { zoom: number; pan_x: number; pan_y: number } | null): void => {
  liveView = v
}

/** SpacesBar's height (`.spaces-bar { flex: 0 0 34px }`): the plane sits under it. */
const SPACES_BAR_H = 34

/**
 * The plane's screen size. While Canvas is unmounted (Send to Space or a sidebar drop from a classic
 * view) there is no element to measure, so it is derived from the shell instead: the `.app` grid's
 * content column (the window minus the sidebar) and its height minus the SpacesBar. The whole window
 * is only the last resort, when there is no shell to measure.
 */
const planeSize = (): { width: number; height: number } => {
  const r = viewportEl?.getBoundingClientRect()
  if (r) return { width: r.width, height: r.height }
  const app = typeof document === 'undefined' ? null : document.querySelector<HTMLElement>('.app')
  if (app) {
    const cols = getComputedStyle(app).gridTemplateColumns.split(' ').map(parseFloat).filter(Number.isFinite)
    const width = cols.length ? cols[cols.length - 1] : app.clientWidth
    const height = app.clientHeight - SPACES_BAR_H
    if (width > 0 && height > 0) return { width, height }
  }
  return { width: window.innerWidth, height: window.innerHeight }
}

/** The active canvas as the screen sees it. Measures the shell for the plane size while Canvas is unmounted. */
export const viewport = (): Viewport => {
  const s = useCanvas.getState()
  const c = s.activeCanvasId ? s.canvases[s.activeCanvasId] : undefined
  const { width, height } = planeSize()
  return {
    zoom: liveView?.zoom ?? c?.zoom ?? 1,
    panX: liveView?.pan_x ?? c?.pan_x ?? 0,
    panY: liveView?.pan_y ?? c?.pan_y ?? 0,
    width,
    height
  }
}

/** A client point in viewport-local screen px, which is what `edgeZone` expects. */
export const viewportPoint = (e: { clientX: number; clientY: number }): Point => {
  const r = viewportEl?.getBoundingClientRect()
  return { x: e.clientX - (r?.left ?? 0), y: e.clientY - (r?.top ?? 0) }
}

let sizeOf: ((kind: WidgetKind) => Size | undefined) | null = null
/**
 * widgets' registry injects its default sizes here, because the canvas store must not import the
 * registry. Without it new windows take the backend default of 520x640.
 */
export const setDefaultSizes = (f: (kind: WidgetKind) => Size | undefined): void => {
  sizeOf = f
}

let configOf: ((kind: WidgetKind) => Record<string, unknown> | undefined) | null = null
/** The same injection for §6's `defaultConfig`: an explicit config still wins, key by key. */
export const setDefaultConfigs = (f: (kind: WidgetKind) => Record<string, unknown> | undefined): void => {
  configOf = f
}

const byZ = (a: CanvasWindow, b: CanvasWindow): number => a.z - b.z || a.created_at - b.created_at
const topZ = (ws: CanvasWindow[]): number => ws.reduce((n, w) => Math.max(n, w.z), -1)

const findWin = (s: CanvasState, id: string): CanvasWindow | undefined => {
  for (const cid of s.order) {
    const w = s.canvases[cid]?.windows.find((x) => x.id === id)
    if (w) return w
  }
  return undefined
}

const fail = (e: unknown): void => useStore.getState().toast((e as Error).message, 'error')

/**
 * The dirty ids as `PUT /canvases/{id}/layout` bodies, one per canvas. Drains ids whose window is gone
 * from every canvas: there is nothing left to write for them.
 */
const layoutGroups = (s: CanvasState): Map<string, WindowLayout[]> => {
  const groups = new Map<string, WindowLayout[]>()
  for (const id of [...dirty]) {
    const w = findWin(s, id)
    if (!w) {
      dirty.delete(id)
      continue
    }
    const rows = groups.get(w.canvas_id) ?? []
    rows.push({ id: w.id, x: w.x, y: w.y, w: w.w, h: w.h, z: w.z, state: w.state })
    groups.set(w.canvas_id, rows)
  }
  return groups
}

/**
 * The same write as `flushLayout`, but `keepalive` so the teardown cannot outrun it: an ordinary fetch
 * issued from `beforeunload` is dropped with the document, which loses the move the handler exists for.
 * Fire-and-forget by definition — there is no page left to retry on, so nothing is left dirty either.
 */
export const flushLayoutOnUnload = (s: CanvasState): void => {
  if (!dirty.size) return
  for (const [cid, windows] of layoutGroups(s)) {
    void fetch(`${getBase()}/canvases/${cid}/layout`, {
      method: 'PUT',
      keepalive: true,
      headers: { 'Content-Type': 'application/json', ...(getToken() ? { 'X-Personal-OS-Token': getToken() } : {}) },
      body: JSON.stringify({ windows })
    }).catch(() => undefined)
    for (const w of windows) dirty.delete(w.id)
  }
}

/**
 * Writes `canvasId`'s pending pan/zoom now instead of at the end of `setViewport`'s debounce. A no-op
 * when nothing is pending. Anything that snapshots the server row (Save as preset) awaits it first.
 */
export const flushViewport = async (canvasId: string): Promise<void> => {
  const t = viewportTimers.get(canvasId)
  if (!t) return
  clearTimeout(t)
  viewportTimers.delete(canvasId)
  const c = useCanvas.getState().canvases[canvasId]
  if (c) await api.canvases.update(canvasId, { zoom: c.zoom, pan_x: c.pan_x, pan_y: c.pan_y }).catch(() => undefined)
}

/** A non-active canvas as it would appear if shown now: its stored pan/zoom, the current plane size. */
const viewportOf = (c: Canvas): Viewport => {
  const v = viewport()
  return { ...v, zoom: c.zoom, panX: c.pan_x, panY: c.pan_y }
}

/**
 * Where a new window lands: the first spot in the visible viewport where its rect covers no existing
 * window (scanned in reading order on a coarse grid), so opening several widgets in a row tiles them
 * instead of stacking them. Only when the viewport is genuinely full does it fall back to the old
 * cascade off the top-left.
 */
const spawnAt = (windows: CanvasWindow[], size?: Size, vp: Viewport = viewport()): Point => {
  const v = visibleRect(vp)
  const w = size?.w ?? 420
  const h = size?.h ?? 340
  const pad = 14
  const others = windows.filter((x) => x.state === 'normal' || x.state === 'maximized')
  const free = (x: number, y: number): boolean =>
    others.every((o) => x + w + pad <= o.x || o.x + o.w + pad <= x || y + h + pad <= o.y || o.y + o.h + pad <= y)
  const step = 48
  const x0 = v.x + 48
  const y0 = v.y + 48
  const maxX = Math.max(x0, v.x + v.w - w - 24)
  const maxY = Math.max(y0, v.y + v.h - h - 24)
  for (let y = y0; y <= maxY; y += step) {
    for (let x = x0; x <= maxX; x += step) {
      if (free(x, y)) return { x: Math.round(x), y: Math.round(y) }
    }
  }
  const k = windows.length % 6
  return { x: Math.round(x0 + k * 32), y: Math.round(y0 + k * 32) }
}

export const useCanvas = create<CanvasState>((set, get) => {
  const putCanvas = (c: Canvas): void =>
    set((s) => ({
      canvases: { ...s.canvases, [c.id]: { ...c, windows: [...c.windows].sort(byZ) } },
      order: s.order.includes(c.id) ? s.order : [...s.order, c.id]
    }))

  const patchCanvas = (canvasId: string, patch: Partial<Canvas>): void =>
    set((s) => {
      const c = s.canvases[canvasId]
      return c ? { canvases: { ...s.canvases, [canvasId]: { ...c, ...patch } } } : {}
    })

  const putWindows = (canvasId: string, fn: (ws: CanvasWindow[]) => CanvasWindow[]): void =>
    set((s) => {
      const c = s.canvases[canvasId]
      if (!c) return {}
      return { canvases: { ...s.canvases, [canvasId]: { ...c, windows: fn(c.windows).sort(byZ) } } }
    })

  const putWindow = (w: CanvasWindow): void =>
    putWindows(w.canvas_id, (ws) => (ws.some((x) => x.id === w.id) ? ws.map((x) => (x.id === w.id ? w : x)) : [...ws, w]))

  const transition = (): void => {
    set({ interacting: true })
    if (transitionTimer) clearTimeout(transitionTimer)
    transitionTimer = setTimeout(() => {
      transitionTimer = null
      set({ interacting: false })
    }, TRANSITION_MS)
  }

  const goto = (i: number): void => {
    const { order, activeCanvasId } = get()
    const id = order[i]
    if (!id || id === activeCanvasId) return
    get().setActiveCanvas(id)
  }

  /**
   * Registered at app start (the sidebar lists spaces), so everything here that is not meant to work
   * from a classic view is guarded on `view === 'canvas'`: ⌘W/⌘M must still close/minimize the app
   * window there, and the canvas-only actions must do nothing.
   */
  const onMenu = (action: string): void => {
    const s = get()
    const inCanvas = useStore.getState().view === 'canvas'
    if (action === 'close-window') return inCanvas && s.focusedWindowId ? void s.closeFocused() : window.os.closeSelf()
    if (action === 'minimize-window') return inCanvas && s.focusedWindowId ? void s.minimizeFocused() : window.os.minimizeSelf()
    // These three work from anywhere: they navigate into the canvas.
    if (action === 'canvas:toggle') return void s.toggleCanvas()
    if (action === 'canvas:new-space') return void s.newSpace()
    if (action.startsWith('canvas:space:')) {
      const n = Number(action.slice('canvas:space:'.length))
      // A failed app-start load leaves `order` empty: retry it (as enterSpace does) before resolving ⌃N.
      void (async () => {
        if (!get().loaded) await get().load().catch(fail)
        const id = get().order[n - 1]
        if (id) await get().enterSpace(id)
      })()
      return
    }
    // The app store leaves ⌘N to us in the canvas view.
    if (action === 'new-chat') {
      if (inCanvas) void s.newChatWindow()
      return
    }
    if (!inCanvas || !action.startsWith('canvas:')) return
    if (action === 'canvas:next-space') s.nextSpace()
    else if (action === 'canvas:prev-space') s.prevSpace()
    else if (action === 'canvas:overview') s.toggleOverview()
    else if (action === 'canvas:tidy') s.tidyUp()
    else if (action === 'canvas:popout') void s.popOutFocused()
    else if (action === 'canvas:unpopout') {
      if (s.focusedWindowId) void s.returnToCanvas(s.focusedWindowId)
    } else if (action === 'canvas:pin') void s.togglePinFocused()
  }

  /** Optimistic hints from other renderers. The backend stays authoritative; a hint never creates state. */
  const onBus = (m: { kind: string; windowId?: string; data?: Record<string, unknown> }): void => {
    const id = m.windowId
    if (m.kind === 'canvas-invalidate') return void get().load()
    if (!id) return
    if (m.kind === 'window-bounds') get().patchWindow(id, m.data as Partial<CanvasWindow>)
    else if (m.kind === 'window-state') get().patchWindow(id, { state: m.data?.state as WindowState })
    else if (m.kind === 'window-config') {
      const w = findWin(get(), id)
      if (w) get().patchWindow(id, { config: { ...w.config, ...(m.data ?? {}) } })
    }
  }

  const listen = (): void => {
    if (listening) return
    listening = true
    // Last chance for a move made inside the 400 ms debounce; the PUT is fire-and-forget by then.
    window.addEventListener('beforeunload', () => flushLayoutOnUnload(get()))
    window.os.onMenu(onMenu)
    window.os.bus.on(onBus)
    window.os.popout.onChanged((c) => {
      if (c.event === 'closed') get().patchWindow(c.windowId, { state: 'normal' })
    })
  }

  return {
    canvases: {},
    order: [],
    activeCanvasId: null,
    focusedWindowId: null,
    overview: false,
    interacting: false,
    loaded: false,
    loadFailed: false,

    load: async () => {
      listen()
      let list: Canvas[]
      try {
        list = await api.canvases.list()
      } catch (e) {
        set({ loadFailed: true })
        throw e
      }
      const canvases: Record<string, Canvas> = {}
      for (const c of list) canvases[c.id] = { ...c, windows: [...c.windows].sort(byZ) }
      const order = list.map((c) => c.id)
      set((s) => ({
        canvases,
        order,
        activeCanvasId: s.activeCanvasId && canvases[s.activeCanvasId] ? s.activeCanvasId : order[0] ?? null,
        loaded: true,
        loadFailed: false
      }))
    },

    newSpace: async (name, copyFrom = null) => {
      try {
        const c = await api.canvases.create({ name, copy_from: copyFrom })
        putCanvas(c)
        set({ activeCanvasId: c.id, focusedWindowId: null, overview: false })
        useStore.getState().setView('canvas')
      } catch (e) {
        fail(e)
      }
    },
    enterSpace: async (canvasId) => {
      if (!get().loaded) await get().load().catch(fail)
      const s = get()
      const id = canvasId && s.canvases[canvasId] ? canvasId : s.activeCanvasId ?? s.order[0]
      // Zero spaces: create one (newSpace navigates).
      if (!id) return void (await get().newSpace())
      if (id !== s.activeCanvasId) s.setActiveCanvas(id)
      useStore.getState().setView('canvas')
    },
    toggleCanvas: async () => {
      const app = useStore.getState()
      if (app.view === 'canvas') app.leaveCanvas()
      else await get().enterSpace()
    },
    adoptSpace: (c, navigate = true) => {
      putCanvas(c)
      if (!navigate) return
      get().setActiveCanvas(c.id)
      useStore.getState().setView('canvas')
    },
    spaceForProject: (projectId) => get().order.find((id) => get().canvases[id]?.project_id === projectId) ?? null,
    renameSpace: async (canvasId, name) => {
      patchCanvas(canvasId, { name })
      await api.canvases.update(canvasId, { name }).catch(fail)
    },
    deleteSpace: async (canvasId) => {
      await api.canvases.delete(canvasId).catch(fail)
      set((s) => {
        const canvases = { ...s.canvases }
        delete canvases[canvasId]
        const order = s.order.filter((id) => id !== canvasId)
        const i = Math.max(0, s.order.indexOf(canvasId) - 1)
        return {
          canvases,
          order,
          activeCanvasId: s.activeCanvasId === canvasId ? order[i] ?? order[0] ?? null : s.activeCanvasId,
          focusedWindowId: null
        }
      })
      if (!get().order.length) await get().load()
    },
    setActiveCanvas: (canvasId) => {
      if (!get().canvases[canvasId]) return
      // Synchronous up to its first await, so the rows are grouped while findWin still resolves them.
      void get().flushLayout()
      transition()
      set({ activeCanvasId: canvasId, focusedWindowId: null, overview: false })
    },
    gotoSpace: (n) => goto(n - 1),
    nextSpace: () => {
      const { order, activeCanvasId } = get()
      if (order.length < 2) return
      goto((order.indexOf(activeCanvasId ?? '') + 1 + order.length) % order.length)
    },
    prevSpace: () => {
      const { order, activeCanvasId } = get()
      if (order.length < 2) return
      goto((order.indexOf(activeCanvasId ?? '') - 1 + order.length) % order.length)
    },
    toggleOverview: () => {
      transition()
      set((s) => ({ overview: !s.overview }))
    },
    bindSpace: async (canvasId, projectId) => {
      patchCanvas(canvasId, { project_id: projectId })
      const patch = projectId ? { project_id: projectId } : { clear_project: true }
      await api.canvases.update(canvasId, patch).catch(fail)
    },
    setSnap: async (canvasId, patch) => {
      patchCanvas(canvasId, patch)
      await api.canvases.update(canvasId, patch).catch(fail)
    },
    setViewport: (canvasId, v) => {
      patchCanvas(canvasId, v)
      const t = viewportTimers.get(canvasId)
      if (t) clearTimeout(t)
      viewportTimers.set(
        canvasId,
        setTimeout(() => void flushViewport(canvasId), VIEWPORT_MS)
      )
    },

    openWindow: async (kind, refId = null, at, config, target) => {
      const s = get()
      const canvasId = target ?? s.activeCanvasId
      const canvas = canvasId ? s.canvases[canvasId] : undefined
      if (!canvasId || !canvas) return null
      const isActive = canvasId === s.activeCanvasId
      const pos = at ?? spawnAt(canvas.windows, sizeOf?.(kind), isActive ? viewport() : viewportOf(canvas))
      try {
        const w = await api.canvases.addWindow(canvasId, {
          kind,
          ref_id: refId,
          project_id: canvas.project_id,
          x: Math.round(pos.x),
          y: Math.round(pos.y),
          ...(sizeOf?.(kind) ?? {}),
          config: { ...(configOf?.(kind) ?? {}), ...(config ?? {}) }
        })
        putWindow(w)
        // A window sent to another space lands there quietly: focus is per the active space.
        if (isActive) set({ focusedWindowId: w.id })
        // Attach rather than load: a chat opened over a reply already in flight adopts that run even
        // when this resolves before the widget's own effect sees an unloaded session.
        if (w.kind === 'chat' && w.ref_id) void useStore.getState().attachSession(w.ref_id)
        return w
      } catch (e) {
        fail(e)
        return null
      }
    },
    openChat: async (conversationId) => {
      const s = get()
      for (const cid of s.order) {
        const w = s.canvases[cid]?.windows.find((x) => x.kind === 'chat' && x.ref_id === conversationId)
        if (!w) continue
        if (cid !== s.activeCanvasId) s.setActiveCanvas(cid)
        if (w.state === 'minimized') await get().setWindowState(w.id, 'normal')
        get().focusWindow(w.id)
        return
      }
      await get().openWindow('chat', conversationId)
    },
    newChatWindow: async (projectId, at) => {
      const s = get()
      const canvas = s.activeCanvasId ? s.canvases[s.activeCanvasId] : undefined
      if (!canvas) return null
      const c = await useStore.getState().createConversation(projectId !== undefined ? projectId : canvas.project_id)
      return c ? get().openWindow('chat', c.id, at) : null
    },
    ensureWindow: async (canvasId, kind, refId = null, config, at) => {
      const canvas = get().canvases[canvasId]
      if (!canvas) return { win: null, existed: false }
      const hit = canvas.windows.find((w) => w.kind === kind && (w.ref_id ?? null) === (refId ?? null))
      if (!hit) return { win: await get().openWindow(kind, refId, at, config, canvasId), existed: false }
      if (hit.state === 'minimized') await get().setWindowState(hit.id, 'normal')
      else if (hit.state === 'popped') void window.os.popout.focus(hit.id)
      if (canvasId === get().activeCanvasId) get().focusWindow(hit.id)
      return { win: hit, existed: true }
    },
    closeWindow: async (windowId) => {
      const w = findWin(get(), windowId)
      if (!w) return
      dirty.delete(windowId)
      if (w.state === 'popped') void window.os.popout.close(windowId)
      putWindows(w.canvas_id, (ws) => ws.filter((x) => x.id !== windowId))
      set((s) => (s.focusedWindowId === windowId ? { focusedWindowId: null } : {}))
      await api.windows.delete(windowId).catch(fail)
    },
    focusWindow: (windowId) => {
      const s = get()
      const w = findWin(s, windowId)
      if (!w) return
      const top = topZ(s.canvases[w.canvas_id]?.windows ?? EMPTY)
      if (s.focusedWindowId !== windowId) set({ focusedWindowId: windowId })
      if (w.z >= top) return
      s.patchWindow(windowId, { z: top + 1 })
      void api.windows
        .raise(windowId)
        .then((row) => get().patchWindow(windowId, { z: row.z }))
        .catch(() => undefined)
    },
    patchWindow: (windowId, patch) => {
      const w = findWin(get(), windowId)
      if (!w) return
      putWindows(w.canvas_id, (ws) => ws.map((x) => (x.id === windowId ? { ...x, ...patch } : x)))
    },
    setWindowState: async (windowId, state) => {
      const s = get()
      const w = findWin(s, windowId)
      if (!w || w.state === state) return
      if (state === 'maximized') {
        const r = zoneRect('top', viewport(), { w: w.w, h: w.h })
        const box = { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.w), h: Math.round(r.h) }
        // Never overwrite bounds still owed back: maximizing a minimized-while-maximized window
        // would otherwise persist the full-viewport box as its "original" geometry.
        const restore_bounds = w.restore_bounds ?? { x: w.x, y: w.y, w: w.w, h: w.h }
        s.patchWindow(windowId, { state, restore_bounds, ...box })
        await api.windows.update(windowId, { state, restore_bounds, ...box }).catch(fail)
        return
      }
      // Any return to 'normal' pays the bounds back, not just maximized -> normal: a minimize in
      // between leaves the window full-viewport-sized and stranded otherwise.
      if (state === 'normal' && w.restore_bounds) {
        const r = w.restore_bounds
        s.patchWindow(windowId, { state, x: r.x, y: r.y, w: r.w, h: r.h, restore_bounds: null })
        await api.windows.update(windowId, { state, x: r.x, y: r.y, w: r.w, h: r.h, clear_restore_bounds: true }).catch(fail)
        return
      }
      s.patchWindow(windowId, { state })
      if (state === 'minimized' && s.focusedWindowId === windowId) set({ focusedWindowId: null })
      await api.windows.update(windowId, { state }).catch(fail)
    },
    setWindowTitle: async (windowId, title) => {
      get().patchWindow(windowId, { title })
      await api.windows.update(windowId, { title }).catch(fail)
    },
    setWindowConfig: async (windowId, patch) => {
      const w = findWin(get(), windowId)
      if (!w) return
      get().patchWindow(windowId, { config: { ...w.config, ...patch } })
      await api.windows.update(windowId, { config: patch }).catch(fail)
    },
    setWindowPinned: async (windowId, pinned) => {
      get().patchWindow(windowId, { pinned: pinned ? 1 : 0 })
      void window.os.popout.setPinned(windowId, pinned)
      await api.windows.update(windowId, { pinned }).catch(fail)
    },
    moveWindowToCanvas: async (windowId, canvasId) => {
      const w = findWin(get(), windowId)
      if (!w || w.canvas_id === canvasId) return
      putWindows(w.canvas_id, (ws) => ws.filter((x) => x.id !== windowId))
      try {
        putWindow(await api.windows.update(windowId, { canvas_id: canvasId }))
      } catch (e) {
        putWindow(w)
        fail(e)
      }
    },
    markLayoutDirty: (windowIds) => {
      for (const id of windowIds) dirty.add(id)
      if (!dirty.size) return
      if (layoutTimer) clearTimeout(layoutTimer)
      layoutTimer = setTimeout(() => {
        layoutTimer = null
        void get().flushLayout()
      }, LAYOUT_MS)
    },
    flushLayout: async () => {
      if (layoutTimer) {
        clearTimeout(layoutTimer)
        layoutTimer = null
      }
      if (!dirty.size) return
      const groups = layoutGroups(get())
      // Clear per group, after it lands: a failed PUT must leave its ids dirty for the next flush.
      await Promise.all(
        [...groups].map(([cid, rows]) =>
          api.canvases
            .layout(cid, rows)
            .then(() => {
              for (const r of rows) dirty.delete(r.id)
            })
            .catch(fail)
        )
      )
    },
    tidyUp: () => {
      const s = get()
      const c = s.activeCanvasId ? s.canvases[s.activeCanvasId] : undefined
      if (!c) return
      const movable = c.windows.filter((w) => w.state === 'normal')
      if (!movable.length) return
      const laid = tidyLayout(movable.map((w) => ({ id: w.id, x: w.x, y: w.y, w: w.w, h: w.h })), viewport(), c.grid_size)
      for (const r of laid) s.patchWindow(r.id, { x: r.x, y: r.y })
      s.markLayoutDirty(laid.map((r) => r.id))
    },
    setInteracting: (v) => {
      if (transitionTimer && !v) {
        clearTimeout(transitionTimer)
        transitionTimer = null
      }
      if (get().interacting !== v) set({ interacting: v })
    },

    popOut: async (windowId) => {
      const s = get()
      const w = findWin(s, windowId)
      if (!w || w.state === 'popped') return
      s.patchWindow(windowId, { state: 'popped' })
      const ok = await window.os.popout.open(windowId, {
        bounds: w.popout_bounds ?? { width: Math.round(w.w), height: Math.round(w.h) },
        title: w.title || undefined,
        pinned: !!w.pinned
      })
      if (!ok) {
        s.patchWindow(windowId, { state: w.state })
        useStore.getState().toast('That window could not be detached', 'error')
        return
      }
      await api.windows.update(windowId, { state: 'popped' }).catch(fail)
    },
    returnToCanvas: async (windowId) => {
      const w = findWin(get(), windowId)
      if (!w) return
      const closed = await window.os.popout.close(windowId)
      get().patchWindow(windowId, { state: 'normal' })
      // A real close persists state via main; only the stale case needs the write.
      if (!closed) await api.windows.update(windowId, { state: 'normal' }).catch(fail)
    },
    popOutFocused: async () => {
      const id = get().focusedWindowId
      if (id) await get().popOut(id)
    },
    togglePinFocused: async () => {
      const s = get()
      const w = s.focusedWindowId ? findWin(s, s.focusedWindowId) : undefined
      if (w) await s.setWindowPinned(w.id, !w.pinned)
    },
    closeFocused: async () => {
      const id = get().focusedWindowId
      if (id) await get().closeWindow(id)
      else window.os.closeSelf()
    },
    minimizeFocused: async () => {
      const id = get().focusedWindowId
      if (id) await get().setWindowState(id, 'minimized')
      else window.os.minimizeSelf()
    }
  }
})

export const useActiveCanvas = (): Canvas | null => useCanvas((s) => (s.activeCanvasId ? s.canvases[s.activeCanvasId] ?? null : null))
export const useWindows = (): CanvasWindow[] => useCanvas((s) => (s.activeCanvasId ? s.canvases[s.activeCanvasId]?.windows ?? EMPTY : EMPTY))
export const useWindow = (windowId: string): CanvasWindow | undefined => useCanvas((s) => findWin(s, windowId))
export const useIsFocused = (windowId: string): boolean => useCanvas((s) => s.focusedWindowId === windowId)
