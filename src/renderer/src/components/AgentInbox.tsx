/**
 * The Agent Inbox on Today. Two sections: "Needs you" (pending approvals and proposals) and
 * "While you were away" (what the scheduled jobs did, late fires and failures included).
 *
 * Everything here is rendered from the backend's journal rows — agent_runs, run_events, approvals and
 * proposals (GET /inbox) — never from the assistant's prose. A run's own report is shown as the body of
 * its card, but no number, badge or state is read out of that text.
 */
import { useEffect, useState } from 'react'
import { AlertTriangle, Check, ChevronDown, ChevronRight, Clock, Eye, History, Inbox, Pencil, Play, Plus, Timer, Trash2, Wrench, X } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { AgentProposal, Job, JobRunRecord, JobRunSummary, JobStats } from '@shared/types'
import { useStore } from '../store'
import { api } from '../lib/api'
import { WEEKDAYS, buildCron, describeCron } from '../lib/cron'
import { SAFE_MD } from './Message'

const fmtClock = (ts: number): string => new Date(ts * 1000).toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })
const fmtWhen = (ts: number): string => {
  const d = new Date(ts * 1000)
  const today = new Date().toDateString() === d.toDateString()
  return today ? fmtClock(ts) : d.toLocaleDateString(undefined, { weekday: 'short', hour: 'numeric', minute: '2-digit' })
}
/** A one-off's instant can be weeks out, so it carries its date where fmtWhen would only say the weekday. */
const fmtDate = (ts: number): string =>
  new Date(ts * 1000).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
const fmtLate = (seconds: number): string => {
  const m = Math.round(seconds / 60)
  return m < 60 ? `${Math.max(1, m)} min late` : m < 1440 ? `${Math.round(m / 60)} h late` : `${Math.round(m / 1440)} d late`
}
/** Which argument of a proposed call is worth showing first, and which are worth letting the user edit. */
const TEXT_KEYS = ['body', 'summary', 'subject', 'content', 'to', 'title', 'start', 'end', 'notes', 'description']
const argText = (args: Record<string, unknown>): string =>
  TEXT_KEYS.filter((k) => typeof args[k] === 'string' && args[k])
    .map((k) => `${k}: ${args[k] as string}`)
    .join('\n') || JSON.stringify(args)

function ProposalCard({ p }: { p: AgentProposal }): JSX.Element {
  const decideProposal = useStore((s) => s.decideProposal)
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState(false)
  const editable = TEXT_KEYS.filter((k) => typeof p.args[k] === 'string')

  const decide = async (accept: boolean): Promise<void> => {
    setBusy(true)
    const args = editing && Object.keys(draft).length ? { ...p.args, ...draft } : undefined
    await decideProposal(p.id, accept, args)
    setBusy(false)
  }

  return (
    <li className="inbox-item">
      <div className="inbox-item-head">
        <span className="inbox-tool">{p.tool}</span>
        <span className="muted small">proposed {fmtWhen(p.created_at)}</span>
        <span style={{ flex: 1 }} />
        <button className="icon-btn sm" title={editing ? 'Stop editing' : 'Edit before accepting'} disabled={!editable.length || busy}
          onClick={() => setEditing((v) => !v)}><Pencil size={13} /></button>
        <button className="ghost-btn sm" disabled={busy} onClick={() => void decide(false)}><X size={13} /> Reject</button>
        <button className="primary-btn sm" disabled={busy} onClick={() => void decide(true)}><Check size={13} /> Accept</button>
      </div>
      {editing ? (
        <div className="inbox-edit">
          {editable.map((k) => (
            <label key={k}>
              <span className="muted small">{k}</span>
              <textarea rows={k === 'body' || k === 'content' ? 6 : 1} value={draft[k] ?? (p.args[k] as string)}
                onChange={(e) => setDraft((d) => ({ ...d, [k]: e.target.value }))} />
            </label>
          ))}
          <p className="muted small">Accept sends exactly what is in these boxes.</p>
        </div>
      ) : (
        <pre className="inbox-args">{argText(p.args)}</pre>
      )}
    </li>
  )
}

