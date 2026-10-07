import { useCallback, useEffect, useState } from 'react'
import { Bot, ChevronDown, ChevronRight, RotateCcw, Square } from 'lucide-react'
import type { WorkerInfo } from '@shared/types'
import { api } from '../lib/api'
import { useStore, useConversation } from '../store'
import { sortWorkers, upsertWorker, workerActions, workerIsLive, workerLine } from '../lib/workers'

const TONE: Record<WorkerInfo['status'], string> = { queued: '', running: 'working', awaiting_approval: 'needs-you', done: 'done', error: 'failed', interrupted: 'failed', stopped: '' }
/** Ended workers stay listed so their transcript can be opened; only the newest few, to keep the panel short. */
const ENDED_SHOWN = 4

/**
 * The chat's background workers (the assistant's `delegate` tool), under its checklist: status, the current action,
 * Stop / Resume, and approval cards the worker is waiting on. Refetches on the app topic's `workers` event with a
 * 3 s poll as a fallback while any worker is live. Hidden when the chat has none.
 */
export default function WorkersPanel({ conversationId: focusId }: { conversationId?: string }): JSX.Element | null {
  const conversationId = useConversation(focusId)?.id // the main view passes no id: resolve the focused chat
  const [workers, setWorkers] = useState<WorkerInfo[]>([])
  const [open, setOpen] = useState(true)
  const [busy, setBusy] = useState<string | null>(null)
  const { openSubagent, toast } = useStore()

  const load = useCallback((): void => {
    if (conversationId) void api.workers.list(conversationId).then((r) => setWorkers(r.workers)).catch(() => undefined)
  }, [conversationId])
  useEffect(() => {
    setWorkers([])
    load()
    const onEvent = (e: Event): void => {
      const d = (e as CustomEvent<{ conversation_id: string; worker: WorkerInfo }>).detail
      if (d.conversation_id === conversationId) setWorkers((ws) => upsertWorker(ws, d.worker))
    }
    window.addEventListener('grain-workers', onEvent)
    return () => window.removeEventListener('grain-workers', onEvent)
  }, [conversationId, load])
  const anyLive = workers.some(workerIsLive)
  useEffect(() => {
    if (!anyLive) return
    const t = setInterval(load, 3000)
    return () => clearInterval(t)
  }, [anyLive, load])

  if (!workers.length) return null
  const sorted = sortWorkers(workers)
  const shown = [...sorted.filter(workerIsLive), ...sorted.filter((w) => !workerIsLive(w)).slice(0, ENDED_SHOWN)]
  const live = workers.filter(workerIsLive).length

  const act = async (id: string, fn: () => Promise<unknown>): Promise<void> => {
    setBusy(id)
    try { await fn() } catch (e) { toast((e as Error).message, 'error') } finally { setBusy(null); load() }
  }

  return (
    <section className="plan-panel worker-panel" aria-label="Workers">
      <header>
        <button className="plan-head" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
          {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
          <Bot size={14} />
          <b>Workers</b>
          <span className="muted">{live ? `${live} running` : `${workers.length} finished`}</span>
        </button>
      </header>
      {open && (
        <ul className="plan-steps worker-list">
          {shown.map((w) => {
            const a = workerActions(w)
            return (
              <li key={w.id} className="worker-row">
                <span className={`inbox-dot ${TONE[w.status]}`} aria-hidden />
                <button className="worker-open" title="Open the transcript" onClick={() => openSubagent(w.id)}>
                  <span className="worker-title">{w.title || w.goal}</span>
                  <small>{workerLine(w)}</small>
                </button>
                {a.stop && <button className="ghost-btn sm" disabled={busy === w.id} onClick={() => void act(w.id, () => api.workers.stop(w.id))}><Square size={11} /> Stop</button>}
                {a.resume && <button className="ghost-btn sm" disabled={busy === w.id} onClick={() => void act(w.id, () => api.workers.resume(w.id))}><RotateCcw size={11} /> Resume</button>}
                {w.pending_approvals.map((p) => (
                  <div key={p.call_id} className="worker-approval">
                    <span>Wants to use <code>{p.tool}</code></span>
                    <button className="ghost-btn sm" disabled={busy === p.call_id} onClick={() => void act(p.call_id, () => api.approve(p.call_id, 'allow'))}>Approve</button>
                    <button className="ghost-btn sm danger" disabled={busy === p.call_id} onClick={() => void act(p.call_id, () => api.approve(p.call_id, 'deny'))}>Deny</button>
                  </div>
                ))}
              </li>
            )
          })}
        </ul>
      )}
    </section>
  )
}
