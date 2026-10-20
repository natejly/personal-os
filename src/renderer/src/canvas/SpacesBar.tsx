import type { SnapMode } from '@shared/types'
import { useState } from 'react'
import { AlignJustify, LayoutGrid, Lock, LockOpen, SlidersHorizontal } from '../icons'
import { Popover } from './Popover'
import { useStore } from '../store'
import { AddWidgetButton } from './AddWidgetMenu'
import AppSwitcher from '../components/AppSwitcher'
import { useCanvas, useSpaceLocked } from './store'
import { GRID_SIZES } from './snapping'
import { tip } from '../lib/tip'

const SNAP_LABEL: Record<SnapMode, string> = { off: 'No snap', grid: 'Grid', guides: 'Guides', both: 'Grid + guides' }

/** The active space's snapping, saved on the space as soon as it changes. */
function SnapSelect({ canvasId }: { canvasId: string | null }): JSX.Element {
  const mode = useCanvas((s) => (canvasId ? s.canvases[canvasId]?.snap_mode : undefined) ?? 'both')
  const grid = useCanvas((s) => (canvasId ? s.canvases[canvasId]?.grid_size : undefined) ?? 16)
  const set = (patch: { snap_mode?: SnapMode; grid_size?: number }): void => {
    if (canvasId) void useCanvas.getState().setSnap(canvasId, patch)
  }
  return (
    <div className="space-layout">
      <label>Snapping
      <select className="space-snap" title="Snapping" aria-label="Snapping" value={mode} disabled={!canvasId} onChange={(e) => set({ snap_mode: e.target.value as SnapMode })}>
        {(Object.keys(SNAP_LABEL) as SnapMode[]).map((m) => <option key={m} value={m}>{SNAP_LABEL[m]}</option>)}
      </select>
      </label>
      {(mode === 'grid' || mode === 'both') && (
        <label>Grid size
        <select className="space-snap" title="Grid size" aria-label="Grid size" value={grid} disabled={!canvasId} onChange={(e) => set({ grid_size: Number(e.target.value) })}>
          {GRID_SIZES.map((g) => <option key={g} value={g}>{g} pt</option>)}
        </select>
        </label>
      )}
    </div>
  )
}

function LayoutButton({ canvasId }: { canvasId: string | null }): JSX.Element {
  const [at, setAt] = useState<{ x: number; y: number } | null>(null)
  return (
    <>
      <button className={`icon-btn ghost sm${at ? ' on' : ''}`} title="Layout" aria-label="Layout" aria-haspopup="dialog" aria-expanded={!!at} disabled={!canvasId}
        onClick={(e) => { const r = e.currentTarget.getBoundingClientRect(); setAt({ x: r.left - 80, y: r.bottom + 4 }) }}>
        <SlidersHorizontal size={14} />
      </button>
      {at && <Popover at={at} onClose={() => setAt(null)}><SnapSelect canvasId={canvasId} /></Popover>}
    </>
  )
}

export default function SpacesBar(): JSX.Element {
  const overview = useCanvas((s) => s.overview)
  const activeId = useCanvas((s) => s.activeCanvasId)
  const locked = useSpaceLocked()
  const sidebarOpen = useStore((s) => s.sidebarOpen)

  return (
    <div className="spaces-bar">
      {/* Clears the window's traffic lights once the sidebar is gone, and drags the app window. */}
      {!sidebarOpen && <span className="spaces-inset drag" />}
      <div className="space-tab active" title="Canvas 1"><span>Canvas 1</span></div>
      <span className="spacer" />
      {/* Deliberately apart from the new-space + beside the tabs: this one adds to the space. */}
      <AddWidgetButton />
      <button className="icon-btn ghost sm" title={tip('Tidy up', 'canvas-tidy')} disabled={locked} onClick={() => useCanvas.getState().tidyUp()}><AlignJustify size={14} /></button>
      <LayoutButton canvasId={activeId} />
      <button
        className={`icon-btn ghost sm${locked ? ' on' : ''}`}
        title={tip(locked ? 'Unlock canvas' : 'Lock canvas', 'canvas-lock')}
        disabled={!activeId}
        onClick={() => useCanvas.getState().toggleLock()}
      >
        {locked ? <Lock size={14} /> : <LockOpen size={14} />}
      </button>
      <button className={`icon-btn ghost sm${overview ? ' on' : ''}`} title={tip('Canvas overview', 'canvas-overview')} onClick={() => useCanvas.getState().toggleOverview()}><LayoutGrid size={14} /></button>
      <AppSwitcher />
    </div>
  )
}
