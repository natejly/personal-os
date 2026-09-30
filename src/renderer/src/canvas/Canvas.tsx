import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore, type DragEvent as ReactDragEvent, type PointerEvent as ReactPointerEvent } from 'react'
import type { CanvasWindow, Rect, WidgetKind } from '@shared/types'
import { api } from '../lib/api'
import { useStore } from '../store'
import Dock from './Dock'
import Overview from './Overview'
import SpacesBar from './SpacesBar'
import StatusRing from './StatusRing'
import WindowFrame from './WindowFrame'
import { hasDrag, hasFiles, readDrag } from './dnd'
import { WIDGETS } from './registry'
import { canvasFromScreen, screenFromCanvas, snapValue, visibleRect, type Point, type Viewport } from './snapping'
import { setLiveViewport, setViewportEl, useActiveCanvas, useCanvas, useWindows, viewport, viewportPoint } from './store'
import { getDragOverlay, schedule, subscribeDragOverlay } from './useDrag'
import '../styles/canvas.css'

const MIN_ZOOM = 0.5
const MAX_ZOOM = 2
/** Below this, every widget renders its proxy card instead (contract §6). */
const LIVE_ZOOM = 0.6
/** Dots are noise below this; the pitch is `grid * zoom` so what you see is what you snap to. */
const GRID_ZOOM = 0.75
/** Concurrently live iframe / d3 / poller widgets (plan §13); which kinds count is `heavy` in the registry. */
/**
 * Render every window on the space up front instead of mounting one when it scrolls into view, becomes
 * focused or is zoomed past 60%. Lazy mounting meant a click could be the moment a widget first
 * rendered, so a failure inside that widget looked like clicking caused a crash. Eager rendering costs
 * more with many heavy widgets (iframes, the d3 graph, Recharts) -- flip this to false to restore the
 * viewport/zoom/heavy-cap gating below, which is kept intact for exactly that reason.
 */
const EAGER = true
const HEAVY_CAP = 6
const GHOST = { w: 420, h: 360 }
const IDLE_MS = 180

const clamp = (n: number, lo: number, hi: number): number => Math.min(hi, Math.max(lo, n))
const hits = (a: Rect, b: Rect): boolean => a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h

/**
 * Which windows may keep polling, mount iframes and run a simulation. Pure so it is testable: this
 * is the whole reason twenty windows stay cheap, and the cap goes to the topmost ones because those
 * are the ones being looked at.
 */
export const liveWindows = (windows: CanvasWindow[], view: Viewport, overview: boolean): Set<string> => {
  const out = new Set<string>()
  if (EAGER) {
    // Everything on the space renders up front. Scrolling a window into view, zooming in or focusing it
    // then mounts nothing, so there is no work at the moment of a click -- which is when failures were
    // being seen. A minimized or popped window still has no body to render.
    for (const w of windows) if (w.state !== 'minimized' && w.state !== 'popped') out.add(w.id)
    return out
  }
  if (overview || view.zoom < LIVE_ZOOM || !view.width) return out
  const vis = visibleRect(view)
  let heavy = 0
  for (let i = windows.length - 1; i >= 0; i--) {
    const w = windows[i]
    if (w.state === 'minimized' || w.state === 'popped') continue
    if (!hits(vis, w)) continue
    if (WIDGETS[w.kind]?.heavy) {
      if (heavy >= HEAVY_CAP) continue
      heavy++
    }
    out.add(w.id)
  }
  return out
}

/** A client point in canvas space. The one conversion every drop and marquee goes through. */
export const canvasPointFromEvent = (e: { clientX: number; clientY: number }): Point => canvasFromScreen(viewportPoint(e), viewport())

const screenRect = (r: Rect, v: Viewport): { left: number; top: number; width: number; height: number } => {
  const p = screenFromCanvas({ x: r.x, y: r.y }, v)
  return { left: p.x, top: p.y, width: r.w * v.zoom, height: r.h * v.zoom }
}