function RunCard({ r }: { r: JobRunSummary }): JSX.Element {
  const selectChat = useStore((s) => s.selectChat)
  const [open, setOpen] = useState(r.late || r.status === 'error' || r.pending_proposals > 0)
  const failed = r.status === 'error' || r.status === 'interrupted'

  return (
    <li className="inbox-item">
      <div className="inbox-item-head">
        <button className="icon-btn sm" aria-label={open ? 'Collapse' : 'Expand'} onClick={() => setOpen((v) => !v)}>
          {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
        </button>
        <span className="inbox-job">{r.job}</span>
        {r.manual && <span className="chip">by hand</span>}
        {r.attempt > 1 && <span className="chip warn" title="Re-launched after the earlier run ended in an error">retry {r.attempt}</span>}
        {r.late && (
          <span className="chip warn" title={r.due_at ? `Due ${fmtWhen(r.due_at)}, ran ${fmtWhen(r.fired_at)}` : undefined}>
            <Clock size={11} /> {fmtLate(r.late_seconds)}{r.missed_slots > 0 ? ` · ${r.missed_slots} skipped` : ''}
          </span>
        )}
        {failed && <span className="chip bad"><AlertTriangle size={11} /> {r.status === 'interrupted' ? 'interrupted' : 'failed'}</span>}
        {r.pending_proposals > 0 && <span className="chip">{r.pending_proposals} waiting on you</span>}
        <span style={{ flex: 1 }} />
        <span className="muted small">{fmtWhen(r.fired_at)}</span>
        {r.conversation_id && <button className="link small" onClick={() => void selectChat(r.conversation_id as string)}>open</button>}
      </div>
      {open && (
        <>
          {r.error && <p className="msg-error">{r.error}</p>}
          {r.summary ? (
            <div className="markdown inbox-summary"><ReactMarkdown remarkPlugins={[remarkGfm]} components={SAFE_MD}>{r.summary}</ReactMarkdown></div>
          ) : (
            !r.error && <p className="muted">It wrote nothing. {r.tool_calls} tool call{r.tool_calls === 1 ? '' : 's'}.</p>
          )}
        </>
      )}
    </li>
  )
}

const fmtDur = (s: number | null): string => (s === null ? '' : s < 90 ? `${Math.round(s)}s` : `${Math.round(s / 60)} min`)
const STATUS_LABEL: Record<JobRunRecord['status'], string> = {
  running: 'running', done: 'done', error: 'failed', interrupted: 'interrupted', timed_out: 'timed out'
}

/** A job's last 50 runs from rows: a success-rate strip, then one line per run with a link to its transcript. */
function JobHistory({ job }: { job: Job }): JSX.Element {
  const selectChat = useStore((s) => s.selectChat)
  const [runs, setRuns] = useState<JobRunRecord[] | null>(null)
  const [stats, setStats] = useState<JobStats | null>(null)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    let live = true
    Promise.all([api.jobs.runs(job.id, 50), api.jobs.stats(job.id, 30)])
      .then(([r, s]) => { if (live) { setRuns(r); setStats(s) } })
      .catch((e: Error) => { if (live) setErr(e.message) })
    return () => { live = false }
  }, [job.id])

  const exportCsv = async (): Promise<void> => {
    try {
      const url = URL.createObjectURL(new Blob([await api.jobs.csv(job.id)], { type: 'text/csv' }))
      const a = document.createElement('a')
      a.href = url
      a.download = `${job.name.replace(/[^\w.-]+/g, '-')}-runs.csv`
      a.click()
      URL.revokeObjectURL(url)
    } catch (e) {
      setErr((e as Error).message)
    }
  }

  return (
    <li className="job-history">
      {err && <p className="msg-error">{err}</p>}
      {stats && (
        <div className="job-history-stats muted small">
          {stats.success_rate === null
            ? (runs?.some((r) => r.dry_run) ? `No scheduled runs yet (${runs.filter((r) => r.dry_run).length} preview${runs.filter((r) => r.dry_run).length === 1 ? '' : 's'})` : 'No finished runs in 30 days')
            : `${Math.round(stats.success_rate * 100)}% ok`}
          {` · ${stats.runs} run${stats.runs === 1 ? '' : 's'}`}
          {stats.median_duration_s !== null && ` · median ${fmtDur(stats.median_duration_s)}`}
          {stats.total_cost > 0 && ` · $${stats.total_cost.toFixed(2)}`}
          <span style={{ flex: 1 }} />
          <button className="link small" onClick={() => void exportCsv()}>Export CSV</button>
        </div>
      )}
      {runs && runs.length === 0 && <p className="muted small">Not run yet.</p>}
      {runs && runs.map((r) => (
        <div key={r.run_id} className="job-history-run small">
          <span className={`chip ${r.status === 'done' ? '' : r.status === 'running' ? 'warn' : 'bad'}`}>{STATUS_LABEL[r.status]}</span>
          <span>{fmtDate(r.started_at)}</span>
          <span className="muted">{fmtDur(r.duration_s)}</span>
          {r.attempt > 1 && <span className="chip warn">retry {r.attempt}</span>}
          {r.dry_run ? <span className="chip">preview</span> : r.manual && <span className="chip">by hand</span>}
          <span className="muted">{r.tool_calls} call{r.tool_calls === 1 ? '' : 's'}</span>
          {r.proposals.pending + r.proposals.accepted + r.proposals.rejected > 0 && (
            <span className="muted">{r.proposals.accepted}/{r.proposals.pending + r.proposals.accepted + r.proposals.rejected} proposals accepted</span>
          )}
          <span style={{ flex: 1 }} />
          {r.conversation_id && <button className="link small" onClick={() => void selectChat(r.conversation_id as string)}>open</button>}
        </div>
      ))}
    </li>
  )
}

