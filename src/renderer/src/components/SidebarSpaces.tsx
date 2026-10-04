import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { ChevronRight, LayoutGrid, Lock, MoreHorizontal, Plus } from 'lucide-react'
import { useProject, useStore } from '../store'
import type { DragKind } from '@shared/types'
import { useCanvas } from '../canvas/store'
import { useDropTarget } from '../canvas/dnd'
import { openPayload } from '../canvas/drops'
import { PresetsButton, SavePresetForm } from '../canvas/PresetsMenu'
import { focusFirstItem, menuKeyDown, useReturnFocus } from '../canvas/Menu'

/** A space row takes everything the plane does. A project opens a Project window; binding stays a tab gesture. */
const ALL: DragKind[] = ['conversation', 'todo', 'document', 'memory', 'project', 'widget', 'note', 'file', 'nav']

/** Keep a fixed popover this far inside the viewport. */
const MENU_GAP = 8

/** `keep`: the item swaps the menu's body instead of closing it. */
interface MenuItem { label: string; danger?: boolean; keep?: boolean; run: () => void }

/** A space row's actions, in a body portal so the sidebar's scroll container cannot clip it. */
function SpaceMenu({ at, canvasId, onClose, onRename }: { at: { x: number; y: number }; canvasId: string; onClose: () => void; onRename: () => void }): JSX.Element {
  const ref = useRef<HTMLDivElement>(null)
  const [pos, setPos] = useState(at)
  const [saving, setSaving] = useState(false)
  useReturnFocus()
  useEffect(() => focusFirstItem(ref.current), [])

  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    const r = el.getBoundingClientRect()
    setPos({
      x: Math.max(MENU_GAP, Math.min(at.x, window.innerWidth - r.width - MENU_GAP)),
      y: Math.max(MENU_GAP, Math.min(at.y, window.innerHeight - r.height - MENU_GAP))
    })
  }, [at, saving])

  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      if (e.key !== 'Escape') return
      e.stopPropagation()
      onClose()
    }
    window.addEventListener('keydown', onKey, true)
    return () => window.removeEventListener('keydown', onKey, true)
  }, [onClose])

  const run = (fn: () => void) => (): void => {
    onClose()
    fn()
  }
  const locked = useCanvas((s) => !!s.canvases[canvasId]?.locked)
  const items: MenuItem[] = [
    { label: 'Rename', run: onRename },
    { label: locked ? 'Unlock space' : 'Lock space', run: () => void useCanvas.getState().setLocked(canvasId, !locked) },
    { label: 'Save as preset…', keep: true, run: () => setSaving(true) },
    // Left in place while locked: `deleteSpace` refuses, and the toast says why.
    {
      label: 'Delete space',
      danger: true,
      run: () => {
        const c = useCanvas.getState().canvases[canvasId]
        if (!c) return
        const count = c.windows.length
        if (count === 0 || confirm(`Delete "${c.name}" and its ${count} windows?`)) void useCanvas.getState().deleteSpace(canvasId)
      }
    }
  ]

  return createPortal(
    <>
      <div
        className="popover-backdrop space-menu-backdrop"
        onMouseDown={onClose}
        onContextMenu={(e) => {
          e.preventDefault()
          onClose()
        }}
      />
      <div ref={ref} className="project-menu space-menu" style={{ position: 'fixed', left: pos.x, top: pos.y }} role="menu" onKeyDown={(e) => menuKeyDown(e, onClose)}>
        {saving ? <SavePresetForm canvasId={canvasId} onDone={onClose} /> : items.map((it) => (
          <button key={it.label} className={`project-item${it.danger ? ' danger' : ''}`} role="menuitem" onClick={it.keep ? it.run : run(it.run)}>
            {it.label}
          </button>
        ))}
      </div>
    </>,
    document.body
  )
}

