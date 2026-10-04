import { useEffect } from 'react'
import { BookOpen, PanelLeftOpen, Plug, Sparkles, Terminal, Users, Workflow } from 'lucide-react'
import { useStore, type LibraryTab } from '../store'
import SkillsPanel from './SkillsPanel'
import McpSettings from './McpSettings'
import WorkflowsPanel from './WorkflowsPanel'
import { AgentsPanel, CommandsPanel } from './DefsPanels'
import AppSwitcher from './AppSwitcher'

const TABS: { key: LibraryTab; label: string; icon: JSX.Element; blurb: string }[] = [
  { key: 'skills', label: 'Skills', icon: <Sparkles size={14} />, blurb: 'Procedures the assistant may follow again' },
  { key: 'workflows', label: 'Workflows', icon: <Workflow size={14} />, blurb: 'Repeatable multi-step jobs you approve once, by hash' },
  { key: 'agents', label: 'Agents', icon: <Users size={14} />, blurb: 'Agent roles you write; they cannot be spawned until you approve them' },
  { key: 'commands', label: 'Commands', icon: <Terminal size={14} />, blurb: 'Saved prompt templates with $ARGUMENTS' },
  { key: 'connectors', label: 'Connectors', icon: <Plug size={14} />, blurb: 'MCP servers whose tools the assistant can call' }
]

export default function LibraryView(): JSX.Element {
  const tab = useStore((s) => s.libraryTab)
  const sidebarOpen = useStore((s) => s.sidebarOpen)
  const candidates = useStore((s) => s.skills.filter((x) => x.status === 'candidate').length)
  const { setLibraryTab, toggleSidebar, refreshLibrary } = useStore()

  // Cheap enough to re-run on every entry, and it is the only thing that notices a connector that
  // fell over while the view was closed.
  useEffect(() => { void refreshLibrary().catch(() => undefined) }, [refreshLibrary])

  return (
    <main className="page">
      <header className="page-header drag">
        {!sidebarOpen && <button className="icon-btn no-drag" aria-label="Show sidebar" onClick={toggleSidebar}><PanelLeftOpen size={16} /></button>}
        <h2><BookOpen size={16} /> Library</h2>
        <div className="no-drag header-right">
          <div className="seg">
            {TABS.map((t) => (
              <button key={t.key} className={tab === t.key ? 'on' : ''} title={t.blurb} onClick={() => setLibraryTab(t.key)}>
                {t.icon} {t.label}
                {t.key === 'skills' && candidates > 0 && <span className="count">{candidates}</span>}
              </button>
            ))}
          </div>
        </div>
        <AppSwitcher />
      </header>
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