/** Alignment guides, gap pills and the armed edge zone, in screen space so hairlines stay 1 px. */
function Guides({ view }: { view: Viewport }): JSX.Element | null {
  const o = useSyncExternalStore(subscribeDragOverlay, getDragOverlay, getDragOverlay)
  if (!o.mode) return null
  const at = (x: number, y: number): Point => screenFromCanvas({ x, y }, view)
  return (
    <>
      {o.guides.map((g, i) => {
        const a = g.axis === 'x' ? at(g.at, g.span[0]) : at(g.span[0], g.at)
        const b = g.axis === 'x' ? at(g.at, g.span[1]) : at(g.span[1], g.at)
        return g.axis === 'x' ? (
          <div key={i} className="guide-v" style={{ left: a.x, top: Math.min(a.y, b.y), height: Math.abs(b.y - a.y) }} />
        ) : (
          <div key={i} className="guide-h" style={{ top: a.y, left: Math.min(a.x, b.x), width: Math.abs(b.x - a.x) }} />
        )
      })}
      {o.gaps.map((g, i) => {
        const a = g.axis === 'x' ? at(g.from, g.cross) : at(g.cross, g.from)
        const b = g.axis === 'x' ? at(g.to, g.cross) : at(g.cross, g.to)
        return g.axis === 'x' ? (
          <div key={i} className="gap-pill" style={{ left: a.x, top: a.y, width: b.x - a.x }} />
        ) : (
          <div key={i} className="gap-pill v" style={{ left: a.x, top: a.y, height: b.y - a.y }} />
        )
      })}
      {o.preview && <div className="zone-preview" style={screenRect(o.preview, view)} />}
    </>
  )
}