/** Checkboxes for the tools a job may use, grouped like Settings. A job can only be narrowed: nothing here turns a tool on. */
function ToolPicker({ value, onChange }: { value: string[]; onChange: (next: string[]) => void }): JSX.Element {
  const tools = useStore((s) => s.tools)
  const groups = new Map<string, string[]>()
  // 'schedules' tools are never offered: a scheduled run that can schedule runs is a loop.
  for (const t of tools) if (t.danger !== 'schedules') groups.set(t.group, [...(groups.get(t.group) ?? []), t.name])
  const set = new Set(value)
  const flip = (n: string): void => onChange(set.has(n) ? value.filter((x) => x !== n) : [...value, n])
  return (
    <div className="job-tools">
      {[...groups.entries()].map(([g, names]) => (
        <div key={g} className="job-tools-group">
          <span className="muted small">{g}</span>
          {names.map((n) => (
            <label key={n} className="chip-check-row small">
              <input type="checkbox" checked={set.has(n)} onChange={() => flip(n)} /> <span>{n}</span>
            </label>
          ))}
        </div>
      ))}
    </div>
  )
}

function JobRow({ job }: { job: Job }): JSX.Element {
  const { setJobEnabled, runJobNow, deleteJob, refreshJobs, selectChat, toast } = useStore()
  const [history, setHistory] = useState(false)
  const [toolsOpen, setToolsOpen] = useState(false)

  const preview = async (): Promise<void> => {
    try {
      const r = await api.jobs.dryRun(job.id)
      if (r.conversation_id) await selectChat(r.conversation_id)
      else toast('Preview did not start', 'error')
    } catch (e) {
      toast(`Jobs: ${(e as Error).message}`, 'error')
    }
  }
  const saveTools = async (allowed: string[] | null): Promise<void> => {
    try {
      await api.jobs.update(job.id, { allowed_tools: allowed })
      await refreshJobs()
    } catch (e) {
      toast(`Jobs: ${(e as Error).message}`, 'error')
    }
  }
  const once = job.kind === 'once'
  // A one-off that has already fired has no slot left to wait for, so it is shown as what it did rather than
  // as a switch: the backend refuses to re-arm it, and a toggle that does nothing is worse than no toggle.
  const spent = once && job.last_fired_at !== null && job.next_due_at === null

  return (
    <>
    <li className={spent ? 'spent' : undefined}>
      {spent ? (
        <span className="chip-check-row ev-title">{job.name}</span>
      ) : (
        <label className="chip-check-row">
          <input type="checkbox" checked={job.enabled} onChange={() => void setJobEnabled(job.id, !job.enabled)} />
          <span className="ev-title">{job.name}</span>
        </label>
      )}
      {once
        ? <span className="muted small">{job.run_at ? fmtDate(job.run_at) : 'no time set'}</span>
        : <span className="muted small" title={job.cron}>{describeCron(job.cron)}</span>}
      <span className="muted small">
        {spent
          ? `ran ${fmtWhen(job.last_fired_at as number)}`
          : job.enabled && job.next_due_at ? `next ${fmtWhen(job.next_due_at)}` : 'paused'}
      </span>
      {job.last_skip_reason && job.last_skip_at && (
        <span className="muted small" title={`Slot at ${fmtWhen(job.last_skip_at)} was skipped`}>skipped: {job.last_skip_reason.replace('previous run still running', 'still running')}</span>
      )}
      <button className={`icon-btn sm ${toolsOpen ? 'on' : ''}`} title={job.allowed_tools ? `${job.allowed_tools.length} tools allowed` : 'All tools'}
        aria-label={`Tools for ${job.name}`} onClick={() => setToolsOpen((v) => !v)}>
        <Wrench size={12} />
      </button>
      <button className="icon-btn sm" title="Preview: run it read-only, nothing is proposed or changed" aria-label={`Preview ${job.name}`}
        onClick={() => void preview()}>
        <Eye size={12} />
      </button>
      <button className={`icon-btn sm ${history ? 'on' : ''}`} title="Run history" aria-label={`History of ${job.name}`}
        onClick={() => setHistory((v) => !v)}>
        <History size={12} />
      </button>
      <button className="icon-btn sm" title="Run it now" aria-label={`Run ${job.name} now`} onClick={() => void runJobNow(job.id)}>
        <Play size={12} />
      </button>
      <button className="icon-btn sm" title="Delete" aria-label={`Delete ${job.name}`}
        onClick={() => { if (confirm(`Delete “${job.name}”?`)) void deleteJob(job.id) }}>
        <Trash2 size={12} />
      </button>
    </li>
    {toolsOpen && (
      <li className="job-history">
        <label className="chip-check-row small">
          <input type="radio" name={`tools-${job.id}`} checked={job.allowed_tools === null} onChange={() => void saveTools(null)} /> <span>All tools</span>
        </label>
        <label className="chip-check-row small">
          <input type="radio" name={`tools-${job.id}`} checked={job.allowed_tools !== null}
            onChange={() => void saveTools(job.allowed_tools ?? ['current_time'])} /> <span>Only these</span>
        </label>
        {job.allowed_tools !== null && <ToolPicker value={job.allowed_tools} onChange={(n) => void saveTools(n)} />}
        <p className="muted small">A run can only use tools it is given here, on top of your own tool settings. Anything
          that leaves the app is still a proposal.</p>
      </li>
    )}
    {history && <JobHistory job={job} />}
    </>
  )
}

