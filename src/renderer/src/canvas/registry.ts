import type { FC } from 'react'
import type { CanvasWindow, DragKind, WidgetKind } from '@shared/types'
import { setDefaultConfigs, setDefaultSizes } from './store'
import { def as activity } from './widgets/activity'
import { def as board } from './widgets/board'
import { def as calendar } from './widgets/calendar'
import { def as chat } from './widgets/chat'
import { def as dashboardWidget } from './widgets/dashboardWidget'
import { def as documents } from './widgets/documents'
import { def as graph } from './widgets/graph'
import { def as memory } from './widgets/memory'
import { def as note } from './widgets/note'
import { def as project } from './widgets/project'
import { def as recap } from './widgets/recap'
import { def as todos } from './widgets/todos'
import { def as usage } from './widgets/usage'
import '../styles/widgets.css'

/** One entry of the catalog: everything the canvas needs to open, size, chrome and drop onto a kind. */
export interface WidgetDef {
  kind: WidgetKind
  label: string
  /** An element, not a component — matches Sidebar's NAV, MemoryPanel's MODES, ProjectView's TABS. */
  icon: JSX.Element
  defaultSize: { w: number; h: number }
  minSize: { w: number; h: number }
  /** 'minimal' = title bar with a close button only (the note) */
  chrome: 'full' | 'minimal'
  /** wants a status ring (chat does; a note does not) */
  statusful?: boolean
  /** counts against the 6-slot concurrent-live cap: iframes, d3, pollers */
  heavy?: boolean
  /** cannot open without a ref_id: chat, board, note, dashboard-widget, project */
  needsRef?: boolean
  defaultConfig?: Record<string, unknown>
  /** drag payload kinds this widget accepts as a drop target */
  accepts?: DragKind[]
  Component: FC<WidgetProps>
}

export interface WidgetProps {
  window: CanvasWindow
  focused: boolean
  /** false when off-screen, minimized, below 60% zoom, or over the heavy cap: stop polling, unmount iframes */
  live: boolean
  onConfig: (patch: Record<string, unknown>) => void
  onTitle: (t: string) => void
}

/**
 * The catalog, in the order of contract §6. The annotation is the guard: `Record<WidgetKind, WidgetDef>`
 * makes a missing kind a compile error, and a missing kind is a window that cannot render.
 */
export const WIDGETS: Record<WidgetKind, WidgetDef> = {
  chat,
  todos,
  calendar,
  board,
  note,
  'dashboard-widget': dashboardWidget,
  memory,
  graph,
  documents,
  recap,
  project,
  usage,
  activity
}

// The canvas store may not import the registry (its own note), so the catalog comes to it instead.
setDefaultSizes((kind) => WIDGETS[kind]?.defaultSize)
setDefaultConfigs((kind) => WIDGETS[kind]?.defaultConfig)
