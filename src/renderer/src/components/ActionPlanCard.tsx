import { useEffect, useRef, useState } from 'react'
import { ChevronRight, Pencil, ShieldAlert, Wrench } from 'lucide-react'
import type { PlanDecision, PlanEdit, PlanRecord, PlanRecordStep, ToolDanger } from '@shared/types'
import { argRows, broken, edited as reallyEdited, editPayload, invalid } from '../lib/planDigest'
import { useStore } from '../store'
// This card mounts inline in a chat bubble as well as in the Plan tab, so it carries its own sheet
// rather than relying on another component having loaded it first.
import '../styles/cowork.css'

/**
 * The approval artifact, with two mount points. The desk's Plan tab (DeskPlan) passes a stored plan and decides
 * it through the store's `decidePlan`. A chat's pending `propose_plan` call (ToolEvents) passes the call read as
 * a plan (`planOfCall`) and its own `onDecide`, which answers through `approveTool`. Either way the answer is one
 * payload: the kept steps by `idx`, with new arguments only where the user edited them (`editPayload`).
 * Approving a step approves `sha256(canon(arguments))` for that one call, once — so the card's job is to show
 * exactly what will be run and to let the user rewrite it before it is bound.
 *
 * Nothing here subscribes to store STATE. The inline mount lives inside a streaming message, where
 * Message.tsx:10-11 documents that any broader subscription re-renders every message in every open
 * transcript on every token. Actions come through single selectors (a stable function reference), the
 * plan arrives as a prop, and the decision is folded back in by the store — the card deliberately
 * keeps no copy of `plan.status`, because a second window may decide first.
 */

const DANGER_LABEL: Record<ToolDanger, string> = {
  safe: 'read-only',
  writes: 'changes data',
  network: 'reads the web',
  executes: 'runs code',
  external: 'acts outside the app',
  plan: 'always asks',
  schedules: 'schedules a run'
}

interface Effect {
  test: (s: PlanRecordStep) => boolean
  phrase: (n: number) => string
}

const plural = (n: number, one: string, many = `${one}s`): string => `${n} ${n === 1 ? one : many}`

/**
 * The side-effect strip, computed from the steps rather than written by the model: the model is the
 * thing being approved, so it does not get to summarise itself. Each step is counted by the FIRST
 * rule it matches, named ones before the generic danger buckets.
 */
const EFFECTS: Effect[] = [
  { test: (s) => s.tool === 'gmail_send', phrase: (n) => `sends ${plural(n, 'email')}` },
  { test: (s) => s.tool === 'gmail_draft', phrase: (n) => `drafts ${plural(n, 'email')}` },
  { test: (s) => s.tool.startsWith('calendar_') && s.danger !== 'safe', phrase: (n) => `changes ${plural(n, 'calendar event')}` },
  { test: (s) => s.tool === 'desk_write_file', phrase: (n) => `writes ${plural(n, 'file')}` },
  { test: (s) => s.tool.startsWith('browser_') || s.tool === 'browser', phrase: (n) => `drives the browser (${plural(n, 'step')})` },
  { test: (s) => s.tool === 'shell_run', phrase: (n) => `runs ${plural(n, 'shell command')}` },
  { test: (s) => s.tool === 'run_python' || s.tool === 'python_install', phrase: (n) => `runs ${plural(n, 'Python step')}` },
  { test: (s) => s.tool === 'fs_edit' || s.tool === 'fs_copy' || s.tool === 'fs_mkdir', phrase: (n) => `changes files ${plural(n, 'time')}` },
  { test: (s) => s.tool === 'desk_fetch_file', phrase: (n) => `downloads ${plural(n, 'file')}` },
  { test: (s) => s.tool === 'convert_document', phrase: (n) => `converts ${plural(n, 'document')}` },
  { test: (s) => s.tool === 'desk_deliver', phrase: (n) => `delivers ${plural(n, 'file')} for review` },
  { test: (s) => s.tool === 'desk_trash_file', phrase: (n) => `trashes ${plural(n, 'file')}` },
  { test: (s) => s.tool === 'doc_create' || s.tool === 'doc_edit' || s.tool.startsWith('google_docs_'), phrase: (n) => `writes ${plural(n, 'doc')}` },
  { test: (s) => s.danger === 'executes', phrase: (n) => `runs ${plural(n, 'command')}` },
  { test: (s) => s.danger === 'network', phrase: () => 'reads the web' },
  { test: (s) => s.danger === 'external', phrase: (n) => `acts outside the app ${plural(n, 'time')}` },
  { test: (s) => s.danger === 'writes', phrase: (n) => `makes ${plural(n, 'other change')}` }
]

