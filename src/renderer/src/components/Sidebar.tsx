import { useMemo, useState } from 'react'
import { MessageSquarePlus, Search, Settings, Trash2, PanelLeftClose, Sparkles, Brain, FileText, NotebookPen, Plus, FolderKanban, ChevronRight, Home, CheckSquare, Calendar, KanbanSquare, LayoutDashboard, LayoutGrid, Mail, MonitorDot } from 'lucide-react'
import { useStore, type View } from '../store'
import { ActivityIndicator } from './ActivityView'
import ChatPulse from './ChatPulse'
import { viewHidden } from '../modules'
import { dragProps } from '../canvas/dnd'
import { useCanvas } from '../canvas/store'
import type { Conversation, WidgetKind } from '@shared/types'

const DAY = 86_400_000
function groupLabel(ts: number): string {
  const start = new Date()
  start.setHours(0, 0, 0, 0)
  const diff = start.getTime() - ts * 1000
  if (diff < 0) return 'Today'
  if (diff < DAY) return 'Yesterday'
  if (diff < 7 * DAY) return 'Previous 7 days'
  if (diff < 30 * DAY) return 'Previous 30 days'
  return 'Older'
}

/**
 * `kind` makes the row a canvas drag source (contract §7, payload kind 'nav'). Boards and Dashboards
 * have none: their widgets need a `ref_id`, so a bare drag would open a window with nothing in it.
 */
const NAV: { view: View; label: string; icon: JSX.Element; kind?: WidgetKind }[] = [
  { view: 'home', label: 'Today', icon: <Home size={15} />, kind: 'recap' },
  { view: 'todos', label: 'Todos', icon: <CheckSquare size={15} />, kind: 'todos' },
  { view: 'calendar', label: 'Calendar', icon: <Calendar size={15} />, kind: 'calendar' },
  { view: 'mail', label: 'Mail', icon: <Mail size={15} /> },
  { view: 'boards', label: 'Boards', icon: <KanbanSquare size={15} /> },
  { view: 'dashboards', label: 'Dashboards', icon: <LayoutDashboard size={15} /> },
  { view: 'memory', label: 'Memory', icon: <Brain size={15} />, kind: 'memory' },
  { view: 'documents', label: 'Documents', icon: <FileText size={15} />, kind: 'documents' },
  { view: 'docs', label: 'Docs', icon: <NotebookPen size={15} /> },
  { view: 'activity', label: 'Activity', icon: <MonitorDot size={15} />, kind: 'activity' }
]

