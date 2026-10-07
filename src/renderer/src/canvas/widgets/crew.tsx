import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Check, ExternalLink, Play, RotateCw, Square, Users } from 'lucide-react'
import type { CrewAgent, CrewView, WorkflowPlanStep, WorkflowStepRow } from '@shared/types'
import { api } from '../../lib/api'
import { useChatFaceById, useStore, useWorkerFace } from '../../store'
import { agentHue, faceSeed, libraryAgent, type FaceLook } from '../../lib/faces'
import Face from '../../components/Face'
import CrewRing from '../../components/CrewRing'
import { STATUS_LABEL as DESK_LABEL, fmtDur } from '../../lib/deskStatus'
import { ParamForm } from '../../components/WorkflowsPanel'
import type { WidgetDef, WidgetProps } from '../registry'

/**
 * The crew window: a desk, a workflow run or a saved workflow at the root and the agents working under it
 * as a small tree. Each row is a face posed by its status plus one line of what it is on right now; clicking
 * a row opens a card with the task, the live tool call and the numbers. When the root finishes while the
 * window is open, a pill at the head says so until it is clicked.
 */

type Tone = 'working' | 'needs-you' | 'done' | 'failed' | 'idle'

const TONE: Record<string, Tone> = {
  // desks
  planning: 'working', working: 'working', queued: 'idle', draft: 'idle', paused: 'idle', stopped: 'idle',
  awaiting_plan: 'needs-you', needs_approval: 'needs-you', blocked: 'needs-you', review: 'needs-you',
  interrupted: 'failed',
  // workflow runs
  awaiting_approval: 'needs-you', running: 'working', waiting_approval: 'needs-you', cancelled: 'idle', stale: 'idle',
  // agents (row status and live state)
  completed: 'done', partial: 'needs-you', error: 'failed',
  done: 'done', failed: 'failed', idle: 'idle'
}
const tone = (status: string): Tone => TONE[status] ?? 'idle'

const RUN_LABEL: Record<string, string> = {
  awaiting_approval: 'Plan to approve', running: 'Running', waiting_approval: 'Waiting on you', done: 'Done',
  failed: 'Failed', cancelled: 'Cancelled', interrupted: 'Interrupted', stale: 'Out of date', idle: 'Never run'
}
const AGENT_LABEL: Record<string, string> = {
  running: 'working', completed: 'done', done: 'done', partial: 'stopped early', error: 'failed', interrupted: 'interrupted',
  awaiting_approval: 'needs approval'
}
const STEP_MARK: Record<string, string> = {
  pending: '·', running: '…', waiting_approval: '?', done: '✓', failed: '✗', skipped: '–', blocked: '⊘'
}

/** Root states that end the work: the signal pill fires on the move into one of these. */
const ROOT_DONE = new Set(['done', 'failed', 'stopped', 'review', 'cancelled', 'interrupted', 'stale'])
const ROOT_LIVE = new Set(['planning', 'working', 'queued', 'needs_approval', 'blocked', 'awaiting_plan', 'running', 'waiting_approval'])

const rootLabel = (v: CrewView): string =>
  v.root.kind === 'desk' ? (DESK_LABEL as Record<string, string>)[v.root.status] ?? v.root.status : RUN_LABEL[v.root.status] ?? v.root.status

const agentStatus = (a: CrewAgent): string => a.state ?? a.status
const agentNow = (a: CrewAgent): string => {
  if (a.state === 'running') return a.now || 'thinking'
  if (a.state === null && a.status === 'running') return 'working'  // a row without a live child: an earlier process's
  if (a.error) return a.error
  return AGENT_LABEL[agentStatus(a)] ?? agentStatus(a)
}
const elapsed = (a: { started_at: number | null; ended_at: number | null }): string =>
  a.started_at ? fmtDur((a.ended_at ?? Date.now() / 1000) - a.started_at) : ''

