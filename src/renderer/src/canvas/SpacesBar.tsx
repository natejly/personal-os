import { useState } from 'react'
import { AlignJustify, LayoutGrid, Lock, LockOpen, Plus, Trash2 } from 'lucide-react'
import type { DragPayload, SnapMode } from '@shared/types'
import { api } from '../lib/api'
import { useProject, useStore } from '../store'
import { AddWidgetButton } from './AddWidgetMenu'
import { PresetsButton } from './PresetsMenu'
import { hasDrag, readDrag } from './dnd'
import { GRID_SIZES } from './snapping'
import { useCanvas, useSpaceLocked } from './store'

/** Reorder payload, local to the bar: a space tab is not one of the shared `DragKind`s. */
const SPACE_MIME = 'application/x-personal-os-space'

const SNAP_LABEL: Record<SnapMode, string> = { off: 'No snap', grid: 'Grid', guides: 'Guides', both: 'Grid + guides' }

/** §7: a project dropped on a tab binds the space; every other payload belongs to the plane. */
const projectDrag = (dt: DataTransfer): DragPayload | null => {
  const p = readDrag(dt)
  return p && p.kind === 'project' ? p : null
}

/** `position` is writable but the store has no `reorderSpace`, so the bar renumbers and reloads. */
const reorder = async (fromId: string, toId: string): Promise<void> => {
  const st = useCanvas.getState()
  const ids = st.order.filter((id) => id !== fromId)
  const at = ids.indexOf(toId)
  ids.splice(at < 0 ? ids.length : at, 0, fromId)
  await Promise.all(ids.map((id, i) => api.canvases.update(id, { position: i })))
  await st.load()
}

/** Its own component so a tab subscribes to its own primitives, not to the whole canvases map. */
function Tab({ canvasId, index }: { canvasId: string; index: number }): JSX.Element | null {
  const name = useCanvas((s) => s.canvases[canvasId]?.name)
  const count = useCanvas((s) => s.canvases[canvasId]?.windows.length ?? 0)
  const projectId = useCanvas((s) => s.canvases[canvasId]?.project_id ?? null)
  const active = useCanvas((s) => s.activeCanvasId === canvasId)
  const locked = useCanvas((s) => !!s.canvases[canvasId]?.locked)
  const project = useProject(projectId)
  const [editing, setEditing] = useState<string | null>(null)
  const [over, setOver] = useState<'' | 'project' | 'space'>('')
  const [dragging, setDragging] = useState(false)
  if (name === undefined) return null

  const commit = (): void => {
    const next = (editing ?? '').trim()
    setEditing(null)
    if (next && next !== name) void useCanvas.getState().renameSpace(canvasId, next)
  }
  const drop = (e: React.DragEvent): void => {
    e.preventDefault()
    setOver('')
    const from = e.dataTransfer.getData(SPACE_MIME)
    if (from && from !== canvasId) return void reorder(from, canvasId)
    const p = projectDrag(e.dataTransfer)
    if (p) void useCanvas.getState().bindSpace(canvasId, p.id)
  }

  return (
    <div
      className={['space-tab', active && 'active', over && 'drop-target', dragging && 'dragging'].filter(Boolean).join(' ')}
      title={`${name}${project ? ` · ${project.name}` : ''} · ${count} window${count === 1 ? '' : 's'}${locked ? ' · locked' : ''}`}
      draggable={editing === null}
      onDragStart={(e) => {
        setDragging(true)
        e.dataTransfer.setData(SPACE_MIME, canvasId)
        e.dataTransfer.effectAllowed = 'move'
      }}
      onDragEnd={() => setDragging(false)}
      onDragOver={(e) => {
        const kind = e.dataTransfer.types.includes(SPACE_MIME) ? 'space' : hasDrag(e.dataTransfer) ? 'project' : ''
        if (!kind) return
        e.preventDefault()
        e.dataTransfer.dropEffect = kind === 'space' ? 'move' : 'link'
        setOver(kind)
      }}
      onDragLeave={() => setOver('')}
      onDrop={drop}
      onClick={() => useCanvas.getState().setActiveCanvas(canvasId)}
      onDoubleClick={() => setEditing(name)}
    >
      {project && <span className="space-dot" style={{ background: project.color }} />}
      {editing === null ? <span>{name}</span> : (
        <input
          autoFocus
          value={editing}
          onChange={(e) => setEditing(e.target.value)}
          onBlur={commit}
          onClick={(e) => e.stopPropagation()}
          onKeyDown={(e) => {
            if (e.key === 'Enter') commit()
            if (e.key === 'Escape') setEditing(null)
          }}
        />
      )}
      {index < 9 && <span className="space-count">⌃{index + 1}</span>}
      {/* The padlock replaces the delete button rather than joining it: `deleteSpace` refuses a
          locked space, so the trash would be a button that does nothing but toast. */}
      {locked ? <Lock size={11} className="space-lock" /> : active && (
        <button
          className="icon-btn ghost sm danger"
          title="Delete space"
          onClick={(e) => {
            e.stopPropagation()
            if (count === 0 || confirm(`Delete "${name}" and its ${count} windows?`)) void useCanvas.getState().deleteSpace(canvasId)
          }}
        >
          <Trash2 size={11} />
        </button>
      )}
    </div>
  )
}

