import { api } from './api'
import { useCanvas } from '../canvas/store'
import { useStore } from '../store'
import { chartToWidgetSpec } from './boundWidget'
import type { Spec } from '../components/ChartBlock'

/** The same dashboard widget_tools.py files the model's widgets on: one auto-dashboard for chat charts. */
const PINNED = 'Chat widgets'

/** Toast where a window went, with a way there. */
export function addedToSpace(canvasId: string): void {
  const cv = useCanvas.getState()
  useStore.getState().toast(`Added to ${cv.canvases[canvasId]?.name ?? 'the space'}`, 'info', { label: 'Open', run: () => void cv.enterSpace(canvasId) })
}

/** Store a chat chart as a static-data widget on the "Chat widgets" dashboard and open it in the last-active space. */
export async function pinChart(spec: Spec): Promise<void> {
  const target = useCanvas.getState().activeCanvasId
  if (!target) throw new Error('No space yet — create one first')
  const dashboards = await api.dashboards.list()
  const d = dashboards.find((x) => x.name === PINNED) ?? (await api.dashboards.create({ name: PINNED }))
  // `spec` is a ready widget spec, so the backend binds the stored rows and makes no model call
  const w = await api.dashboards.addWidget(d.id, { kind: 'chart', title: spec.title || `${spec.type} chart`, spec: chartToWidgetSpec(spec) })
  if (await useCanvas.getState().openWindow('dashboard-widget', w.id, undefined, { dashboard_id: d.id }, target)) addedToSpace(target)
}
