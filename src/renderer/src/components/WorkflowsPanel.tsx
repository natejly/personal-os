import { useCallback, useEffect, useRef, useState } from 'react'
import { Check, ChevronDown, ChevronRight, Pencil, Play, Plus, RotateCw, Square, Trash2, X } from 'lucide-react'
import { useStore } from '../store'
import { api } from '../lib/api'
import type { Workflow, WorkflowRun, WorkflowRunStatus, WorkflowStepStatus } from '@shared/types'

const TEMPLATE = JSON.stringify({
  name: 'folder-digest',
  description: 'Summarize every file in a folder into digest.md',
  params: { folder: { type: 'string', required: true } },
  steps: [
    { id: 'scan', tool: 'read_local_file', args: { path: '{{folder}}' } },
    { id: 'summaries', fan_out: { over: '{{scan.result.entries}}', max_parallel: 3, agent: { role: 'researcher', task: 'Read {{folder}}/{{item}} and summarize it in three sentences.' } } },
    { id: 'write', tool: 'write_local_file', approval: 'required', args: { path: '{{folder}}/digest.md', content: '{{summaries.result}}' } }
  ],
  output: '{{write.result}}'
}, null, 2)

const RUN_LABEL: Record<WorkflowRunStatus, string> = {
  awaiting_approval: 'waiting for your approval', running: 'running', waiting_approval: 'waiting for you', done: 'done',
  failed: 'failed', cancelled: 'cancelled', interrupted: 'interrupted', stale: 'out of date'
}
const STEP_MARK: Record<WorkflowStepStatus, string> = {
  pending: '·', running: '…', waiting_approval: '?', done: '✓', failed: '✗', skipped: '–', blocked: '⊘'
}
const LIVE: WorkflowRunStatus[] = ['running', 'waiting_approval']

function RunCard({ run, onChange }: { run: WorkflowRun; onChange: () => void }): JSX.Element {
  const { toast } = useStore()
  const [open, setOpen] = useState(run.status === 'awaiting_approval' || LIVE.includes(run.status))
  const [full, setFull] = useState<WorkflowRun | null>(null)
  const cur = full && full.id === run.id ? full : run
  const act = async (fn: () => Promise<unknown>): Promise<void> => {
    try { await fn() } catch (e) { toast((e as Error).message, 'error') }
    onChange()
  }
  useEffect(() => {
    if (open) void api.workflows.run(run.id).then(setFull).catch(() => undefined)
  }, [open, run.id, run.status, run.updated_at])
  const waiting = cur.steps.filter((s) => s.status === 'waiting_approval' && s.approval_call_id)

  return (
    <div className={`skill-row wf-run ${run.status}`}>
      <div className="skill-head" onClick={() => setOpen(!open)} role="button" tabIndex={0}
        onKeyDown={(e) => { if (e.key === 'Enter') setOpen(!open) }}>
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        <span className="skill-name">{run.name}</span>
        <span className="skill-desc muted">{Object.entries(run.params).map(([k, v]) => `${k}=${typeof v === 'string' ? v : JSON.stringify(v)}`).join(' · ')}</span>
        <small className="muted">{RUN_LABEL[run.status]} · {new Date(run.created_at * 1000).toLocaleString()}</small>
        <div className="skill-actions no-drag" onClick={(e) => e.stopPropagation()}>
          {run.status === 'awaiting_approval' && (
            <button className="primary-btn small" title="Approve exactly this plan and start it"
              onClick={() => void act(() => api.workflows.approveRun(run.id, run.plan_digest))}><Check size={13} /> Approve and run</button>
          )}
          {['interrupted', 'failed', 'cancelled'].includes(run.status) && run.approved_digest && (
            <button className="small" title="Continue from the first step that is not done"
              onClick={() => void act(() => api.workflows.resumeRun(run.id))}><RotateCw size={13} /> Resume</button>
          )}
          {(LIVE.includes(run.status) || run.status === 'awaiting_approval') && (
            <button className="small" onClick={() => void act(() => api.workflows.cancelRun(run.id))}><Square size={13} /> Cancel</button>
          )}
        </div>
      </div>
      {open && (
        <div className="skill-body">
          {run.error && <p className="muted small">{run.error}</p>}
          <ol className="wf-steps">
            {(cur.plan ?? []).map((p) => {
              const row = cur.steps.find((s) => s.step_id === p.id)
              const status = row?.status ?? 'pending'
              const what = p.tool ?? (p.kind === 'fan_out' ? `fan out ${String((p.fan_out as { over?: unknown })?.over ?? '')}` : `agent (${String((p.agent as { role?: unknown })?.role ?? 'researcher')})`)
              const done = row?.items ? Object.keys(row.items).length : 0
              return (
                <li key={p.id} className={`wf-step ${status}`}>
                  <span className="wf-mark" aria-label={status}>{STEP_MARK[status]}</span>
                  <b>{p.id}</b> <span className="muted">{what}</span>
                  {p.approval === 'required' && <small className="muted"> · asks first</small>}
                  {p.kind === 'fan_out' && done > 0 && status !== 'done' && <small className="muted"> · {done} item(s) finished</small>}
                  {row?.error && <div className="muted small">{row.error}</div>}
                </li>
              )
            })}
          </ol>
          {waiting.map((s) => (
            <div key={s.step_id} className="row-actions">
              <span className="muted small">Step {s.step_id} is waiting for you.</span>
              <button className="primary-btn small" onClick={() => void act(() => api.approve(s.approval_call_id!, 'allow'))}><Check size={13} /> Allow</button>
              <button className="small" onClick={() => void act(() => api.approve(s.approval_call_id!, 'deny'))}><X size={13} /> Deny</button>
            </div>
          ))}
          {run.status === 'done' && cur.result != null && <pre className="muted small">{typeof cur.result === 'string' ? cur.result : JSON.stringify(cur.result, null, 2)}</pre>}
          <small className="muted">Plan {run.plan_digest.slice(0, 12)}. Editing the workflow withdraws this approval.</small>
        </div>
      )}
    </div>
  )
}

