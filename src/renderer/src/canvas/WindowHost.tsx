import { useCallback, type FC } from 'react'
import {
  Brain, Calendar, CheckSquare, FileText, KanbanSquare, LayoutDashboard, MessageSquare, Network,
  Notebook, FolderKanban, Sparkles, Gauge
} from 'lucide-react'
import type { CanvasWindow, WidgetKind } from '@shared/types'
import { useCanvas } from './store'

/**
 * Structurally identical to widgets' `WidgetProps` (contract §6). It lives here so windowmgr
 * compiles before `registry.ts` exists; Wave 3 swaps it for `import type { WidgetProps }`.
 */
export interface WidgetBodyProps {
  window: CanvasWindow
  focused: boolean
  /** false when off-screen, minimized, below 60 % zoom, or over the heavy cap */
  live: boolean
  onConfig: (patch: Record<string, unknown>) => void
  onTitle: (t: string) => void
}

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
  usage: 'Usage'
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
  usage: <Gauge size={18} />
}

/** Every widget's low-cost stand-in: what Overview draws, and what a non-live window falls back to. */
function Proxy({ window: win }: WidgetBodyProps): JSX.Element {
  return (
    <div className="proxy-card">
      {KIND_ICON[win.kind]}
      <strong>{win.title || KIND_LABEL[win.kind]}</strong>
      <span>Paused while off-screen</span>
    </div>
  )
}

function Stub(props: WidgetBodyProps): JSX.Element {
  const { window: win, live } = props
  if (!live) return <Proxy {...props} />
  const keys = Object.keys(win.config)
  return (
    <div className="win-stub">
      <div className="win-stub-kind">{KIND_LABEL[win.kind]}</div>
      <div className="win-stub-ref">{win.ref_id ? `ref ${win.ref_id}` : 'no ref'}</div>
      {keys.length > 0 && <div className="win-stub-ref">{keys.map((k) => `${k}=${String(win.config[k])}`).join(' · ')}</div>}
      <p className="muted small">The real widget lands in Wave 3.</p>
    </div>
  )
}

/**
 * The single function widgets rewrites in this file (contract §1), to
 * `(kind) => WIDGETS[kind].Component`. Everything else here is windowmgr's.
 */
const resolveWidget = (_kind: WidgetKind): FC<WidgetBodyProps> => Stub

export default function WindowHost({ win, focused, live }: { win: CanvasWindow; focused: boolean; live: boolean }): JSX.Element {
  const setWindowConfig = useCanvas((s) => s.setWindowConfig)
  const setWindowTitle = useCanvas((s) => s.setWindowTitle)
  const onConfig = useCallback((patch: Record<string, unknown>) => void setWindowConfig(win.id, patch), [setWindowConfig, win.id])
  const onTitle = useCallback((t: string) => void setWindowTitle(win.id, t), [setWindowTitle, win.id])
  const Body = resolveWidget(win.kind)
  return <Body window={win} focused={focused} live={live} onConfig={onConfig} onTitle={onTitle} />
}
