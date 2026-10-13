import { describeCall } from '../lib/toolDisplay'
import { useEffect, useState } from 'react'
import { useShallow } from 'zustand/react/shallow'
import { RotateCcw, Square } from '../icons'
import type { WorkerInfo } from '@shared/types'
import { api } from '../lib/api'
import { useStore, useConversation, useWorkerFace, useWorkers, useChatFaceById, useStreamingMessageId } from '../store'
import TraceView from './TraceView'
import Face from './Face'
import WorkerChat from './WorkerChat'
import { workersCardShown } from '../lib/statusChrome'
import { fmtCost } from '../lib/chatMeta'
import { fmtMs } from '../lib/toolDisplay'
import { ENDED_SHOWN, liveWorkerCount, listedWorkers, treeOrder, workerActions, workerElapsedMs, workerIsLive, workerWord } from '../lib/workers'

const TONE: Record<WorkerInfo['status'], string> = { queued: '', running: 'working', awaiting_approval: 'needs-you', done: 'done', error: 'failed', interrupted: 'failed', stopped: '' }

function WorkerFace({ w }: { w: WorkerInfo }): JSX.Element {
  return <Face {...useWorkerFace(w)} status={w.status === 'awaiting_approval' ? 'needs_approval' : w.status} size={16} />
}

/** The main agent's row: unfolds the steps of the chat's latest reply (the streaming one while it runs). */
function MainAgentRow({ conversationId }: { conversationId: string }): JSX.Element {
  const [on, setOn] = useState(false)
  const face = useChatFaceById(conversationId)
  const streamingId = useStreamingMessageId(conversationId)
  const msg = useConversation(conversationId)?.messages?.findLast((m) => m.id === streamingId || m.trace?.length)
  const spans = msg?.trace ?? []
  return (
    <li className={`worker-row ${on ? 'on' : ''}`}>
      <div className="worker-line">
        <button className="worker-open" aria-expanded={on} title={on ? 'Hide steps' : 'Show steps'} onClick={() => setOn((v) => !v)}>
          <Face {...face} status={streamingId ? 'streaming' : 'idle'} size={16} />
          <span className="worker-title">Main agent</span>
          <small>{streamingId ? 'working' : ''}</small>
        </button>
      </div>
      {on && (
        <div className="worker-chat">
          {spans.length ? <div className="worker-steps"><TraceView spans={spans} live={!!streamingId && msg?.id === streamingId} model={msg?.model ?? null} reasoning={msg?.reasoning} /></div> : <p className="muted small">No steps yet.</p>}
        </div>
      )}
    </li>
  )
}

/**
 * The chat's background workers (the assistant's `delegate` tool), under its checklist: a face, the title, a status word and
 * Stop / Resume per row; a row unfolds to the worker's chat (WorkerChat): its history, and a box to steer or stop it.
 * Approval cards the worker is waiting on stay inline. Fed by the app topic's `workers` event (see the store), with a
 * 3 s poll as a fallback while any worker is live. The card shows only while one is live; finished workers are listed
 * (newest few) only after "Show finished", a link at the foot of the open list.
 */
