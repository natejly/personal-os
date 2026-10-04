/**
 * The one list of what the shell is made of: which cards the Today screen shows and which views the
 * sidebar offers. Both are user-toggleable (Settings → Modules, or the slider button on Today) and
 * persist in settings as exceptions — a missing homeWidgets key means "on". Cowork, Meetings
 * and Activity ship hidden (see llm.DEFAULT_SETTINGS); Settings → Modules turns them back on.
 */
import type { View } from './store'
import { moduleHome, moduleForView } from './shell/registry'

export interface HomeModule {
  key: string
  label: string
}

/** The row for a module-owned card or view; throws at load if the module is missing, since that is a build mistake. */
function homeRow(key: string): HomeModule {
  const h = moduleHome(key)?.home
  if (!h) throw new Error(`modules: no module owns the Today card "${key}"`)
  return { key: h.key, label: h.label }
}
function viewRow(view: View): { view: View; label: string } {
  const m = moduleForView(view)
  if (!m?.view) throw new Error(`modules: no module owns the view "${view}"`)
  return { view: m.view.id, label: m.label }
}

export const HOME_MODULES: HomeModule[] = [
  { key: 'agent', label: 'Agent inbox' },
  { key: 'recap', label: 'Daily recap' },
  { key: 'calendar', label: 'Calendar' },
  homeRow('todos'),
  homeRow('health'),
  { key: 'inbox', label: 'Unread mail' },
  { key: 'mailwatch', label: 'Waiting mail' },
  { key: 'plan', label: 'Day plan' },
  { key: 'gtasks', label: 'Google Tasks' },
  { key: 'drive', label: 'Drive files' },
  { key: 'meetings', label: 'Upcoming meetings' },
  { key: 'projects', label: 'Projects' },
  { key: 'memories', label: 'Recently learned' },
  { key: 'chats', label: 'Recent chats' }
]

/** Views that may be removed from the sidebar. Home and chats are the shell itself and stay. */
export const OPTIONAL_VIEWS: { view: View; label: string }[] = [
  viewRow('todos'),
  viewRow('health'),
  { view: 'calendar', label: 'Calendar' },
  { view: 'mail', label: 'Mail' },
  { view: 'dashboards', label: 'Dashboards' },
  { view: 'library', label: 'Library' },
  { view: 'cowork', label: 'Cowork' },
  // Showing the view records nothing. Recording is `meetings.enabled` plus an acknowledged consent
  // notice, both off until the user sets them, so this toggle only decides whether the row is there.
  { view: 'meetings', label: 'Meetings' },
  { view: 'activity', label: 'Activity' }
]

export { homeModuleOn, viewHidden } from './moduleToggles'
