import {
  isValidElement, memo, useEffect, useRef, useState,
  type MouseEvent as ReactMouseEvent, type PointerEvent as ReactPointerEvent, type ReactNode
} from 'react'
import type { CanvasWindow } from '@shared/types'
import { canExpand, expandWindow } from './expand'
import ContextMenu, { type MenuEntry } from './Menu'
import { WIDGETS } from './registry'
import { useCanvas, useIsFocused, useSpaceLocked } from './store'
import { getDragOverlay, rectStyle, subscribeDragOverlay, useWindowDrag, type DragOverlay } from './useDrag'
import WindowHost from './WindowHost'
import type { Handle, Point } from './snapping'

/** Edges before corners in the DOM so a corner grab never resolves to the edge underneath it. */
const EDGES: Handle[] = ['s', 'e', 'w']  // no 'n': the top edge moves the window
const CORNERS: Handle[] = ['nw', 'ne', 'sw', 'se']
/** Floor for a kind the registry has no entry for; `useDrag` applies the same one. */
const MIN = { w: 200, h: 140 }
/** Right-click belongs to these, not to the window: a caret and a link carry their own menu. */
const OWN_MENU = 'input, textarea, select, [contenteditable="true"], a[href]'

export interface WindowFrameProps {
  win: CanvasWindow
  live: boolean
  selected?: boolean
  /**
   * Status slot: Canvas passes a `<StatusRing>` for a statusful kind, `null` for the rest. It renders
   * inside the grip, which is otherwise empty, and it lights the window's border through the
   * `:has(.ring)` rules — so `useRingStatus` stays the only status source and nothing here repeats it.
   */
  status?: ReactNode
}

const shallow = (a: object, b: object): boolean => {
  if (a === b) return true
  const x = a as Record<string, unknown>
  const y = b as Record<string, unknown>
  const keys = Object.keys(x)
  return keys.length === Object.keys(y).length && keys.every((k) => Object.is(x[k], y[k]))
}

/** Canvas rebuilds the status element on every render of the plane, so identity alone defeats the memo. */
const sameNode = (a: ReactNode, b: ReactNode): boolean =>
  Object.is(a, b) ||
  (isValidElement(a) && isValidElement(b) && a.type === b.type && a.key === b.key && shallow(a.props, b.props))

/**
 * The memo gate. `win` is compared field by field rather than by identity because a reload rebuilds
 * every row, and the status node by element shape because Canvas builds a fresh one each render.
 */
export const sameFrameProps = (a: WindowFrameProps, b: WindowFrameProps): boolean =>
  a.live === b.live && !!a.selected === !!b.selected && shallow(a.win, b.win) && sameNode(a.status, b.status)

/**
 * A chromeless panel: no title bar, no traffic lights, content flush to the border. Geometry goes
 * through `rectStyle` only, so React and the imperative drag write the same three properties, and the
 * `.dragging` / `.resizing` classes are toggled straight on the node — a `useSyncExternalStore` here
 * would re-render every window on every guide change.
 *
 * Memoized: a Canvas re-render must not cascade into twenty widget bodies.
 */
