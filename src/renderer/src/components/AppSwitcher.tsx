import { Sparkles } from 'lucide-react'
import { useShallow } from 'zustand/react/shallow'
import { useStore, type View } from '../store'
import { viewHidden } from '../moduleToggles'
import { MODULES } from '../shell/registry'
import { navEntries, navTitle, placeOf } from '../shell/nav'
import { dragProps } from '../canvas/dnd'

// Read on first render, never at import: the registry imports module views, and those views render
// this component, so MODULES is still uninitialised while this file is first evaluated.
let mods: typeof MODULES | null = null
const navModules = (): typeof MODULES => (mods ??= MODULES.filter((m) => m.nav && m.view))

/**
 * The views placed in the title bar (Settings → Modules; Lists, Calendar and Mail by default) as icons
 * at the right end of every title bar, so they sit in the same spot in each view and stay reachable
 * with the sidebar hidden. A click navigates; a drag drops the widget into a space, so the canvas keeps
 * them as drag sources. The last button opens the page agent (⌘I).
 */
export default function AppSwitcher(): JSX.Element {
  const view = useStore((s) => s.view)
  const settings = useStore((s) => s.settings)
  const setView = useStore((s) => s.setView)
  const pageAgentOpen = useStore((s) => s.pageAgentOpen)
  const togglePageAgent = useStore((s) => s.togglePageAgent)
  // useShallow compares element-wise, so a fresh array with the same counts does not re-render.
  const modules = navModules()
  const badges = useStore(useShallow((s) => modules.map((m) => m.nav?.badge?.(s) ?? null)))
  const badgeOf = (v: View): number => badges[modules.findIndex((m) => m.view?.id === v)] ?? 0
  const apps = navEntries().filter((a) => placeOf(settings, a) === 'apps' && !viewHidden(settings, a.view))
  return (
    <div className="app-switcher no-drag" role="toolbar" aria-label="Apps">
      {apps.map((a) => {
        const n = badgeOf(a.view)
        return (
          <button key={a.view} className={`icon-btn app-switch ${view === a.view ? 'on' : ''}`}
            title={navTitle(a)} aria-label={a.label} aria-pressed={view === a.view}
            onClick={() => setView(a.view)}
            {...(a.kind ? dragProps({ kind: 'nav', id: a.kind, label: a.label }) : {})}>
            {a.icon}
            {n > 0 && <span className="app-badge">{n > 99 ? '99+' : n}</span>}
          </button>
        )
      })}
      <button className={`icon-btn app-switch ${pageAgentOpen ? 'on' : ''}`} title="Ask about this page (⌘I)"
        aria-label="Ask about this page" aria-pressed={pageAgentOpen} onClick={togglePageAgent}>
        <Sparkles size={15} />
      </button>
    </div>
  )
}
