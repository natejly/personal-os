import { statusWord } from '../lib/statusWord'
import { useEffect, useState } from 'react'
import { ArrowRight, Scheduled, Play, Trash2 } from '../icons'
import type { Job, JobRunRecord, JobSkipRecord } from '@shared/types'
import { useStore } from '../store'
import { api } from '../lib/api'
import { jobAttention } from '../lib/attention'
import { AttentionDot } from './ChatPulse'
import AppSwitcher from './AppSwitcher'
import SidebarToggle from './SidebarToggle'
import { NewTask, JobHistory, ReportBody, describeJobSchedule, fmtDate, fmtWhen } from './AgentInbox'

const jobState = (j: Job): string => {
  if (j.paused_reason) return 'Switched off by the scheduler'
  if (!j.enabled) return 'Paused'
  return j.next_due_at ? `Next ${fmtWhen(j.next_due_at)}` : j.kind === 'watch' ? 'Watching' : 'On'
}

/** Scheduled jobs: a grid of cards, and one job's detail (edit, controls, history, latest reports) in place of it. */
export default function JobsView(): JSX.Element {
  const jobs = useStore((s) => s.jobs)
  const focusId = useStore((s) => s.jobsFocusId)
  const openJobs = useStore((s) => s.openJobs)
  const [creating, setCreating] = useState(false)
  useEffect(() => { void useStore.getState().refreshJobs() }, [])
  const job = focusId ? jobs.find((j) => j.id === focusId) : undefined

  return (
    <main className="page jobs-page">
      <header className="page-header drag">
        <SidebarToggle />
        <h2><Scheduled size={16} /> Jobs</h2>
        {!job && <button className="primary-btn sm no-drag" onClick={() => setCreating((v) => !v)}>New job</button>}
        <AppSwitcher />
      </header>
      <div className="page-body jobs-body">
        {job ? <JobDetail job={job} onBack={() => openJobs(null)} /> : (
          <>
            {creating && <div className="jobs-new"><NewTask onDone={() => setCreating(false)} /></div>}
            {jobs.length === 0 && !creating && <p className="muted">No jobs</p>}
            <div className="jobs-grid">
              {jobs.map((j) => (
                <button key={j.id} className={`job-card${j.enabled ? '' : ' off'}`} onClick={() => openJobs(j.id)}>
                  <span className="job-card-head">
                    <AttentionDot state={jobAttention(j)} detail={j.paused_reason ? 'switched off by the scheduler' : j.last_error ?? undefined} />
                    <span className="job-card-name">{j.name}</span>
                  </span>
                  <span className="muted small">{describeJobSchedule(j)}</span>
                  <span className="small">{jobState(j)}</span>
                  <span className="muted small">{j.last_fired_at ? `Last run ${fmtWhen(j.last_fired_at)}` : 'Not run yet'}</span>
                  {j.last_error && <span className="job-card-error small" title={j.last_error}>{j.last_error}</span>}
                </button>
              ))}
            </div>
          </>
        )}
      </div>
    </main>
  )
}

function JobDetail({ job, onBack }: { job: Job; onBack: () => void }): JSX.Element {
  const setJobEnabled = useStore((s) => s.setJobEnabled)
  const runJobNow = useStore((s) => s.runJobNow)
  const deleteJob = useStore((s) => s.deleteJob)
  const selectChat = useStore((s) => s.selectChat)
  const [editing, setEditing] = useState(false)
  const [runs, setRuns] = useState<(JobRunRecord | JobSkipRecord)[] | null>(null)
  // Re-read when a run lands (last_fired_at moves) so the reports follow the schedule.
  useEffect(() => {
    let live = true
    api.jobs.runs(job.id, 5).then((r) => { if (live) setRuns(r) }).catch(() => { if (live) setRuns([]) })
    return () => { live = false }
  }, [job.id, job.last_fired_at, job.last_run_id])
  const reports = (runs ?? []).filter((r): r is JobRunRecord => r.status !== 'skipped' && !!(r.summary || r.error)).slice(0, 3)

  return (
    <div className="job-detail">
      <button className="link small" onClick={onBack}>All jobs</button>
      <div className="job-detail-head">
        <AttentionDot state={jobAttention(job)} detail={job.paused_reason ? 'switched off by the scheduler' : job.last_error ?? undefined} />
        <h3>{job.name}</h3>
        <span className="spacer" />
        <label className="switch-wrap" title={job.enabled ? 'Turn this job off' : 'Turn this job on'}>
          <input type="checkbox" checked={job.enabled} aria-label={`${job.name} enabled`} onChange={() => void setJobEnabled(job.id, !job.enabled)} />
          <span className="switch" />
        </label>
        <button className="ghost-btn sm" onClick={() => void runJobNow(job.id)}><Play size={12} /> Run now</button>
        <button className="ghost-btn sm" aria-expanded={editing} onClick={() => setEditing((v) => !v)}>Edit</button>
        <button className="ghost-btn sm danger" onClick={() => { if (confirm(`Delete “${job.name}”?`)) { void deleteJob(job.id); onBack() } }}><Trash2 size={12} /> Delete</button>
      </div>
      <p className="muted small">{describeJobSchedule(job)} · {jobState(job)}{job.last_fired_at ? ` · last run ${fmtDate(job.last_fired_at)}` : ''}</p>
      {job.last_error && <p className="msg-error">{job.last_error}</p>}
      {editing && <NewTask job={job} onDone={() => setEditing(false)} />}

      <h4>Latest outputs</h4>
      {runs && reports.length === 0 && <p className="muted small">No reports</p>}
      {reports.map((r) => (
        <section key={r.run_id} className="job-report">
          <div className="job-report-head small muted">
            <span>{fmtDate(r.started_at)} · {statusWord(r.status)}</span>
            <span className="spacer" />
            {r.conversation_id && <button className="link small" onClick={() => void selectChat(r.conversation_id as string)}>Open chat <ArrowRight size={11} /></button>}
          </div>
          {r.error && <p className="msg-error">{r.error}</p>}
          {r.summary && <ReportBody text={r.summary} />}
        </section>
      ))}

      <h4>Run history</h4>
      <ul className="inbox-jobs"><JobHistory job={job} /></ul>
    </div>
  )
}
