import {
  isValidElement, memo, useEffect, useLayoutEffect, useRef, useState,
  type MouseEvent as ReactMouseEvent, type PointerEvent as ReactPointerEvent, type ReactNode
} from 'react'
import { createPortal } from 'react-dom'
import { GripVertical } from 'lucide-react'
import type { CanvasWindow } from '@shared/types'
import { WIDGETS } from './registry'
import { useCanvas, useIsFocused } from './store'
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
const MENU_GAP = 8

interface MenuItem {
  label: string
  /** rendered as-is beside the label; must match the accelerator in main's Window menu */
  accel?: string
  danger?: boolean
  run: () => void
}

/**
 * The window menu, in a body portal because `.win` clips its overflow and the plane is scaled.
 * Renderer UI, not an `Electron.Menu`: it has to sit in the same material as everything else.
 */
function WindowMenu({ at, items, onClose }: { at: Point; items: MenuItem[]; onClose: () => void }): JSX.Element {
  const box = useRef<HTMLDivElement | null>(null)
  const [pos, setPos] = useState(at)

  useLayoutEffect(() => {
    const el = box.current
    if (!el) return
    const { width, height } = el.getBoundingClientRect()
    setPos({
      x: Math.max(MENU_GAP, Math.min(at.x, window.innerWidth - width - MENU_GAP)),
      y: Math.max(MENU_GAP, Math.min(at.y, window.innerHeight - height - MENU_GAP))
    })
  }, [at.x, at.y])

  useEffect(() => {
    // Capture phase: Canvas also listens for Escape on window, and it would clear the selection too.
    const onKey = (e: KeyboardEvent): void => {
      if (e.key !== 'Escape') return
      e.stopPropagation()
      onClose()
    }
    window.addEventListener('keydown', onKey, true)
    window.addEventListener('wheel', onClose, { capture: true, passive: true })
    window.addEventListener('resize', onClose)
    return () => {
      window.removeEventListener('keydown', onKey, true)
      window.removeEventListener('wheel', onClose, true)
      window.removeEventListener('resize', onClose)
    }
  }, [onClose])

  return createPortal(
    <>
      <div className="win-menu-backdrop" onPointerDown={onClose} onContextMenu={(e) => { e.preventDefault(); onClose() }} />
      <div ref={box} className="win-menu" role="menu" style={{ left: pos.x, top: pos.y }}>
        {items.map((it) => (
          <button
            key={it.label}
            role="menuitem"
            className={it.danger ? 'win-menu-item danger' : 'win-menu-item'}
            onClick={() => {
              onClose()
              it.run()
            }}
          >
            <span>{it.label}</span>
            {it.accel && <kbd>{it.accel}</kbd>}
          </button>
        ))}
      </div>
    </>,
    document.body
  )
}

export interface WindowFrameProps {
  win: CanvasWindow
  live: boolean
  selected?: boolean
  /**
   * Status slot: Canvas passes a `<StatusRing>` for a statusful kind, `null` for the rest. It renders
   * inside the grip, which swaps its dots for the glyph, and it lights the window's border through the
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
  const def = WIDGETS[win.kind]
  // §6/§10: the registry is the floor a resize honours, and the natural size the bottom-centre zone restores to.
  const { onDragPointerDown, onResizePointerDown } = useWindowDrag(win, { node, min: def?.minSize ?? MIN, natural: def?.defaultSize })
  const [menu, setMenu] = useState<Point | null>(null)

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
  const items: MenuItem[] = [
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
    { label: 'Close', accel: '⌘W', danger: true, run: () => void closeWindow(win.id) }
  ]

  /** ⌘-drag is the escape hatch for a panel whose content fills every pixel; the grip is the route. */
  const onPointerDown = (e: ReactPointerEvent<HTMLDivElement>): void => {
    // `begin` cancels the pointerdown, and that is what suppresses the click on the control beneath.
    if (e.metaKey && e.button === 0) return onDragPointerDown(e)
    focusWindow(win.id)
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
      className={['win', focused && 'focused', selected && 'selected', win.state !== 'normal' && win.state].filter(Boolean).join(' ')}
      data-window-id={win.id}
      data-kind={win.kind}
      style={{ ...rectStyle(win), zIndex: win.z + 1 }}
      onPointerDown={onPointerDown}
      onContextMenu={onContextMenu}
    >
      {/* The whole top edge moves the window, so there is a generous target without a title bar.
          The grip sits inside it as the visible hint and as the status glyph's home. */}
      <div className="win-move" title="Drag to move · right-click for window options" onPointerDown={onDragPointerDown}>
        <span className="win-grip">
          <span className="win-grip-dots"><GripVertical size={14} strokeWidth={2.25} /></span>
          {status}
        </span>
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

      {win.state === 'normal' && (
        <>
          {EDGES.map((h) => <div key={h} className={`win-resize ${h}`} onPointerDown={(e) => onResizePointerDown(e, h)} />)}
          {CORNERS.map((h) => <div key={h} className={`win-resize corner ${h}`} onPointerDown={(e) => onResizePointerDown(e, h)} />)}
        </>
      )}

      {menu && <WindowMenu at={menu} items={items} onClose={() => setMenu(null)} />}
    </div>
  )
}

export default memo(WindowFrame, sameFrameProps)
