import { useEffect, useState } from 'react'
import type { ShellJobInfo, ShellJobTail } from '@shared/types'
import { api } from '../lib/api'
import { ageLabel, jobIsLive, jobStateLabel } from '../lib/runningViews'

/**
 * The shell commands the agent started, as a "Running" section: command, folder, age, state, a tail of the output and
 * Kill. Chat jobs end with their reply, so what stays here is mostly desk jobs and processes an earlier run of the app
 * left behind. It refetches on the app topic's `shell_jobs` event; there is no poller. Hidden when nothing is tracked.
 */
export default function ShellJobs(): JSX.Element | null {
  const [jobs, setJobs] = useState<ShellJobInfo[]>([])
  const [open, setOpen] = useState<string | null>(null)
  const [tail, setTail] = useState<ShellJobTail | null>(null)
  const [error, setError] = useState<string | null>(null)
  const load = (): void => { void api.shellJobs().then((r) => setJobs(r.jobs)).catch(() => undefined) }
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
  const live = jobs.filter(jobIsLive).length
  const kill = async (id: string): Promise<void> => {
    setError(null)
    try { await api.killShellJob(id) } catch (e) { setError((e as Error).message) }
    load()
  }
  return (
    <section className="ctx-section">
      <h4>Running{live ? ` (${live})` : ''}</h4>
      <ul className="plain-list">
        {jobs.map((j) => (
          <li key={j.job_id}>
            <div className="ctx-meta">
              <button className="link" title={`${j.cwd}\npid ${j.pid ?? '?'}`} onClick={() => setOpen(open === j.job_id ? null : j.job_id)}>
                <code>{j.command}</code>
              </button>
              {jobIsLive(j) && <button className="link" onClick={() => void kill(j.job_id)}>Kill</button>}
            </div>
            <small className="muted">{jobStateLabel(j)} · {ageLabel(j.started)} ago · {j.cwd}</small>
            {open === j.job_id && tail?.job_id === j.job_id && (
              tail.note ? <p className="muted small">{tail.note}</p> : <pre className="ctx-prompt">{tail.output || '(no output yet)'}</pre>
            )}
          </li>
        ))}
      </ul>
      {error && <p className="muted small">{error}</p>}
    </section>
  )
}
