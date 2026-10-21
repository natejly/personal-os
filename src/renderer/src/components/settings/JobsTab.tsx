import { useEffect, useMemo, useState } from 'react'
import type { Job } from '@shared/types'
import { api } from '../../lib/api'
import { jobSummaryLine, summarizeAppJobs } from '../../lib/jobsSummary'

const dueText = (at: number): string => new Date(at * 1000).toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })

export default function JobsTab(): JSX.Element {
  const [jobs, setJobs] = useState<Job[] | null>(null)
  const [failed, setFailed] = useState(false)
  useEffect(() => {
    let live = true
    api.jobs.list().then((v) => { if (live) setJobs(v) }).catch(() => { if (live) setFailed(true) })
    return () => { live = false }
  }, [])
  const app = useMemo(() => (jobs ? summarizeAppJobs(jobs) : null), [jobs])
  return (
    <div className="jobs-summary">
      {failed && <p className="jobs-note" role="status">Scheduled jobs could not be read</p>}
      {!app && !failed && <p className="jobs-note">Loading…</p>}
      {app && <p className="jobs-note">{jobSummaryLine(app)}</p>}
      {app && app.upcoming.length > 0 && (
        <ul className="jobs-lines">{app.upcoming.map((u) => <li key={u.id}>{u.name} <span className="muted">{dueText(u.dueAt)}</span></li>)}</ul>
      )}
    </div>
  )
}
