import {
  useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore,
  type DragEvent as ReactDragEvent, type MouseEvent as ReactMouseEvent, type PointerEvent as ReactPointerEvent
} from 'react'
import type { CanvasWindow, Rect } from '@shared/types'
import { useStore } from '../store'
import { addWidgetEntries } from './AddWidgetMenu'
import Dock from './Dock'
import { openPayload } from './drops'
import ContextMenu, { type MenuEntry } from './Menu'
import Overview from './Overview'
import SpacesBar from './SpacesBar'
import StatusRing from './StatusRing'
import WindowFrame from './WindowFrame'
import { hasDrag, hasFiles, readDrag } from './dnd'
import { WIDGETS } from './registry'
import { canvasFromScreen, screenFromCanvas, snapValue, visibleRect, type Point, type Viewport } from './snapping'
import { setLiveViewport, setViewportEl, spaceLocked, useActiveCanvas, useCanvas, useWindows, viewport, viewportPoint } from './store'
import { getDragOverlay, schedule, subscribeDragOverlay } from './useDrag'
import '../styles/canvas.css'
import { MessageSquarePlus, Plus } from 'lucide-react'
import { lines, usePageContext } from '../lib/pageContext'

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
/** Set once the first-run Spaces explainer is dismissed; after that an empty space shows the one-line hint. */
const INTRO_KEY = 'grain.spacesIntroSeen'
const introSeen = (): boolean => {
  try { return localStorage.getItem(INTRO_KEY) === '1' } catch { return false /* private window */ }
}

const clamp = (n: number, lo: number, hi: number): number => Math.min(hi, Math.max(lo, n))
/**
 * The order the frames MOUNT in, which is deliberately not the order the store keeps them in. The
 * store sorts `windows` by z (`byZ`) and a click raises the window it hit, so rendering in store
 * order made the clicked window the last keyed child -- and React moves a reordered child by
 * re-inserting its DOM node. Re-insertion restarts the `win-open` animation and reloads every iframe
 * inside the window, which is why clicking a widget looked like it reloaded it.
 * Creation order never changes, so a raise now rewrites nothing but `zIndex` -- and stacking has
 * always come from that, never from DOM order.
 */
export const renderOrder = (windows: CanvasWindow[]): CanvasWindow[] =>
  windows
    .filter((w) => w.state !== 'minimized')
    .sort((a, b) => a.created_at - b.created_at || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0))

const hits = (a: Rect, b: Rect): boolean => a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h

/** Can this element itself consume the wheel delta, i.e. it scrolls and is not already at the end? */
const scrolls = (el: HTMLElement, dx: number, dy: number): boolean => {
  const cs = getComputedStyle(el)
  if (dy && /auto|scroll|overlay/.test(cs.overflowY) && el.scrollHeight > el.clientHeight + 1) {
    if (dy < 0 ? el.scrollTop > 0 : el.scrollTop + el.clientHeight < el.scrollHeight - 1) return true
  }
  if (dx && /auto|scroll|overlay/.test(cs.overflowX) && el.scrollWidth > el.clientWidth + 1) {
    if (dx < 0 ? el.scrollLeft > 0 : el.scrollLeft + el.clientWidth < el.scrollWidth - 1) return true
  }
  return false
}

/**
 * What a two-finger scroll lands on: 'scroll' when something between the target and its window frame
 * can consume it (the wheel is the widget's, left to scroll natively), 'win' when it is over a window
 * with nothing left to scroll, null over bare canvas. Walks the DOM because widget bodies are
 * arbitrary; anything scrollable inside `.win` counts.
 */
const wheelHit = (e: WheelEvent): 'scroll' | 'win' | null => {
  for (let el = e.target instanceof HTMLElement ? e.target : null; el; el = el.parentElement) {
    if (el.classList.contains('win')) return 'win'
    if (scrolls(el, e.deltaX, e.deltaY)) return 'scroll'
  }
  return null
}

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

/**
 * The plane's transform, with the pan snapped to whole device pixels.
 *
 * Pan is a float the backend stores verbatim, so a settled pan of 311.4px put every window -- and so
 * every glyph in it -- on a fractional device pixel, which the rasteriser resolves by smearing the
 * stems across two. Rounding shifts the plane by under half a pixel and nobody can see it; the text it
 * sharpens is the whole point. Zoom is left alone: text is laid out in CSS pixels and rastered at the
 * composited scale, so a fractional zoom is crisp as long as nothing in the window is its own render
 * surface (see the TEXT SHARPNESS note in canvas.css).
 */
