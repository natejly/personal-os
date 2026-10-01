import { useEffect, useMemo, useRef, type PointerEvent as ReactPointerEvent, type RefObject } from 'react'
import type { CanvasWindow, Rect } from '@shared/types'
import { spaceLocked, useCanvas, viewport, viewportPoint } from './store'
import {
  EDGE_HOLD_MS, clampSize, constrain, edgeZone, guideLines, resizeRect, snapMove, snapResize, zoneRect,
  type AppliedGuide, type GapPill, type Handle, type Point, type Size, type SnapContext, type WindowRect, type Zone
} from './snapping'

/**
 * Pointer-capture drag and 8-way resize. During a drag the rect is written straight to the node and
 * React never re-renders: the store and the backend hear about it on pointerup. One module-level
 * rAF loop drives the whole canvas, and the guide overlay publishes only when it actually changes.
 */

export interface DragOptions {
  /** the `.win` element the transform is written to during a drag */
  node: RefObject<HTMLElement | null>
  /** registry `minSize`; a drag never goes below it */
  min?: Size
  /** registry `defaultSize`; the bottom-centre edge zone restores to it */
  natural?: Size
}

export interface DragHandlers {
  onDragPointerDown: (e: ReactPointerEvent) => void
  onResizePointerDown: (e: ReactPointerEvent, handle: Handle) => void
}

/** What the plane draws while a drag is live. Canvas.tsx reads it with `useSyncExternalStore`. */
export interface DragOverlay {
  windowId: string | null
  mode: 'move' | 'resize' | null
  guides: AppliedGuide[]
  gaps: GapPill[]
  /** the armed edge zone, once it has been held for `EDGE_HOLD_MS` */
  zone: Zone | null
  /** the landing rect of the armed zone, in canvas space */
  preview: Rect | null
}

const MIN: Size = { w: 200, h: 140 }
const IDLE: DragOverlay = { windowId: null, mode: null, guides: [], gaps: [], zone: null, preview: null }

// The standalone `translate` property, not `transform`: the win-open/close animations keyframe
// `transform`, and an animation on `transform` would replace an inline translate for its whole
// duration — every new window played its opening at the plane origin, then slid home.
/**
 * Geometry is rounded on the way to the DOM only. A drag divides the pointer delta by the zoom, so a
 * window settles on coordinates like 311.4 -- and a box at a fractional pixel smears the text inside
 * it across two. The store keeps the exact rect (snapping and the guides are computed from it); this is
 * the presentation layer deciding that half a pixel of position is worth less than sharp glyphs.
 */
export const rectStyle = (r: Rect): { translate: string; width: string; height: string } => ({
  translate: `${Math.round(r.x)}px ${Math.round(r.y)}px`,
  width: `${Math.round(r.w)}px`,
  height: `${Math.round(r.h)}px`
})

/** The one way a window's geometry reaches the DOM, so the drag and React agree on the convention. */
export const applyRect = (el: HTMLElement, r: Rect): void => {
  const s = rectStyle(r)
  el.style.translate = s.translate
  el.style.width = s.width
  el.style.height = s.height
}

const tasks = new Set<() => void>()
let raf = 0

const run = (): void => {
  raf = 0
  const batch = [...tasks]
  tasks.clear()
  for (const t of batch) t()
}

export const schedule = (t: () => void): void => {
  tasks.add(t)
  if (!raf) raf = requestAnimationFrame(run)
}

/** StrictMode double-mounts and teardowns: a task from a dead drag must never reach a frame. */
export const resetDragLoop = (): void => {
  tasks.clear()
  if (raf) cancelAnimationFrame(raf)
  raf = 0
}

let overlay: DragOverlay = IDLE
let signature = ''
const subs = new Set<(o: DragOverlay) => void>()

export const getDragOverlay = (): DragOverlay => overlay
export const subscribeDragOverlay = (cb: (o: DragOverlay) => void): (() => void) => {
  subs.add(cb)
  return () => subs.delete(cb)
}

