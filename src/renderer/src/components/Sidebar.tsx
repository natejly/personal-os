import { useEffect, useMemo, useRef, useState } from 'react'
import { Pin, ArchiveRestore, MessageSquare, MessageSquarePlus, Search, Settings, Sparkles, PanelLeftClose, Brain, FileText, Files, Plus, Folder, FolderKanban, ChevronRight, Home, KanbanSquare, LayoutDashboard, LayoutGrid, Library, Mic, Users, MonitorDot, BookOpen, Globe } from 'lucide-react'
import { useShallow } from 'zustand/react/shallow'
import GrainLogo from './GrainLogo'
import { useStore, type View } from '../store'
import { ActivityIndicator } from './ActivityView'
import { MeetingIndicator } from './MeetingsView'
import ChatPulse from './ChatPulse'
import SidebarSpaces from './SidebarSpaces'
import ResizeHandle from './ResizeHandle'
import { viewHidden } from '../moduleToggles'
import { MODULES } from '../shell/registry'
import { dragProps } from '../canvas/dnd'
import { useCanvas } from '../canvas/store'
import { api } from '../lib/api'
import { partitionChats } from '../lib/chatRows'
import ChatRow from './ChatRow'
import type { ChatSearchHit, Conversation, Doc, WidgetKind } from '@shared/types'
import { mergeChatSearch, snippetParts } from '../lib/chatSearch'

/** The first matching excerpt under a search result, with the matched words marked. */
function Snippet({ hit }: { hit?: ChatSearchHit }): JSX.Element | null {
  const s = hit?.snippets[0]
  if (!s) return null
  return (
    <span className="convo-snippet">
      {snippetParts(s.text).map((p, i) => (p.hit ? <mark key={i}>{p.text}</mark> : <span key={i}>{p.text}</span>))}
    </span>
  )
}

/** Rows shown under a project group before the "View all" link takes over. */
const PROJECT_ROWS = 4
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
const SHELL_NAV: NavEntry[] = [
  { view: 'home', label: 'Today', icon: <Home size={15} />, kind: 'recap' },
  { view: 'boards', label: 'Boards', icon: <KanbanSquare size={15} /> },
  { view: 'dashboards', label: 'Dashboards', icon: <LayoutDashboard size={15} /> },
  { view: 'docs', label: 'Files', icon: <Files size={15} /> },
  // No `kind`: no `meeting` widget kind ships in this slice, and a kind outside the WidgetKind
  // union would not typecheck — so the row is not a canvas drag source.
  { view: 'meetings', label: 'Meetings', icon: <Mic size={15} /> },
  // Deliberately no `kind`: a desk is a place you go to, not something to pin on a canvas, and a
  // kind outside the WidgetKind union would not typecheck anyway.
  { view: 'cowork', label: 'Cowork', icon: <Users size={15} /> },
  // No widget kind: the Library is a place to review and author, not something to pin on a canvas.
  { view: 'library', label: 'Library', icon: <Library size={15} /> },
  { view: 'activity', label: 'Activity', icon: <MonitorDot size={15} />, kind: 'activity' },
  { label: 'Web', icon: <Globe size={15} />, kind: 'web' }
]

/** The shell's own rows take 0, 10, 20… in their listed order; a module's `nav.order` slots between them. */
function withModules(shell: NavEntry[], section: 'main' | 'knowledge'): NavEntry[] {
  const rows = shell.map((n, i) => ({ n, order: i * 10 }))
  for (const m of MODULES) {
    if (m.nav?.section !== section || !m.view) continue
    rows.push({ n: { view: m.view.id, label: m.label, icon: m.icon, kind: m.widget?.kind }, order: m.nav.order })
  }
  // Array.sort is stable, so equal orders keep the shell row first.
  return rows.sort((a, b) => a.order - b.order).map((r) => r.n)
}
// Memory and Documents moved into Settings → Knowledge base, so the sidebar has no Knowledge section
// any more; a module that asks for one is listed with the main rows instead.
const NAV = [...withModules(SHELL_NAV, 'main'), ...withModules([], 'knowledge')]
const NAV_MODULES = MODULES.filter((m) => m.nav && m.view)

