import { Calendar, Mail, Mic, Users, Library, MonitorDot } from 'lucide-react'
import type { Settings, WidgetKind } from '@shared/types'
import type { View } from '../store'
import { MODULES } from './registry'

/** Where a view's entry point lives: a sidebar row or an icon at the right of every title bar. */
export type NavPlace = 'sidebar' | 'apps'

export interface NavEntry {
  view: View
  label: string
  icon: JSX.Element
  /** The canvas widget the entry drags in, where one exists. */
  kind?: WidgetKind
  /** Where it goes until Settings → Modules says otherwise. */
  place: NavPlace
  order: number
}

/**
 * The shell's own movable views. Sidebar rows take 20..50 (Today and Files are fixed and come before),
 * title-bar apps 110 and 120; a module slots by its `nav.order`, plus 100 when it asks for the title bar.
 */
const SHELL: NavEntry[] = [
  { view: 'meetings', label: 'Meetings', icon: <Mic size={15} />, place: 'sidebar', order: 20 },
  { view: 'cowork', label: 'Cowork', icon: <Users size={15} />, place: 'sidebar', order: 30 },
  { view: 'library', label: 'Library', icon: <Library size={15} />, place: 'sidebar', order: 40 },
  { view: 'activity', label: 'Activity', icon: <MonitorDot size={15} />, kind: 'activity', place: 'sidebar', order: 50 },
  { view: 'calendar', label: 'Calendar', icon: <Calendar size={15} />, kind: 'calendar', place: 'apps', order: 110 },
  { view: 'mail', label: 'Mail', icon: <Mail size={15} />, place: 'apps', order: 120 }
]

// Built on first call, never at import: the registry imports module views, and those views render the
// AppSwitcher, so MODULES is still uninitialised while this file is first evaluated.
let cache: NavEntry[] | null = null
export function navEntries(): NavEntry[] {
  if (cache) return cache
  const mods: NavEntry[] = MODULES.filter((m) => m.nav && m.view).map((m) => {
    const apps = m.nav!.section === 'apps'
    return { view: m.view!.id, label: m.label, icon: m.icon, kind: m.widget?.kind, place: apps ? 'apps' : 'sidebar', order: m.nav!.order + (apps ? 100 : 0) }
  })
  // Array.sort is stable, so on a tie the shell's own entry comes first.
  return (cache = [...SHELL, ...mods].sort((a, b) => a.order - b.order))
}

export const placeOf = (s: Settings, e: NavEntry): NavPlace => s.navPlacement?.[e.view] ?? e.place
