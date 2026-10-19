import { useEffect, useState } from 'react'
import { useShallow } from 'zustand/react/shallow'
import { Library, Plug, Sparkles, Terminal, Users, Workflow, Zap } from '../icons'
import { useStore, type LibraryTab } from '../store'
import SkillsPanel from './SkillsPanel'
import McpSettings from './McpSettings'
import WorkflowsPanel from './WorkflowsPanel'
import { CommandsPanel } from './DefsPanels'
import AgentsPanel from './AgentsPanel'
import AppSwitcher from './AppSwitcher'
import SidebarToggle from './SidebarToggle'

const TABS: { key: LibraryTab; label: string; icon: JSX.Element; blurb: string }[] = [
  { key: 'skills', label: 'Skills', icon: <Sparkles size={14} />, blurb: 'Reusable instructions' },
  { key: 'agents', label: 'Agents', icon: <Users size={14} />, blurb: 'Roles with their own instructions and tools' },
  { key: 'automations', label: 'Automations', icon: <Zap size={14} />, blurb: 'Workflows and commands' },
  { key: 'connectors', label: 'Connectors', icon: <Plug size={14} />, blurb: 'MCP servers' }
]

// Automations holds two kinds of saved thing; the filter narrows to one, All stacks them.
const KINDS = [
  { key: 'workflows', label: 'Workflows', icon: <Workflow size={12} />, blurb: 'Multi-step plans', panel: <WorkflowsPanel /> },
  { key: 'commands', label: 'Commands', icon: <Terminal size={12} />, blurb: 'Saved prompts; $ARGUMENTS fills in what follows the name', panel: <CommandsPanel /> }
]

export default function LibraryView(): JSX.Element {
  const tab = useStore((s) => s.libraryTab)
  const candidates = useStore((s) => s.skills.filter((x) => x.status === 'candidate').length)
  const { setLibraryTab, refreshLibrary } = useStore(useShallow((s) => ({ setLibraryTab: s.setLibraryTab, refreshLibrary: s.refreshLibrary })))
  const [kind, setKind] = useState('all')

  // Cheap enough to re-run on every entry, and it is the only thing that notices a connector that
  // fell over while the view was closed.
  useEffect(() => { void refreshLibrary().catch(() => undefined) }, [refreshLibrary])

  // Arrow keys move between tabs and select as they go; only the selected tab is in the tab order.
  const onTabKey = (e: React.KeyboardEvent): void => {
    const d = e.key === 'ArrowRight' ? 1 : e.key === 'ArrowLeft' ? -1 : 0
    const end = e.key === 'Home' ? 0 : e.key === 'End' ? TABS.length - 1 : -1
    if (!d && end < 0) return
    e.preventDefault()
    const i = TABS.findIndex((t) => t.key === tab)
    const next = TABS[end >= 0 ? end : (i + d + TABS.length) % TABS.length].key
    setLibraryTab(next)
    document.getElementById(`library-tab-${next}`)?.focus()
  }

  return (
    <main className="page library-page">
      <header className="page-header drag">
        <SidebarToggle />
        <h2><Library size={16} /> Library</h2>
        <AppSwitcher />
      </header>
      {/* The shelves are sections of this page, so they are tabs under the header, not a switch in it. */}
      <div className="library-tabs" role="tablist" aria-label="Library" onKeyDown={onTabKey}>
        {TABS.map((t) => (
          <button key={t.key} id={`library-tab-${t.key}`} role="tab" aria-selected={tab === t.key} aria-controls="library-panel" tabIndex={tab === t.key ? 0 : -1}
            className={tab === t.key ? 'active' : ''} title={t.blurb} onClick={() => setLibraryTab(t.key)}>
            {t.icon} {t.label}
            {t.key === 'skills' && candidates > 0 && <span className="count" title={`${candidates} waiting for you`}>{candidates}</span>}
          </button>
        ))}
      </div>
      <div className="page-body" id="library-panel" role="tabpanel" aria-labelledby={`library-tab-${tab}`}>
        {tab === 'skills' && <SkillsPanel />}
        {tab === 'agents' && <AgentsPanel />}
        {tab === 'automations' && (
          <div className="library-panel">
            <div className="seg" role="tablist" aria-label="Kind" style={{ alignSelf: 'flex-start' }}>
              {[{ key: 'all', label: 'All', icon: null, blurb: 'Every kind' }, ...KINDS].map((k) => (
                <button key={k.key} role="tab" aria-selected={kind === k.key} className={kind === k.key ? 'on' : ''} title={k.blurb} onClick={() => setKind(k.key)}>{k.icon} {k.label}</button>
              ))}
            </div>
            {KINDS.filter((k) => kind === 'all' || kind === k.key).map((k) => (
              <section key={k.key}>
                {kind === 'all' && <h3>{k.icon} {k.label}</h3>}
                {k.panel}
              </section>
            ))}
          </div>
        )}
        {tab === 'connectors' && <div className="library-panel"><McpSettings /></div>}
      </div>
    </main>
  )
}