const sign = (o: DragOverlay): string =>
  `${o.windowId}|${o.mode}|${o.zone}|${o.preview ? `${o.preview.x},${o.preview.y},${o.preview.w},${o.preview.h}` : ''}|` +
  `${o.guides.map((g) => `${g.axis}${g.at}${g.edge}${g.id}`).join(';')}|${o.gaps.map((g) => `${g.axis}${g.from}-${g.to}`).join(';')}`

const publish = (next: DragOverlay): void => {
  const s = sign(next)
  if (s === signature) return
  signature = s
  overlay = next
  for (const cb of subs) cb(next)
}

interface Session {
  windowId: string
  op: 'move' | 'resize'
  /** null for a move; the dragged corner or edge for a resize */
  handle: Handle | null
  node: HTMLElement
  start: Rect
  origin: Point
  pointer: Point
  pointerId: number
  capture: Element
  ctx: SnapContext
  min: Size
  natural: Size
  mods: { meta: boolean; shift: boolean; alt: boolean }
  rect: Rect
  preview: Rect | null
  zone: Zone | null
  zoneAt: number
  zoneTimer: ReturnType<typeof setTimeout> | null
}

let live: Session | null = null

const modsOf = (e: { metaKey: boolean; shiftKey: boolean; altKey: boolean }): Session['mods'] => ({
  meta: e.metaKey,
  shift: e.shiftKey,
  alt: e.altKey
})

const same = (a: Rect, b: Rect): boolean => a.x === b.x && a.y === b.y && a.w === b.w && a.h === b.h

const frame = (): void => {
  const s = live
  if (!s) return
  const raw = { x: (s.pointer.x - s.origin.x) / s.ctx.view.zoom, y: (s.pointer.y - s.origin.y) / s.ctx.view.zoom }
  const d = s.mods.shift ? constrain(raw) : raw
  const ctx: SnapContext = { ...s.ctx, free: s.mods.meta }
  let guides: AppliedGuide[] = []
  let gaps: GapPill[] = []
  if (s.handle) {
    const out = snapResize(resizeRect(s.start, s.handle, d, s.min, s.mods.alt), s.handle, ctx, s.min, s.mods.alt)
    s.rect = clampSize(out.rect, s.min)
    guides = out.guides
  } else {
    const out = snapMove({ ...s.start, x: s.start.x + d.x, y: s.start.y + d.y }, ctx)
    s.rect = out.rect
    guides = out.guides
    gaps = out.gaps
  }
  const armed = s.op === 'move' && !s.mods.meta && s.zone && Date.now() - s.zoneAt >= EDGE_HOLD_MS ? s.zone : null
  s.preview = armed ? zoneRect(armed, s.ctx.view, s.natural) : null
  applyRect(s.node, s.rect)
  publish({
    windowId: s.windowId,
    mode: s.op,
    guides: s.preview ? [] : guides,
    gaps: s.preview ? [] : gaps,
    zone: armed,
    preview: s.preview
  })
}

const teardown = (s: Session): void => {
  if (s.zoneTimer) clearTimeout(s.zoneTimer)
  if (s.capture.hasPointerCapture?.(s.pointerId)) s.capture.releasePointerCapture(s.pointerId)
  window.removeEventListener('pointermove', onMove)
  window.removeEventListener('pointerup', onUp)
  window.removeEventListener('pointercancel', onCancel)
  window.removeEventListener('keydown', onKey)
  window.removeEventListener('keyup', onKey)
}

/** `commit: false` is Esc or a cancelled pointer: put the window back where it started. */
const finish = (commit: boolean): void => {
  const s = live
  if (!s) return
  live = null
  resetDragLoop()
  teardown(s)
  const rect = commit ? s.preview ?? s.rect : s.start
  applyRect(s.node, rect)
  publish(IDLE)
  const st = useCanvas.getState()
  st.setInteracting(false)
  if (!commit || same(rect, s.start)) return
  st.patchWindow(s.windowId, { x: rect.x, y: rect.y, w: rect.w, h: rect.h })
  st.markLayoutDirty([s.windowId])
}

