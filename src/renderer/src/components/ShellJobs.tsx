import { useEffect, useState } from 'react'
import type { ShellJobInfo, ShellJobTail } from '@shared/types'
import { api } from '../lib/api'
import { ageLabel, jobIsLive, jobStateLabel } from '../lib/runningViews'

/**
 * The shell commands the agent started, as Terminal-card rows: command, age, End and, on click, a tail of the output. Only live processes are listed (finished jobs drop out), mostly desk jobs and processes an earlier run of the app
 * left behind. It refetches on the app topic's `shell_jobs` event; there is no poller. Hidden when nothing is tracked.
 */
export default function ShellJobs(): JSX.Element | null {
  const [jobs, setJobs] = useState<ShellJobInfo[]>([])
  const [open, setOpen] = useState<string | null>(null)
  const [tail, setTail] = useState<ShellJobTail | null>(null)
  const [error, setError] = useState<string | null>(null)
  const load = (): void => { void api.shellJobs().then((r) => setJobs(r.jobs.filter(jobIsLive))).catch(() => undefined) }
  useEffect(() => {
    load()
    window.addEventListener('grain-shell-jobs', load)
    return () => window.removeEventListener('grain-shell-jobs', load)
  }, [])
  useEffect(() => {
    if (!open) return setTail(null)
    void api.shellJobTail(open).then(setTail).catch(() => setTail(null))
  }, [open, jobs])
  if (!jobs.length) return null
  const kill = async (id: string): Promise<void> => {
    setError(null)
    try { await api.killShellJob(id) } catch (e) { setError((e as Error).message) }
    load()
  }
  return (
    <>
      {jobs.map((j) => (
        <div key={j.job_id} className="term-row">
          <button type="button" className="term-row-main" title={`${j.cwd}\npid ${j.pid ?? '?'}`} onClick={() => setOpen(open === j.job_id ? null : j.job_id)}>
            <span className="inbox-dot working" role="img" aria-label={jobStateLabel(j)} />
            <span className="term-row-name"><code>{j.command}</code></span>
            <span className="term-row-sub">{ageLabel(j.started)}</span>
          </button>
          <span className="term-row-actions"><button type="button" className="link small" onClick={() => void kill(j.job_id)}>End</button></span>
          {open === j.job_id && tail?.job_id === j.job_id && (
            tail.note ? <p className="muted small term-tail">{tail.note}</p> : <pre className="ctx-prompt term-tail">{tail.output || '(no output yet)'}</pre>
          )}
        </div>
      ))}
      {error && <p className="muted small">{error}</p>}
    </>
  )
}
