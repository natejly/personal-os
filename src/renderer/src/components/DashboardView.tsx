import { useCallback, useEffect, useState } from 'react'
import type { DashboardRender } from '@shared/types'
import { AlertCircle, PanelRight, RefreshCw } from '../icons'
import { api } from '../lib/api'
import { dashboardShowItem, failureLines, stampText } from '../lib/dashboards'
import { useStore } from '../store'
import ChartBlock from './ChartBlock'
import HtmlBlock from './HtmlBlock'
import InteractiveBlock from './InteractiveBlock'
import { ShowCtx } from './ShowButton'

/**
 * A saved dashboard: fetches /render on every mount (every open is a refresh) and shows each view with the
 * reply's own block renderers, so the same sandbox and parsers apply. `chat` offers the side-panel button.
 */
export default function DashboardView({ id, chat }: { id: string; chat?: string }): JSX.Element {
  const [r, setR] = useState<DashboardRender | null>(null)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const projects = useStore((s) => s.projects)
  const load = useCallback(() => {
    setBusy(true)
    api.savedDashboards.render(id).then((d) => { setR(d); setErr('') }).catch((e: Error) => setErr(e.message)).finally(() => setBusy(false))
  }, [id])
  useEffect(() => { setR(null); load() }, [load])

  const pin = (v: string): void => {
    const projectId = v || null
    api.savedDashboards.pin(id, projectId).then(() => setR((d) => (d ? { ...d, project_id: projectId } : d))).catch((e: Error) => setErr(e.message))
  }
  if (!r) return <div className="chart-block placeholder">{err ? <><AlertCircle size={14} /> Couldn&apos;t open dashboard: {err}</> : 'Opening dashboard…'}</div>
  const fails = failureLines(r)
  return (
    // No ShowCtx: the blocks inside carry no Save or Open-in-panel buttons of their own.
    <ShowCtx.Provider value={null}>
      <div className="dashboard-view">
        <header className="dashboard-head">
          <strong>{r.title}</strong>
          <span className="muted small">{r.mode === 'live' ? 'Live' : 'Snapshot'} · {stampText(r.mode, r.updatedAt)}</span>
          <span className="art-fence-actions">
            <select className="show-pick" aria-label="Pin to project" title="Pin to project" value={r.project_id ?? ''} onChange={(e) => pin(e.target.value)}>
              <option value="">Personal</option>
              {projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
            {r.mode === 'live' && <button className="icon-btn ghost" title="Refresh" aria-label="Refresh dashboard" disabled={busy} onClick={load}><RefreshCw size={13} /></button>}
            {chat && <button className="icon-btn ghost" title="Open in side panel" aria-label="Open in side panel" onClick={() => useStore.getState().openShow(chat, dashboardShowItem(id, r.title))}><PanelRight size={13} /></button>}
          </span>
        </header>
        {err && <div className="chart-err"><AlertCircle size={14} /> {err}</div>}
        {fails.length > 0 && <div className="chart-err" role="alert"><AlertCircle size={14} /> {fails.map((l) => <div key={l}>{l}</div>)}</div>}
        {r.html != null && <HtmlBlock source={r.html} streaming={false} dashboard={{ data: r.data ?? undefined }} />}
        {r.views?.map((v, i) => {
          const src = JSON.stringify(v.spec)
          return v.kind === 'interactive' ? <InteractiveBlock key={i} source={src} streaming={false} /> : <ChartBlock key={i} source={src} streaming={false} />
        })}
      </div>
    </ShowCtx.Provider>
  )
}