function effects(steps: PlanRecordStep[]): string[] {
  const counts = new Map<number, number>()
  for (const s of steps) {
    const i = EFFECTS.findIndex((e) => e.test(s))
    if (i >= 0) counts.set(i, (counts.get(i) ?? 0) + 1)
  }
  return [...counts.entries()].sort((a, b) => a[0] - b[0]).map(([i, n]) => EFFECTS[i].phrase(n))
}

const pretty = (args: Record<string, unknown>): string => JSON.stringify(args, null, 2)

/** One step row: the drop checkbox, the tool chip, the why, and the arguments — read or rewritten. */
function StepRow({ step, dropped, draft, open, decided, onDrop, onDraft, onToggle }: {
  step: PlanRecordStep
  dropped: boolean
  draft: string | undefined
  open: boolean
  decided: boolean
  onDrop: (v: boolean) => void
  onDraft: (v: string) => void
  onToggle: () => void
}): JSX.Element {
  const err = draft === undefined ? null : invalid(draft)
  const changed = draft !== undefined && err === null && reallyEdited(step, draft)
  const rows = argRows(step.arguments)
  return (
    <li className={`aplan-step ${dropped ? 'dropped' : ''} ${step.status}`}>
      <span className="aplan-idx">{step.idx + 1}</span>
      <div className="aplan-step-main">
        <div className="aplan-step-head">
          <b>{step.title || step.tool || 'Think it through'}</b>
          {step.tool
            ? <span className={`aplan-tool danger-${step.danger}`} title={DANGER_LABEL[step.danger]}><Wrench size={10} />{step.tool.replace(/_/g, ' ')}</span>
            : <span className="aplan-tool danger-safe" title="A reasoning step: it calls nothing">no tool</span>}
          {changed && <span className="tag">edited</span>}
          {step.edited && !changed && <span className="tag">yours</span>}
          {!decided && step.tool !== '' && (
            <label className="aplan-drop" title="Leave this step out of the approval">
              <input type="checkbox" checked={dropped} onChange={(e) => onDrop(e.target.checked)} />
              <span>drop</span>
            </label>
          )}
        </div>
        {step.why && <p className="aplan-why">{step.why}</p>}
        {step.tool !== '' && rows.length > 0 && !open && (
          <dl className="aplan-args">
            {rows.map((r) => (
              <div key={r.key} className={r.multiline ? 'wide' : ''}>
                <dt>{r.key}</dt>
                <dd>{r.value}</dd>
              </div>
            ))}
          </dl>
        )}
        {step.tool !== '' && !decided && (
          <button className="aplan-edit-toggle" onClick={onToggle} aria-expanded={open}>
            <ChevronRight size={11} className={open ? 'rot90' : ''} /><Pencil size={11} /> Edit arguments
          </button>
        )}
        {open && (
          <div className="aplan-edit">
            <textarea
              spellCheck={false}
              rows={Math.min(14, pretty(step.arguments).split('\n').length + 1)}
              value={draft ?? pretty(step.arguments)}
              onChange={(e) => onDraft(e.target.value)}
            />
            {err
              ? <p className="aplan-invalid">{err}</p>
              : <p className="muted small">
                  {changed ? 'The agent will be held to these arguments, not its own.' : 'Unchanged — reformatting is not an edit.'}
                  {changed && <> <button className="aplan-edit-toggle" onClick={() => onDraft(pretty(step.arguments))}>revert</button></>}
                </p>}
          </div>
        )}
        {step.result_error && <p className="aplan-invalid">{step.result_error}</p>}
      </div>
    </li>
  )
}

