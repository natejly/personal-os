/**
 * The one list of what the shell is made of: which cards the Today screen shows and which views the
 * sidebar offers. Both are user-toggleable (Settings → Modules, or the slider button on Today) and
 * persist in settings as exceptions — a missing key means "on", so new modules ship enabled.
 */
import type { Settings } from '@shared/types'
import type { View } from './store'

export interface HomeModule {
  key: string
  label: string
}

export const HOME_MODULES: HomeModule[] = [
  { key: 'recap', label: 'Daily recap' },
  { key: 'calendar', label: 'Calendar' },
  { key: 'todos', label: 'Todos' },
  { key: 'inbox', label: 'Inbox' },
  { key: 'projects', label: 'Projects' },
  { key: 'memories', label: 'Recently learned' },
  { key: 'chats', label: 'Recent chats' }
]

/** Views that may be removed from the sidebar. Home and chats are the shell itself and stay. */
export const OPTIONAL_VIEWS: { view: View; label: string }[] = [
  { view: 'todos', label: 'Todos' },
  { view: 'calendar', label: 'Calendar' },
  { view: 'boards', label: 'Boards' },
  { view: 'dashboards', label: 'Dashboards' },
  { view: 'memory', label: 'Memory' },
  { view: 'documents', label: 'Documents' }
]

export const homeModuleOn = (s: Settings, key: string): boolean => s.homeWidgets?.[key] !== false
export const viewHidden = (s: Settings, view: string): boolean => (s.hiddenViews ?? []).includes(view)