export default function Sidebar(): JSX.Element {
  const conversations = useStore((s) => s.conversations)
  const projects = useStore((s) => s.projects)
  const focusedId = useStore((s) => s.focusedConversationId)
  const view = useStore((s) => s.view)
  const projectViewId = useStore((s) => s.projectViewId)
  const settings = useStore((s) => s.settings)
  const docCount = useStore((s) => s.docs.length)
  const docsPending = useStore((s) => s.docsPending)
  const skillCandidates = useStore((s) => s.skills.filter((x) => x.status === 'candidate').length)
  /** Desks with something unseen that needs you: the one badge worth interrupting for. */
  const needsYou = useStore((s) => new Set(s.deskInbox.map((e) => e.desk_id)).size)
  const meetingsPending = useStore((s) => s.meetingsPending)
  const inCanvas = useStore((s) => s.view === 'canvas')
  // One selector per action. Sidebar is mounted in every view, the canvas included, so a bare
  // useStore() here is what made App's whole subtree commit once per streamed token.
  const newChat = useStore((s) => s.newChat)
  const selectChat = useStore((s) => s.selectChat)
  const deleteChat = useStore((s) => s.deleteChat)
  const setSettingsOpen = useStore((s) => s.setSettingsOpen)
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
  const [searching, setSearching] = useState(false)
  // Full-text hits over message bodies, for queries of 3+ characters. Title matches stay instant; these
  // arrive after a short debounce, and a stale reply is dropped by the sequence check.
  const [hits, setHits] = useState<ChatSearchHit[]>([])
  const searchSeq = useRef(0)
  const composing = useRef(false)
  // Nothing is sent while an IME composition is open; `composed` re-runs the effect once it ends.
  const [composed, setComposed] = useState(0)
  useEffect(() => {
    const q = query.trim()
    const seq = ++searchSeq.current
    if (q.length < 3) { setHits([]); return }
    const t = setTimeout(() => {
      if (composing.current) return
      api.conversations.search(q)
        .then((r) => { if (seq === searchSeq.current) setHits(r) })
        .catch(() => { if (seq === searchSeq.current) setHits([]) })
    }, 250)
    return () => clearTimeout(t)
  }, [query, composed])
  const searchRef = useRef<HTMLInputElement>(null)
  const [projectsOpen, setProjectsOpen] = useState(true)
  const [recentsOpen, setRecentsOpen] = useState(true)
  useEffect(() => {
    if (searching) searchRef.current?.focus()
  }, [searching])
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

  const convById = useMemo(() => Object.fromEntries(conversations.map((c) => [c.id, c])), [conversations])
  const { pinned, groups } = useMemo(() => partitionChats(conversations, query), [conversations, query])
  const projectDot = (c: Conversation): JSX.Element | null =>
    c.project_id && projectById[c.project_id] ? <span className="project-dot sm" style={{ background: projectById[c.project_id].color }} title={projectById[c.project_id].name} /> : null
  const hitById = useMemo(() => new Map(hits.map((h) => [h.id, h])), [hits])
  // Body-only hits: a chat already listed by its title (pinned or grouped) shows its excerpt in place.
  const inMessages = useMemo(
    () => (query.trim().length >= 3 ? mergeChatSearch([...pinned, ...groups.flatMap((g) => g.items)], hits).inMessages : []),
    [pinned, groups, hits, query]
  )
  // Archived chats are not in the store's list; loaded when the section opens and after each change.
  const [archivedOpen, setArchivedOpen] = useState(false)
  const [archived, setArchived] = useState<Conversation[]>([])
  const archiveBump = conversations.length
  useEffect(() => {
    if (archivedOpen) void api.conversations.listArchived().then(setArchived).catch(() => undefined)
  }, [archivedOpen, archiveBump])
  const archiveChat = useStore((s) => s.archiveChat)
  // ⌘⇧F: the store opens the sidebar; this brings the search field up. The tick seen at mount is
  // skipped, or a remount would reopen the search for a press handled before it.
  const searchTick = useStore((s) => s.sidebarSearchTick)
  const seenTick = useRef(searchTick)
  useEffect(() => {
    if (searchTick === seenTick.current) return
    seenTick.current = searchTick
    setRecentsOpen(true)
    setSearching(true)
    // Already showing: the effect on `searching` will not fire, so focus and select here.
    searchRef.current?.focus()
    searchRef.current?.select()
  }, [searchTick])

  // Module badges are pure functions of the store; useShallow compares the array element-wise, so a
  // fresh array with the same counts does not re-render.
  const moduleBadges = useStore(useShallow((s) => NAV_MODULES.map((m) => m.nav?.badge?.(s) ?? null)))
  const libCount = (v: View): number | null => {
    if (v === 'home' || v === 'boards' || v === 'dashboards' || v === 'activity') return null
    const mi = NAV_MODULES.findIndex((m) => m.view?.id === v)
    if (mi >= 0) return moduleBadges[mi]
    // Counted off the inbox rather than `desks`, which is only loaded once Cowork has been opened:
    // the badge has to be right before you have been there.
    if (v === 'cowork') return needsYou || null
    if (v === 'library') return skillCandidates || null
    if (v === 'docs') return docCount
    // Load-bearing, not cosmetic: without it a Meetings row would show no review count.
    if (v === 'meetings') return meetingsPending || null
    return null
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

  return (
    <aside className="sidebar">
      <ResizeHandle id="sidebar-w" defaultSize={260} min={190} max={480} grows="right" onCollapse={toggleSidebar} label="Sidebar width" className="at-right" />
      <div className="sidebar-top drag">
        <button className="brand no-drag" onClick={() => setView('home')}><GrainLogo size={15} /><span>Grain</span></button>
        <button className="icon-btn no-drag" aria-label="Hide sidebar" title="Hide sidebar (⌘B)" onClick={toggleSidebar}><PanelLeftClose size={16} /></button>
      </div>

      {/* One scroller for nav, projects, and chats. Recents used to be the only part
          that scrolled, so with a few projects open it was squeezed to a sliver at the bottom. */}
      <div className="sidebar-scroll">
      <nav className="nav">
        {NAV.filter((n) => (n.view ? n.view === 'home' || !viewHidden(settings, n.view) : inCanvas)).map(navItem)}
      </nav>

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
                    <ChatRow key={`c${r.id}`} conv={convById[r.id]} sub active={r.id === focusedId && view === 'chat'} />
                  )))}
                  {rows.length > 0 && <button className="project-viewall" onClick={() => openProject(p.id)}>View all</button>}
                </div>
              </div>
            )
          })}
        </div>
      )}

      <div className="section-row">
        <button className="section-toggle" onClick={() => setRecentsOpen((o) => !o)}>
          <ChevronRight size={12} className={recentsOpen ? 'rot90' : ''} /><MessageSquare size={13} /> Recents
        </button>
        <button
          className={`icon-btn sm${searching ? ' on' : ''}`}
          aria-label="Search recents"
          aria-expanded={searching}
          title="Search recents"
          onClick={() => {
            setRecentsOpen(true)
            setSearching((on) => {
              if (on) setQuery('')
              return !on
            })
          }}
        >
          <Search size={13} />
        </button>
      </div>
      <button className="new-chat" onClick={() => (inCanvas ? void useCanvas.getState().newChatWindow() : newChat(null))}>
        <MessageSquarePlus size={16} /><span>New chat</span><kbd>⌘N</kbd>
      </button>
      {recentsOpen && (<>
      {searching && (
        <label className="search">
          <input
            ref={searchRef}
            placeholder="Search"
            aria-label="Search recents"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onCompositionStart={() => { composing.current = true }}
            onCompositionEnd={() => { composing.current = false; setComposed((n) => n + 1) }}
            onKeyDown={(e) => {
              // Escape and Enter belong to the IME while a composition is open.
              if (composing.current) return
              if (e.key === 'Escape') { setQuery(''); setSearching(false) }
              else if (e.key === 'Enter') {
                const first = pinned[0] ?? groups[0]?.items[0] ?? inMessages[0]
                if (first) { openConversation(first.id); setQuery(''); setSearching(false) }
              } else if (e.key === 'ArrowDown') {
                e.preventDefault()
                document.querySelector<HTMLElement>('.convo-list .convo-item')?.focus()
              }
            }}
          />
        </label>
      )}
      <div className="convo-list">
        {pinned.length > 0 && (
          <section>
            <h4 className="pinned-head"><Pin size={11} /> Pinned</h4>
            {pinned.map((c) => <ChatRow key={c.id} conv={c} active={c.id === focusedId && view === 'chat'} lead={projectDot(c)} trail={<Snippet hit={hitById.get(c.id)} />} />)}
          </section>
        )}
        {groups.length === 0 && pinned.length === 0 && inMessages.length === 0 && <p className="empty-hint">{query ? 'No matches.' : 'No personal chats yet.'}</p>}
        {groups.map((g) => (
          <section key={g.label}>
            <h4>{g.label}</h4>
            {g.items.map((c) => <ChatRow key={c.id} conv={c} active={c.id === focusedId && view === 'chat'} lead={projectDot(c)} trail={<Snippet hit={hitById.get(c.id)} />} />)}
          </section>
        ))}
        {inMessages.length > 0 && (
          <section>
            <h4>In messages</h4>
            {inMessages.map((h) => (
              <div key={h.id} className={`convo-item ${h.id === focusedId && view === 'chat' ? 'active' : ''}`} onClick={() => openConversation(h.id)} role="button" tabIndex={0}
                onKeyDown={(e) => { if (e.target === e.currentTarget && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); openConversation(h.id) } }}>
                <span className="convo-title">
                  {h.project_id && projectById[h.project_id] && <span className="project-dot sm" style={{ background: projectById[h.project_id].color }} title={projectById[h.project_id].name} />}
                  {h.title}
                  {h.hits > 1 && <span className="convo-hits">+{h.hits - 1}</span>}
                  <Snippet hit={h} />
                </span>
              </div>
            ))}
          </section>
        )}
        <section>
          <h4 className="archived-head"><button className="section-toggle" onClick={() => setArchivedOpen((o) => !o)}><ChevronRight size={11} className={archivedOpen ? 'rot90' : ''} /> Archived</button></h4>
          {archivedOpen && archived.length === 0 && <p className="empty-hint">Nothing archived.</p>}
          {archivedOpen && archived.map((c) => (
            <div key={c.id} className="convo-item archived" role="button" tabIndex={0} onClick={() => void selectChat(c.id)}
              onKeyDown={(e) => { if (e.target === e.currentTarget && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); void selectChat(c.id) } }}>
              <span className="convo-title">{c.title}</span>
              <button className="icon-btn ghost" aria-label={`Unarchive chat: ${c.title}`} title="Unarchive" onClick={(e) => { e.stopPropagation(); void archiveChat(c.id, false) }}><ArchiveRestore size={13} /></button>
            </div>
          ))}
        </section>
      </div>
      </>)}
      </div>

      <div className="sidebar-bottom">
        {/* Both are mounted in every view: a capture running somewhere must never be invisible. */}
        <MeetingIndicator />
        <ActivityIndicator />
        <button className="settings-btn" onClick={() => setSettingsOpen(true)}><Settings size={16} /><span>Settings</span><kbd>⌘,</kbd></button>
      </div>
    </aside>
  )
}