export default function WorkersPanel({ conversationId: focusId }: { conversationId?: string }): JSX.Element | null {
  const conversationId = useConversation(focusId)?.id // the main view passes no id: resolve the focused chat
  const workers = useWorkers(conversationId)
  const [open, setOpen] = useState(true)
  const [showFinished, setShowFinished] = useState(false)
  const [showAll, setShowAll] = useState(false)
  const [busy, setBusy] = useState<string | null>(null)
  const [unfolded, setUnfolded] = useState<string | null>(null)
  const { toast, loadWorkers } = useStore(useShallow((s) => ({ toast: s.toast, loadWorkers: s.loadWorkers })))

  useEffect(() => { if (conversationId) void loadWorkers(conversationId) }, [conversationId, loadWorkers])
  const anyLive = workers.some(workerIsLive)
  useEffect(() => {
    if (!anyLive || !conversationId) return
    const t = setInterval(() => void loadWorkers(conversationId), 3000)
    return () => clearInterval(t)
  }, [anyLive, conversationId, loadWorkers])

  if (!workers.length || !workersCardShown(liveWorkerCount(workers))) return null
  const shown = treeOrder(listedWorkers(workers, showFinished, showAll))
  const live = liveWorkerCount(workers)
  const anyEnded = workers.some((w) => !workerIsLive(w))

  const act = async (id: string, fn: () => Promise<unknown>): Promise<void> => {
    setBusy(id)
    try { await fn() } catch (e) { toast((e as Error).message, 'error') } finally { setBusy(null); if (conversationId) void loadWorkers(conversationId) }
  }

  return (
    <section className="plan-panel worker-panel" aria-label="Workers">
      <header>
        <button className="plan-head" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
          <b>Workers</b>
          {open
            ? <span className="muted">· {live ? `${live} running` : workers.length}</span>
            : <span className="worker-faces">{workers.filter(workerIsLive).map((w) => <WorkerFace key={w.id} w={w} />)}</span>}
        </button>
      </header>
      {open && (
        <ul className="plan-steps worker-list">
          {conversationId && <MainAgentRow conversationId={conversationId} />}
          {shown.map((w) => {
            const a = workerActions(w)
            const on = unfolded === w.id
            return (
              <li key={w.id} className={`worker-row ${on ? 'on' : ''}`} style={w.depth > 1 ? { paddingLeft: (w.depth - 1) * 14 } : undefined}>
                <div className="worker-line">
                  <button className="worker-open" aria-expanded={on} title={on ? 'Hide chat' : 'Open chat'} onClick={() => setUnfolded(on ? null : w.id)}>
                    <WorkerFace w={w} />
                    <span className="worker-title">{w.title || w.goal}</span>
                    <small className={w.status === 'awaiting_approval' ? 'needs-you' : undefined}>{workerWord(w)}</small>
                  </button>
                  {a.stop && !on && <button className="ghost-btn xs" disabled={busy === w.id} onClick={() => void act(w.id, () => api.workers.stop(w.id))}><Square size={11} /> Stop</button>}
                  {a.resume && <button className="ghost-btn xs" disabled={busy === w.id} onClick={() => void act(w.id, () => api.workers.resume(w.id))}><RotateCcw size={11} /> Resume</button>}
                </div>
                {(() => {
                  const bits = [w.now, w.cost ? fmtCost(w.cost) : '', w.started_at ? fmtMs(workerElapsedMs(w)) : '', on ? [w.model, w.effort].filter(Boolean).join(' · ') : ''].filter(Boolean)
                  return bits.length ? <div className="worker-meta muted small">{bits.join(' · ')}</div> : null
                })()}
                {on && w.error && (w.status === 'error' || w.status === 'interrupted') && <p className="worker-error small">{w.error}</p>}
                {on && <WorkerChat id={w.id} onStop={() => act(w.id, () => api.workers.stop(w.id))} />}
                {w.pending_approvals.map((p) => (
                  <div key={p.call_id} className="worker-approval">
                    <span>Wants to: {describeCall(p.tool, p.args).verb.toLowerCase()}</span>
                    <button className="ghost-btn xs" disabled={busy === p.call_id} onClick={() => void act(p.call_id, () => api.approve(p.call_id, 'allow'))}>Approve</button>
                    <button className="ghost-btn xs danger" disabled={busy === p.call_id} onClick={() => void act(p.call_id, () => api.approve(p.call_id, 'deny'))}>Deny</button>
                  </div>
                ))}
              </li>
            )
          })}
        </ul>
      )}
      {open && showFinished && workers.filter((w) => !workerIsLive(w)).length > ENDED_SHOWN && <button className="ghost-btn xs worker-finished" aria-expanded={showAll} onClick={() => setShowAll((v) => !v)}>{showAll ? 'Show fewer' : 'Show all'}</button>}
      {open && anyEnded && <button className="ghost-btn xs worker-finished" aria-expanded={showFinished} aria-label={showFinished ? 'Hide finished workers' : 'Show finished workers'} onClick={() => setShowFinished((v) => !v)}>{showFinished ? 'Hide finished' : 'Show finished'}</button>}
    </section>
  )
}
