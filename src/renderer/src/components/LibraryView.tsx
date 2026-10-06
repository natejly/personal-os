import { useEffect, useState } from 'react'
import { Library, Plug, Sparkles, Terminal, Users, Workflow, Zap } from 'lucide-react'
import { useStore, type LibraryTab } from '../store'
import SkillsPanel from './SkillsPanel'
import McpSettings from './McpSettings'
import WorkflowsPanel from './WorkflowsPanel'
import { CommandsPanel } from './DefsPanels'
import AgentsPanel from './AgentsPanel'
import AppSwitcher from './AppSwitcher'
import SidebarToggle from './SidebarToggle'

const TABS: { key: LibraryTab; label: string; icon: JSX.Element; blurb: string }[] = [
  { key: 'skills', label: 'Skills', icon: <Sparkles size={14} />, blurb: 'Skills the assistant can reuse, e.g. how you like a weekly review done' },
  { key: 'agents', label: 'Agents', icon: <Users size={14} />, blurb: 'Roles with their own face, instructions, tools and skills; replies delegate to them, and you can chat with one' },
  { key: 'automations', label: 'Automations', icon: <Zap size={14} />, blurb: 'Saved things the assistant may run: workflows and commands' },
  { key: 'connectors', label: 'Connectors', icon: <Plug size={14} />, blurb: 'MCP servers whose tools the assistant can call' }
]

// Automations holds two kinds of saved thing; the filter narrows to one, All stacks them.
const KINDS = [
  { key: 'workflows', label: 'Workflows', icon: <Workflow size={12} />, blurb: 'Repeatable multi-step plans you approve once, e.g. a Monday inbox triage', panel: <WorkflowsPanel /> },
  { key: 'commands', label: 'Commands', icon: <Terminal size={12} />, blurb: 'Saved prompts you reuse, e.g. "summarise this thread for my manager"; $ARGUMENTS fills in what you type after the name', panel: <CommandsPanel /> }
]

export default function LibraryView(): JSX.Element {
  const tab = useStore((s) => s.libraryTab)
  const candidates = useStore((s) => s.skills.filter((x) => x.status === 'candidate').length)
  const { setLibraryTab, refreshLibrary } = useStore()
  const [kind, setKind] = useState('all')

  // Cheap enough to re-run on every entry, and it is the only thing that notices a connector that
  // fell over while the view was closed.
  useEffect(() => { void refreshLibrary().catch(() => undefined) }, [refreshLibrary])

  return (
    <main className="page library-page">
      <header className="page-header drag">
        <SidebarToggle />
        <h2><Library size={16} /> Library</h2>
        <AppSwitcher />
      </header>
      {/* The shelves are sections of this page, so they are tabs under the header, not a switch in it. */}
      <div className="library-tabs tabs" role="tablist">
        {TABS.map((t) => (
          <button key={t.key} role="tab" aria-selected={tab === t.key} className={tab === t.key ? 'active' : ''} title={t.blurb} onClick={() => setLibraryTab(t.key)}>
            {t.icon} {t.label}
            {t.key === 'skills' && candidates > 0 && <span className="count" title={`${candidates} waiting for you`}>{candidates}</span>}
          </button>
        ))}
      </div>
      <div className="page-body">
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