function WindowFrame({ win, live, selected = false, status = null }: WindowFrameProps): JSX.Element {
  const node = useRef<HTMLDivElement | null>(null)
  const focused = useIsFocused(win.id)
  const focusWindow = useCanvas((s) => s.focusWindow)
  const closeWindow = useCanvas((s) => s.closeWindow)
  const setWindowState = useCanvas((s) => s.setWindowState)
  const popOut = useCanvas((s) => s.popOut)
  const returnToCanvas = useCanvas((s) => s.returnToCanvas)
  // Frozen space: the frame keeps its rect and its body, and gives up its grip and its handles.
  const locked = useSpaceLocked()
  const def = WIDGETS[win.kind]
  // §6/§10: the registry is the floor a resize honours, and the natural size the bottom-centre zone restores to.
  const { onDragPointerDown, onResizePointerDown } = useWindowDrag(win, { node, min: def?.minSize ?? MIN, natural: def?.defaultSize })
  const [menu, setMenu] = useState<Point | null>(null)

  // The open animation promotes the window to its own compositor layer, and a promoted layer with a
  // backdrop-filter over a transparent native window renders see-through (same race the .dragging
  // rule closes). Stay opaque until the animation is over; a timer, not onAnimationEnd, so
  // prefers-reduced-motion (which skips the animation) cannot leave the window opaque forever.
  const [settled, setSettled] = useState(false)
  useEffect(() => {
    const t = setTimeout(() => setSettled(true), 380)
    return () => clearTimeout(t)
  }, [])

  useEffect(() => {
    const el = node.current
    if (!el) return
    const apply = (o: DragOverlay): void => {
      const mine = o.windowId === win.id
      el.classList.toggle('dragging', mine && o.mode === 'move')
      el.classList.toggle('resizing', mine && o.mode === 'resize')
    }
    apply(getDragOverlay())
    return subscribeDragOverlay(apply)
  }, [win.id])

  /** §6 'minimal': a note never offered zoom or minimize, and still does not. */
  const minimal = def?.chrome === 'minimal'
  const zoomed = win.state === 'maximized'
  const items: MenuEntry[] = locked
    ? [
        { label: 'Bring to front', run: () => focusWindow(win.id) },
        // Coming home is the one geometry change a lock allows: it undoes a pop-out the lock itself
        // would now refuse, and without it a detached window is stranded until the space is unlocked.
        ...(win.state === 'popped' ? [{ label: 'Return to canvas', accel: '⌃⌘⇧O', run: () => void returnToCanvas(win.id) }] : []),
        // Expand only navigates the main window; it moves nothing on the plane, so a lock keeps it.
        ...(canExpand(win) ? [{ label: 'Open full view', run: () => expandWindow(win) }] : []),
        { label: 'Unlock space', accel: '⌃⌘L', run: () => useCanvas.getState().toggleLock() }
      ]
    : [
        { label: 'Bring to front', run: () => focusWindow(win.id) },
        ...(minimal
          ? []
          : [
              { label: zoomed ? 'Restore' : 'Zoom', run: () => void setWindowState(win.id, zoomed ? 'normal' : 'maximized') },
              { label: 'Minimize', accel: '⌘M', run: () => void setWindowState(win.id, 'minimized') }
            ]),
        win.state === 'popped'
          ? { label: 'Return to canvas', accel: '⌃⌘⇧O', run: () => void returnToCanvas(win.id) }
          : { label: 'Pop out', accel: '⌃⌘O', run: () => void popOut(win.id) },
        ...(canExpand(win) ? [{ label: 'Open full view', run: () => expandWindow(win) }] : []),
        { label: 'Close', accel: '⌘W', danger: true, run: () => void closeWindow(win.id) }
      ]

  /** ⌘-drag is the escape hatch for a panel whose content fills every pixel; the grip is the route. */
  const onPointerDown = (e: ReactPointerEvent<HTMLDivElement>): void => {
    // `begin` cancels the pointerdown, and that is what suppresses the click on the control beneath.
    if (e.metaKey && e.button === 0 && !locked) return onDragPointerDown(e)
    // `deferRaise`: this pointerdown may be the browser anchoring a text selection in a transcript, and
    // restacking the window underneath it in the same event crashes the renderer. See focusWindow.
    focusWindow(win.id, { deferRaise: true })
  }

  const onContextMenu = (e: ReactMouseEvent<HTMLDivElement>): void => {
    if (e.defaultPrevented) return
    if ((e.target as HTMLElement | null)?.closest(OWN_MENU)) return
    e.preventDefault()
    focusWindow(win.id)
    setMenu({ x: e.clientX, y: e.clientY })
  }

  return (
    <div
      ref={node}
      className={['win', focused && 'focused', selected && 'selected', win.state !== 'normal' && win.state,
        def?.heavy && 'heavy', !settled && 'opening'].filter(Boolean).join(' ')}
      data-window-id={win.id}
      data-kind={win.kind}
      style={{ ...rectStyle(win), zIndex: win.z + 1 }}
      onPointerDown={onPointerDown}
      onContextMenu={onContextMenu}
    >
      {/* The whole top edge moves the window, so there is a generous target without a title bar. The
          cursor and the tooltip are the hint now that the grip draws nothing of its own; the grip
          stays as the status glyph's home. */}
      <div
        className="win-move"
        title={locked ? 'Space locked · right-click for window options' : 'Drag to move · right-click for window options'}
        onPointerDown={locked ? undefined : onDragPointerDown}
      >
        <span className="win-grip">{status}</span>
        {!locked && (
          <button className="win-close" title="Close (⌘W)" aria-label={`Close ${win.title || def?.label || 'window'}`}
            onPointerDown={(e) => e.stopPropagation()} onClick={() => void closeWindow(win.id)}>×</button>
        )}
      </div>

      <div className="win-body">
        {win.state === 'popped' ? (
          <div className="win-ghost">
            <span>Detached to its own window</span>
            <button className="ghost-btn" onClick={() => void returnToCanvas(win.id)}>Return to canvas</button>
          </div>
        ) : (
          <WindowHost win={win} focused={focused} live={live} />
        )}
      </div>

      {win.state === 'normal' && !locked && (
        <>
          {EDGES.map((h) => <div key={h} className={`win-resize ${h}`} onPointerDown={(e) => onResizePointerDown(e, h)} />)}
          {CORNERS.map((h) => <div key={h} className={`win-resize corner ${h}`} onPointerDown={(e) => onResizePointerDown(e, h)} />)}
        </>
      )}

      {menu && <ContextMenu at={menu} items={items} onClose={() => setMenu(null)} />}
    </div>
  )
}

export default memo(WindowFrame, sameFrameProps)