/** Its own component so a row subscribes to its own primitives, not to the whole canvases map. */
export function SpaceRow({ canvasId }: { canvasId: string }): JSX.Element | null {
  const name = useCanvas((s) => s.canvases[canvasId]?.name)
  const count = useCanvas((s) => s.canvases[canvasId]?.windows.length ?? 0)
  const projectId = useCanvas((s) => s.canvases[canvasId]?.project_id ?? null)
  const isActiveSpace = useCanvas((s) => s.activeCanvasId === canvasId)
  // A drop here lands through `openWindow`, which a locked space refuses: say so on the row.
  const locked = useCanvas((s) => !!s.canvases[canvasId]?.locked)
  const inCanvas = useStore((s) => s.view === 'canvas')
  const active = isActiveSpace && inCanvas
  const project = useProject(projectId)
  const [editing, setEditing] = useState<string | null>(null)
  const [menu, setMenu] = useState<{ x: number; y: number } | null>(null)
  // Drops land in this space without navigating or changing the active space.
  const { over, handlers } = useDropTarget(ALL, (p, e) => void (async () => {
    const w = await openPayload(p, { canvasId, files: e.dataTransfer.files })
    if (w) useStore.getState().toast(`Added to "${useCanvas.getState().canvases[canvasId]?.name ?? name}"`, 'info')
  })())
  if (name === undefined) return null

  const commit = (): void => {
    const next = (editing ?? '').trim()
    setEditing(null)
    if (next && next !== name) void useCanvas.getState().renameSpace(canvasId, next)
  }

  // The menu is the row's sibling, not its child: React events bubble through a portal, so a click in
  // it would otherwise reach the row's onClick and navigate. The row div is the root element below,
  // and the drop target.
  return (
    <>
      <div
        className={`project-item space-row${active ? ' active' : ''}${over ? ' drop-target' : ''}`}
        {...handlers}
        role="button"
        tabIndex={0}
        title={`${name}${project ? ` · ${project.name}` : ''} · ${count} window${count === 1 ? '' : 's'}${locked ? ' · locked' : ''}`}
        onClick={() => void useCanvas.getState().enterSpace(canvasId)}
        onKeyDown={(e) => {
          if (e.target !== e.currentTarget || (e.key !== 'Enter' && e.key !== ' ')) return
          e.preventDefault()
          void useCanvas.getState().enterSpace(canvasId)
        }}
        onContextMenu={(e) => {
          e.preventDefault()
          setMenu({ x: e.clientX, y: e.clientY })
        }}
      >
        {project ? <span className="project-dot" style={{ background: project.color }} /> : <LayoutGrid size={12} className="space-row-icon" />}
        {editing === null ? <span className="project-name">{name}</span> : (
          <span className="project-name">
            <input
              autoFocus
              value={editing}
              onChange={(e) => setEditing(e.target.value)}
              onBlur={commit}
              onClick={(e) => e.stopPropagation()}
              onKeyDown={(e) => {
                e.stopPropagation()
                if (e.key === 'Enter') commit()
                if (e.key === 'Escape') setEditing(null)
              }}
            />
          </span>
        )}
        {project && <span className="space-hint">{project.name}</span>}
        {locked && <Lock size={11} className="space-lock" />}
        <span className="count">{count}</span>
        <button
          className="icon-btn ghost xs"
          title="Space actions"
          aria-label="Space actions"
          aria-haspopup="menu"
          aria-expanded={!!menu}
          onClick={(e) => {
            e.stopPropagation()
            const r = e.currentTarget.getBoundingClientRect()
            setMenu({ x: r.left, y: r.bottom + 4 })
          }}
        >
          <MoreHorizontal size={12} />
        </button>
      </div>
      {menu && (
        <SpaceMenu
          at={menu}
          canvasId={canvasId}
          onClose={() => setMenu(null)}
          onRename={() => setEditing(useCanvas.getState().canvases[canvasId]?.name ?? name)}
        />
      )}
    </>
  )
}

/** The sidebar's Spaces section: every space, one click from any view. */
export default function SidebarSpaces(): JSX.Element {
  const order = useCanvas((s) => s.order)
  const loaded = useCanvas((s) => s.loaded)
  const loadFailed = useCanvas((s) => s.loadFailed)
  const [open, setOpen] = useState(true)

  return (
    <>
      <div className="section-row">
        <button className="section-toggle" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
          <ChevronRight size={12} className={open ? 'rot90' : ''} /><LayoutGrid size={13} /> Spaces
        </button>
        <span className="section-actions">
          <PresetsButton canvasId={null} />
          <button className="icon-btn ghost sm" title="New space (⌃⌘N)" onClick={() => void useCanvas.getState().newSpace()}><Plus size={14} /></button>
        </span>
      </div>
      {open && (
        <div className="project-list space-list">
          {loaded && order.length === 0 && <p className="empty-hint">No spaces yet.</p>}
          {!loaded && loadFailed && (
            <p className="empty-hint">
              Couldn't load spaces.{' '}
              <button className="link" onClick={() => void useCanvas.getState().load().catch(() => undefined)}>Retry</button>
            </p>
          )}
          {order.map((id) => <SpaceRow key={id} canvasId={id} />)}
        </div>
      )}
    </>
  )
}
