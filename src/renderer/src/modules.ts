/**
 * The one list of what the shell is made of: which cards the Today screen shows and which views the
 * sidebar offers. Both are user-toggleable (Settings → Views, or the slider button on Today) and
 * persist in settings as exceptions — a missing homeWidgets key means "on".
 * Meetings and Activity ship hidden (see llm.DEFAULT_SETTINGS); Settings → Views turns them back on.
 */
import type { View } from './store'
import { moduleHome } from './shell/registry'
import { navEntries } from './shell/nav'

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

/** Each label is its card's title on Today, word for word, so the toggle and the thing it hides read the same. */
export const HOME_MODULES: HomeModule[] = [
  { key: 'agent', label: 'Agent inbox' },
  { key: 'recap', label: 'Daily recap' },
  { key: 'calendar', label: 'Calendar' },
  homeRow('todos'),
  homeRow('health'),
  // "Mail inbox", not "Inbox": the agent inbox is on the same list.
  { key: 'inbox', label: 'Mail inbox' },
  { key: 'mailwatch', label: 'Waiting mail' },
  { key: 'plan', label: 'Day plan' },
  { key: 'gtasks', label: 'Google Tasks (when sync is off)' },
  { key: 'drive', label: 'Drive' },
  { key: 'meetings', label: 'Meetings' },
  { key: 'projects', label: 'Projects' },
  { key: 'memories', label: 'Recently learned' },
  { key: 'chats', label: 'Recent chats' }
]

/**
 * Views that may be hidden or moved between the sidebar and the title bar (shell/nav.tsx). Home, chats
 * and Files are the shell itself and stay. Showing Meetings records nothing: recording is
 * `meetings.enabled` plus an acknowledged consent notice, both off until the user sets them.
 */
export const OPTIONAL_VIEWS: { view: View; label: string }[] = navEntries().map(({ view, label }) => ({ view, label }))

export { homeModuleOn, viewHidden } from './moduleToggles'
