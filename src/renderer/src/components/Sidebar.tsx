import { useEffect, useMemo, useState } from 'react'
import { MessageSquarePlus, Search, Settings, Sparkles, Trash2, PanelLeftClose, Brain, FileText, NotebookPen, Plus, Folder, FolderKanban, ChevronRight, Home, KanbanSquare, LayoutDashboard, LayoutGrid, MonitorDot, BookOpen, Globe } from 'lucide-react'
import GrainLogo from './GrainLogo'
import { useStore, type View } from '../store'
import { ActivityIndicator } from './ActivityView'
import ChatPulse from './ChatPulse'
import SidebarSpaces from './SidebarSpaces'
import { viewHidden } from '../modules'
import { dragProps } from '../canvas/dnd'
import { useCanvas } from '../canvas/store'
import { api } from '../lib/api'
import type { Conversation, Doc, WidgetKind } from '@shared/types'

const DAY = 86_400_000
/** Rows shown under a project group before the "View all" link takes over. */
const PROJECT_ROWS = 4
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
 * A row without a `view` (Web) exists only as a widget, so it only shows in canvas mode and a click
 * opens its window directly.
 */
type NavEntry = { view?: View; label: string; icon: JSX.Element; kind?: WidgetKind }

/** One line under a project group header: a chat or a doc, sorted together by recency. */
type ProjectRow =
  | { kind: 'chat'; id: string; title: string; at: number }
  | { kind: 'doc'; id: string; title: string; at: number }

// Todos, Calendar and Mail live in the title bar instead (AppSwitcher).
const NAV: NavEntry[] = [
  { view: 'home', label: 'Today', icon: <Home size={15} />, kind: 'recap' },
  { view: 'boards', label: 'Boards', icon: <KanbanSquare size={15} /> },
  { view: 'dashboards', label: 'Dashboards', icon: <LayoutDashboard size={15} /> },
  { view: 'docs', label: 'Docs', icon: <NotebookPen size={15} /> },
  { view: 'activity', label: 'Activity', icon: <MonitorDot size={15} />, kind: 'activity' },
  { label: 'Web', icon: <Globe size={15} />, kind: 'web' }
]

