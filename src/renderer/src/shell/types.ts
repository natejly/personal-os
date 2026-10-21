/**
 * The frontend half of a feature module (docs/module-manifest.md). One ModuleDef carries every
 * surface a feature puts into the shell, and the shell reads them from `MODULES` instead of naming
 * the feature: App.tsx renders `view`, the canvas
 * catalog takes `widget`, and Settings → Sidebar derives its toggles from `view`.
 *
 * A widget's `kind` is a built-in one or `ext:<id>:<name>` (an extension registers the latter with `ui.widget`). A built-in extension keeps its own store slice beside its view (extensions/todos/store.ts).
 */
import type { FC } from 'react'
import type { State, View } from '../store'
import type { WidgetDef } from '../canvas/registry'

export interface ModuleView {
  id: View
  Component: FC
  /** May be hidden from the sidebar under Settings → Views (Chat is the shell and may not). */
  optional?: boolean
}

export interface ModuleNav {
  /** Position among the sidebar rows, after Files; the shell's own entries use 16, 30 and 40. */
  order: number
}

export interface ModuleDef {
  key: string
  label: string
  /** One line on what the view is for, shown on hover. */
  description?: string
  icon: JSX.Element
  view?: ModuleView
  widget?: WidgetDef
}
