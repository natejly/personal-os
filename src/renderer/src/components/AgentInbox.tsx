/**
 * The Agent Inbox on Today. Two sections: "Needs you" (pending approvals and proposals, desks waiting on the user, and
 * a link into every other review queue: doc edits, skills, workflow runs, memory tidy-ups) and
 * "While you were away" (what the scheduled jobs did, late fires and failures included).
 *
 * Everything here is rendered from the backend's journal rows — agent_runs, run_events, approvals and
 * proposals (GET /inbox) — never from the assistant's prose. A run's own report is shown as the body of
 * its card, but no number, badge or state is read out of that text.
 */
import { splitReport } from '../lib/report'
import { type RoutineDraft } from '../lib/routine'
import { useEffect, useState } from 'react'
import { AlertTriangle, ArrowRight, Check, ChevronDown, ChevronRight, Clock, Eye, History, Inbox, Pencil, Play, Plus, Rocket, Timer, Trash2, Users, Wrench, X } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { AgentInbox as AgentInboxData, AgentProposal, InboxQueueKey, Job, JobNotifyMode, JobRunRecord, JobRunSummary, JobSkipRecord, JobStats } from '@shared/types'
import { useStore } from '../store'
import { api } from '../lib/api'
import { DAYS, DEFAULT_SCHEDULE, type Preset, type Schedule, cronPreset, diffJob, presetCron, toLocalInput } from '../lib/jobSchedule'
import { chatModelIds, modelLabel } from '../lib/modelLabel'
import { describeCron } from '../lib/cron'
import { SAFE_MD } from './Message'
import { AUTONOMY } from '../lib/deskStatus'
import Face from './Face'
import { ShipChecklistView } from './toolcards/ShipChecklistCard'
import type { ShipChecklist } from '@shared/types'

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
/** The same arguments on one line, for the collapsed row. */
const argLine = (args: Record<string, unknown>): string => argText(args).replace(/\s*\n\s*/g, ' · ').replace(/\s+/g, ' ').slice(0, 240)
/** A run's report as one plain line: enough to tell runs apart without opening them. */
const oneLine = (text: string): string => text.replace(/[#*_`>|]+/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 240)

/** The app-wide status vocabulary (`--st-*`), as the dot that leads every row. */
type Tone = 'needs-you' | 'working' | 'done' | 'failed' | 'idle'
const Dot = ({ tone, label }: { tone: Tone; label: string }): JSX.Element =>
  <span className={`inbox-dot ${tone}`} role="img" aria-label={label} title={label} />

/** The row's disclosure: everything past the one line (arguments, the report) sits behind it. */
const Disclosure = ({ open, what, onToggle }: { open: boolean; what: string; onToggle: () => void }): JSX.Element => (
  <button className="icon-btn sm" aria-expanded={open} aria-label={`${open ? 'Hide' : 'Show'} ${what}`} title={`${open ? 'Hide' : 'Show'} ${what}`} onClick={onToggle}>
    {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
  </button>
)

/** Cards a bare Approve cannot decide: a plan has steps to read and edit, a question wants an answer. These, and any card
 * of a desk, open where they are decided instead. */
const OPEN_ONLY = new Set(['propose_plan', 'desk_ask', 'ask_user'])

function ApprovalRow({ a, onDesk, onChat }: {
  a: AgentInboxData['needs_you']['approvals'][number]
  onDesk: (deskId: string) => void
  onChat: (conversationId: string) => void
}): JSX.Element {
  const approveTool = useStore((s) => s.approveTool)
  const [open, setOpen] = useState(false)
  const line = argLine(a.args)

  return (
    <li className="inbox-item">
      <div className="inbox-row">
        <Dot tone="needs-you" label="Waiting on your approval" />
        <span className="inbox-tool">{a.tool}</span>
        <span className="inbox-line" title={line}>{line}</span>
        {a.forced && <span className="chip warn">untrusted content in that chat</span>}
        <span className="muted small inbox-when">{a.job ? `${a.job} · ` : ''}asked {fmtWhen(a.created_at)}</span>
        {a.desk_id || OPEN_ONLY.has(a.tool) ? (
          <button className="primary-btn sm" disabled={!a.desk_id && !a.conversation_id}
            onClick={() => { if (a.desk_id) onDesk(a.desk_id); else if (a.conversation_id) onChat(a.conversation_id) }}>
            Open {a.desk_id ? 'desk' : 'chat'} <ArrowRight size={13} />
          </button>
        ) : (
          <>
            <button className="primary-btn sm" onClick={() => void approveTool(a.call_id, 'allow', a.conversation_id ?? undefined)}>
              <Check size={13} /> Approve
            </button>
            <button className="ghost-btn sm" onClick={() => void approveTool(a.call_id, 'deny', a.conversation_id ?? undefined)}>
              <X size={13} /> Deny
            </button>
            {/* Arguments cannot be edited here; the chat's card can. */}
            {a.conversation_id && <button className="link small" onClick={() => onChat(a.conversation_id as string)}>Open chat</button>}
          </>
        )}
        <Disclosure open={open} what="the arguments" onToggle={() => setOpen((v) => !v)} />
      </div>
      {open && <pre className="inbox-args">{argText(a.args)}</pre>}
    </li>
  )
}

function ProposalCard({ p, onOpen }: { p: AgentProposal; onOpen?: () => void }): JSX.Element {
  const decideProposal = useStore((s) => s.decideProposal)
  const [open, setOpen] = useState(false)
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const editable = p.editable ? TEXT_KEYS.filter((k) => typeof p.args[k] === 'string') : []
  const line = argLine(p.args)

  const decide = async (accept: boolean): Promise<void> => {
    setBusy(true)
    const args = editing && Object.keys(draft).length ? { ...p.args, ...draft } : undefined
    // A refused decision leaves the proposal pending: the message shows here and the draft stays open to fix.
    setErr(await decideProposal(p.id, accept, args))
    setBusy(false)
  }
  // Closing the row ends the edit too: Accept must never send text from boxes that are not on screen.
  const toggle = (): void => {
    if (open) setEditing(false)
    setOpen(!open)
  }

  return (
    <li className="inbox-item">
      <div className="inbox-row">
        <Dot tone="needs-you" label="Waiting on you" />
        <span className="inbox-tool">{p.tool}</span>
        <span className="inbox-line" title={line}>{line}</span>
        <span className="muted small inbox-when">
          {p.source ? `from ${p.source.kind === 'desk' ? `desk ${p.source.name}` : p.source.name} · ` : ''}proposed {fmtWhen(p.created_at)}
        </span>
        {onOpen && <button className="link small" onClick={onOpen}>Open</button>}
        <button className="primary-btn sm" disabled={busy} onClick={() => void decide(true)}><Check size={13} /> Accept</button>
        <button className="ghost-btn sm" disabled={busy} onClick={() => void decide(false)}><X size={13} /> Reject</button>
        <Disclosure open={open} what="the details" onToggle={toggle} />
      </div>
      {open && (editing ? (
        <div className="inbox-edit">
          {editable.map((k) => (
            <label key={k}>
              <span className="muted small">{k}</span>
              <textarea rows={k === 'body' || k === 'content' ? 6 : 1} value={draft[k] ?? (p.args[k] as string)}
                onChange={(e) => setDraft((d) => ({ ...d, [k]: e.target.value }))} />
            </label>
          ))}
          <div className="inbox-edit-foot">
            <p className="muted small">Accept sends exactly what is in these boxes.</p>
            <button className="ghost-btn sm" disabled={busy} onClick={() => setEditing(false)}>Stop editing</button>
          </div>
        </div>
      ) : (
        <>
          <pre className="inbox-args">{argText(p.args)}</pre>
          {editable.length > 0 && (
            <button className="ghost-btn sm inbox-edit-btn" disabled={busy} onClick={() => setEditing(true)}><Pencil size={12} /> Edit before accepting</button>
          )}
        </>
      ))}
      {err && <p className="error small" role="alert">{err}</p>}
    </li>
  )
}

/** A run's report: the fixed headings (Verified, Assumptions, Done, ...) as labelled sections when it has them, else plain markdown. */
function ReportBody({ text }: { text: string }): JSX.Element {
  const md = (t: string): JSX.Element => (
    <div className="markdown inbox-summary"><ReactMarkdown remarkPlugins={[remarkGfm]} components={SAFE_MD}>{t}</ReactMarkdown></div>
  )
  const sections = splitReport(text)
  if (!sections) return md(text)
  return (
    <>
      {sections.map((x, i) => (
        <section key={i} className="report-section">
          {x.heading && <h4 className="report-heading">{x.heading}</h4>}
          {x.body && md(x.body)}
        </section>
      ))}
    </>
  )
}


function RunCard({ r }: { r: JobRunSummary }): JSX.Element {
  const selectChat = useStore((s) => s.selectChat)
  const markInboxRunSeen = useStore((s) => s.markInboxRunSeen)
  // An unread problem opens itself; reading it (Mark all read included) collapses it.
  const [open, setOpen] = useState(!r.seen && (r.late || r.status === 'error' || r.pending_proposals > 0))
  const failed = r.status === 'error' || r.status === 'interrupted'
  useEffect(() => { if (r.seen) setOpen(false) }, [r.seen])
  const tone: Tone = failed ? 'failed' : r.pending_proposals > 0 || r.status === 'awaiting_approval' ? 'needs-you' : r.status === 'running' ? 'working' : 'done'
  const toneLabel = failed
    ? (r.status === 'interrupted' ? 'Interrupted' : 'Failed')
    : tone === 'needs-you' ? 'Waiting on you' : tone === 'working' ? 'Running' : 'Done'
  const line = r.error ? oneLine(r.error) : r.summary ? oneLine(r.summary) : `It wrote nothing. ${r.tool_calls} tool call${r.tool_calls === 1 ? '' : 's'}.`

  return (
    <li className={`inbox-item ${r.seen ? 'seen' : ''}`}>
      <div className="inbox-row">
        <Face name={r.job} status={r.status} size={18} />
        <Dot tone={tone} label={toneLabel} />
        <span className="inbox-job">{r.job}</span>
        <span className="inbox-line" title={line}>{line}</span>
        {r.test ? <span className="chip">test</span> : r.manual && <span className="chip">by hand</span>}
        {r.attempt > 1 && <span className="chip warn" title="Re-launched after the earlier run ended in an error">retry {r.attempt}</span>}
        {r.late && (
          <span className="chip warn" title={r.due_at ? `Due ${fmtWhen(r.due_at)}, ran ${fmtWhen(r.fired_at)}` : undefined}>
            <Clock size={11} /> {fmtLate(r.late_seconds)}{r.missed_slots > 0 ? ` · ${r.missed_slots} skipped` : ''}
          </span>
        )}
        {failed && <span className="chip bad"><AlertTriangle size={11} /> {r.status === 'interrupted' ? 'interrupted' : 'failed'}</span>}
        {r.pending_proposals > 0 && <span className="chip needs">{r.pending_proposals} waiting on you</span>}
        <span className="muted small inbox-when">{fmtWhen(r.fired_at)}</span>
        {r.conversation_id && <button className="link small" onClick={() => void selectChat(r.conversation_id as string)}>Open</button>}
        {!r.seen && (
          <button className="icon-btn sm" title="Mark read" aria-label={`Mark ${r.job} read`} onClick={() => void markInboxRunSeen(r.run_id)}>
            <Check size={13} />
          </button>
        )}
        <Disclosure open={open} what="the report" onToggle={() => setOpen((v) => !v)} />
      </div>
      {open && (
        <>
          {r.error && <p className="msg-error">{r.error}</p>}
          {r.summary ? <ReportBody text={r.summary} /> : (
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
const skipReason = (why: string): string => why.replace('previous run still running', 'still running')
/** Every outcome gets a colour, success included: an unmarked chip read as "no status". */
const STATUS_CHIP: Record<JobRunRecord['status'], string> = {
  running: 'working', done: 'ok', error: 'bad', interrupted: 'bad', timed_out: 'bad'
}

/** A job's last 20 runs from rows (the backend keeps no more): a success-rate strip, then one line per run with a link to its transcript. */
function JobHistory({ job }: { job: Job }): JSX.Element {
  const selectChat = useStore((s) => s.selectChat)
  const [runs, setRuns] = useState<(JobRunRecord | JobSkipRecord)[] | null>(null)
  const previews = runs?.filter((r) => 'dry_run' in r && r.dry_run).length ?? 0
  const [stats, setStats] = useState<JobStats | null>(null)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    let live = true
    Promise.all([api.jobs.runs(job.id, 20), api.jobs.stats(job.id, 30)])
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
            ? (previews ? `No scheduled runs yet (${previews} preview${previews === 1 ? '' : 's'})` : 'No finished runs in 30 days')
            : `${Math.round(stats.success_rate * 100)}% ok`}
          {` · ${stats.runs} run${stats.runs === 1 ? '' : 's'}`}
          {stats.median_duration_s !== null && ` · median ${fmtDur(stats.median_duration_s)}`}
          {stats.total_cost > 0 && ` · $${stats.total_cost.toFixed(2)}`}
          {!!stats.skipped && ` · ${stats.skipped} skipped`}
          <span className="spacer" />
          <button className="link small" onClick={() => void exportCsv()}>Export CSV</button>
        </div>
      )}
      {runs && runs.length === 0 && <p className="muted small">Not run yet.</p>}
      {runs && runs.map((r) => r.status === 'skipped' ? (
        <div key={r.run_id} className="job-history-run small muted" title={`Skipped ${fmtDate(r.started_at)}`}>
          <span className="chip">skipped</span>
          <span>{fmtDate(r.due_at ?? r.started_at)}</span>
          <span>{skipReason(r.reason)}</span>
        </div>
      ) : (
        <div key={r.run_id} className="job-history-run small">
          <span className={`chip ${STATUS_CHIP[r.status]}`}>{STATUS_LABEL[r.status]}</span>
          <span>{fmtDate(r.started_at)}</span>
          <span className="muted">{fmtDur(r.duration_s)}</span>
          {r.attempt > 1 && <span className="chip warn">retry {r.attempt}</span>}
          {r.dry_run ? <span className="chip">preview</span> : r.test ? <span className="chip">test</span> : r.manual && <span className="chip">by hand</span>}
          {r.unchanged && <span className="chip" title="Same result as the run before; not announced">unchanged</span>}
          {r.late && (
            <span className="chip warn" title={r.due_at ? `Due ${fmtDate(r.due_at)}` : undefined}>
              late{r.missed_slots > 0 ? ` · ${r.missed_slots} slot${r.missed_slots === 1 ? '' : 's'} missed` : ''}
            </span>
          )}
          <span className="muted">{r.tool_calls} call{r.tool_calls === 1 ? '' : 's'}</span>
          {r.proposals.pending + r.proposals.accepted + r.proposals.rejected > 0 && (
            <span className="muted">{r.proposals.accepted}/{r.proposals.pending + r.proposals.accepted + r.proposals.rejected} proposals accepted</span>
          )}
          <span className="spacer" />
          {r.conversation_id && <button className="link small" onClick={() => void selectChat(r.conversation_id as string)}>Open</button>}
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

export function JobRow({ job }: { job: Job }): JSX.Element {
  const { setJobEnabled, runJobNow, deleteJob, refreshJobs, selectChat, toast } = useStore()
  const [history, setHistory] = useState(false)
  const [toolsOpen, setToolsOpen] = useState(false)
  const [editing, setEditing] = useState(false)
  const [shipOpen, setShipOpen] = useState(false)
  const upsertShip = useStore((s) => s.upsertShip)
  // The job's latest ship checklist, kept live by the ship_checklist event.
  const ship = useStore((s) => Object.values(s.shipChecklists).filter((c) => c.job_id === job.id)
    .reduce<ShipChecklist | null>((a, c) => (!a || c.created_at > a.created_at ? c : a), null))
  useEffect(() => {
    api.ship.latest(job.id).then((c) => { if (c) upsertShip(c) }).catch(() => undefined)
  }, [job.id, upsertShip])
  const shipLive = ship?.status === 'running' || ship?.status === 'awaiting_confirm'

  const preview = async (): Promise<void> => {
    try {
      const r = await api.jobs.dryRun(job.id)
      if (r.conversation_id) await selectChat(r.conversation_id)
      else toast('Preview did not start', 'error')
    } catch (e) {
      toast(`Jobs: ${(e as Error).message}`, 'error')
    }
  }
  const [tested, setTested] = useState(false)
  const testRun = async (): Promise<void> => {
    try {
      const r = await api.jobs.runNow(job.id, true)
      if (!r.run_id && !r.desk_id) return toast('Test run did not start', 'error')
      setTested(true)
      toast('Test run started. Its result shows under “While you were away”, labelled test.', 'info')
      void refreshJobs()
    } catch (e) {
      toast(`Jobs: ${(e as Error).message}`, 'error')
    }
  }
  const saveTools = (allowed: string[] | null): Promise<void> => save({ allowed_tools: allowed })
  const save = async (patch: Parameters<typeof api.jobs.update>[1]): Promise<void> => {
    try {
      await api.jobs.update(job.id, patch)
      await refreshJobs()
    } catch (e) {
      toast(`Jobs: ${(e as Error).message}`, 'error')
    }
  }
  const saveNotify = async (notify: JobNotifyMode): Promise<void> => {
    try {
      await api.jobs.update(job.id, { notify })
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
    <li className={`job-row${spent ? ' spent' : ''}`}>
      {spent ? <span className="job-switch-gap" /> : (
        <label className="switch-wrap" title={job.enabled ? 'Turn this task off' : 'Turn this task on'}>
          <input type="checkbox" checked={job.enabled} aria-label={`${job.name} enabled`} onChange={() => void setJobEnabled(job.id, !job.enabled)} />
          <span className="switch" />
        </label>
      )}
      <span className="job-name ev-title" title={job.name}>{job.name}</span>
      {once
        ? <span className="muted small">{job.run_at ? fmtDate(job.run_at) : 'no time set'}</span>
        : job.kind === 'watch'
          ? <span className="muted small" title={job.watch_dir ?? ''}>watching {tildePath(job.watch_dir ?? '')}{job.cron && <> · <code>{job.cron}</code></>}</span>
          : job.kind === 'mail'
            ? <code className="muted small" title="Runs when matching mail arrives">{job.mail_query}</code>
            : job.kind === 'calendar'
              ? <span className="muted small" title="Runs shortly before each matching event starts">{job.minutes_before ?? 15} min before <code>{job.calendar_query}</code></span>
          : <span className="muted small" title={job.cron}>{describeCron(job.cron)}</span>}
      <span className="muted small">
        {spent
          ? `ran ${fmtWhen(job.last_fired_at as number)}`
          : job.enabled && job.next_due_at ? `next ${fmtWhen(job.next_due_at)}` : job.enabled && job.kind === 'watch' ? 'on' : 'paused'}
      </span>
      {job.only_on_change && (
        <span className="muted small" title="Runs whose result matches the last one are not announced">
          {job.last_change_at ? `last change: ${fmtWhen(job.last_change_at)}` : 'no change recorded yet'}
        </span>
      )}
      {job.last_skip_reason && job.last_skip_at && (
        <span className="muted small" title={`Slot at ${fmtWhen(job.last_skip_at)} was skipped`}>skipped: {skipReason(job.last_skip_reason)}</span>
      )}
      {job.target === 'desk' && (
        <span className="muted small" title="Each fire opens a desk with this prompt as its brief">desk · {AUTONOMY.find((a) => a.value === (job.desk_autonomy ?? 'plan'))?.label}</span>
      )}
      {/* Run and History are the two a row is opened for, so they stay put. The rest appear when the
          row is hovered or holds focus; they keep their space, so nothing shifts when they do. */}
      <span className="job-more">
        {(job.kind === 'cron' || job.kind === 'once' || job.kind === 'calendar') && (
          <button className={`icon-btn sm ${editing ? 'on' : ''}`} title={spent ? 'Run again at…' : 'Edit'}
            aria-label={`Edit ${job.name}`} aria-expanded={editing} onClick={() => setEditing((v) => !v)}>
            <Pencil size={12} />
          </button>
        )}
        <label className="chip-check-row small" title="Runs whose result matches the last one are recorded as unchanged and not announced">
          <input type="checkbox" checked={!!job.only_on_change} aria-label={`Notify only when ${job.name} changes`}
            onChange={(e) => void save({ only_on_change: e.target.checked })} />
          <span>Only on change</span>
        </label>
        <select className="small" value={job.notify ?? 'problems'} aria-label={`Notifications for ${job.name}`}
          title="When a run of this job sends a system notification"
          onChange={(e) => void saveNotify(e.target.value as JobNotifyMode)}>
          <option value="problems">Notify on problems</option>
          <option value="always">Notify every run</option>
          <option value="never">Never notify</option>
        </select>
        {job.target !== 'desk' && (
          <>
            <button className={`icon-btn sm ${toolsOpen ? 'on' : ''}`} title={(job.allowed_tools ? `${job.allowed_tools.length} tools allowed` : 'All tools') + (job.model ? ` · ${job.model}` : '')}
              aria-label={`Tools for ${job.name}`} aria-expanded={toolsOpen} onClick={() => setToolsOpen((v) => !v)}>
              <Wrench size={12} />
            </button>
            <button className="icon-btn sm" title="Preview: run it read-only, nothing is proposed or changed" aria-label={`Preview ${job.name}`}
              onClick={() => void preview()}>
              <Eye size={12} />
            </button>
          </>
        )}
        <button className={`icon-btn sm ${shipOpen ? 'on' : ''}`} title="Ship checklist: tests, push, pull request, merge"
          aria-label={`Ship checklist for ${job.name}`} aria-expanded={shipOpen} onClick={() => setShipOpen((v) => !v)}>
          <Rocket size={12} />
        </button>
        <button className="icon-btn sm danger" title="Delete" aria-label={`Delete ${job.name}`}
          onClick={() => { if (confirm(`Delete “${job.name}”?`)) void deleteJob(job.id) }}>
          <Trash2 size={12} />
        </button>
      </span>
      <button className={`icon-btn sm ${history ? 'on' : ''}`} title="Run history" aria-label={`History of ${job.name}`}
        aria-expanded={history} onClick={() => setHistory((v) => !v)}>
        <History size={12} />
      </button>
      {!job.enabled && !spent && (
        <button className="ghost-btn sm" title="Run once as a test: proposal-only, the schedule is untouched" onClick={() => void testRun()}>Test run</button>
      )}
      {tested && !job.enabled && !spent && (
        <button className="primary-btn sm" title="Turn the schedule on" onClick={() => void setJobEnabled(job.id, true)}>Enable</button>
      )}
      <button className="icon-btn sm" title="Run it now" aria-label={`Run ${job.name} now`} onClick={() => void runJobNow(job.id)}>
        <Play size={12} />
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
        <JobRunSettings job={job} save={save} />
      </li>
    )}
    {editing && <li className="job-history"><NewTask job={job} onDone={() => setEditing(false)} /></li>}
    {(shipOpen || shipLive) && (
      <li className="job-history">
        {ship && <ShipChecklistView checklist={ship} />}
        {shipOpen && !shipLive && <ShipStart jobId={job.id} prev={ship} />}
      </li>
    )}
    {history && <JobHistory job={job} />}
    </>
  )
}

/** Start a ship checklist for a job by hand. The fields default to its last checklist's. */
function ShipStart({ jobId, prev }: { jobId: string; prev: ShipChecklist | null }): JSX.Element {
  const upsertShip = useStore((s) => s.upsertShip)
  const toast = useStore((s) => s.toast)
  const [f, setF] = useState({ repo_path: prev?.repo_path ?? '', branch: prev?.branch ?? '', base: prev?.base ?? 'main', test_command: prev?.test_command ?? '' })
  const [busy, setBusy] = useState(false)
  const field = (k: keyof typeof f, label: string, placeholder: string): JSX.Element => (
    <label className="small">
      <span className="muted">{label}</span>{' '}
      <input value={f[k]} placeholder={placeholder} aria-label={label} onChange={(e) => setF({ ...f, [k]: e.target.value })} />
    </label>
  )
  const start = async (): Promise<void> => {
    setBusy(true)
    try {
      upsertShip(await api.ship.start(jobId, { repo_path: f.repo_path.trim(), branch: f.branch.trim(), base: f.base.trim() || 'main', ...(f.test_command.trim() ? { test_command: f.test_command.trim() } : {}) }))
    } catch (e) {
      toast(`Ship: ${(e as Error).message}`, 'error')
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="job-run-settings">
      {field('repo_path', 'Repository', '/Users/me/code/app')}
      {field('branch', 'Branch', 'feature-branch')}
      {field('base', 'Into', 'main')}
      {field('test_command', 'Test command', 'npm test or pytest (detected)')}
      <button type="button" className="primary-btn sm" disabled={busy || !f.repo_path.trim() || !f.branch.trim()} onClick={() => void start()}>
        {prev ? 'Run again' : 'Start'}
      </button>
      <p className="muted small">Runs the tests, then pushes the branch, then opens a pull request. The merge waits for you;
        nothing is ever force-pushed or pushed to main.</p>
    </div>
  )
}

/** Which model a job's runs use. */
function JobRunSettings({ job, save }: { job: Job; save: (patch: Parameters<typeof api.jobs.update>[1]) => Promise<void> }): JSX.Element {
  const models = useStore((s) => s.models)
  const ids = chatModelIds(models)
  if (job.model && !ids.includes(job.model)) ids.unshift(job.model)
  return (
    <div className="job-run-settings">
      <label className="small">
        <span className="muted">Model</span>{' '}
        <select value={job.model ?? ''} aria-label={`Model for ${job.name}`}
          onChange={(e) => void save({ model: e.target.value || null })}>
          <option value="">Default model</option>
          {ids.map((id) => <option key={id} value={id}>{modelLabel(id)}</option>)}
        </select>
      </label>
    </div>
  )
}

const BLANK = { name: '', prompt: '', when: '', dir: '', query: '', mins: '15', onlyChange: false, mode: 'once' as 'once' | 'repeat' | 'folder' | 'mail' | 'calendar', onlyTools: false, desk: false, autonomy: 'plan' as 'plan' | 'propose' }

/** `/Users/me/Downloads` -> `~/Downloads`, for display. */
function tildePath(p: string): string {
  return p.replace(/^\/Users\/[^/]+(?=\/|$)/, '~')
}

/** Hourly / daily / weekdays / weekly presets that compile to cron, with Custom for the raw field, and the next fires. */
function SchedulePicker({ value, onChange, timezone }: { value: Schedule; onChange: (s: Schedule) => void; timezone?: string }): JSX.Element {
  const cron = presetCron(value)
  const [preview, setPreview] = useState<{ next: number[]; error?: string } | null>(null)
  useEffect(() => {
    if (!cron) { setPreview(null); return }
    let live = true
    const t = setTimeout(() => {
      api.jobs.preview(cron, timezone)
        .then((r) => { if (live) setPreview(r) })
        .catch((e: Error) => { if (live) setPreview({ next: [], error: e.message }) })
    }, 300)
    return () => { live = false; clearTimeout(t) }
  }, [cron, timezone])
  const timed = value.preset !== 'hourly' && value.preset !== 'custom'
  return (
    <>
      <select value={value.preset} aria-label="How often" onChange={(e) => onChange({ ...value, preset: e.target.value as Preset })}>
        <option value="hourly">Hourly</option>
        <option value="daily">Daily</option>
        <option value="weekdays">Weekdays</option>
        <option value="weekly">Weekly</option>
        <option value="custom">Custom cron</option>
      </select>
      {value.preset === 'weekly' && (
        <select value={value.day} aria-label="Day of the week" onChange={(e) => onChange({ ...value, day: Number(e.target.value) })}>
          {DAYS.map((d, i) => <option key={d} value={i}>{d}</option>)}
        </select>
      )}
      {timed && <input type="time" value={value.time} aria-label="At" onChange={(e) => onChange({ ...value, time: e.target.value })} />}
      {value.preset === 'custom' && (
        <input type="text" placeholder="cron, e.g. 0 17 * * 5" value={value.cron} aria-label="Cron expression"
          onChange={(e) => onChange({ ...value, cron: e.target.value })} />
      )}
      {preview && (
        <span className={preview.error ? 'msg-error small' : 'muted small'}>
          {preview.error ?? `Next: ${preview.next.map(fmtWhen).join(', ')}`}
        </span>
      )}
    </>
  )
}

/** Schedule a task by hand (a one-off instant by default, a repeating schedule if it should repeat, or a folder to
 * watch: a typed path the backend refuses outside home or hidden, and the toast says why, or a Gmail search to run on
 * as matching mail arrives), or, given `job`, edit that
 * one: only the changed fields are sent. A spent one-off is offered a new time to run again at. */
/** `agentId` makes it a routine of that agent: every fire runs as it, so a desk is not offered. */
export function NewTask({ onDone, job, draft, agentId }: { onDone: () => void; job?: Job; draft?: RoutineDraft | null; agentId?: string }): JSX.Element {
  const { createJob, updateJob, clearRoutineDraft } = useStore()
  const spent = !!job && job.kind === 'once' && job.last_fired_at !== null && job.next_due_at === null
  const [f, setF] = useState(job
    ? { ...BLANK, name: job.name, prompt: job.prompt, mode: job.kind === 'cron' ? 'repeat' as const : job.kind === 'mail' ? 'mail' as const : job.kind === 'calendar' ? 'calendar' as const : 'once' as const, when: job.run_at && !spent ? toLocalInput(job.run_at) : '', query: (job.kind === 'calendar' ? job.calendar_query : job.mail_query) ?? '', mins: String(job.minutes_before ?? 15), onlyChange: !!job.only_on_change }
    : draft ? { ...BLANK, name: draft.name, prompt: draft.prompt, mode: 'repeat' as const } : BLANK)
  const [sched, setSched] = useState<Schedule>(job?.kind === 'cron' ? cronPreset(job.cron) : DEFAULT_SCHEDULE)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [picked, setPicked] = useState<string[]>(['current_time'])
  const cron = presetCron(sched)
  const ready = !!f.name.trim() && !!f.prompt.trim() && (f.mode === 'repeat' ? !!cron : f.mode === 'folder' ? !!f.dir.trim() : (f.mode === 'mail' || f.mode === 'calendar') ? !!f.query.trim() : !!f.when)

  const submit = async (e: React.FormEvent): Promise<void> => {
    e.preventDefault()
    if (!ready || busy) return
    setBusy(true)
    setErr(null)
    const common = { name: f.name.trim(), prompt: f.prompt.trim(), only_on_change: f.onlyChange }
    // datetime-local has no zone, so Date.parse reads it as local time — which is what the user typed.
    const schedule = f.mode === 'repeat'
      ? { kind: 'cron' as const, cron }
      : f.mode === 'folder'
        ? { kind: 'watch' as const, watch_dir: f.dir.trim() }
        : f.mode === 'mail'
          ? { kind: 'mail' as const, mail_query: f.query.trim() }
          : f.mode === 'calendar'
            ? { kind: 'calendar' as const, calendar_query: f.query.trim(), minutes_before: Math.max(0, Math.min(1440, Math.round(Number(f.mins) || 0))) }
          // An untouched time keeps the job's exact instant: the input only holds minutes, and a re-sent past instant is a 400.
          : { kind: 'once' as const, run_at: job?.run_at && f.when === toLocalInput(job.run_at) ? job.run_at : Math.round(Date.parse(f.when) / 1000) }
    let ok: boolean
    if (job) {
      const patch = diffJob<Job>(job, { ...common, ...schedule })
      // A one-off that already ran is switched back on by giving it a new time; the backend refuses `enabled` alone.
      if (spent) patch.enabled = true
      const refused = Object.keys(patch).length ? await updateJob(job.id, patch) : null
      setErr(refused)
      ok = refused === null
    } else {
      ok = await createJob({ ...common, ...schedule, enabled: !draft, allowed_tools: f.onlyTools && !f.desk ? picked : null,
        ...(f.desk && !agentId ? { target: 'desk' as const, desk_autonomy: f.autonomy } : {}), ...(agentId ? { agent_id: agentId } : {}) })
    }
    setBusy(false)
    if (ok) {
      if (!job) setF(BLANK)
      if (draft) clearRoutineDraft()
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
        <select value={f.mode} aria-label="When it runs" onChange={(e) => setF({ ...f, mode: e.target.value as typeof f.mode })}>
          <option value="once">Once</option>
          <option value="repeat">Repeat</option>
          {!job && <option value="folder">When files appear in a folder</option>}
          <option value="mail">When matching mail arrives</option>
          <option value="calendar">Before a calendar event</option>
        </select>
        {f.mode === 'repeat'
          ? <SchedulePicker value={sched} onChange={setSched} timezone={job?.timezone} />
          : f.mode === 'folder'
            ? <input type="text" placeholder="Folder, e.g. ~/Downloads" value={f.dir} aria-label="Folder to watch"
                onChange={(e) => setF({ ...f, dir: e.target.value })} />
            : f.mode === 'calendar'
            ? <>
                <input type="text" placeholder="Event title or guest, e.g. standup" value={f.query} maxLength={300}
                  aria-label="Calendar event words" title="Every word must appear in the event's title or guest list"
                  onChange={(e) => setF({ ...f, query: e.target.value })} />
                <input type="number" min={0} max={1440} value={f.mins} aria-label="Minutes before the event" style={{ width: 64 }}
                  onChange={(e) => setF({ ...f, mins: e.target.value })} />
                <span className="muted small">min before</span>
              </>
            : f.mode === 'mail'
            ? <input type="text" placeholder="Gmail search, e.g. from:landlord" value={f.query} maxLength={500}
                aria-label="Gmail search" title="Checked every five minutes; mail already there when you save does not count"
                onChange={(e) => setF({ ...f, query: e.target.value })} />
            : <>
                {spent && <span className="muted small">Run again at…</span>}
                <input type="datetime-local" value={f.when} aria-label={spent ? 'Run again at' : 'When it should run'}
                  onChange={(e) => setF({ ...f, when: e.target.value })} />
              </>}
        {job && <button className="ghost-btn sm" type="button" onClick={onDone}>Cancel</button>}
        <button className="primary-btn sm" type="submit" disabled={!ready || busy}>{job ? 'Save' : 'Schedule'}</button>
      </div>
      {err && <p className="msg-error">{err}</p>}
      <label className="chip-check-row small">
        <input type="checkbox" checked={f.onlyChange} onChange={(e) => setF({ ...f, onlyChange: e.target.checked })} />
        <span>Notify only when the result changes</span>
      </label>
      {draft && <p className="muted small">It starts switched off. Pick when it repeats, save, then use Test run on its row and Enable once the result looks right.</p>}
      {!job && !agentId && (
        <label className="chip-check-row small">
          <input type="checkbox" checked={f.desk} onChange={(e) => setF({ ...f, desk: e.target.checked })} />
          <span>Start a desk</span>
        </label>
      )}
      {!job && f.desk && (
        <select value={f.autonomy} aria-label="How the desk works"
          onChange={(e) => setF({ ...f, autonomy: e.target.value as 'plan' | 'propose' })}>
          {AUTONOMY.filter((a) => a.value !== 'ask').map((a) => <option key={a.value} value={a.value} title={a.hint}>{a.label}</option>)}
        </select>
      )}
      {!job && !f.desk && (
        <label className="chip-check-row small">
          <input type="checkbox" checked={f.onlyTools} onChange={(e) => setF({ ...f, onlyTools: e.target.checked })} />
          <span>Only allow some tools</span>
        </label>
      )}
      {!job && f.onlyTools && !f.desk && <ToolPicker value={picked} onChange={setPicked} />}
    </form>
  )
}

export default function AgentInbox(): JSX.Element | null {
  const box = useStore((s) => s.agentInbox)
  const jobs = useStore((s) => s.jobs)
  const { refreshJobs, setJobEnabled, setView, openFiles, openDoc, goToDesk, selectChat, setLibraryTab, markDeskSeen, markInboxRunSeen, rejectJobProposals } = useStore()
  const draft = useStore((s) => s.routineDraft)
  const [showJobs, setShowJobs] = useState(!!draft)
  const [adding, setAdding] = useState(!!draft)
  useEffect(() => { if (draft) { setShowJobs(true); setAdding(true) } }, [draft])
  useEffect(() => { void refreshJobs() }, [refreshJobs])  // once, so the Scheduled count is real before it is opened

  if (!box) return null
  const { approvals, proposals } = box.needs_you
  const paused = box.needs_you.paused_jobs ?? []
  // A desk waiting on an approval is already a row above with Allow/Deny, so its desk event is dropped.
  const approvalRuns = new Set(approvals.map((a) => a.run_id).filter(Boolean))
  const deskRows = (box.needs_you.desks ?? []).filter((e) => !(e.run_id && approvalRuns.has(e.run_id)))
  const elsewhere = box.needs_you.elsewhere ?? []
  // A job with several pending proposals gets one "Reject all" row, so a noisy job is one click to clear.
  const perJob = new Map<string, { name: string; n: number }>()
  for (const p of proposals) {
    if (!p.job_id) continue
    const g = perJob.get(p.job_id) ?? { name: p.source?.name ?? 'A deleted job', n: 0 }
    perJob.set(p.job_id, { ...g, n: g.n + 1 })
  }
  const bulk = [...perJob.entries()].filter(([, g]) => g.n > 1)

  const goDesk = (deskId: string): void => { void goToDesk(deskId) }
  const goChat = (conversationId: string): void => {
    setView('chat')
    void selectChat(conversationId)
  }
  const goQueue = (key: InboxQueueKey): void => {
    if (key === 'doc_edits') {
      // Straight to the first doc with an edit waiting, so the diff is on screen rather than a marker in the tree.
      const first = useStore.getState().docs.find((d) => typeof d.pending === 'number' && d.pending > 0)
      if (first) void openDoc(first.id)
      else openFiles('notes')
    }
    else if (key === 'memory') useStore.getState().openMemory('list')
    else { setLibraryTab(key === 'workflows' ? 'automations' : key); setView('library') }
  }
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
        {box.counts.needs_you > 0 && <span className="chip needs">{box.counts.needs_you} need{box.counts.needs_you === 1 ? 's' : ''} you</span>}
        <span className="spacer" />
        {box.scheduler.next_due_at && <span className="muted small"><Timer size={11} /> next job {fmtWhen(box.scheduler.next_due_at)}</span>}
        {box.scheduler.wake_unavailable && <span className="muted small" title="This Mac is not woken for a job; a slot missed while asleep runs as soon as it wakes">Jobs run while the Mac is awake and Grain is open</span>}
        <button className={`ghost-btn sm ${showJobs ? 'on' : ''}`} aria-expanded={showJobs} onClick={toggleJobs}>
          Scheduled ({jobs.length})
        </button>
      </header>

      {showJobs && (
        <div className="inbox-jobs">
          <h5>
            Scheduled tasks <span className="muted small">{box.scheduler.timezone}</span>
            <span className="spacer" />
            <button className={`icon-btn sm ${adding ? 'on' : ''}`} title="Schedule a task" aria-label="Schedule a task"
              onClick={() => setAdding((v) => !v)}><Plus size={13} /></button>
          </h5>
          {adding && <NewTask key={draft?.prompt ?? ''} draft={draft} onDone={() => setAdding(false)} />}
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
            {approvals.map((a) => <ApprovalRow key={a.call_id} a={a} onDesk={goDesk} onChat={goChat} />)}
            {deskRows.map((e) => (
              <li className="inbox-item" key={e.id}>
                <div className="inbox-row">
                  <Dot tone="needs-you" label="Waiting on you" />
                  <span className="inbox-job"><Users size={12} /> {e.desk_title || 'Desk'}</span>
                  <span className="inbox-line" title={e.body || e.kind}>{e.body || e.kind}</span>
                  <span className="muted small inbox-when">{fmtWhen(e.created_at)}</span>
                  <button className="primary-btn sm" onClick={() => goDesk(e.desk_id)}>{e.kind === 'review' ? 'Review' : 'Open'} <ArrowRight size={13} /></button>
                  <button className="icon-btn sm" title="Mark seen" aria-label="Mark seen" onClick={() => void markDeskSeen(e.desk_id)}><X size={13} /></button>
                </div>
              </li>
            ))}
            {paused.map((p) => (
              <li className="inbox-item" key={p.id}>
                <div className="inbox-row">
                  <Dot tone="failed" label="Paused after failing" />
                  <span className="inbox-job">{p.name}</span>
                  <span className="inbox-line" title={p.reason}>Paused: {p.reason}</span>
                  <button className="primary-btn sm" onClick={() => void setJobEnabled(p.id, true)}><Play size={13} /> Resume</button>
                </div>
              </li>
            ))}
            {bulk.map(([jobId, g]) => (
              <li className="inbox-item" key={`bulk-${jobId}`}>
                <div className="inbox-row">
                  <Dot tone="needs-you" label="Waiting on you" />
                  <span className="inbox-job">{g.name}</span>
                  <span className="inbox-line">{g.n} proposals</span>
                  <button className="ghost-btn sm"
                    onClick={() => { if (confirm(`Reject all ${g.n} proposals from “${g.name}”?`)) void rejectJobProposals(jobId) }}>
                    <X size={13} /> Reject all from {g.name}
                  </button>
                </div>
              </li>
            ))}
            {proposals.map((p) => (
              <ProposalCard key={p.id} p={p} onOpen={
                p.source?.kind === 'desk' ? () => goDesk((p.source as { id: string }).id)
                  : p.conversation_id ? () => void selectChat(p.conversation_id as string) : undefined} />
            ))}
            {elsewhere.map((q) => (
              <li className="inbox-item" key={q.key}>
                <div className="inbox-row">
                  <Dot tone="needs-you" label="Waiting on you" />
                  <span className="inbox-job">{q.label}</span>
                  <span className="inbox-line">{q.count} waiting</span>
                  <button className="ghost-btn sm" onClick={() => goQueue(q.key)}>Review <ArrowRight size={13} /></button>
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}

      {away.length > 0 && (
        <div className="inbox-group">
          <h5>While you were away <span className="muted small">
            {box.counts.late > 0 ? `${box.counts.late} late · ` : ''}{box.counts.failed > 0 ? `${box.counts.failed} failed · ` : ''}
            {away.length} run{away.length === 1 ? '' : 's'}</span>
            {(box.counts.unseen_runs ?? 0) > 0 && (
              <><span className="spacer" /><button className="link small" onClick={() => void markInboxRunSeen(null)}>Mark all read</button></>
            )}</h5>
          <ul className="inbox-list">{away.map((r) => <RunCard key={r.run_id} r={r} />)}</ul>
        </div>
      )}
    </section>
  )
}
