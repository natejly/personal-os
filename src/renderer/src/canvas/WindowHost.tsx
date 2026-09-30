import { useCallback, type FC } from 'react'
import {
  Brain, Calendar, CheckSquare, FileText, KanbanSquare, LayoutDashboard, MessageSquare, Network,
  Notebook, FolderKanban, Sparkles, Gauge, MonitorDot, Globe
} from 'lucide-react'
import type { CanvasWindow, WidgetKind } from '@shared/types'
import { WIDGETS, type WidgetProps } from './registry'
import { useCanvas } from './store'
import WidgetBoundary from './WidgetBoundary'

/** Placeholder catalog. Wave 3 reads `WIDGETS[kind].label` / `.icon` instead. */
export const KIND_LABEL: Record<WidgetKind, string> = {
  chat: 'Chat',
  todos: 'Todos',
  calendar: 'Calendar',
  board: 'Board',
  note: 'Note',
  'dashboard-widget': 'Widget',
  memory: 'Memory',
  graph: 'Graph',
  documents: 'Documents',
  recap: 'Recap',
  project: 'Project',
  usage: 'Usage',
  activity: 'Activity',
  web: 'Web'
}

export const KIND_ICON: Record<WidgetKind, JSX.Element> = {
  chat: <MessageSquare size={18} />,
  todos: <CheckSquare size={18} />,
  calendar: <Calendar size={18} />,
  board: <KanbanSquare size={18} />,
  note: <Notebook size={18} />,
  'dashboard-widget': <LayoutDashboard size={18} />,
  memory: <Brain size={18} />,
  graph: <Network size={18} />,
  documents: <FileText size={18} />,
  recap: <Sparkles size={18} />,
  project: <FolderKanban size={18} />,
  usage: <Gauge size={18} />,
  activity: <MonitorDot size={18} />,
  web: <Globe size={18} />
}

/** A row the backend accepted that this build has no widget for: still titled, still closable. */
function Unknown({ window: win }: WidgetProps): JSX.Element {
  return (
    <div className="win-stub">
      <div className="win-stub-kind">{win.kind}</div>
      <p className="muted small">No widget for this kind in this build.</p>
    </div>
  )
}

/** The catalog is the resolver; an unknown kind degrades to a placeholder instead of tearing the plane. */
const resolveWidget = (kind: WidgetKind): FC<WidgetProps> => WIDGETS[kind]?.Component ?? Unknown

export default function WindowHost({ win, focused, live }: { win: CanvasWindow; focused: boolean; live: boolean }): JSX.Element {
  const setWindowConfig = useCanvas((s) => s.setWindowConfig)
  const setWindowTitle = useCanvas((s) => s.setWindowTitle)
  const onConfig = useCallback((patch: Record<string, unknown>) => void setWindowConfig(win.id, patch), [setWindowConfig, win.id])
  const onTitle = useCallback((t: string) => void setWindowTitle(win.id, t), [setWindowTitle, win.id])
  const Body = resolveWidget(win.kind)
  // The ring lives in the title bar's `.win-status`, filled by Canvas: a body may never render chrome.
  return (
    <WidgetBoundary label={win.title || KIND_LABEL[win.kind] || win.kind}>
      <Body window={win} focused={focused} live={live} onConfig={onConfig} onTitle={onTitle} />
    </WidgetBoundary>
  )
}