export default function Canvas(): JSX.Element {
  const el = useRef<HTMLDivElement | null>(null)
  const plane = useRef<HTMLDivElement | null>(null)
  const dots = useRef<HTMLDivElement | null>(null)
  const canvas = useActiveCanvas()
  const windows = useWindows()
  const overview = useCanvas((s) => s.overview)
  const interacting = useCanvas((s) => s.interacting)
  const loaded = useCanvas((s) => s.loaded)
  const sidebarOpen = useStore((s) => s.sidebarOpen)
  const [size, setSize] = useState({ w: 0, h: 0 })
  const [marquee, setMarquee] = useState<Rect | null>(null)
  const [selected, setSelected] = useState<string[]>([])
  const [ghost, setGhost] = useState<Rect | null>(null)
  const idle = useRef<ReturnType<typeof setTimeout> | null>(null)

  const zoom = canvas?.zoom ?? 1
  const panX = canvas?.pan_x ?? 0
  const panY = canvas?.pan_y ?? 0
  const grid = canvas?.grid_size ?? 16
  const view = useMemo<Viewport>(() => ({ zoom, panX, panY, width: size.w, height: size.h }), [zoom, panX, panY, size.w, size.h])

  /**
   * Pan and zoom are driven like a window drag: straight to the plane node on one rAF, with the store
   * and its debounced PUT hearing the gesture once, when it settles. A wheel tick through the store
   * would re-render the plane and every window instead.
   */
  const gesture = useRef<{ canvasId: string; zoom: number; panX: number; panY: number } | null>(null)
  const pitchRef = useRef(grid)
  pitchRef.current = grid

  const paint = useCallback((): void => {
    const v = gesture.current ?? viewport()
    if (plane.current) plane.current.style.transform = `translate(${v.panX}px, ${v.panY}px) scale(${v.zoom})`
    const g = dots.current
    if (!g) return
    const pitch = pitchRef.current * v.zoom
    g.style.backgroundSize = `${pitch}px ${pitch}px`
    g.style.backgroundPosition = `${v.panX}px ${v.panY}px`
    g.style.display = v.zoom >= GRID_ZOOM ? '' : 'none'
  }, [])

  const settle = useCallback((): void => {
    const g = gesture.current
    gesture.current = null
    setLiveViewport(null)
    const st = useCanvas.getState()
    st.setInteracting(false)
    if (g) st.setViewport(g.canvasId, { zoom: g.zoom, pan_x: g.panX, pan_y: g.panY })
  }, [])

  /** `setInteracting(true)` has no natural release for a wheel or a pinch, so tail it off. */
  const bump = useCallback((): void => {
    useCanvas.getState().setInteracting(true)
    if (idle.current) clearTimeout(idle.current)
    idle.current = setTimeout(() => {
      idle.current = null
      settle()
    }, IDLE_MS)
  }, [settle])

  const nudge = useCallback(
    (canvasId: string, v: { zoom: number; panX: number; panY: number }): void => {
      gesture.current = { canvasId, ...v }
      setLiveViewport({ zoom: v.zoom, pan_x: v.panX, pan_y: v.panY })
      schedule(paint)
      bump()
    },
    [bump, paint]
  )

  useEffect(
    () => () => {
      if (idle.current) clearTimeout(idle.current)
      gesture.current = null
      setLiveViewport(null)
    },
    []
  )

  // A marquee selection belongs to the space it was made in — `findWin` resolves ids across every
  // canvas, so a stale one deletes windows nobody can see. An uncommitted gesture is stale too.
  useEffect(() => {
    setSelected([])
    gesture.current = null
    setLiveViewport(null)
    paint()
  }, [canvas?.id, paint])

  const mount = useCallback((node: HTMLDivElement | null): void => {
    el.current = node
    setViewportEl(node)
    if (node) setSize({ w: node.clientWidth, h: node.clientHeight })
  }, [])

  useEffect(() => {
    const node = el.current
    if (!node) return
    const ro = new ResizeObserver(() => setSize({ w: node.clientWidth, h: node.clientHeight }))
    ro.observe(node)
    return () => {
      ro.disconnect()
      setViewportEl(null)
    }
  }, [])

  useEffect(() => {
    const node = el.current
    if (!node) return
    const onWheel = (e: WheelEvent): void => {
      const id = useCanvas.getState().activeCanvasId
      if (!id) return
      e.preventDefault()
      const v = viewport()
      if (e.ctrlKey || e.metaKey) {
        const p = viewportPoint(e)
        const before = canvasFromScreen(p, v)
        const zoom = clamp(v.zoom * Math.exp(-e.deltaY / 240), MIN_ZOOM, MAX_ZOOM)
        nudge(id, { zoom, panX: p.x - before.x * zoom, panY: p.y - before.y * zoom })
      } else {
        nudge(id, { zoom: v.zoom, panX: v.panX - e.deltaX, panY: v.panY - e.deltaY })
      }
    }
    node.addEventListener('wheel', onWheel, { passive: false })
    return () => node.removeEventListener('wheel', onWheel)
  }, [nudge])

  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      const t = e.target as HTMLElement | null
      if (t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName))) return
      const st = useCanvas.getState()
      if (e.key === 'Escape') {
        if (st.overview) st.toggleOverview()
        setSelected([])
        return
      }
      if ((e.key === 'Backspace' || e.key === 'Delete') && selected.length) {
        e.preventDefault()
        const here = new Set((st.activeCanvasId ? st.canvases[st.activeCanvasId]?.windows ?? [] : []).map((w) => w.id))
        for (const id of selected) if (here.has(id)) void st.closeWindow(id)
        setSelected([])
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [selected])

  const liveIds = useMemo(() => liveWindows(windows, view, overview), [windows, view, overview])

  const onPointerDown = (e: ReactPointerEvent<HTMLDivElement>): void => {
    if (e.target !== e.currentTarget) return
    const st = useCanvas.getState()
    const id = st.activeCanvasId
    if (!id) return
    useCanvas.setState({ focusedWindowId: null })
    // A plain drag pans (so do middle button and ⌥); ⇧ drags marquee-select, adding to the selection.
    const additive = e.shiftKey
    const pan = !additive
    const from = { x: e.clientX, y: e.clientY }
    const v0 = viewport()
    const origin = canvasPointFromEvent(e)
    const node = e.currentTarget
    node.setPointerCapture(e.pointerId)
    if (pan) node.style.cursor = 'grabbing'
    bump()
    if (!additive) setSelected([])

    const move = (ev: PointerEvent): void => {
      if (pan) {
        nudge(id, { zoom: v0.zoom, panX: v0.panX + (ev.clientX - from.x), panY: v0.panY + (ev.clientY - from.y) })
        return
      }
      const p = canvasPointFromEvent(ev)
      setMarquee({ x: Math.min(origin.x, p.x), y: Math.min(origin.y, p.y), w: Math.abs(p.x - origin.x), h: Math.abs(p.y - origin.y) })
    }
    const up = (ev: PointerEvent): void => {
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', up)
      node.style.cursor = ''
      if (idle.current) {
        clearTimeout(idle.current)
        idle.current = null
      }
      settle()
      if (pan) return
      const p = canvasPointFromEvent(ev)
      const box = { x: Math.min(origin.x, p.x), y: Math.min(origin.y, p.y), w: Math.abs(p.x - origin.x), h: Math.abs(p.y - origin.y) }
      setMarquee(null)
      if (box.w < 6 && box.h < 6) return
      const list = useCanvas.getState()
      const ws = list.activeCanvasId ? list.canvases[list.activeCanvasId]?.windows ?? [] : []
      const ids = ws.filter((w) => w.state === 'normal' && hits(box, w)).map((w) => w.id)
      setSelected((prev) => (additive ? [...new Set([...prev, ...ids])] : ids))
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
  }

  const landing = (e: ReactDragEvent): Rect => {
    const p = canvasPointFromEvent(e)
    return { x: snapValue(p.x, grid), y: snapValue(p.y, grid), w: GHOST.w, h: GHOST.h }
  }

  const onDragOver = (e: ReactDragEvent<HTMLDivElement>): void => {
    const dt = e.dataTransfer
    if (!hasDrag(dt) && !hasFiles(dt)) return
    e.preventDefault()
    dt.dropEffect = 'copy'
    // dragover fires continuously; only a changed landing cell is worth a render.
    const r = landing(e)
    setGhost((g) => (g && g.x === r.x && g.y === r.y ? g : r))
  }

  /** dragleave also fires crossing into a child window, which must not clear the ghost. */
  const onDragLeave = (e: ReactDragEvent<HTMLDivElement>): void => {
    const to = e.relatedTarget as Node | null
    if (!to || !e.currentTarget.contains(to)) setGhost(null)
  }

  const onDrop = async (e: ReactDragEvent<HTMLDivElement>): Promise<void> => {
    const dt = e.dataTransfer
    if (!hasDrag(dt) && !dt.files.length) return
    e.preventDefault()
    setGhost(null)
    const at = landing(e)
    const st = useCanvas.getState()
    const app = useStore.getState()
    const projectId = canvas?.project_id ?? null
    const p = readDrag(dt)
    if (!p) {
      if (!dt.files.length) return
      await app.uploadDocuments(dt.files, projectId)
      await st.openWindow('documents', null, at)
      return
    }
    switch (p.kind) {
      case 'conversation': await st.openWindow('chat', p.id, at); break
      case 'nav': await st.openWindow(p.id as WidgetKind, null, at); break
      case 'todo': await st.openWindow('todos', null, at); break
      case 'document': await st.openWindow('documents', null, at); break
      case 'memory': await st.openWindow('memory', null, at); break
      case 'project': await st.openWindow('project', p.id, at); break
      case 'note': await st.openWindow('note', p.id, at); break
      case 'widget': await st.openWindow('dashboard-widget', p.id, at, { dashboard_id: p.dashboardId }); break
      case 'board-card': {
        const note = await api.notes.create({ body: p.label, project_id: projectId }).catch(() => null)
        if (note) await st.openWindow('note', note.id, at)
        break
      }
      case 'file': {
        if (!dt.files.length) break
        await app.uploadDocuments(dt.files, projectId)
        await st.openWindow('documents', null, at)
        break
      }
    }
  }

  const pitch = grid * zoom
  // Mounted for the whole space, hidden below the threshold, so an imperative zoom can reveal it.
  const gridOn = canvas?.snap_mode === 'grid' || canvas?.snap_mode === 'both'
  const shown = windows.filter((w) => w.state !== 'minimized')

  return (
    <div className="canvas-root">
      <SpacesBar />
      <div
        ref={mount}
        className={interacting ? 'canvas interacting' : 'canvas'}
        onPointerDown={onPointerDown}
        onDragOver={onDragOver}
        onDragLeave={onDragLeave}
        onDrop={(e) => void onDrop(e)}
      >
        {!sidebarOpen && <div className="canvas-drag-strip drag" />}
        {gridOn && (
          <div
            ref={dots}
            className="canvas-grid"
            style={{ backgroundSize: `${pitch}px ${pitch}px`, backgroundPosition: `${panX}px ${panY}px`, display: zoom >= GRID_ZOOM ? undefined : 'none' }}
          />
        )}
        <div ref={plane} className="canvas-plane" style={{ transform: `translate(${panX}px, ${panY}px) scale(${zoom})` }}>
          {shown.map((w) => (
            <WindowFrame
              key={w.id}
              win={w}
              live={liveIds.has(w.id)}
              selected={selected.includes(w.id)}
              status={WIDGETS[w.kind]?.statusful ? <StatusRing conversationId={w.ref_id} /> : null}
            />
          ))}
        </div>
        <div className="canvas-guides">
          <Guides view={view} />
          {ghost && <div className="drop-ghost" style={screenRect(ghost, view)} />}
        </div>
        {marquee && <div className="canvas-marquee" style={screenRect(marquee, view)} />}
        {loaded && !shown.length && (
          <div className="canvas-empty">
            <strong>Empty space</strong>
            <span>Drag anything from the sidebar.</span>
          </div>
        )}
        <Dock />
      </div>
      {overview && <Overview />}
    </div>
  )
}
