import { useEffect, useRef, useState, type ReactNode } from 'react'
import { Maximize2, Minus, X } from 'lucide-react'
import type { CanvasWindow } from '@shared/types'
import { useCanvas, useIsFocused } from './store'
import { getDragOverlay, rectStyle, subscribeDragOverlay, useWindowDrag, type DragOverlay } from './useDrag'
import WindowHost, { KIND_LABEL } from './WindowHost'
import type { Handle } from './snapping'

const HANDLES: Handle[] = ['n', 's', 'e', 'w', 'ne', 'nw', 'se', 'sw']
/** Fallbacks until widgets injects the registry; `useDrag` applies the same floor. */
const MIN = { w: 200, h: 140 }

export interface WindowFrameProps {
  win: CanvasWindow
  live: boolean
  selected?: boolean
  /** Status-ring slot. Wave 3 passes `<StatusRing>`; `null` leaves the slot empty. */
  status?: ReactNode
}

/**
 * Apple window chrome. Geometry goes through `rectStyle` only, so React and the imperative drag
 * write the same three properties, and the `.dragging` / `.resizing` classes are toggled straight
 * on the node — a `useSyncExternalStore` here would re-render every window on every guide change.
 */
export default function WindowFrame({ win, live, selected = false, status = null }: WindowFrameProps): JSX.Element {
  const node = useRef<HTMLDivElement | null>(null)
  const focused = useIsFocused(win.id)
  const focusWindow = useCanvas((s) => s.focusWindow)
  const closeWindow = useCanvas((s) => s.closeWindow)
  const setWindowState = useCanvas((s) => s.setWindowState)
  const setWindowTitle = useCanvas((s) => s.setWindowTitle)
  const returnToCanvas = useCanvas((s) => s.returnToCanvas)
  const { onDragPointerDown, onResizePointerDown } = useWindowDrag(win, { node, min: MIN })
  const [editing, setEditing] = useState<string | null>(null)

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

  const label = win.title || KIND_LABEL[win.kind]
  const commit = (): void => {
    const next = (editing ?? '').trim()
    setEditing(null)
    if (next && next !== win.title) void setWindowTitle(win.id, next)
  }

  return (
    <div
      ref={node}
      className={['win', focused && 'focused', selected && 'selected', win.state !== 'normal' && win.state].filter(Boolean).join(' ')}
      style={{ ...rectStyle(win), zIndex: win.z + 1 }}
      onPointerDown={() => focusWindow(win.id)}
    >
      <div className="win-bar" onPointerDown={onDragPointerDown} onDoubleClick={() => void setWindowState(win.id, win.state === 'maximized' ? 'normal' : 'maximized')}>
        <div className="win-lights" onPointerDown={(e) => e.stopPropagation()}>
          <button className="win-light close" title="Close (⌘W)" onClick={() => void closeWindow(win.id)}><X size={8} strokeWidth={3} /></button>
          <button className="win-light min" title="Minimize (⌘M)" onClick={() => void setWindowState(win.id, 'minimized')}><Minus size={8} strokeWidth={3} /></button>
          <button className="win-light max" title="Zoom" onClick={() => void setWindowState(win.id, win.state === 'maximized' ? 'normal' : 'maximized')}><Maximize2 size={7} strokeWidth={3} /></button>
        </div>
        <div className="win-title" title={label} onDoubleClick={(e) => { e.stopPropagation(); setEditing(win.title || label) }}>
          {editing === null ? (
            label
          ) : (
            <input
              autoFocus
              value={editing}
              onPointerDown={(e) => e.stopPropagation()}
              onChange={(e) => setEditing(e.target.value)}
              onBlur={commit}
              onKeyDown={(e) => {
                if (e.key === 'Enter') commit()
                if (e.key === 'Escape') setEditing(null)
              }}
            />
          )}
        </div>
        {status && <div className="win-status">{status}</div>}
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

      {win.state === 'normal' &&
        HANDLES.map((h) => <div key={h} className={`win-resize ${h}`} onPointerDown={(e) => onResizePointerDown(e, h)} />)}
    </div>
  )
}
