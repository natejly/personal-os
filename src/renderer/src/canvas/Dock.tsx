import { useState } from 'react'
import { WIDGETS } from './registry'
import StatusRing from './StatusRing'
import { useCanvas, useSpaceLocked, useWindows } from './store'
import { KIND_ICON, KIND_LABEL } from './WindowHost'

/** 1.35 under the pointer, 1.175 for its neighbour, 1.0 beyond — the Dock falloff. */
const magnify = (i: number, hover: number): number => (hover < 0 ? 1 : 1 + 0.35 * Math.max(0, 1 - Math.abs(i - hover) / 2))

export default function Dock(): JSX.Element | null {
  const windows = useWindows()
  const focusedId = useCanvas((s) => s.focusedWindowId)
  const setWindowState = useCanvas((s) => s.setWindowState)
  const focusWindow = useCanvas((s) => s.focusWindow)
  // Restoring a tile puts a window back on the plane, which a locked space does not allow.
  const locked = useSpaceLocked()
  const [hover, setHover] = useState(-1)

  const tiles = windows.filter((w) => w.state === 'minimized')
  if (!tiles.length) return null

  return (
    <div className="dock" onPointerLeave={() => setHover(-1)}>
      {tiles.map((w, i) => (
        <button
          key={w.id}
          className={focusedId === w.id ? 'dock-tile focused' : 'dock-tile'}
          style={{ transform: `scale(${magnify(i, hover)})` }}
          disabled={locked}
          title={locked ? 'Space locked' : undefined}
          onPointerEnter={() => setHover(i)}
          onClick={() => {
            void setWindowState(w.id, 'normal')
            focusWindow(w.id)
          }}
        >
          {KIND_ICON[w.kind]}
          {/* §8: the tile reads the same `useRingStatus` as the window, so a minimized chat still shows amber. */}
          {WIDGETS[w.kind]?.statusful && <StatusRing conversationId={w.ref_id} size={12} />}
          <span className="dock-tile-label">{w.title || KIND_LABEL[w.kind]}</span>
        </button>
      ))}
    </div>
  )
}
