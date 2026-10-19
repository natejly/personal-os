import { useEffect, useMemo, useState } from 'react'
import type { ExtensionsDev as Listing, Job } from '@shared/types'
import { api } from '../../lib/api'
import { formatEvery, formatMs } from '../../lib/extensionsDev'
import { extensionJobRows, groupHooksByEvent, jobSummaryLines, summarizeAppJobs } from '../../lib/hooksSummary'
import { Sec } from './shared'

type Loaded<T> = { state: 'loading' } | { state: 'error'; message: string } | { state: 'ok'; value: T }

/** Fetch once on mount; a failure is a value the view shows, never a throw. */
function useLoaded<T>(load: () => Promise<T>): Loaded<T> {
  const [res, setRes] = useState<Loaded<T>>({ state: 'loading' })
  useEffect(() => {
    let live = true
    load().then((value) => { if (live) setRes({ state: 'ok', value }) }).catch((e: unknown) => { if (live) setRes({ state: 'error', message: e instanceof Error ? e.message : String(e) }) })
    return () => { live = false }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- fetch once per mount
  }, [])
  return res
}

const dueText = (at: number): string => new Date(at * 1000).toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })

export default function HooksTab(): JSX.Element {
  const ext = useLoaded<Listing>(() => api.devExtensions.list())
  const jobs = useLoaded<Job[]>(() => api.jobs.list())
  const groups = useMemo(() => (ext.state === 'ok' ? groupHooksByEvent(ext.value.extensions) : []), [ext])
  const extJobs = useMemo(() => (ext.state === 'ok' ? extensionJobRows(ext.value.extensions) : null), [ext])
  const app = useMemo(() => (jobs.state === 'ok' ? summarizeAppJobs(jobs.value) : null), [jobs])
  const lines = jobSummaryLines(app, extJobs)
  return (
    <section>
      <Sec id="hooks">
        <h4>Hooks</h4>
        {ext.state === 'loading' && <p className="hooks-note">Loading…</p>}
        {ext.state === 'error' && <p className="hooks-note" role="status">Hooks could not be read right now ({ext.message}).</p>}
        {ext.state === 'ok' && groups.length === 0 && <p className="hooks-note">No extension has registered a hook.</p>}
        {groups.map((g) => (
          <div key={g.event} className="hooks-event">
            <h5>{g.event}</h5>
            <table className="hooks-table">
              <thead><tr><th>Extension</th><th className="num">Calls</th><th className="num">p50</th><th className="num">p95</th><th>State</th></tr></thead>
              <tbody>
                {g.rows.map((r, i) => (
                  <tr key={`${r.extensionId}:${i}`}>
                    <td>{r.extensionName}</td><td className="num">{r.calls}</td><td className="num">{formatMs(r.p50Ms)}</td><td className="num">{formatMs(r.p95Ms)}</td>
                    <td className={`hooks-state-${r.state}`}>{r.stateLabel}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ))}
      </Sec>
      <Sec id="jobs">
        <h4>Scheduled jobs</h4>
        {ext.state === 'loading' && jobs.state === 'loading' && <p className="hooks-note">Loading…</p>}
        <ul className="hooks-lines">
          {lines.map((l) => <li key={l}>{l}</li>)}
          {jobs.state === 'error' && <li className="muted" role="status">Scheduled jobs could not be read right now.</li>}
          {ext.state === 'error' && <li className="muted" role="status">Extension jobs could not be read right now.</li>}
        </ul>
        {app && app.upcoming.length > 0 && (
          <>
            <p className="hooks-note">Next to run</p>
            <ul className="hooks-lines">{app.upcoming.map((u) => <li key={u.id}>{u.name} <span className="muted">{dueText(u.dueAt)}</span></li>)}</ul>
          </>
        )}
        {extJobs && extJobs.length > 0 && (
          <table className="hooks-table">
            <thead><tr><th>Extension job</th><th>Every</th><th className="num">Runs</th><th>State</th></tr></thead>
            <tbody>
              {extJobs.map((j) => (
                <tr key={`${j.extensionId}:${j.name}`}>
                  <td>{j.extensionName}: {j.name}</td><td>{formatEvery(j.everySeconds)}</td><td className="num">{j.runs}</td><td className={`hooks-state-${j.state}`}>{j.state}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Sec>
    </section>
  )
}