function Editor({ wf, onDone }: { wf: Workflow | null; onDone: () => void }): JSX.Element {
  const { toast } = useStore()
  const [text, setText] = useState(wf?.text || TEMPLATE)
  const [errors, setErrors] = useState<string[]>([])
  const seq = useRef(0)
  useEffect(() => {
    const n = ++seq.current
    const t = setTimeout(() => {
      void api.workflows.validate(text).then((r) => { if (n === seq.current) setErrors(r.errors) }).catch(() => undefined)
    }, 350)
    return () => clearTimeout(t)
  }, [text])
  const save = async (): Promise<void> => {
    try {
      if (wf) await api.workflows.update(wf.id, text)
      else await api.workflows.create(text)
      onDone()
    } catch (e) { toast((e as Error).message, 'error') }
  }
  return (
    <div className="skill-body">
      <label>Definition (JSON)
        <textarea rows={16} spellCheck={false} value={text} onChange={(e) => setText(e.target.value)} />
      </label>
      {errors.length > 0
        ? <ul className="muted small">{errors.map((e) => <li key={e}>{e}</li>)}</ul>
        : <p className="muted small">Valid. Saving changes the plan hash, so runs proposed earlier will need to be proposed again.</p>}
      <div className="row-actions">
        <button className="primary-btn small" disabled={errors.length > 0} onClick={() => void save()}>Save</button>
        <button className="small" onClick={onDone}>Cancel</button>
      </div>
    </div>
  )
}