const BLANK = { name: '', prompt: '', when: '', cron: '', freq: '*', time: '09:00', repeat: false, onlyTools: false }

/** Schedule a task by hand: a one-off instant by default, a cron expression if it should repeat. */
function NewTask({ onDone }: { onDone: () => void }): JSX.Element {
  const createJob = useStore((s) => s.createJob)
  const [f, setF] = useState(BLANK)
  const [busy, setBusy] = useState(false)
  const [picked, setPicked] = useState<string[]>(['current_time'])
  const cron = f.freq === 'custom' ? f.cron.trim() : buildCron(f.freq, f.time)
  const ready = !!f.name.trim() && !!f.prompt.trim() && (f.repeat ? !!cron : !!f.when)

  const submit = async (e: React.FormEvent): Promise<void> => {
    e.preventDefault()
    if (!ready || busy) return
    setBusy(true)
    const common = { name: f.name.trim(), prompt: f.prompt.trim(), enabled: true, allowed_tools: f.onlyTools ? picked : null }
    // datetime-local has no zone, so Date.parse reads it as local time — which is what the user typed.
    const ok = await createJob(f.repeat
      ? { ...common, kind: 'cron' as const, cron }
      : { ...common, kind: 'once' as const, run_at: Math.round(Date.parse(f.when) / 1000) })
    setBusy(false)
    if (ok) {
      setF(BLANK)
      onDone()
    }
  }

  return (
    <form className="new-task" onSubmit={(e) => void submit(e)}>
      <input placeholder="Name, e.g. Chase the invoice" value={f.name} maxLength={120}
        onChange={(e) => setF({ ...f, name: e.target.value })} />
      <textarea rows={2} placeholder="What should it do? It runs in a fresh chat, so write it so it stands alone."
        value={f.prompt} maxLength={8000} onChange={(e) => setF({ ...f, prompt: e.target.value })} />
      <div className="new-task-when">
        <label className="chip-check-row">
          <input type="checkbox" checked={f.repeat} onChange={(e) => setF({ ...f, repeat: e.target.checked })} />
          <span>Repeat</span>
        </label>
        {f.repeat && (
          <select value={f.freq} aria-label="How often" onChange={(e) => setF({ ...f, freq: e.target.value })}>
            <option value="*">Every day</option>
            <option value="1-5">Weekdays</option>
            {WEEKDAYS.map((d, i) => <option key={d} value={String(i)}>{d}</option>)}
            <option value="custom">Advanced (cron)</option>
          </select>
        )}
        {f.repeat
          ? f.freq === 'custom'
            ? <input type="text" placeholder="cron, e.g. 0 17 * * 5" value={f.cron} aria-label="Cron expression"
                onChange={(e) => setF({ ...f, cron: e.target.value })} />
            : <input type="time" value={f.time} aria-label="At what time" onChange={(e) => setF({ ...f, time: e.target.value })} />
          : <input type="datetime-local" value={f.when} aria-label="When it should run"
              onChange={(e) => setF({ ...f, when: e.target.value })} />}
        <button className="primary-btn sm" type="submit" disabled={!ready || busy}>Schedule</button>
      </div>
      <label className="chip-check-row small">
        <input type="checkbox" checked={f.onlyTools} onChange={(e) => setF({ ...f, onlyTools: e.target.checked })} />
        <span>Only allow some tools</span>
      </label>
      {f.onlyTools && <ToolPicker value={picked} onChange={setPicked} />}
    </form>
  )
}

