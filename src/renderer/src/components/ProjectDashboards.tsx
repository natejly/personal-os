import { useEffect, useState } from 'react'
import type { SavedDashboard } from '@shared/types'
import { LayoutDashboard } from '../icons'
import { api } from '../lib/api'
import { rowButton } from '../lib/rowButton'
import DashboardView from './DashboardView'

/** The dashboards pinned to a project, one click to open (DashboardView re-reads its data on open). Hidden when there are none. */
export default function ProjectDashboards({ projectId }: { projectId: string }): JSX.Element | null {
  const [rows, setRows] = useState<SavedDashboard[]>([])
  const [open, setOpen] = useState<string | null>(null)
  useEffect(() => {
    setOpen(null)
    api.savedDashboards.list(projectId).then(setRows).catch(() => setRows([]))
  }, [projectId])
  if (rows.length === 0) return null
  return (
    <section className="project-sec" aria-label="Dashboards">
      <h3 className="project-sec-head"><LayoutDashboard size={14} />Dashboards<span className="count">{rows.length}</span></h3>
      <div className="chat-rows">
        {rows.map((d) => (
          <div key={d.id}>
            <div className="chat-row" {...rowButton(() => setOpen(open === d.id ? null : d.id))}>
              <LayoutDashboard size={14} />
              <span className="chat-row-title">{d.title}</span>
              <span className="muted small">{d.mode === 'live' ? 'Live' : 'Snapshot'}</span>
            </div>
            {open === d.id && <DashboardView id={d.id} />}
          </div>
        ))}
      </div>
    </section>
  )
}