function ParamForm({ wf, onProposed }: { wf: Workflow; onProposed: () => void }): JSX.Element {
  const { toast } = useStore()
  const [vals, setVals] = useState<Record<string, string>>({})
  const propose = async (): Promise<void> => {
    const params: Record<string, unknown> = {}
    for (const [k, v] of Object.entries(vals)) if (v !== '') params[k] = v
    try { await api.workflows.propose(wf.id, params); onProposed() } catch (e) { toast((e as Error).message, 'error') }
  }
  return (
    <div className="skill-body">
      {Object.entries(wf.params).map(([k, p]) => (
        <label key={k}>{k}{p.required ? ' (required)' : ''}
          <input value={vals[k] ?? ''} placeholder={p.default != null ? String(p.default) : p.type}
            onChange={(e) => setVals({ ...vals, [k]: e.target.value })} />
        </label>
      ))}
      <div className="row-actions">
        <button className="primary-btn small" onClick={() => void propose()}><Play size={13} /> Review the plan</button>
        <span className="muted small">Nothing starts until you approve the plan.</span>
      </div>
    </div>
  )
}

export default function WorkflowsPanel(): JSX.Element {
  const { toast } = useStore()
  const [wfs, setWfs] = useState<Workflow[]>([])
  const [runs, setRuns] = useState<WorkflowRun[]>([])
  const [mode, setMode] = useState<{ kind: 'edit' | 'run'; id: string | 'new' } | null>(null)

  const load = useCallback(async (): Promise<void> => {
    const [w, r] = await Promise.all([api.workflows.list(), api.workflows.runs()])
    setWfs(w)
    setRuns(r)
  }, [])
  useEffect(() => { void load().catch(() => undefined) }, [load])
  const active = runs.some((r) => LIVE.includes(r.status))
  useEffect(() => {
    if (!active) return
    const t = setInterval(() => { void load().catch(() => undefined) }, 2000)
    return () => clearInterval(t)
  }, [active, load])

  const remove = async (w: Workflow): Promise<void> => {
    try { await api.workflows.delete(w.id); await load() } catch (e) { toast((e as Error).message, 'error') }
  }

  return (
    <div className="library-panel">
      <div className="add-row">
        <p className="muted small">A workflow is a saved plan of steps. You approve a run by its hash before anything happens, every step goes through the same permissions as chat, and a run that stops can be resumed without repeating finished steps.</p>
        <button className="primary-btn small" onClick={() => setMode({ kind: 'edit', id: 'new' })}><Plus size={13} /> New workflow</button>
      </div>
      {mode?.kind === 'edit' && mode.id === 'new' && <div className="skill-row"><Editor wf={null} onDone={() => { setMode(null); void load() }} /></div>}
      <section className="skill-section">
        <h4>Saved workflows</h4>
        {wfs.length === 0 && <div className="empty-hint"><p className="muted small">None yet.</p></div>}
        {wfs.map((w) => (
          <div key={w.id} className="skill-row">
            <div className="skill-head">
              <span className="skill-name">{w.name}</span>
              <span className="skill-desc muted">{w.description}</span>
              <div className="skill-actions no-drag">
                <button className="small" onClick={() => setMode(mode?.id === w.id && mode.kind === 'run' ? null : { kind: 'run', id: w.id })}><Play size={13} /> Run</button>
                <button className="icon-btn ghost" aria-label={`Edit ${w.name}`} onClick={() => setMode(mode?.id === w.id && mode.kind === 'edit' ? null : { kind: 'edit', id: w.id })}><Pencil size={13} /></button>
                <button className="icon-btn ghost danger" aria-label={`Delete ${w.name}`} onClick={() => void remove(w)}><Trash2 size={13} /></button>
              </div>
            </div>
            {mode?.id === w.id && mode.kind === 'edit' && <Editor wf={w} onDone={() => { setMode(null); void load() }} />}
            {mode?.id === w.id && mode.kind === 'run' && <ParamForm wf={w} onProposed={() => { setMode(null); void load() }} />}
          </div>
        ))}
      </section>
      <section className="skill-section">
        <h4>Runs</h4>
        {runs.length === 0 && <div className="empty-hint"><p className="muted small">No runs yet. Run a workflow to review its plan here.</p></div>}
        {runs.map((r) => <RunCard key={r.id} run={r} onChange={() => void load()} />)}
      </section>
    </div>
  )
}