export default function SpacesBar(): JSX.Element {
  const order = useCanvas((s) => s.order)
  const overview = useCanvas((s) => s.overview)
  const activeId = useCanvas((s) => s.activeCanvasId)
  const snapMode = useCanvas((s) => (s.activeCanvasId ? s.canvases[s.activeCanvasId]?.snap_mode : undefined))
  const gridSize = useCanvas((s) => (s.activeCanvasId ? s.canvases[s.activeCanvasId]?.grid_size : undefined))
  const locked = useSpaceLocked()
  const sidebarOpen = useStore((s) => s.sidebarOpen)

  return (
    <div className="spaces-bar">
      {/* Clears the window's traffic lights once the sidebar is gone, and drags the app window. */}
      {!sidebarOpen && <span className="spaces-inset drag" />}
      {order.map((id, i) => <Tab key={id} canvasId={id} index={i} />)}
      <button className="icon-btn ghost sm" title="New space (⌃⌘N)" onClick={() => void useCanvas.getState().newSpace()}><Plus size={14} /></button>
      <PresetsButton canvasId={activeId} />
      <span className="spacer" />
      {/* Deliberately apart from the new-space + beside the tabs: this one adds to the space. */}
      <AddWidgetButton />
      {activeId && (
        <>
          <select
            className="space-snap"
            title="Snapping"
            value={snapMode ?? 'both'}
            onChange={(e) => void useCanvas.getState().setSnap(activeId, { snap_mode: e.target.value as SnapMode })}
          >
            {(Object.keys(SNAP_LABEL) as SnapMode[]).map((m) => <option key={m} value={m}>{SNAP_LABEL[m]}</option>)}
          </select>
          <select
            className="space-snap"
            title="Grid pitch"
            value={gridSize ?? 16}
            onChange={(e) => void useCanvas.getState().setSnap(activeId, { grid_size: Number(e.target.value) })}
          >
            {GRID_SIZES.map((g) => <option key={g} value={g}>{g} pt</option>)}
          </select>
        </>
      )}
      <button className="icon-btn ghost sm" title="Tidy up (⌃⌘T)" disabled={locked} onClick={() => useCanvas.getState().tidyUp()}><AlignJustify size={14} /></button>
      <button
        className={`icon-btn ghost sm${locked ? ' on' : ''}`}
        title={locked ? 'Unlock space (⌃⌘L)' : 'Lock space (⌃⌘L)'}
        disabled={!activeId}
        onClick={() => useCanvas.getState().toggleLock()}
      >
        {locked ? <Lock size={14} /> : <LockOpen size={14} />}
      </button>
      <button className={`icon-btn ghost sm${overview ? ' on' : ''}`} title="Space overview (⌥⌘↑)" onClick={() => useCanvas.getState().toggleOverview()}><LayoutGrid size={14} /></button>
    </div>
  )
}
