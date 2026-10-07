import { Brain, Calendar, Mail, Library } from 'lucide-react'
import type { WidgetKind } from '@shared/types'
import type { View } from '../store'
import { MODULES } from './registry'

export interface NavEntry {
  view: View
  label: string
  /** One line on what the view is for, shown on hover. */
  description?: string
  icon: JSX.Element
  /** The canvas widget the entry drags in, where one exists. */
  kind?: WidgetKind
  order: number
}

/**
 * The shell's own sidebar views. Today and Files are fixed and come before; Calendar and Mail take 14 and 16,
 * Memory and Library 30 and 40, and a module slots by its `nav.order` (Lists 12, Health 18).
 */
const SHELL: NavEntry[] = [
  { view: 'memory', label: 'Memory', description: 'What Grain remembers about you: standing preferences, a dated log, notes, your voice and the knowledge graph', icon: <Brain size={15} />, kind: 'memory', order: 30 },
  { view: 'library', description: 'Skills, agents, automations and connectors', label: 'Library', icon: <Library size={15} />, order: 40 },
  { view: 'calendar', description: 'Your week and the day\'s events, from Google Calendar', label: 'Calendar', icon: <Calendar size={15} />, kind: 'calendar', order: 14 },
  { view: 'mail', description: 'Your Gmail inbox: read, reply and draft', label: 'Mail', icon: <Mail size={15} />, order: 16 }
]

// Built on first call, never at import: the registry imports module views, and those views render the
// AppSwitcher, so MODULES is still uninitialised while this file is first evaluated.
let cache: NavEntry[] | null = null
export function navEntries(): NavEntry[] {
  if (cache) return cache
  const mods: NavEntry[] = MODULES.filter((m) => m.nav && m.view).map((m) => (
    { view: m.view!.id, label: m.label, description: m.description, icon: m.icon, kind: m.widget?.kind, order: m.nav!.order }
  ))
  // Array.sort is stable, so on a tie the shell's own entry comes first.
  return (cache = [...SHELL, ...mods].sort((a, b) => a.order - b.order))
}

/** Hover text for a nav entry: its label, then what it is for. */
export const navTitle = (e: { label: string; description?: string }): string => (e.description ? `${e.label} — ${e.description}` : e.label)
