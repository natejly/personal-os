import { useEffect } from 'react'
import { Library, Plug, Sparkles, Terminal, Users, Workflow } from 'lucide-react'
import { useStore, type LibraryTab } from '../store'
import SkillsPanel from './SkillsPanel'
import McpSettings from './McpSettings'
import WorkflowsPanel from './WorkflowsPanel'
import { AgentsPanel, CommandsPanel } from './DefsPanels'
import AppSwitcher from './AppSwitcher'
import SidebarToggle from './SidebarToggle'

const TABS: { key: LibraryTab; label: string; icon: JSX.Element; blurb: string }[] = [
  { key: 'skills', label: 'Skills', icon: <Sparkles size={14} />, blurb: 'Skills the assistant can reuse, e.g. how you like a weekly review done' },
  { key: 'workflows', label: 'Workflows', icon: <Workflow size={14} />, blurb: 'Repeatable multi-step plans you approve once, e.g. a Monday inbox triage' },
  { key: 'agents', label: 'Agents', icon: <Users size={14} />, blurb: 'Agent roles you write; they cannot be spawned until you approve them' },
  { key: 'commands', label: 'Commands', icon: <Terminal size={14} />, blurb: 'Saved prompts you reuse, e.g. "summarise this thread for my manager"; $ARGUMENTS fills in what you type after the name' },
  { key: 'connectors', label: 'Connectors', icon: <Plug size={14} />, blurb: 'MCP servers whose tools the assistant can call' }
]

export default function LibraryView(): JSX.Element {
  const tab = useStore((s) => s.libraryTab)
  const candidates = useStore((s) => s.skills.filter((x) => x.status === 'candidate').length)
  const { setLibraryTab, refreshLibrary } = useStore()

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
        {tab === 'workflows' && <WorkflowsPanel />}
        {tab === 'agents' && <AgentsPanel />}
        {tab === 'commands' && <CommandsPanel />}
        {tab === 'connectors' && <div className="library-panel"><McpSettings /></div>}
      </div>
    </main>
  )
}
