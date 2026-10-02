import type { CanvasWindow, WidgetKind } from '@shared/types'
import { handoff } from '../lib/handoff'
import { viewHidden } from '../moduleToggles'
import { useStore } from '../store'

/** Kinds with a classic equivalent. A note and usage have none, so they get no Expand. */
const EXPANDABLE = new Set<WidgetKind>(['chat', 'todos', 'calendar', 'board', 'dashboard-widget', 'memory', 'graph', 'documents', 'recap', 'project'])

/** Kinds whose classic equivalent is a view the user can hide (Settings → Views). Memory and documents
 * live in Settings → Knowledge base, which cannot be hidden. */
const HIDEABLE_VIEW: Partial<Record<WidgetKind, string>> = {
  todos: 'todos', calendar: 'calendar', board: 'boards', 'dashboard-widget': 'dashboards'
}

/** Whether a window kind has a reachable classic equivalent (false for note, usage, or a hidden view). */
export const canExpand = (w: CanvasWindow): boolean => {
  if (!EXPANDABLE.has(w.kind)) return false
  const view = HIDEABLE_VIEW[w.kind]
  if (view && viewHidden(useStore.getState().settings, view)) return false
  // A chat or project window without its referent has nothing to expand to.
  if (w.kind === 'chat' || w.kind === 'project') return !!w.ref_id
  return true
}

/** Navigate the main window to the classic equivalent. Leaves the canvas; the window stays. Memory,
 * graph and documents open Settings → Knowledge base over the canvas instead. */
export function expandWindow(w: CanvasWindow): void {
  if (!canExpand(w)) return
  const app = useStore.getState()
  switch (w.kind) {
    case 'chat':
      if (w.ref_id) app.selectChat(w.ref_id).catch((e: unknown) => useStore.getState().toast((e as Error)?.message ?? String(e), 'error'))
      break
    case 'todos':
      app.setView('todos')
      break
    case 'calendar':
      app.setView('calendar')
      break
    case 'board':
      handoff('board', w.ref_id)
      app.setView('boards')
      break
    case 'dashboard-widget':
      handoff('dashboard', String(w.config.dashboard_id ?? '') || null)
      app.setView('dashboards')
      break
    case 'memory':
      app.openMemory('split')
      break
    case 'graph':
      app.openMemory('graph')
      break
    case 'documents':
      app.openSettings('knowledge', 'documents')
      break
    case 'recap':
      app.setView('home')
      break
    case 'project':
      if (w.ref_id) app.openProject(w.ref_id)
      break
  }
}