export default function AgentInbox(): JSX.Element | null {
  const box = useStore((s) => s.agentInbox)
  const jobs = useStore((s) => s.jobs)
  const { approveTool, refreshJobs, setJobEnabled } = useStore()
  const [showJobs, setShowJobs] = useState(false)
  const [adding, setAdding] = useState(false)

  if (!box) return null
  const { approvals, proposals } = box.needs_you
  const paused = box.needs_you.paused_jobs ?? []
  const away = box.while_you_were_away
  const quiet = box.counts.needs_you === 0 && away.length === 0

  const toggleJobs = (): void => {
    setShowJobs((v) => !v)
    if (!showJobs) void refreshJobs()
    else setAdding(false)
  }

  return (
    <section className="agent-inbox">
      <header>
        <Inbox size={14} /> Agent inbox
        {box.counts.needs_you > 0 && <span className="chip">{box.counts.needs_you} need you</span>}
        <span style={{ flex: 1 }} />
        {box.scheduler.next_due_at && <span className="muted small"><Timer size={11} /> next job {fmtWhen(box.scheduler.next_due_at)}</span>}
        <button className={`icon-btn sm ${showJobs ? 'on' : ''}`} title="Scheduled tasks" aria-label="Scheduled tasks" onClick={toggleJobs}>
          <Clock size={13} />
        </button>
      </header>

      {showJobs && (
        <div className="inbox-jobs">
          <h5>
            Scheduled tasks <span className="muted small">{box.scheduler.timezone}</span>
            <span style={{ flex: 1 }} />
            <button className={`icon-btn sm ${adding ? 'on' : ''}`} title="Schedule a task" aria-label="Schedule a task"
              onClick={() => setAdding((v) => !v)}><Plus size={13} /></button>
          </h5>
          {adding && <NewTask onDone={() => setAdding(false)} />}
          {jobs.length === 0 ? <p className="muted">None yet.</p> : <ul>{jobs.map((j) => <JobRow key={j.id} job={j} />)}</ul>}
          <p className="muted small">A scheduled task can read, search and write inside Grain. Anything that leaves the
            app — mail, calendar, Docs — comes back here as a proposal; accepting it is what sends it. You can also just
            ask in a chat: “tomorrow at 3pm, check whether they replied”.</p>
        </div>
      )}

      {quiet && <p className="muted">Nothing waiting, nothing ran.</p>}

      {box.counts.needs_you > 0 && (
        <div className="inbox-group">
          <h5>Needs you</h5>
          <ul className="inbox-list">
            {approvals.map((a) => (
              <li className="inbox-item" key={a.call_id}>
                <div className="inbox-item-head">
                  <span className="inbox-tool">{a.tool}</span>
                  <span className="muted small">{a.job ? `${a.job} · ` : ''}asked {fmtWhen(a.created_at)}</span>
                  {a.forced && <span className="chip warn">untrusted content in that chat</span>}
                  <span style={{ flex: 1 }} />
                  <button className="ghost-btn sm" onClick={() => void approveTool(a.call_id, 'deny', a.conversation_id ?? undefined)}>
                    <X size={13} /> Deny
                  </button>
                  <button className="primary-btn sm" onClick={() => void approveTool(a.call_id, 'allow', a.conversation_id ?? undefined)}>
                    <Check size={13} /> Allow
                  </button>
                </div>
                <pre className="inbox-args">{argText(a.args)}</pre>
              </li>
            ))}
            {paused.map((p) => (
              <li className="inbox-item" key={p.id}>
                <div className="inbox-item-head">
                  <span className="inbox-job">{p.name}</span>
                  <span className="chip bad"><AlertTriangle size={11} /> Paused: {p.reason}</span>
                  <span style={{ flex: 1 }} />
                  <button className="primary-btn sm" onClick={() => void setJobEnabled(p.id, true)}><Play size={13} /> Resume</button>
                </div>
              </li>
            ))}
            {proposals.map((p) => <ProposalCard key={p.id} p={p} />)}
          </ul>
        </div>
      )}

      {away.length > 0 && (
        <div className="inbox-group">
          <h5>While you were away <span className="muted small">
            {box.counts.late > 0 ? `${box.counts.late} late · ` : ''}{box.counts.failed > 0 ? `${box.counts.failed} failed · ` : ''}
            {away.length} run{away.length === 1 ? '' : 's'}</span></h5>
          <ul className="inbox-list">{away.map((r) => <RunCard key={r.run_id} r={r} />)}</ul>
        </div>
      )}
    </section>
  )
}
