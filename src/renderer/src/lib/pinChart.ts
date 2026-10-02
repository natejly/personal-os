import { api } from './api'
import { useCanvas } from '../canvas/store'
import { chartToWidgetSpec } from './boundWidget'
import type { Spec } from '../components/ChartBlock'

const PINNED = 'Pinned charts'

/** Store a chat chart as a static-data widget on the "Pinned charts" dashboard and open it in the active space. */
export async function pinChart(spec: Spec): Promise<void> {
  const dashboards = await api.dashboards.list()
  const d = dashboards.find((x) => x.name === PINNED) ?? (await api.dashboards.create({ name: PINNED }))
  // `spec` is a ready widget spec, so the backend binds the stored rows and makes no model call
  const w = await api.dashboards.addWidget(d.id, { kind: 'chart', title: spec.title || `${spec.type} chart`, spec: chartToWidgetSpec(spec) })
  const cv = useCanvas.getState()
  if (cv.activeCanvasId) await cv.openWindow('dashboard-widget', w.id, undefined, { dashboard_id: d.id }, cv.activeCanvasId)
}
