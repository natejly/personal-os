import type { DashboardRender, ShowItem } from '@shared/types'

/** Saved dashboards: docs holding a JSON envelope. Pure helpers for the view, the doc hook and the tool card. */

/** True when a doc body is a dashboard envelope (`{"grainDashboard":1,...}`). */
export function isDashboardContent(content: string): boolean {
  const t = content.trimStart()
  if (!t.startsWith('{')) return false
  try {
    const v = JSON.parse(t) as { grainDashboard?: unknown }
    return !!v && typeof v === 'object' && v.grainDashboard === 1
  } catch { return false }
}

/** "Oct 13, 3:42 PM" in the 12-hour clock; empty for an unparseable value. */
export function fmtStamp(iso: string | number | null | undefined): string {
  if (iso === null || iso === undefined || iso === '') return ''
  const d = new Date(typeof iso === 'number' && iso < 1e12 ? iso * 1000 : iso)
  if (Number.isNaN(d.getTime())) return ''
  return d.toLocaleString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit', hour12: true })
}

/** "Last updated <time>" for a live dashboard, "Saved <time>" for a snapshot. */
export const stampText = (mode: 'live' | 'snapshot', iso: string): string => `${mode === 'live' ? 'Last updated' : 'Saved'} ${fmtStamp(iso)}`.trim()

/** One line per failed binding, named by its label (falling back to the binding name). */
export function failureLines(r: Pick<DashboardRender, 'failed' | 'bindings'>): string[] {
  return r.failed.map((f) => {
    const label = r.bindings.find((b) => b.name === f.binding)?.label || f.binding
    const when = fmtStamp(f.lastGoodAt)
    return `${label}: could not refresh, ${when ? `showing data from ${when}` : 'no earlier data'}`
  })
}

/** The dashboard a tool result names, from the event's structured field or its JSON result preview. */
export function dashboardOf(t: { dashboard?: { id?: string; title?: string } | null; result_preview?: string }): { id: string; title: string } | null {
  let d = t.dashboard
  if (!d?.id && t.result_preview) {
    try {
      const p = JSON.parse(t.result_preview) as { dashboard?: { id?: string; title?: string } }
      d = p.dashboard
    } catch { /* not JSON */ }
  }
  return d?.id ? { id: d.id, title: d.title ?? 'Dashboard' } : null
}

/** A saved dashboard as a side-panel item; the source is the doc id. */
export const dashboardShowItem = (id: string, title: string): ShowItem => ({ kind: 'dashboard', title, source: id })
