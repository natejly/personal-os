import type { FC, PointerEvent as ReactPointerEvent } from 'react'
import type { BuiltinWidgetKind, CanvasWindow, DragKind, WidgetKind } from '@shared/types'
import type { MenuEntry } from './Menu'
import { setDefaultConfigs, setDefaultSizes, setMinSizes } from './store'
import { def as chat } from './widgets/chat'
import { def as crew } from './widgets/crew'
import { def as doc } from './widgets/doc'
import { def as documents } from './widgets/documents'
import { def as face } from './widgets/face'
import { def as graph } from './widgets/graph'
import { def as memory } from './widgets/memory'
import { def as project } from './widgets/project'
import { def as recap } from './widgets/recap'
import { def as usage } from './widgets/usage'
import { MODULES } from '../shell/registry'
import { CORE, registerWidget, WIDGETS } from '../extensions/host'
import '../styles/widgets.css'

/** One entry of the catalog: everything the canvas needs to open, size, chrome and drop onto a kind. */
export interface WidgetDef {
  kind: WidgetKind
  label: string
  /** An element, not a component — matches Sidebar's NAV, MemoryPanel's MODES, ProjectView's TABS. */
  icon: JSX.Element
  defaultSize: { w: number; h: number }
  minSize: { w: number; h: number }
  /** 'minimal' = title bar with a close button only (the face) */
  chrome: 'full' | 'minimal'
  /** wants a status ring (chat does; a doc does not) */
  statusful?: boolean
  /** counts against the 6-slot concurrent-live cap: iframes, d3, pollers */
  heavy?: boolean
  /** cannot open without a ref_id: chat, project */
  needsRef?: boolean
  defaultConfig?: Record<string, unknown>
  /** drag payload kinds this widget accepts as a drop target */
  accepts?: DragKind[]
  /** Entries the frame adds to this window's right-click menu, under 'Bring to front'. */
  menu?: (win: CanvasWindow, onConfig: (patch: Record<string, unknown>) => void) => MenuEntry[]
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

/** A kind a module owns; absent means the module list and the catalog disagree, which cannot render. */
function moduleWidget(kind: WidgetKind): WidgetDef {
  const w = MODULES.find((m) => m.widget?.kind === kind)?.widget
  if (!w) throw new Error(`canvas registry: no module provides the "${kind}" widget`)
  return w
}
const todos = moduleWidget('todos')
const calendar = moduleWidget('calendar')

/**
 * The built-in kinds, in the order of contract §6. The annotation is the guard: `Record<BuiltinWidgetKind, WidgetDef>`
 * makes a missing kind a compile error, and a missing kind is a window that cannot render.
 */
const BUILTIN_WIDGETS: Record<BuiltinWidgetKind, WidgetDef> = {
  chat,
  todos,
  calendar,
  memory,
  graph,
  documents,
  recap,
  project,
  usage,
  doc,
  face,
  crew
}

for (const def of Object.values(BUILTIN_WIDGETS)) registerWidget(CORE, def)

/** The catalog: the built-in kinds and those extensions registered with `ui.widget`. */
export { WIDGETS }

// The canvas store may not import the registry (its own note), so the catalog comes to it instead.
setDefaultSizes((kind) => WIDGETS[kind]?.defaultSize)
setMinSizes((kind) => WIDGETS[kind]?.minSize)
setDefaultConfigs((kind) => WIDGETS[kind]?.defaultConfig)
