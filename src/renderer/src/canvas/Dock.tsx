import { useState } from 'react'
import { useCanvas, useWindows } from './store'
import { KIND_ICON, KIND_LABEL } from './WindowHost'

/** 1.35 under the pointer, 1.175 for its neighbour, 1.0 beyond — the Dock falloff. */
const magnify = (i: number, hover: number): number => (hover < 0 ? 1 : 1 + 0.35 * Math.max(0, 1 - Math.abs(i - hover) / 2))

export default function Dock(): JSX.Element | null {
  const windows = useWindows()
  const focusedId = useCanvas((s) => s.focusedWindowId)
  const setWindowState = useCanvas((s) => s.setWindowState)
  const focusWindow = useCanvas((s) => s.focusWindow)
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
          onPointerEnter={() => setHover(i)}
          onClick={() => {
            void setWindowState(w.id, 'normal')
            focusWindow(w.id)
          }}
        >
          {KIND_ICON[w.kind]}
          <span className="dock-tile-label">{w.title || KIND_LABEL[w.kind]}</span>
        </button>
      ))}
    </div>
  )
}