export default function Sidebar(): JSX.Element {
  const conversations = useStore((s) => s.conversations)
  const projects = useStore((s) => s.projects)
  const focusedId = useStore((s) => s.focusedConversationId)
  const view = useStore((s) => s.view)
  const projectViewId = useStore((s) => s.projectViewId)
  const personalStats = useStore((s) => s.personalStats)
  const settings = useStore((s) => s.settings)
  const docCount = useStore((s) => s.docs.length)
  const docsPending = useStore((s) => s.docsPending)
  const mode = useStore((s) => s.mode)
  // One selector per action. Sidebar is mounted in both modes, so a bare useStore() here is what made
  // App's whole subtree commit once per streamed token.
  const newChat = useStore((s) => s.newChat)
  const toggleMode = useStore((s) => s.toggleMode)
  const selectChat = useStore((s) => s.selectChat)
  const deleteChat = useStore((s) => s.deleteChat)
  const setSettingsOpen = useStore((s) => s.setSettingsOpen)
  const toggleSidebar = useStore((s) => s.toggleSidebar)
  const setView = useStore((s) => s.setView)
  const openProject = useStore((s) => s.openProject)
  const setProjectModal = useStore((s) => s.setProjectModal)
  // In canvas mode a chat lives in a window, not the router: clicking one focuses or opens its window.
  const openConversation = (id: string): void => {
    if (useStore.getState().mode === 'canvas') void useCanvas.getState().openChat(id)
    else void selectChat(id)
  }
  const [query, setQuery] = useState('')
  const [projectsOpen, setProjectsOpen] = useState(true)
  const [expanded, setExpanded] = useState<Record<string, boolean>>({})
  const chatsByProject = useMemo(() => {
    const m: Record<string, Conversation[]> = {}
    for (const c of conversations) if (c.project_id) (m[c.project_id] ??= []).push(c)
    return m
  }, [conversations])
  const projectById = useMemo(() => Object.fromEntries(projects.map((p) => [p.id, p])), [projects])

  const groups = useMemo(() => {
    const q = query.trim().toLowerCase()
    const personal = conversations.filter((c) => !c.project_id)
    const filtered = q ? conversations.filter((c) => c.title.toLowerCase().includes(q)) : personal
    const out: { label: string; items: Conversation[] }[] = []
    for (const c of filtered) {
      const label = groupLabel(c.updated_at)
      const g = out[out.length - 1]
      if (g && g.label === label) g.items.push(c)
      else out.push({ label, items: [c] })
    }
    return out
  }, [conversations, query])

  const todoStats = useStore((s) => s.dashboard?.todo_stats)
  const libCount = (v: View): number | null => {
    if (v === 'home' || v === 'calendar' || v === 'mail' || v === 'boards' || v === 'dashboards' || v === 'activity') return null
    if (v === 'todos') return todoStats?.open ?? null
    if (v === 'docs') return docCount
    const total = (key: 'memories' | 'nodes' | 'documents'): number =>
      (personalStats?.[key] ?? 0) + projects.reduce((n, p) => n + (p.stats?.[key] ?? 0), 0)
    // Memory is one panel now: memories and graph entities counted together.
    return v === 'memory' ? total('memories') + total('nodes') : total('documents')
  }

  return (
    <aside className="sidebar">
      <div className="sidebar-top drag">
        <button className="brand no-drag" onClick={() => setView('home')}><Sparkles size={15} /><span>Personal OS</span></button>
        <button className={`icon-btn no-drag ${mode === 'canvas' ? 'on' : ''}`} title={mode === 'canvas' ? 'Leave Canvas (⌘⇧C)' : 'Canvas Mode (⌘⇧C)'} onClick={toggleMode}><LayoutGrid size={16} /></button>
        <button className="icon-btn no-drag" aria-label="Hide sidebar" title="Hide sidebar (⌘B)" onClick={toggleSidebar}><PanelLeftClose size={16} /></button>
      </div>

      <button className="new-chat" onClick={() => newChat(null)}>
        <MessageSquarePlus size={16} /><span>New chat</span><kbd>⌘N</kbd>
      </button>

      <nav className="nav">
        {NAV.filter((n) => n.view === 'home' || !viewHidden(settings, n.view)).map((n) => (
          <button key={n.view} className={`nav-item ${view === n.view ? 'active' : ''}`} onClick={() => setView(n.view)}
            {...(n.kind ? dragProps({ kind: 'nav', id: n.kind, label: n.label }) : {})}>
            {n.icon}<span>{n.label}</span>
            {n.view === 'docs' && docsPending > 0 && (
              <span className="count pending" title={`${docsPending} assistant edit${docsPending === 1 ? '' : 's'} awaiting review`}>{docsPending}</span>
            )}
            {libCount(n.view) !== null && <span className="count">{libCount(n.view)}</span>}
          </button>
        ))}
      </nav>

      <div className="section-row">
        <button className="section-toggle" onClick={() => setProjectsOpen((o) => !o)}>
          <ChevronRight size={12} className={projectsOpen ? 'rot90' : ''} /><FolderKanban size={13} /> Projects
        </button>
        <button className="icon-btn ghost sm" aria-label="New project" title="New project" onClick={() => setProjectModal({ mode: 'create' })}><Plus size={14} /></button>
      </div>
      {projectsOpen && (
        <div className="project-list">
          {projects.length === 0 && <p className="empty-hint">No projects yet.</p>}
          {projects.map((p) => {
            const chats = chatsByProject[p.id] ?? []
            const isOpen = expanded[p.id] ?? (view === 'project' && projectViewId === p.id) ?? false
            return (
              <div key={p.id} className="project-group">
                <div className={`project-item ${view === 'project' && projectViewId === p.id ? 'active' : ''}`} onClick={() => openProject(p.id)} role="button" tabIndex={0}
                  {...dragProps({ kind: 'project', id: p.id, label: p.name })}>
                  <button className="icon-btn ghost xs" aria-label={isOpen ? `Collapse ${p.name}` : `Expand chats in ${p.name}`} aria-expanded={isOpen} title={isOpen ? 'Collapse' : 'Expand chats'} onClick={(e) => { e.stopPropagation(); setExpanded((x) => ({ ...x, [p.id]: !isOpen })) }}><ChevronRight size={12} className={isOpen ? 'rot90' : ''} /></button>
                  <span className="project-dot" style={{ background: p.color }} />
                  <span className="project-name">{p.name}</span>
                  <span className="count">{chats.length}</span>
                </div>
                {isOpen && (
                  <div className="project-chats">
                    {chats.length === 0 && <button className="convo-item sub muted" onClick={() => newChat(p.id)}><MessageSquarePlus size={12} /> New chat in project</button>}
                    {chats.slice(0, 12).map((c) => (
                      <div key={c.id} className={`convo-item sub ${c.id === focusedId && view === 'chat' ? 'active' : ''}`} onClick={() => openConversation(c.id)} role="button" tabIndex={0}
                        {...dragProps({ kind: 'conversation', id: c.id, label: c.title, projectId: p.id })}>
                        <span className="convo-title"><ChatPulse conversationId={c.id} />{c.title}</span>
                        <button className="icon-btn ghost" aria-label={`Delete chat: ${c.title}`} title="Delete" onClick={(e) => { e.stopPropagation(); void deleteChat(c.id) }}><Trash2 size={13} /></button>
                      </div>
                    ))}
                    {chats.length > 12 && <button className="link small sub" onClick={() => openProject(p.id)}>all {chats.length} chats…</button>}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}

      <div className="section-row">
        <span className="section-toggle static">Recents</span>
      </div>
      <label className="search">
        <Search size={14} />
        <input placeholder="Search" value={query} onChange={(e) => setQuery(e.target.value)} />
      </label>
      <div className="convo-list">
        {groups.length === 0 && <p className="empty-hint">{query ? 'No matches.' : 'No personal chats yet.'}</p>}
        {groups.map((g) => (
          <section key={g.label}>
            <h4>{g.label}</h4>
            {g.items.map((c) => (
              <div key={c.id} className={`convo-item ${c.id === focusedId && view === 'chat' ? 'active' : ''}`} onClick={() => openConversation(c.id)} role="button" tabIndex={0}
                {...dragProps({ kind: 'conversation', id: c.id, label: c.title, projectId: c.project_id })}>
                <span className="convo-title">
                  <ChatPulse conversationId={c.id} />
                  {c.project_id && projectById[c.project_id] && <span className="project-dot sm" style={{ background: projectById[c.project_id].color }} title={projectById[c.project_id].name} />}
                  {c.title}
                </span>
                <button className="icon-btn ghost" aria-label={`Delete chat: ${c.title}`} title="Delete" onClick={(e) => { e.stopPropagation(); void deleteChat(c.id) }}><Trash2 size={14} /></button>
              </div>
            ))}
          </section>
        ))}
      </div>

      <div className="sidebar-bottom">
        <ActivityIndicator />
        <button className="settings-btn" onClick={() => setSettingsOpen(true)}><Settings size={16} /><span>Settings</span><kbd>⌘,</kbd></button>
      </div>
    </aside>
  )
}
