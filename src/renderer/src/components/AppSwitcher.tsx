import { Calendar, Mail } from 'lucide-react'
import { useShallow } from 'zustand/react/shallow'
import { useStore, type View } from '../store'
import { viewHidden } from '../moduleToggles'
import { MODULES } from '../shell/registry'
import { dragProps } from '../canvas/dnd'
import type { WidgetKind } from '@shared/types'

type AppEntry = { view: View; label: string; icon: JSX.Element; kind?: WidgetKind }

/** The shell's own apps take 0, 10, 20… in their listed order; a module's `nav.order` slots between them. */
const SHELL_APPS: AppEntry[] = [
  { view: 'calendar', label: 'Calendar', icon: <Calendar size={15} />, kind: 'calendar' },
  { view: 'mail', label: 'Mail', icon: <Mail size={15} /> }
]
// Built on first render, never at import: the registry imports module views, and those views render
// this component, so MODULES is still uninitialised while this file is first evaluated.
let cache: { modules: typeof MODULES; apps: AppEntry[] } | null = null
const strip = (): NonNullable<typeof cache> => {
  if (cache) return cache
  const modules = MODULES.filter((m) => m.nav?.section === 'apps' && m.view)
  const apps = [
    ...SHELL_APPS.map((a, i) => ({ a, order: i * 10 })),
    ...modules.map((m) => ({ a: { view: m.view!.id, label: m.label, icon: m.icon, kind: m.widget?.kind }, order: m.nav!.order }))
  ].sort((x, y) => x.order - y.order).map((r) => r.a)
  return (cache = { modules, apps })
}

/**
 * Todos, Calendar and Mail as icons at the right end of every title bar, so they sit in the same
 * spot in each view and stay reachable with the sidebar hidden. A click navigates (as the sidebar
 * rows did); a drag drops the widget into a space, so the canvas keeps them as drag sources.
 * Modules join the strip with `nav.section: 'apps'`.
 */
export default function AppSwitcher(): JSX.Element | null {
  const view = useStore((s) => s.view)
  const settings = useStore((s) => s.settings)
  const setView = useStore((s) => s.setView)
  // useShallow compares element-wise, so a fresh array with the same counts does not re-render.
  const { modules, apps: all } = strip()
  const badges = useStore(useShallow((s) => modules.map((m) => m.nav?.badge?.(s) ?? null)))
  const badgeOf = (v: View): number => badges[modules.findIndex((m) => m.view?.id === v)] ?? 0
  const apps = all.filter((a) => !viewHidden(settings, a.view))
  if (!apps.length) return null
  return (
    <div className="app-switcher no-drag" role="toolbar" aria-label="Apps">
      {apps.map((a) => {
        const n = badgeOf(a.view)
        return (
          <button key={a.view} className={`icon-btn app-switch ${view === a.view ? 'on' : ''}`}
            title={a.label} aria-label={a.label} aria-pressed={view === a.view}
            onClick={() => setView(a.view)}
            {...(a.kind ? dragProps({ kind: 'nav', id: a.kind, label: a.label }) : {})}>
            {a.icon}
            {n > 0 && <span className="app-badge">{n > 99 ? '99+' : n}</span>}
          </button>
        )
      })}
    </div>
  )
}
