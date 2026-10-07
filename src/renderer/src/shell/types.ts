/**
 * The frontend half of a feature module (docs/module-manifest.md). One ModuleDef carries every
 * surface a feature puts into the shell, and the shell reads them from `MODULES` instead of naming
 * the feature: App.tsx renders `view`, the Sidebar lists `nav`, Today renders `home`, the canvas
 * catalog takes `widget`, and Settings → Views derives its toggles from `home` and `view`.
 *
 * Pilot scope: `View` and `WidgetKind` are still closed unions, so a module can only fill a slot
 * whose id already exists in them. Store slices (e.g. `todos` in store.ts) are not moved yet.
 */
import type { FC } from 'react'
import type { TodayDashboard } from '@shared/types'
import type { State, View } from '../store'
import type { WidgetDef } from '../canvas/registry'

export interface ModuleView {
  id: View
  Component: FC
  /** May be hidden from the sidebar under Settings → Views (Home and Chat are the shell and may not). */
  optional?: boolean
}

export interface ModuleNav {
  /** Position among the sidebar rows, after Today and Files; the shell's own entries use 14, 16, 30 and 40. */
  order: number
  /** The count shown beside the entry, or null for none. Runs inside a store selector: keep it pure and cheap. */
  badge?: (s: State) => number | null
}

export interface ModuleHome {
  /** Settings key under `homeWidgets`, and this card's slot in HOME_MODULES. */
  key: string
  label: string
  Card: FC<{ data: TodayDashboard | null }>
}

export interface ModuleDef {
  key: string
  label: string
  /** One line on what the view is for, shown on hover. */
  description?: string
  icon: JSX.Element
  view?: ModuleView
  /** Requires `view` (a nav entry opens it). Its canvas drag kind is `widget.kind` when there is one. */
  nav?: ModuleNav
  widget?: WidgetDef
  home?: ModuleHome
}
