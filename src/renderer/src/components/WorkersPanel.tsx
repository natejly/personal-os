import { useEffect, useState } from 'react'
import { ChevronDown, ChevronRight, RotateCcw, Square } from 'lucide-react'
import type { WorkerInfo } from '@shared/types'
import { api } from '../lib/api'
import { useStore, useConversation, useWorkerFace, useWorkers } from '../store'
import Face from './Face'
import { workersCardShown } from '../lib/statusChrome'
import { liveWorkerCount, sortWorkers, workerActions, workerIsLive, workerWord } from '../lib/workers'

const TONE: Record<WorkerInfo['status'], string> = { queued: '', running: 'working', awaiting_approval: 'needs-you', done: 'done', error: 'failed', interrupted: 'failed', stopped: '' }
/** Ended workers stay listed so their transcript can be opened; only the newest few, to keep the panel short. */
const ENDED_SHOWN = 4

function WorkerFace({ w }: { w: WorkerInfo }): JSX.Element {
  return <Face {...useWorkerFace(w)} status={w.status === 'awaiting_approval' ? 'needs_approval' : w.status} size={16} />
}

/**
 * The chat's background workers (the assistant's `delegate` tool), under its checklist: a face, the title, a status word and
 * Stop / Resume per row; a row unfolds to the current action or, once ended, the final report and a link to the transcript.
 * Approval cards the worker is waiting on stay inline. Fed by the app topic's `workers` event (see the store), with a
 * 3 s poll as a fallback while any worker is live. Hidden when the chat has none.
 */
/** `all`: the side panel's list, finished workers included. Above the composer (the default) it shows only while one is live. */
export default function WorkersPanel({ conversationId: focusId, all = false }: { conversationId?: string; all?: boolean }): JSX.Element | null {
  const conversationId = useConversation(focusId)?.id // the main view passes no id: resolve the focused chat
  const workers = useWorkers(conversationId)
  const [open, setOpen] = useState(true)
  const [busy, setBusy] = useState<string | null>(null)
  const [unfolded, setUnfolded] = useState<string | null>(null)
  const { openSubagent, toast, loadWorkers } = useStore()

  useEffect(() => { if (conversationId) void loadWorkers(conversationId) }, [conversationId, loadWorkers])
  const anyLive = workers.some(workerIsLive)
  useEffect(() => {
    if (!anyLive || !conversationId) return
    const t = setInterval(() => void loadWorkers(conversationId), 3000)
    return () => clearInterval(t)
  }, [anyLive, conversationId, loadWorkers])

  if (!workers.length || (!all && !workersCardShown(liveWorkerCount(workers)))) return null
  const sorted = sortWorkers([...workers])
  const shown = [...sorted.filter(workerIsLive), ...sorted.filter((w) => !workerIsLive(w)).slice(0, ENDED_SHOWN)]
  const live = liveWorkerCount(workers)

  const act = async (id: string, fn: () => Promise<unknown>): Promise<void> => {
    setBusy(id)
    try { await fn() } catch (e) { toast((e as Error).message, 'error') } finally { setBusy(null); if (conversationId) void loadWorkers(conversationId) }
  }

  return (
    <section className="plan-panel worker-panel" aria-label="Workers">
      <header>
        <button className="plan-head" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
          {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
          <b>Workers</b>
          <span className="muted">{live ? `${live} running` : workers.length}</span>
        </button>
      </header>
      {open && (
        <ul className="plan-steps worker-list">
          {shown.map((w) => {
            const a = workerActions(w)
            const on = unfolded === w.id
            return (
              <li key={w.id} className={`worker-row ${on ? 'on' : ''}`}>
                <div className="worker-line">
                  <button className="worker-open" aria-expanded={on} title={on ? 'Hide details' : 'Show details'} onClick={() => setUnfolded(on ? null : w.id)}>
                    {on ? <ChevronDown size={11} /> : <ChevronRight size={11} />}
                    <WorkerFace w={w} />
                    <span className="worker-title">{w.title || w.goal}</span>
                    <small>{workerWord(w)}</small>
                  </button>
                  {a.stop && <button className="ghost-btn xs" disabled={busy === w.id} onClick={() => void act(w.id, () => api.workers.stop(w.id))}><Square size={11} /> Stop</button>}
                  {a.resume && <button className="ghost-btn xs" disabled={busy === w.id} onClick={() => void act(w.id, () => api.workers.resume(w.id))}><RotateCcw size={11} /> Resume</button>}
                </div>
                {on && (
                  <div className="worker-detail">
                    {workerIsLive(w) && w.now && <p className="muted small">{w.now}</p>}
                    {!workerIsLive(w) && w.report && <pre className="worker-report">{w.report}</pre>}
                    <button className="ghost-btn xs" onClick={() => openSubagent(w.id)}>Open transcript</button>
                  </div>
                )}
                {w.pending_approvals.map((p) => (
                  <div key={p.call_id} className="worker-approval">
                    <span>Wants to use <code>{p.tool}</code></span>
                    <button className="ghost-btn xs" disabled={busy === p.call_id} onClick={() => void act(p.call_id, () => api.approve(p.call_id, 'allow'))}>Approve</button>
                    <button className="ghost-btn xs danger" disabled={busy === p.call_id} onClick={() => void act(p.call_id, () => api.approve(p.call_id, 'deny'))}>Deny</button>
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