export default function ActionPlanCard({ plan, onDecide, shortcuts = true }: {
  plan: PlanRecord
  /** Answers the card; defaults to the store's `decidePlan` (a stored plan). `edits` is set only for 'edit'. */
  onDecide?: (decision: PlanDecision, edits: PlanEdit[] | undefined, note: string) => Promise<void>
  /** ⌘⇧A / ⌘⇧D answer the card. Off for the inline mount: several pending cards can be on screen at once. */
  shortcuts?: boolean
}): JSX.Element {
  // Single selector: a stable function reference, so this never re-renders on a streamed token.
  const decidePlan = useStore((s) => s.decidePlan)
  const [drafts, setDrafts] = useState<Record<number, string>>({})
  const [open, setOpen] = useState<Record<number, boolean>>({})
  const [dropped, setDropped] = useState<number[]>([])
  const [note, setNote] = useState('')
  const [sending, setSending] = useState(false)

  const pending = plan.status === 'pending'
  const steps = plan.steps
  const payload = editPayload(steps, drafts, dropped)
  const blocked = broken(steps, drafts, dropped)
  const changed = payload !== null
  const kept = steps.length - dropped.length
  const strip = effects(steps.filter((s) => !dropped.includes(s.idx)))

  const decide = async (decision: 'approve' | 'edit' | 'reject'): Promise<void> => {
    if (sending) return
    setSending(true)
    try {
      const edits = decision === 'edit' ? payload ?? undefined : undefined
      await (onDecide ?? ((d, e, n) => decidePlan(plan.call_id, d, e, n)))(decision, edits, note.trim())
    } finally {
      setSending(false)
    }
  }

  // The shortcuts read the latest handlers out of a ref, so the listener binds once per decidability
  // change rather than on every keystroke in the note box.
  const act = useRef({ approve: () => {}, reject: () => {} })
  act.current = {
    approve: () => { if (!blocked && kept > 0) void decide(changed ? 'edit' : 'approve') },
    reject: () => void decide('reject')
  }
  useEffect(() => {
    if (!pending || !shortcuts) return
    const onKey = (e: KeyboardEvent): void => {
      if (!(e.metaKey || e.ctrlKey) || !e.shiftKey) return
      const k = e.key.toLowerCase()
      if (k === 'a') { e.preventDefault(); act.current.approve() }
      else if (k === 'd') { e.preventDefault(); act.current.reject() }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [pending, shortcuts])

  return (
    <div className={`aplan ${plan.status}`}>
      <header className="aplan-head">
        <ShieldAlert size={14} />
        <b>{plan.title || 'Plan'}</b>
        <span className="aplan-count">{steps.length} step{steps.length === 1 ? '' : 's'}</span>
        <span className="spacer" />
        <span className={`aplan-status ${plan.status}`}>
          {plan.status === 'pending' ? 'waiting on you'
            : plan.status === 'approved' ? 'approved'
            : plan.status === 'rejected' ? 'rejected'
            : 'superseded'}
        </span>
      </header>

      {plan.intent && <p className="aplan-intent">{plan.intent}</p>}
      {strip.length > 0 && <p className="aplan-effects">{strip.join(' · ')}</p>}

      {plan.expected_taint.length > 0 && (
        <p className="aplan-taint">
          <ShieldAlert size={12} />
          This plan reads untrusted content ({plan.expected_taint.join(', ')}) before it acts. Anything it finds there
          could be trying to steer the later steps — read the arguments below as the ones you are agreeing to.
        </p>
      )}
      {plan.tainted && plan.expected_taint.length === 0 && (
        <p className="aplan-taint"><ShieldAlert size={12} /> This reply had already read untrusted content when it wrote the plan.</p>
      )}

      <ol className="aplan-steps">
        {steps.map((s) => (
          <StepRow
            key={s.step_id}
            step={s}
            decided={!pending}
            dropped={dropped.includes(s.idx)}
            draft={drafts[s.idx]}
            open={Boolean(open[s.idx])}
            onDrop={(v) => setDropped((d) => (v ? [...d, s.idx] : d.filter((i) => i !== s.idx)))}
            onDraft={(v) => setDrafts((d) => ({ ...d, [s.idx]: v }))}
            onToggle={() => setOpen((o) => ({ ...o, [s.idx]: !o[s.idx] }))}
          />
        ))}
      </ol>

      {pending ? (
        <div className="aplan-foot">
          <input
            className="aplan-note"
            placeholder="Note to the agent (optional)"
            value={note}
            onChange={(e) => setNote(e.target.value)}
          />
          <div className="aplan-actions">
            <button
              className="primary-btn sm"
              disabled={sending || blocked || kept === 0}
              title={shortcuts ? 'Approve exactly what is shown here (⌘⇧A)' : 'Approve exactly what is shown here'}
              onClick={() => void decide(changed ? 'edit' : 'approve')}
            >
              {!changed ? 'Approve & run' : kept === steps.length ? 'Approve with changes' : `Approve ${kept} of ${steps.length}`}
            </button>
            <button className="ghost-btn sm" disabled={sending} title={shortcuts ? 'Reject and ask for a different plan (⌘⇧D)' : 'Reject and ask for a different plan'} onClick={() => void decide('reject')}>
              Reject
            </button>
          </div>
          {blocked && <p className="aplan-invalid">Fix the JSON in the step you edited, or drop it.</p>}
          {!blocked && kept === 0 && <p className="aplan-invalid">Nothing left to approve — reject the plan instead.</p>}
        </div>
      ) : (
        <p className="muted small aplan-decided">
          {plan.status === 'rejected' ? 'Rejected' : plan.status === 'approved' ? 'Approved' : 'Superseded'}
          {plan.decided_by ? ` by ${plan.decided_by}` : ''}
          {plan.note ? ` — “${plan.note}”` : ''}
        </p>
      )}
    </div>
  )
}
