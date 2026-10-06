import { useEffect, useRef, useState } from 'react'
import { AppWindow, Check, LayoutGrid, Plus } from 'lucide-react'
import type { CanvasWindow, WidgetKind } from '@shared/types'
import { api } from '../lib/api'
import { useProject, useStore } from '../store'
import { useCanvas } from '../canvas/store'
import { Popover } from '../canvas/PresetsMenu'
import '../styles/spaces.css'

export interface SendItem { kind: WidgetKind; refId?: string | null; config?: Record<string, unknown> }

/** How long the button shows its check after a send. */
const SENT_MS = 1200

/** Its own component so a row subscribes to its own primitives, not to the whole canvases map. */
function SendRow({ canvasId, onPick }: { canvasId: string; onPick: (canvasId: string) => void }): JSX.Element | null {
  const name = useCanvas((s) => s.canvases[canvasId]?.name)
  const count = useCanvas((s) => s.canvases[canvasId]?.windows.length ?? 0)
  const projectId = useCanvas((s) => s.canvases[canvasId]?.project_id ?? null)
  const project = useProject(projectId)
  if (name === undefined) return null
  return (
    <button className="send-row" title={`${name}${project ? ` · ${project.name}` : ''} · ${count} window${count === 1 ? '' : 's'}`} onClick={() => onPick(canvasId)}>
      {project ? <span className="project-dot" style={{ background: project.color }} /> : <LayoutGrid size={12} className="send-row-icon" />}
      <span className="project-name">{name}</span>
      <span className="count">{count}</span>
    </button>
  )
}

/**
 * A classic header's "Send to space": adds (or surfaces) this view's content as windows in a chosen
 * space, without leaving the view. Sending the same thing twice focuses the window already there.
 */
export default function SendToSpace({ items, disabled, title }: { items: SendItem[]; disabled?: boolean; title?: string }): JSX.Element {
  const order = useCanvas((s) => s.order)
  const [at, setAt] = useState<{ x: number; y: number } | null>(null)
  const [sent, setSent] = useState(false)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  useEffect(() => () => {
    if (timer.current) clearTimeout(timer.current)
  }, [])
  const close = (): void => setAt(null)

  const send = async (canvasId: string): Promise<void> => {
    close()
    const results: { win: CanvasWindow | null; existed: boolean }[] = []
    // One at a time, so each window's spawn point sees the ones placed before it.
    for (const it of items) results.push(await useCanvas.getState().ensureWindow(canvasId, it.kind, it.refId ?? null, it.config))
    // Every open failed: the store has already toasted why.
    if (!results.some((r) => r.win)) return
    const name = useCanvas.getState().canvases[canvasId]?.name ?? 'space'
    useStore.getState().toast(results.every((r) => r.existed) ? `Already in "${name}"` : `Sent to "${name}"`, 'info')
    setSent(true)
    if (timer.current) clearTimeout(timer.current)
    timer.current = setTimeout(() => {
      timer.current = null
      setSent(false)
    }, SENT_MS)
  }

  const sendToNew = async (): Promise<void> => {
    close()
    try {
      const c = await api.canvases.create({})
      useCanvas.getState().adoptSpace(c, false)
      await send(c.id)
    } catch (e) {
      useStore.getState().toast((e as Error)?.message ?? String(e), 'error')
    }
  }

  return (
    <>
      <button
        className={`icon-btn no-drag send-btn${sent ? ' on' : ''}`}
        title={title ?? 'Send to space'}
        disabled={disabled || !items.length}
        onClick={(e) => {
          const r = e.currentTarget.getBoundingClientRect()
          setAt({ x: r.left, y: r.bottom + 4 })
          if (!useCanvas.getState().loaded) void useCanvas.getState().load().catch(() => undefined)
        }}
      >
        {sent ? <Check size={15} /> : <AppWindow size={15} />}
      </button>
      {at && (
        <Popover at={at} onClose={close}>
          <h4>Send to space</h4>
          {order.map((id) => <SendRow key={id} canvasId={id} onPick={(cid) => void send(cid)} />)}
          <button className="send-row new" onClick={() => void sendToNew()}>
            <Plus size={12} className="send-row-icon" />
            <span className="project-name">New space</span>
          </button>
        </Popover>
      )}
    </>
  )
}
