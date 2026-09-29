import { useState } from 'react'
import type { Canvas, CanvasWindow, Rect } from '@shared/types'
import { useProject } from '../store'
import { useCanvas } from './store'

/** Which window is being dragged between thumbnails. Not a shared `DragKind`: it never leaves here. */
const WINDOW_MIME = 'application/x-personal-os-window'
/** `.overview-proxy` aspect ratio; proxies are fitted into it without distortion. */
const ASPECT = 1.6
const PAD = 0.06

const union = (ws: CanvasWindow[]): Rect => {
  const x = Math.min(...ws.map((w) => w.x))
  const y = Math.min(...ws.map((w) => w.y))
  const w = Math.max(...ws.map((v) => v.x + v.w)) - x
  const h = Math.max(...ws.map((v) => v.y + v.h)) - y
  return { x: x - w * PAD, y: y - h * PAD, w: w * (1 + 2 * PAD), h: h * (1 + 2 * PAD) }
}

function Thumb({ canvas }: { canvas: Canvas }): JSX.Element {
  const active = useCanvas((s) => s.activeCanvasId === canvas.id)
  const project = useProject(canvas.project_id)
  const [over, setOver] = useState(false)

  const shown = canvas.windows.filter((w) => w.state !== 'minimized')
  const box = shown.length ? union(shown) : null
  const s = box ? Math.min(1 / box.w, 1 / (box.h * ASPECT)) : 0
  const ox = box ? (1 - box.w * s) / 2 : 0
  const oy = box ? (1 - box.h * s * ASPECT) / 2 : 0
  const pct = (w: CanvasWindow): { left: string; top: string; width: string; height: string } => ({
    left: `${(ox + (w.x - (box?.x ?? 0)) * s) * 100}%`,
    top: `${(oy + (w.y - (box?.y ?? 0)) * s * ASPECT) * 100}%`,
    width: `${w.w * s * 100}%`,
    height: `${w.h * s * ASPECT * 100}%`
  })

  return (
    <div
      className={['overview-space', active && 'active', over && 'drop-target'].filter(Boolean).join(' ')}
      onClick={(e) => {
        e.stopPropagation()
        useCanvas.getState().setActiveCanvas(canvas.id)
      }}
      onDragOver={(e) => {
        if (!e.dataTransfer.types.includes(WINDOW_MIME)) return
        e.preventDefault()
        e.dataTransfer.dropEffect = 'move'
        setOver(true)
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        e.preventDefault()
        setOver(false)
        const id = e.dataTransfer.getData(WINDOW_MIME)
        if (id) void useCanvas.getState().moveWindowToCanvas(id, canvas.id)
      }}
    >
      <div className="overview-proxy">
        {shown.map((w) => (
          <div
            key={w.id}
            className="overview-proxy-win"
            style={pct(w)}
            draggable
            onClick={(e) => e.stopPropagation()}
            onDragStart={(e) => {
              e.stopPropagation()
              e.dataTransfer.setData(WINDOW_MIME, w.id)
              e.dataTransfer.effectAllowed = 'move'
            }}
          />
        ))}
      </div>
      <div className="overview-label">
        {project && <span className="space-dot" style={{ background: project.color }} />}
        <span>{canvas.name}</span>
        <span className="space-count">{shown.length}</span>
      </div>
    </div>
  )
}

/** Mission Control: every space at once, static proxies only, drag a window between thumbnails. */
export default function Overview(): JSX.Element {
  const order = useCanvas((s) => s.order)
  const canvases = useCanvas((s) => s.canvases)
  return (
    <div className="overview" onClick={() => useCanvas.getState().toggleOverview()}>
      {order.map((id) => canvases[id] && <Thumb key={id} canvas={canvases[id]} />)}
    </div>
  )
}