function AgentCard({ a }: { a: CrewAgent }): JSX.Element {
  return (
    <div className="crew-pop">
      <p>{a.task || <span className="muted">No task recorded.</span>}</p>
      {(a.state === 'running' || a.status === 'running') && <p className="crew-pop-now">Now: {a.now || 'thinking'}</p>}
      {a.error && <p className="crew-pop-err">{a.error}</p>}
      <small className="muted">
        {AGENT_LABEL[agentStatus(a)] ?? agentStatus(a)}{a.exit_reason && a.exit_reason !== 'completed' ? ` (${a.exit_reason})` : ''}
        {a.rounds > 0 && ` · ${a.rounds} round${a.rounds === 1 ? '' : 's'}`}
        {a.calls > 0 && ` · ${a.calls} call${a.calls === 1 ? '' : 's'}`}
        {a.cost > 0 && ` · $${a.cost.toFixed(3)}`}
        {elapsed(a) && ` · ${elapsed(a)}`}
      </small>
    </div>
  )
}

function AgentRow({ a, depth, open, onToggle }: { a: CrewAgent; depth: number; open: boolean; onToggle: () => void }): JSX.Element {
  const status = agentStatus(a)
  const openSubagent = useStore((s) => s.openSubagent)
  const face = useWorkerFace({ id: a.id, agent: a.role })
  return (
    <>
      <div className={`crew-row ${open ? 'on' : ''}`} style={{ paddingLeft: 7 + depth * 18 }} role="button" tabIndex={0}
        aria-expanded={open} title={a.task} onClick={onToggle}
        onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onToggle() } }}>
        <Face {...face} status={status} size={20} />
        <span className="crew-name">{a.role}</span>
        <span className="crew-what">{agentNow(a)}</span>
        <span className={`crew-state ${tone(status)}`}>{AGENT_LABEL[status] ?? status}</span>
        <button className="icon-btn ghost xs" title="Open its transcript" onClick={(e) => { e.stopPropagation(); openSubagent(a.id) }}><ExternalLink size={11} /></button>
      </div>
      {open && <div style={{ paddingLeft: depth * 18 }}><AgentCard a={a} /></div>}
    </>
  )
}

function StepRow({ plan, row, depth }: { plan: WorkflowPlanStep; row: WorkflowStepRow | undefined; depth: number }): JSX.Element {
  const status = row?.status ?? 'pending'
  const what = plan.tool ?? (plan.kind === 'fan_out'
    ? `fan out · ${String((plan.fan_out as { agent?: { role?: unknown } })?.agent?.role ?? 'researcher')}`
    : `agent · ${String((plan.agent as { role?: unknown })?.role ?? 'researcher')}`)
  const items = row?.items ? Object.keys(row.items).length : 0
  const stepTone: Tone = status === 'running' ? 'working' : status === 'waiting_approval' ? 'needs-you' : status === 'done' ? 'done'
    : status === 'failed' || status === 'blocked' ? 'failed' : 'idle'
  return (
    <div className="crew-row step" style={{ paddingLeft: 7 + depth * 18 }} title={row?.error ?? undefined}>
      <span className={`crew-mark ${stepTone}`} aria-hidden>{STEP_MARK[status] ?? '·'}</span>
      <span className="crew-name">{plan.id}</span>
      <span className="crew-what">{what}{items > 0 && plan.kind === 'fan_out' ? ` · ${items} done` : ''}{row?.error ? ` · ${row.error}` : ''}</span>
      <span className={`crew-state ${stepTone}`}>{status === 'waiting_approval' ? 'needs approval' : status}</span>
    </div>
  )
}

