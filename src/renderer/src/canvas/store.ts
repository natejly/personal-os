import { create } from 'zustand'
import type { Canvas, CanvasWindow, SnapMode, WidgetKind, WindowLayout, WindowState } from '@shared/types'
import { api } from '../lib/api'
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

/** The active canvas as the screen sees it. Falls back to the whole window before Canvas mounts. */
export const viewport = (): Viewport => {
  const s = useCanvas.getState()
  const c = s.activeCanvasId ? s.canvases[s.activeCanvasId] : undefined
  const r = viewportEl?.getBoundingClientRect()
  return {
    zoom: c?.zoom ?? 1,
    panX: c?.pan_x ?? 0,
    panY: c?.pan_y ?? 0,
    width: r?.width ?? window.innerWidth,
    height: r?.height ?? window.innerHeight
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

/** Cascade new windows off the visible top-left so two opened in a row do not stack exactly. */
const spawnAt = (n: number): Point => {
  const v = visibleRect(viewport())
  const k = n % 6
  return { x: Math.round(v.x + 48 + k * 32), y: Math.round(v.y + 48 + k * 32) }
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

  const onMenu = (action: string): void => {
    const s = get()
    const canvas = useStore.getState().mode === 'canvas'
    if (action === 'close-window') return canvas && s.focusedWindowId ? void s.closeFocused() : window.os.closeSelf()
    if (action === 'minimize-window') return canvas && s.focusedWindowId ? void s.minimizeFocused() : window.os.minimizeSelf()
    if (!canvas || !action.startsWith('canvas:')) return
    if (action === 'canvas:new-space') void s.newSpace()
    else if (action === 'canvas:next-space') s.nextSpace()
    else if (action === 'canvas:prev-space') s.prevSpace()
    else if (action.startsWith('canvas:space:')) s.gotoSpace(Number(action.slice('canvas:space:'.length)))
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

    load: async () => {
      listen()
      const list = await api.canvases.list()
      const canvases: Record<string, Canvas> = {}
      for (const c of list) canvases[c.id] = { ...c, windows: [...c.windows].sort(byZ) }
      const order = list.map((c) => c.id)
      set((s) => ({
        canvases,
        order,
        activeCanvasId: s.activeCanvasId && canvases[s.activeCanvasId] ? s.activeCanvasId : order[0] ?? null,
        loaded: true
      }))
    },

    newSpace: async (name, copyFrom = null) => {
      try {
        const c = await api.canvases.create({ name, copy_from: copyFrom })
        putCanvas(c)
        set({ activeCanvasId: c.id, focusedWindowId: null, overview: false })
      } catch (e) {
        fail(e)
      }
    },
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
        setTimeout(() => {
          viewportTimers.delete(canvasId)
          const c = get().canvases[canvasId]
          if (c) void api.canvases.update(canvasId, { zoom: c.zoom, pan_x: c.pan_x, pan_y: c.pan_y }).catch(() => undefined)
        }, VIEWPORT_MS)
      )
    },

    openWindow: async (kind, refId = null, at, config) => {
      const s = get()
      const canvasId = s.activeCanvasId
      const canvas = canvasId ? s.canvases[canvasId] : undefined
      if (!canvasId || !canvas) return null
      const pos = at ?? spawnAt(canvas.windows.length)
      try {
        const w = await api.canvases.addWindow(canvasId, {
          kind,
          ref_id: refId,
          project_id: canvas.project_id,
          x: Math.round(pos.x),
          y: Math.round(pos.y),
          ...(sizeOf?.(kind) ?? {}),
          config: config ?? {}
        })
        putWindow(w)
        set({ focusedWindowId: w.id })
        // Loads the session without focusing it: the canvas owns focus, not the chat router.
        if (w.kind === 'chat' && w.ref_id) void useStore.getState().openSession(w.ref_id)
        return w
      } catch (e) {
        fail(e)
        return null
      }
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
        const restore_bounds = { x: w.x, y: w.y, w: w.w, h: w.h }
        s.patchWindow(windowId, { state, restore_bounds, ...box })
        await api.windows.update(windowId, { state, restore_bounds, ...box }).catch(fail)
        return
      }
      if (w.state === 'maximized' && state === 'normal' && w.restore_bounds) {
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
      const ids = [...dirty]
      dirty.clear()
      const s = get()
      const groups = new Map<string, WindowLayout[]>()
      for (const id of ids) {
        const w = findWin(s, id)
        if (!w) continue
        const rows = groups.get(w.canvas_id) ?? []
        rows.push({ id: w.id, x: w.x, y: w.y, w: w.w, h: w.h, z: w.z, state: w.state })
        groups.set(w.canvas_id, rows)
      }
      await Promise.all([...groups].map(([cid, rows]) => api.canvases.layout(cid, rows).catch(fail)))
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
