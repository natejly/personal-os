import type { CanvasWindow, DragPayload, WidgetKind } from '@shared/types'
import { api } from '../lib/api'
import { useStore } from '../store'
import type { Point } from './snapping'
import { useCanvas } from './store'

export interface OpenTarget {
  /** the space to open in; default the active one */
  canvasId?: string
  /** canvas point for the window's top-left; default the target space's spawn point */
  at?: Point
  /** an OS file drop's files (the payload is null then), or the files riding along a 'file' payload */
  files?: FileList | null
}

/**
 * Opens the window a drag payload stands for, in `canvasId` (default: the active space). The single
 * source of truth for "payload → window": the plane and the sidebar's space rows both drop through it.
 */
export async function openPayload(p: DragPayload | null, t: OpenTarget = {}): Promise<CanvasWindow | null> {
  const st = useCanvas.getState()
  const cid = t.canvasId ?? st.activeCanvasId
  if (!cid) return null
  const projectId = st.canvases[cid]?.project_id ?? null
  const open = (kind: WidgetKind, ref: string | null = null, config?: Record<string, unknown>): Promise<CanvasWindow | null> =>
    useCanvas.getState().openWindow(kind, ref, t.at, config, cid)
  const upload = async (): Promise<CanvasWindow | null> => {
    if (!t.files?.length) return null
    await useStore.getState().uploadDocuments(t.files, projectId)
    return open('documents')
  }

  if (!p) return upload()
  switch (p.kind) {
    case 'conversation': return open('chat', p.id)
    case 'nav': return open(p.id as WidgetKind)
    case 'todo': return open('todos')
    case 'document': return open('documents')
    case 'memory': return open('memory')
    case 'project': return open('project', p.id)
    case 'note': return open('note', p.id)
    case 'widget': return open('dashboard-widget', p.id, { dashboard_id: p.dashboardId })
    case 'file': return upload()
  }
  return null
}