function CrewWidget({ window: win, live, onTitle }: WidgetProps): JSX.Element {
  const ref = win.ref_id ?? ''
  const [view, setView] = useState<CrewView | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [openId, setOpenId] = useState<string | null>(null)
  const [focusId, setFocusId] = useState<string | null>(null)  // the agent the ring is centred on; null = the root
  const openSubagent = useStore((s) => s.openSubagent)
  const [running, setRunning] = useState(false)
  const [signal, setSignal] = useState<string | null>(null)
  const prev = useRef<string | null>(null)
  const toast = useStore((s) => s.toast)

  const load = useCallback(async (): Promise<void> => {
    if (!ref) return
    try {
      setView(await api.crew(ref))
      setErr(null)
    } catch (e) {
      setErr((e as Error).message)
    }
  }, [ref])

  // One read on show, then every announced change; nothing at all while off-screen.
  useEffect(() => {
    if (!live) return
    void load()
    const on = (): void => { void load() }
    window.addEventListener('grain-crew', on)
    return () => window.removeEventListener('grain-crew', on)
  }, [live, load])
  // A child's "now" line moves without an announcement, so a live tree also polls.
  const busy = !!view && (ROOT_LIVE.has(view.root.status) || view.agents.some((a) => a.state === 'running' || (a.state === null && a.status === 'running')))
  useEffect(() => {
    if (!live || !busy) return
    const t = setInterval(() => { void load() }, 2000)
    return () => clearInterval(t)
  }, [live, busy, load])

  // The pill fires only on the move into a finished state while the window watched, never on first paint.
  const status = view?.root.status ?? null
  useEffect(() => {
    if (!status) return
    if (prev.current && prev.current !== status && ROOT_DONE.has(status)) setSignal(status)
    prev.current = status
  }, [status])
  useEffect(() => {
    if (view && !win.title) onTitle(view.root.title)
  }, [view, win.title, onTitle])

  const byParent = useMemo(() => {
    const m = new Map<string | null, CrewAgent[]>()
    const ids = new Set((view?.agents ?? []).map((a) => a.id))
    for (const a of view?.agents ?? []) {
      const p = a.parent_id && ids.has(a.parent_id) ? a.parent_id : null
      m.set(p, [...(m.get(p) ?? []), a])
    }
    return m
  }, [view])

  const focus = view?.agents.find((a) => a.id === focusId) ?? null
  const ringKids = byParent.get(focus?.id ?? null) ?? []
  // A desk or chat root wears its chat's face; an agent wears its Library face, else its own id's.
  const rootConv = useStore((s) => (view?.root.kind === 'desk' ? s.desks.find((d) => d.id === view.root.id)?.conversation_id : view?.root.kind === 'chat' ? view.root.id : undefined))
  const rootFace = useChatFaceById(rootConv)
  const defs = useStore((s) => s.agentDefs)
  const look = (a: CrewAgent): FaceLook => { const agent = libraryAgent(a.role); return faceSeed({ id: a.id, agent, hue: agentHue(defs, agent) }) }

  const act = async (fn: () => Promise<unknown>): Promise<void> => {
    try { await fn() } catch (e) { toast((e as Error).message, 'error') }
    void load()
  }

  if (!live) return <div className="widget"><div className="widget-empty">Crew · paused</div></div>
  if (err) return <div className="widget"><div className="widget-empty">{err}</div></div>
  if (!view) return <div className="widget"><div className="widget-empty">Loading…</div></div>

  const { root, run, workflow } = view
  const branch = (parent: string | null, depth: number): JSX.Element[] =>
    (byParent.get(parent) ?? []).flatMap((a) => [
      <AgentRow key={a.id} a={a} depth={depth} open={openId === a.id} onToggle={() => setOpenId(openId === a.id ? null : a.id)} />,
      ...branch(a.id, depth + 1)
    ])

  // A workflow run nests each agent under the step that spawned it; whatever no step claims hangs off the root.
  let tree: JSX.Element[]
  if (run) {
    const rows = new Map(run.steps.map((s) => [s.step_id, s]))
    const claimed = new Set(run.steps.flatMap((s) => s.agents ?? []))
    tree = (run.plan ?? []).flatMap((p) => {
      const row = rows.get(p.id)
      const own = (row?.agents ?? []).map((id) => view.agents.find((a) => a.id === id)).filter((a): a is CrewAgent => !!a)
      return [
        <StepRow key={p.id} plan={p} row={row} depth={0} />,
        ...own.flatMap((a) => [
          <AgentRow key={a.id} a={a} depth={1} open={openId === a.id} onToggle={() => setOpenId(openId === a.id ? null : a.id)} />,
          ...branch(a.id, 2)
        ])
      ]
    })
    const loose = (byParent.get(null) ?? []).filter((a) => !claimed.has(a.id))
    tree.push(...loose.flatMap((a) => [
      <AgentRow key={a.id} a={a} depth={0} open={openId === a.id} onToggle={() => setOpenId(openId === a.id ? null : a.id)} />,
      ...branch(a.id, 1)
    ]))
  } else {
    tree = branch(null, 0)
  }

  const openRoot = (): void => {
    const app = useStore.getState()
    if (root.kind === 'desk') void app.goToDesk(root.id); else { app.setLibraryTab('automations'); app.setView('library') }
  }
  const resumable = run && ['interrupted', 'failed', 'cancelled'].includes(run.status) && run.approved_digest

  return (
    <div className="widget crew">
      <div className="widget-bar crew-head">
        <span className="crew-title" title={root.title}>{root.title}</span>
        <span className={`crew-state ${tone(root.status)}`}>{rootLabel(view)}</span>
        {signal && (
          <button className={`crew-signal ${tone(signal)}`} title="Click to dismiss" onClick={() => setSignal(null)}>
            {tone(signal) === 'done' ? <Check size={11} /> : null}{rootLabel(view)}
          </button>
        )}
        <span className="spacer" />
        {run?.status === 'awaiting_approval' && (
          <button className="primary-btn xs" title="Approve exactly this plan and start it"
            onClick={() => void act(() => api.workflows.approveRun(run.id, run.plan_digest))}><Check size={11} /> Approve</button>
        )}
        {run && ['running', 'waiting_approval', 'awaiting_approval'].includes(run.status) && (
          <button className="icon-btn ghost xs" title="Cancel the run" onClick={() => void act(() => api.workflows.cancelRun(run.id))}><Square size={11} /></button>
        )}
        {resumable && (
          <button className="icon-btn ghost xs" title="Resume from the first step that is not done" onClick={() => void act(() => api.workflows.resumeRun(run.id))}><RotateCw size={11} /></button>
        )}
        {workflow && (!run || ROOT_DONE.has(run.status)) && (
          <button className={`icon-btn ghost xs${running ? ' on' : ''}`} title="Run this workflow again" aria-pressed={running} onClick={() => setRunning((r) => !r)}><Play size={11} /></button>
        )}
        <button className="icon-btn ghost xs" title={root.kind === 'desk' ? 'Open the chat' : 'Open in Library'} onClick={openRoot}><ExternalLink size={11} /></button>
      </div>
      {root.now && !running && <div className="crew-now" title={root.now}>{root.now}</div>}
      <div className="widget-scroll">
        {running && workflow && (
          <div className="crew-run-form"><ParamForm wf={workflow} onProposed={() => { setRunning(false); void load() }} /></div>
        )}
        {!running && ringKids.length > 0 && (
          <div className="crew-ring-box">
            {focus && <button className="crew-up" onClick={() => setFocusId(focus.parent_id && byParent.has(focus.parent_id) ? focus.parent_id : null)}>← {focus.parent_id ? 'Back' : root.title}</button>}
            <CrewRing
              center={{ ...(focus ? look(focus) : rootConv ? rootFace : { name: root.id }), status: focus ? agentStatus(focus) : root.status, title: focus ? `${focus.role}: ${agentNow(focus)}` : rootLabel(view) }}
              kids={ringKids.map((a) => ({ id: a.id, ...look(a), status: agentStatus(a), title: `${a.role}: ${agentNow(a)}` }))}
              onPick={(id) => (byParent.has(id) ? setFocusId(id) : openSubagent(id))}
              onCenter={focus ? () => openSubagent(focus.id) : undefined} />
          </div>
        )}
        {!running && (tree.length > 0
          ? <div className="crew-tree">{tree}</div>
          : <div className="widget-empty">{root.kind === 'workflow' ? 'This workflow has not run yet.' : ROOT_LIVE.has(root.status) ? 'No agents yet.' : 'No agents worked on this.'}</div>)}
      </div>
    </div>
  )
}

export const def: WidgetDef = {
  kind: 'crew',
  label: 'Crew',
  icon: <Users size={18} />,
  defaultSize: { w: 380, h: 320 },
  minSize: { w: 260, h: 160 },
  chrome: 'full',
  needsRef: true,
  heavy: true,
  Component: CrewWidget
}