const onMove = (e: PointerEvent): void => {
  const s = live
  if (!s || e.pointerId !== s.pointerId) return
  s.pointer = { x: e.clientX, y: e.clientY }
  s.mods = modsOf(e)
  if (s.op === 'move') {
    const z = edgeZone(viewportPoint(e), s.ctx.view)
    if (z !== s.zone) {
      s.zone = z
      s.zoneAt = Date.now()
      if (s.zoneTimer) clearTimeout(s.zoneTimer)
      // Nothing moves while the pointer is held in the band, so arm a frame for the hold itself.
      s.zoneTimer = z ? setTimeout(() => schedule(frame), EDGE_HOLD_MS + 16) : null
    }
  }
  schedule(frame)
}

const onUp = (e: PointerEvent): void => {
  if (live && e.pointerId === live.pointerId) finish(true)
}

const onCancel = (e: PointerEvent): void => {
  if (live && e.pointerId === live.pointerId) finish(false)
}

const onKey = (e: KeyboardEvent): void => {
  if (!live) return
  if (e.key === 'Escape') {
    e.preventDefault()
    return finish(false)
  }
  live.mods = modsOf(e)
  schedule(frame)
}

const begin = (e: ReactPointerEvent, win: CanvasWindow, o: DragOptions, handle: Handle | null): void => {
  if (live || e.button !== 0 || win.state !== 'normal') return
  // The single gate for every route into a drag: the grip, a resize handle and the ⌘-drag shortcut.
  if (spaceLocked(win.canvas_id)) return
  const node = o.node.current
  if (!node) return
  e.preventDefault()
  e.stopPropagation()

  const st = useCanvas.getState()
  st.focusWindow(win.id)
  st.setInteracting(true)

  const view = viewport()
  const canvas = st.activeCanvasId ? st.canvases[st.activeCanvasId] : undefined
  const others: WindowRect[] = (canvas?.windows ?? [])
    .filter((w) => w.id !== win.id && w.state === 'normal')
    .map((w) => ({ id: w.id, x: w.x, y: w.y, w: w.w, h: w.h }))
  const start: Rect = { x: win.x, y: win.y, w: win.w, h: win.h }
  const capture = e.currentTarget as Element
  capture.setPointerCapture?.(e.pointerId)

  live = {
    windowId: win.id,
    op: handle ? 'resize' : 'move',
    handle,
    node,
    start,
    origin: { x: e.clientX, y: e.clientY },
    pointer: { x: e.clientX, y: e.clientY },
    pointerId: e.pointerId,
    capture,
    ctx: { mode: canvas?.snap_mode ?? 'both', grid: canvas?.grid_size ?? 16, view, others, lines: guideLines(others, view) },
    min: o.min ?? MIN,
    natural: o.natural ?? { w: start.w, h: start.h },
    mods: modsOf(e),
    rect: start,
    preview: null,
    zone: null,
    zoneAt: 0,
    zoneTimer: null
  }
  window.addEventListener('pointermove', onMove)
  window.addEventListener('pointerup', onUp)
  window.addEventListener('pointercancel', onCancel)
  window.addEventListener('keydown', onKey)
  window.addEventListener('keyup', onKey)
}

export const useWindowDrag = (win: CanvasWindow, o: DragOptions): DragHandlers => {
  const latest = useRef({ win, o })
  latest.current = { win, o }
  useEffect(
    () => () => {
      if (live?.windowId === win.id) finish(false)
    },
    [win.id]
  )
  return useMemo(
    () => ({
      onDragPointerDown: (e: ReactPointerEvent) => begin(e, latest.current.win, latest.current.o, null),
      onResizePointerDown: (e: ReactPointerEvent, handle: Handle) => begin(e, latest.current.win, latest.current.o, handle)
    }),
    []
  )
}