const planeTransform = (panX: number, panY: number, zoom: number): string => {
  const dpr = window.devicePixelRatio || 1
  const snap = (v: number): number => Math.round(v * dpr) / dpr
  return `translate(${snap(panX)}px, ${snap(panY)}px) scale(${zoom})`
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
  // `at` is the grid cell a right-click landed on; the explainer's button opens the menu without one.
  const [menu, setMenu] = useState<{ screen: Point; at?: Point } | null>(null)
  const [intro, setIntro] = useState(() => !introSeen())
  const dismissIntro = (): void => {
    setIntro(false)
    try { localStorage.setItem(INTRO_KEY, '1') } catch { /* private window */ }
  }
  const idle = useRef<ReturnType<typeof setTimeout> | null>(null)

  // A locked space keeps the pan, the zoom and every rect it had: the plane stops taking gestures,
  // while the widgets on it stay as interactive as ever.
  const locked = !!canvas?.locked

  usePageContext(() => ({
    view: 'canvas',
    label: canvas ? `Space “${canvas.name}”` : 'Spaces',
    detail: windows.length
      ? `The space has these widgets open:\n${lines(windows, (w) => `${w.kind}${w.title ? ` “${w.title}”` : ''}${w.ref_id ? ` (\`${w.ref_id}\`)` : ''}`)}`
      : 'The space is empty.',
    refs: windows.filter((w) => w.ref_id).slice(0, 40).map((w) => ({ kind: w.kind, id: w.ref_id as string, name: w.title })),
    hints: ['What is on this space?', 'What should I look at first?']
  }), [canvas?.id, canvas?.name, windows])

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
    if (plane.current) plane.current.style.transform = planeTransform(v.panX, v.panY, v.zoom)
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
    setMenu(null)
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
    // A momentum tail keeps delivering wheel events after a widget's content hits its end; without
    // the latch those spill into a canvas pan mid-fling. Over-window events inside the window stay
    // swallowed until the gesture pauses.
    let latchUntil = 0
    const onWheel = (e: WheelEvent): void => {
      const id = useCanvas.getState().activeCanvasId
      if (!id) return
      if (spaceLocked(id)) {
        // Swallow a pinch so the renderer itself does not zoom instead; a plain scroll is left to
        // whatever is under the pointer, which is how a widget keeps scrolling on a frozen space.
        if (e.ctrlKey || e.metaKey) e.preventDefault()
        return
      }
      const v = viewport()
      if (e.ctrlKey || e.metaKey) {
        e.preventDefault()
        const p = viewportPoint(e)
        const before = canvasFromScreen(p, v)
        const zoom = clamp(v.zoom * Math.exp(-e.deltaY / 240), MIN_ZOOM, MAX_ZOOM)
        nudge(id, { zoom, panX: p.x - before.x * zoom, panY: p.y - before.y * zoom })
        return
      }
      // A two-finger scroll over a window belongs to that window's content while it has room to
      // move; the canvas only pans from bare canvas or a window with nothing left to scroll.
      const hit = wheelHit(e)
      if (hit === 'scroll') {
        latchUntil = e.timeStamp + 250
        return
      }
      if (hit === 'win' && e.timeStamp < latchUntil) return
      e.preventDefault()
      nudge(id, { zoom: v.zoom, panX: v.panX - e.deltaX, panY: v.panY - e.deltaY })
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
      if ((e.key === 'Backspace' || e.key === 'Delete') && selected.length && !spaceLocked()) {
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
    // Right-click (and ⌃-click, macOS's secondary click) opens the add-widget menu via onContextMenu;
    // it must not also start a marquee that clears the selection and holds pointer capture under it.
    if (e.button === 2 || (e.button === 0 && e.ctrlKey)) return
    const st = useCanvas.getState()
    const id = st.activeCanvasId
    if (!id) return
    useCanvas.setState({ focusedWindowId: null })
    // Neither gesture the plane owns survives a lock: panning moves the view, a marquee only leads
    // to moving or deleting what it caught.
    if (spaceLocked(id)) return setSelected([])
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
    if (locked) return
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
    if (locked) return
    if (!hasDrag(dt) && !dt.files.length) return
    e.preventDefault()
    setGhost(null)
    await openPayload(readDrag(dt), { at: landing(e), files: dt.files })
  }

  /** Right-click on the bare plane: the add-widget menu, landing at the clicked grid cell. */
  const onContextMenu = (e: ReactMouseEvent<HTMLDivElement>): void => {
    // The same own-target rule as onPointerDown: a window's own menu never double-opens this one.
    if (e.target !== e.currentTarget || e.defaultPrevented || overview) return
    e.preventDefault()
    const p = canvasPointFromEvent(e)
    setMenu({ screen: { x: e.clientX, y: e.clientY }, at: { x: snapValue(p.x, grid), y: snapValue(p.y, grid) } })
  }
  const closeMenu = useCallback(() => setMenu(null), [])
  const canvasId = canvas?.id
  const menuItems = useMemo<MenuEntry[]>(
    () => {
      if (!menu || !canvasId) return []
      // Nothing can be added to a frozen space, so the menu offers the one thing that can be done.
      if (locked) return [{ label: 'Unlock space', accel: '⌃⌘L', run: () => useCanvas.getState().toggleLock() }]
      return [{ kind: 'header', label: 'Add widget' }, ...addWidgetEntries({ canvasId, at: menu.at })]
    },
    [menu, canvasId, locked]
  )

  const pitch = grid * zoom
  // Mounted for the whole space, hidden below the threshold, so an imperative zoom can reveal it.
  const gridOn = canvas?.snap_mode === 'grid' || canvas?.snap_mode === 'both'
  const shown = useMemo(() => renderOrder(windows), [windows])
  // Putting anything on a space is the explainer's point, so the first window retires it for good.
  useEffect(() => { if (intro && shown.length) dismissIntro() }, [intro, shown.length])

  return (
    <div className="canvas-root">
      <SpacesBar />
      <div
        ref={mount}
        className={['canvas', interacting && 'interacting', locked && 'locked'].filter(Boolean).join(' ')}
        onPointerDown={onPointerDown}
        onDragOver={onDragOver}
        onDragLeave={onDragLeave}
        onDrop={(e) => void onDrop(e)}
        onContextMenu={onContextMenu}
      >
        {!sidebarOpen && <div className="canvas-drag-strip drag" />}
        {gridOn && (
          <div
            ref={dots}
            className="canvas-grid"
            style={{ backgroundSize: `${pitch}px ${pitch}px`, backgroundPosition: `${panX}px ${panY}px`, display: zoom >= GRID_ZOOM ? undefined : 'none' }}
          />
        )}
        <div ref={plane} className="canvas-plane" style={{ transform: planeTransform(panX, panY, zoom) }}>
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
        {loaded && !shown.length && intro && !locked && canvas && (
          <div className="canvas-empty canvas-intro" role="note" aria-label="About spaces">
            <strong>This is a space</strong>
            <span>Lay out chats, notes, docs and your apps side by side, then pop any window out on top of other apps. Drag anything here from the sidebar.</span>
            <div className="canvas-intro-actions">
              <button className="primary-btn" onClick={() => void useCanvas.getState().newChatWindow()}><MessageSquarePlus size={14} /> New chat</button>
              <button className="ghost-btn" onClick={(e) => { const r = e.currentTarget.getBoundingClientRect(); setMenu({ screen: { x: r.left, y: r.bottom + 4 } }) }}><Plus size={14} /> Add a widget</button>
              <button className="ghost-btn" onClick={dismissIntro}>Got it</button>
            </div>
          </div>
        )}
        {loaded && !shown.length && !(intro && !locked && canvas) && (
          <div className="canvas-empty">
            <strong>Empty space</strong>
            <span>{locked ? 'This space is locked. Unlock it (⌃⌘L) to add widgets.' : 'Drag anything from the sidebar, or right-click to add a widget.'}</span>
          </div>
        )}
        <Dock />
      </div>
      {overview && <Overview />}
      {menu && canvas && <ContextMenu at={menu.screen} items={menuItems} onClose={closeMenu} />}
    </div>
  )
}
