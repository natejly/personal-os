import { CheckSquare, Calendar, Mail } from 'lucide-react'
import { useStore, type View } from '../store'
import { viewHidden } from '../modules'
import { dragProps } from '../canvas/dnd'
import type { WidgetKind } from '@shared/types'

type AppEntry = { view: View; label: string; icon: JSX.Element; kind?: WidgetKind }

const APPS: AppEntry[] = [
  { view: 'todos', label: 'Todos', icon: <CheckSquare size={15} />, kind: 'todos' },
  { view: 'calendar', label: 'Calendar', icon: <Calendar size={15} />, kind: 'calendar' },
  { view: 'mail', label: 'Mail', icon: <Mail size={15} /> }
]

/**
 * Todos, Calendar and Mail as icons at the right end of every title bar, so they sit in the same
 * spot in each view and stay reachable with the sidebar hidden. A click navigates (as the sidebar
 * rows did); a drag drops the widget into a space, so the canvas keeps them as drag sources.
 */
export default function AppSwitcher(): JSX.Element | null {
  const view = useStore((s) => s.view)
  const settings = useStore((s) => s.settings)
  const setView = useStore((s) => s.setView)
  const openTodos = useStore((s) => s.dashboard?.todo_stats?.open ?? 0)
  const apps = APPS.filter((a) => !viewHidden(settings, a.view))
  if (!apps.length) return null
  return (
    <div className="app-switcher no-drag" role="toolbar" aria-label="Apps">
      {apps.map((a) => (
        <button key={a.view} className={`icon-btn app-switch ${view === a.view ? 'on' : ''}`}
          title={a.label} aria-label={a.label} aria-pressed={view === a.view}
          onClick={() => setView(a.view)}
          {...(a.kind ? dragProps({ kind: 'nav', id: a.kind, label: a.label }) : {})}>
          {a.icon}
          {a.view === 'todos' && openTodos > 0 && <span className="app-badge">{openTodos > 99 ? '99+' : openTodos}</span>}
        </button>
      ))}
    </div>
  )
}