// What the assistant knows: memories and uploaded documents, grouped under their own section.
const KNOWLEDGE: NavEntry[] = [
  { view: 'memory', label: 'Memory', icon: <Brain size={15} />, kind: 'memory' },
  { view: 'documents', label: 'Documents', icon: <FileText size={15} />, kind: 'documents' }
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
  const inCanvas = useStore((s) => s.view === 'canvas')
  // One selector per action. Sidebar is mounted in every view, the canvas included, so a bare
  // useStore() here is what made App's whole subtree commit once per streamed token.
  const newChat = useStore((s) => s.newChat)
  const selectChat = useStore((s) => s.selectChat)
  const deleteChat = useStore((s) => s.deleteChat)
  const setSettingsOpen = useStore((s) => s.setSettingsOpen)
  const pageAgentOpen = useStore((s) => s.pageAgentOpen)
  const togglePageAgent = useStore((s) => s.togglePageAgent)
  const toggleSidebar = useStore((s) => s.toggleSidebar)
  const setView = useStore((s) => s.setView)
  const openProject = useStore((s) => s.openProject)
  const setProjectModal = useStore((s) => s.setProjectModal)
  const openDoc = useStore((s) => s.openDoc)
  // Picking an item in the sidebar is a navigation gesture: from the canvas it leaves the space and
  // routes to the classic view, rather than opening the chat as one more window in the space. Adding
  // to a space stays the drag gesture (and ⌘N / the dock for a new chat window).
  const openConversation = (id: string): void => void selectChat(id)
  const [query, setQuery] = useState('')
  const [projectsOpen, setProjectsOpen] = useState(true)
  const [knowledgeOpen, setKnowledgeOpen] = useState(true)
  // Project groups list docs beside chats, but `docs` in the store is the Docs view's result set:
  // narrowed by its scope picker and its search box. The sidebar keeps its own unfiltered copy so a
  // search over there cannot empty the groups over here. Debounced, because `docs` changes per keystroke.
  const [projectDocs, setProjectDocs] = useState<Doc[]>([])
  const storeDocs = useStore((s) => s.docs)
  useEffect(() => {
    const t = setTimeout(() => { void api.docs.list('all').then(setProjectDocs).catch(() => undefined) }, 300)
    return () => clearTimeout(t)
  }, [storeDocs])

  // One row list per project, newest first: its chats and its docs interleaved.
  const rowsByProject = useMemo(() => {
    const m: Record<string, ProjectRow[]> = {}
    for (const c of conversations) if (c.project_id) (m[c.project_id] ??= []).push({ kind: 'chat', id: c.id, title: c.title, at: c.updated_at })
    for (const d of projectDocs) if (d.project_id) (m[d.project_id] ??= []).push({ kind: 'doc', id: d.id, title: d.title, at: d.updated_at })
    for (const rows of Object.values(m)) rows.sort((a, b) => b.at - a.at)
    return m
  }, [conversations, projectDocs])
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

  const libCount = (v: View): number | null => {
    if (v === 'home' || v === 'boards' || v === 'dashboards' || v === 'activity') return null
    if (v === 'docs') return docCount
    const total = (key: 'memories' | 'nodes' | 'documents'): number =>
      (personalStats?.[key] ?? 0) + projects.reduce((n, p) => n + (p.stats?.[key] ?? 0), 0)
    // Memory is one panel now: memories and graph entities counted together.
    return v === 'memory' ? total('memories') + total('nodes') : total('documents')
  }

  // A row without a `view` (Web) exists only as a canvas widget: a click opens its window directly.
  const navItem = (n: NavEntry): JSX.Element => (
    <button key={n.label} className={`nav-item ${n.view && view === n.view ? 'active' : ''}`}
      onClick={() => (n.view ? setView(n.view) : void useCanvas.getState().openWindow(n.kind as WidgetKind))}
      {...(n.kind ? dragProps({ kind: 'nav', id: n.kind, label: n.label }) : {})}>
      {n.icon}<span>{n.label}</span>
      {n.view === 'docs' && docsPending > 0 && (
        <span className="count pending" title={`${docsPending} assistant edit${docsPending === 1 ? '' : 's'} awaiting review`}>{docsPending}</span>
      )}
      {n.view != null && libCount(n.view) !== null && <span className="count">{libCount(n.view)}</span>}
    </button>
  )
  const knowledgeItems = KNOWLEDGE.filter((n) => n.view && !viewHidden(settings, n.view))

  return (
    <aside className="sidebar">
      <div className="sidebar-top drag">
        <button className="brand no-drag" onClick={() => setView('home')}><GrainLogo size={15} /><span>Grain</span></button>
        <button className={`icon-btn no-drag ${inCanvas ? 'on' : ''}`} title={inCanvas ? 'Back (⌘⇧C)' : 'Go to space (⌘⇧C)'} onClick={() => void useCanvas.getState().toggleCanvas()}><LayoutGrid size={16} /></button>
        <button className="icon-btn no-drag" aria-label="Hide sidebar" title="Hide sidebar (⌘B)" onClick={toggleSidebar}><PanelLeftClose size={16} /></button>
      </div>

      <button className="new-chat" onClick={() => (inCanvas ? void useCanvas.getState().newChatWindow() : newChat(null))}>
        <MessageSquarePlus size={16} /><span>New chat</span><kbd>⌘N</kbd>
      </button>

      <nav className="nav">
        {NAV.filter((n) => (n.view ? n.view === 'home' || !viewHidden(settings, n.view) : inCanvas)).map(navItem)}
      </nav>

      {knowledgeItems.length > 0 && (
        <>
          <div className="section-row">
            <button className="section-toggle" onClick={() => setKnowledgeOpen((o) => !o)}>
              <ChevronRight size={12} className={knowledgeOpen ? 'rot90' : ''} /><BookOpen size={13} /> Knowledge Base
            </button>
          </div>
          {knowledgeOpen && <nav className="nav">{knowledgeItems.map(navItem)}</nav>}
        </>
      )}
      <SidebarSpaces />

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
            const rows = rowsByProject[p.id] ?? []
            return (
              <div key={p.id} className="project-group">
                <div className={`project-item ${view === 'project' && projectViewId === p.id ? 'active' : ''}`} onClick={() => openProject(p.id)} role="button" tabIndex={0}
                  {...dragProps({ kind: 'project', id: p.id, label: p.name })}>
                  <Folder size={13} style={{ color: p.color }} />
                  <span className="project-name">{p.name}</span>
                </div>
                <div className="project-rows">
                  {rows.length === 0 && <button className="convo-item sub muted" onClick={() => (inCanvas ? void useCanvas.getState().newChatWindow(p.id) : newChat(p.id))}><MessageSquarePlus size={12} /> New chat in project</button>}
                  {rows.slice(0, PROJECT_ROWS).map((r) => (r.kind === 'doc' ? (
                    <div key={`d${r.id}`} className="convo-item sub" onClick={() => void openDoc(r.id)} role="button" tabIndex={0}>
                      <span className="convo-title">{r.title}</span>
                      <FileText size={12} className="row-kind" />
                    </div>
                  ) : (
                    <div key={`c${r.id}`} className={`convo-item sub ${r.id === focusedId && view === 'chat' ? 'active' : ''}`} onClick={() => openConversation(r.id)} role="button" tabIndex={0}
                      {...dragProps({ kind: 'conversation', id: r.id, label: r.title, projectId: p.id })}>
                      <span className="convo-title"><ChatPulse conversationId={r.id} />{r.title}</span>
                      <button className="icon-btn ghost" aria-label={`Delete chat: ${r.title}`} title="Delete" onClick={(e) => { e.stopPropagation(); void deleteChat(r.id) }}><Trash2 size={13} /></button>
                    </div>
                  )))}
                  {rows.length > 0 && <button className="project-viewall" onClick={() => openProject(p.id)}>View all</button>}
                </div>
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
        <button className={`settings-btn ${pageAgentOpen ? 'on' : ''}`} aria-pressed={pageAgentOpen} onClick={togglePageAgent}>
          <Sparkles size={16} /><span>Ask about this page</span><kbd>⌘I</kbd>
        </button>
        <button className="settings-btn" onClick={() => setSettingsOpen(true)}><Settings size={16} /><span>Settings</span><kbd>⌘,</kbd></button>
      </div>
    </aside>
  )
}
