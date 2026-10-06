import { useEffect, useMemo, useRef, useState } from 'react'
import { ArchiveRestore, Trash2, MessageSquare, MessageSquarePlus, Search, Settings, PanelLeftClose, Files, Plus, Folder, FolderKanban, ChevronRight, Home, Bell, CalendarClock } from 'lucide-react'
import { useShallow } from 'zustand/react/shallow'
import GrainLogo from './GrainLogo'
import { chatAttentionOf, useStore, type View } from '../store'
import { ActivityIndicator } from './ActivityView'
import { MeetingIndicator } from './MeetingsView'
import SidebarSpaces from './SidebarSpaces'
import ResizeHandle from './ResizeHandle'
import { viewHidden } from '../moduleToggles'
import { MODULES } from '../shell/registry'
import { navEntries, navTitle, placeOf } from '../shell/nav'
import { dragProps } from '../canvas/dnd'
import { useCanvas } from '../canvas/store'
import { api } from '../lib/api'
import { partitionChats } from '../lib/chatRows'
import ChatRow from './ChatRow'
import { AttentionDot } from './ChatPulse'
import type { Attention, ChatSearchHit, Conversation, Job, WidgetKind } from '@shared/types'
import { ATTENTION_RANK, jobAttention, wantsYou } from '../lib/attention'
import { mergeChatSearch, snippetParts } from '../lib/chatSearch'
import { inboxBadge } from '../lib/inboxBadge'
import { rowButton } from '../lib/rowButton'
import { useChatFileCountsSync } from '../lib/useChatFiles'
import SidebarChatFiles from './SidebarChatFiles'

/** Project groups the user folded shut. Stored as exceptions, so a new project starts open. */
const COLLAPSED_KEY = 'grain.sidebar.collapsedProjects'
const readCollapsed = (): Set<string> => {
  try {
    const v: unknown = JSON.parse(localStorage.getItem(COLLAPSED_KEY) ?? '[]')
    return new Set(Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : [])
  } catch {
    return new Set()
  }
}
const writeCollapsed = (ids: Set<string>): void => {
  try {
    localStorage.setItem(COLLAPSED_KEY, JSON.stringify([...ids]))
  } catch {
    // Per-viewer convenience only; without it the groups are open again next launch.
  }
}

/** The "Needs you" filter over the chat list, remembered per viewer like the folded groups. */
const NEEDS_KEY = 'grain.sidebar.needsYou'
const readNeeds = (): boolean => {
  try { return localStorage.getItem(NEEDS_KEY) === '1' } catch { return false }
}
const writeNeeds = (on: boolean): void => {
  try { localStorage.setItem(NEEDS_KEY, on ? '1' : '0') } catch { /* per-viewer convenience only */ }
}
const NO_STATES: Attention[] = []
/** A job worth a sidebar row: one that runs, or one the scheduler switched off by itself (it is blocked). */
const listedJob = (j: Job): boolean => j.enabled || !!j.paused_reason

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
 * `kind` makes the row a canvas drag source (contract §7, payload kind 'nav').
 * A row without a `view` (Web) exists only as a widget, so it only shows in canvas mode and a click
 * opens its window directly.
 */
type NavEntry = { view?: View; label: string; description?: string; icon: JSX.Element; kind?: WidgetKind }

// The fixed rows. Every other view (shell/nav.tsx) is slotted between these by Settings → Modules,
// which also moves it to the title bar (AppSwitcher) or hides it.
const TOP: NavEntry[] = [
  { view: 'home', description: 'Your day at a glance: plan, mail, events and what the agent did', label: 'Today', icon: <Home size={15} />, kind: 'recap' },
  { view: 'docs', description: 'Your documents, in folders, with the assistant editing alongside you', label: 'Files', icon: <Files size={15} /> }
]
const NAV_MODULES = MODULES.filter((m) => m.nav && m.view)

export default function Sidebar(): JSX.Element {
  const conversations = useStore((s) => s.conversations)
  const projects = useStore((s) => s.projects)
  const focusedId = useStore((s) => s.focusedConversationId)
  const view = useStore((s) => s.view)
  const projectViewId = useStore((s) => s.projectViewId)
  const settings = useStore((s) => s.settings)
  const docsPending = useStore((s) => s.docsPending)
  const skillCandidates = useStore((s) => s.skills.filter((x) => x.status === 'candidate').length)
  const memoryProposals = useStore((s) => s.memoryProposals)
  /** Chats working autonomously with something unseen that needs you: the one badge worth interrupting for. */
  const needsYou = useStore((s) => new Set(s.deskInbox.map((e) => e.desk_id)).size)
  const meetingsPending = useStore((s) => s.meetingsPending)
  /** Everything agents left for the user (approvals, proposals, desks, review queues) plus unread job runs: the Agent inbox on Today. */
  const inboxCount = useStore((s) => inboxBadge(s.agentInbox))
  const inCanvas = useStore((s) => s.view === 'canvas')
  // One selector per action. Sidebar is mounted in every view, the canvas included, so a bare
  // useStore() here is what made App's whole subtree commit once per streamed token.
  const newChat = useStore((s) => s.newChat)
  const selectChat = useStore((s) => s.selectChat)
  const setSettingsOpen = useStore((s) => s.setSettingsOpen)
  const toggleSidebar = useStore((s) => s.toggleSidebar)
  const setView = useStore((s) => s.setView)
  const openProject = useStore((s) => s.openProject)
  const setProjectModal = useStore((s) => s.setProjectModal)
  // Inside a space a chat opens (or focuses) as a window there; from any other view it routes to the chat view.
  const openConversation = (id: string): void => void (inCanvas ? useCanvas.getState().openChat(id) : selectChat(id))
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
  const [chatsOpen, setChatsOpen] = useState(true)
  const [chatsTab, setChatsTab] = useState<'chats' | 'files'>('chats')
  const [filesProjects, setFilesProjects] = useState<Set<string>>(new Set())  // project groups showing Files instead of Chats
  useChatFileCountsSync()
  const [jobsOpen, setJobsOpen] = useState(true)
  const [needsOnly, setNeedsOnly] = useState(readNeeds)
  const jobs = useStore((s) => s.jobs)
  const agentInbox = useStore((s) => s.agentInbox)
  // Job states come from GET /jobs. The inbox is re-read whenever a job run, proposal or pause moves, so the list follows it.
  useEffect(() => { if (agentInbox) void useStore.getState().refreshJobs() }, [agentInbox])
  const shownJobs = useMemo(() => jobs.filter(listedJob), [jobs])
  // Element-wise compared, so a streamed token that moves no chat's state re-renders nothing; empty while the filter is off.
  const chatStates = useStore(useShallow((s) => (needsOnly ? s.conversations.map((c) => chatAttentionOf(s, c)) : NO_STATES)))
  /** Everything that wants the user, needs-you first then blocked; order within each kept (chats newest first, then jobs). */
  const wanting = useMemo(() => {
    if (!needsOnly) return []
    type Item = { a: Attention } & ({ conv: Conversation } | { job: Job })
    const items: Item[] = [
      ...conversations.map((conv, i) => ({ a: chatStates[i] ?? 'idle', conv })),
      ...shownJobs.map((job) => ({ a: jobAttention(job), job }))
    ]
    return items.filter((x) => wantsYou(x.a)).sort((x, y) => ATTENTION_RANK[x.a] - ATTENTION_RANK[y.a])
  }, [needsOnly, conversations, chatStates, shownJobs])
  // A search reaches every chat: typing a query lifts the filter until the box is cleared.
  const filtering = needsOnly && !query.trim()
  const [collapsed, setCollapsed] = useState(readCollapsed)
  useEffect(() => {
    if (searching) searchRef.current?.focus()
  }, [searching])
  // One chat list per project, newest first (the store keeps conversations updated_at-descending).
  const chatsByProject = useMemo(() => {
    const m: Record<string, Conversation[]> = {}
    for (const c of conversations) if (c.project_id) (m[c.project_id] ??= []).push(c)
    return m
  }, [conversations])
  const projectById = useMemo(() => Object.fromEntries(projects.map((p) => [p.id, p])), [projects])
  // The group you are in cannot fold away under you: its header would be the only trace of where you are.
  const activeProjectId = view === 'project' ? projectViewId
    : view === 'chat' ? conversations.find((c) => c.id === focusedId)?.project_id ?? null
      : null
  const toggleCollapsed = (id: string): void => {
    setCollapsed((prev) => {
      // Rebuilt from the live projects, so a deleted project's id does not linger in storage.
      const next = new Set([...prev].filter((x) => projectById[x]))
      if (!next.delete(id)) next.add(id)
      writeCollapsed(next)
      return next
    })
  }

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
  const deleteChat = useStore((s) => s.deleteChat)
  // Search narrows the archived rows by title too, like the active list.
  const shownArchived = useMemo(() => {
    const q = query.trim().toLowerCase()
    return q ? archived.filter((c) => c.title.toLowerCase().includes(q)) : archived
  }, [archived, query])
  // ⌘⇧F: the store opens the sidebar; this brings the search field up. The tick seen at mount is
  // skipped, or a remount would reopen the search for a press handled before it.
  const searchTick = useStore((s) => s.sidebarSearchTick)
  const seenTick = useRef(searchTick)
  useEffect(() => {
    if (searchTick === seenTick.current) return
    seenTick.current = searchTick
    setChatsOpen(true)
    setSearching(true)
    // Already showing: the effect on `searching` will not fire, so focus and select here.
    searchRef.current?.focus()
    searchRef.current?.select()
  }, [searchTick])

  // Module badges are pure functions of the store; useShallow compares the array element-wise, so a
  // fresh array with the same counts does not re-render.
  const moduleBadges = useStore(useShallow((s) => NAV_MODULES.map((m) => m.nav?.badge?.(s) ?? null)))
  const libCount = (v: View): number | null => {
    if (v === 'home' || v === 'activity') return null
    const mi = NAV_MODULES.findIndex((m) => m.view?.id === v)
    if (mi >= 0) return moduleBadges[mi]
    if (v === 'library') return skillCandidates || null
    if (v === 'memory') return memoryProposals || null
    // Load-bearing, not cosmetic: without it a Meetings row would show no review count.
    if (v === 'meetings') return meetingsPending || null
    return null
  }

  // A row without a `view` (Web) exists only as a canvas widget: a click opens its window directly.
  const navItem = (n: NavEntry): JSX.Element => (
    <button key={n.label} title={navTitle(n)} className={`nav-item ${n.view && view === n.view ? 'active' : ''}`} aria-current={n.view && view === n.view ? 'page' : undefined}
      onClick={() => (n.view ? setView(n.view) : void useCanvas.getState().openWindow(n.kind as WidgetKind))}
      {...(n.kind ? dragProps({ kind: 'nav', id: n.kind, label: n.label }) : {})}>
      {n.icon}<span>{n.label}</span>
      {n.view === 'docs' && docsPending > 0 && (
        <span className="count pending" title={`${docsPending} assistant edit${docsPending === 1 ? '' : 's'} awaiting review`}>{docsPending}</span>
      )}
      {n.view === 'home' && inboxCount > 0 && (
        <span className="count pending" title={`${inboxCount} new in the Agent inbox`}>{inboxCount}</span>
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

      {/* Above the scroller, so the one action every session starts with never scrolls away. */}
      <button className="new-chat" onClick={() => (inCanvas ? void useCanvas.getState().newChatWindow() : newChat(null))}>
        <MessageSquarePlus size={15} /><span>New chat</span><kbd>⌘N</kbd>
      </button>

      {/* One scroller for nav, projects, and chats. The chat list used to be the only part
          that scrolled, so with a few projects open it was squeezed to a sliver at the bottom. */}
      <div className="sidebar-scroll">
      <nav className="nav">
        {[...TOP, ...navEntries().filter((e) => placeOf(settings, e) === 'sidebar')]
          .filter((n) => (n.view ? n.view === 'home' || !viewHidden(settings, n.view) : inCanvas)).map(navItem)}
        {/* Hidden views leave no trace otherwise; this is the way back to them. */}
        {navEntries().some((e) => viewHidden(settings, e.view)) && (
          <button className="nav-item nav-more" title="Turn on hidden views in Settings → Modules"
            onClick={() => useStore.getState().openSettings('modules')}>
            <Plus size={15} /><span>More modules…</span>
          </button>
        )}
      </nav>

      <SidebarSpaces />

      <div className="section-row">
        <button className="section-toggle" aria-expanded={projectsOpen} onClick={() => setProjectsOpen((o) => !o)}>
          <ChevronRight size={12} className={projectsOpen ? 'rot90' : ''} /><FolderKanban size={13} /> Projects
        </button>
        <button className="icon-btn ghost sm" aria-label="New project" title="New project" onClick={() => setProjectModal({ mode: 'create' })}><Plus size={14} /></button>
      </div>
      {projectsOpen && (
        <div className="project-list">
          {projects.length === 0 && <p className="empty-hint">No projects yet.</p>}
          {projects.map((p) => {
            const rows = chatsByProject[p.id] ?? []
            const showFiles = filesProjects.has(p.id)
            const pinned = activeProjectId === p.id
            const open = pinned || !collapsed.has(p.id)
            return (
              <div key={p.id} className={`project-group${open ? '' : ' collapsed'}`}>
                <div className={`project-item ${view === 'project' && projectViewId === p.id ? 'active' : ''}`} aria-current={view === 'project' && projectViewId === p.id ? 'page' : undefined} {...rowButton(() => openProject(p.id))}
                  {...dragProps({ kind: 'project', id: p.id, label: p.name })}>
                  {/* The folder is the disclosure: hovering the row swaps it for a chevron, and the name still opens the project. */}
                  <button className="project-twist" aria-expanded={open} aria-disabled={pinned}
                    aria-label={`${open ? 'Collapse' : 'Expand'} ${p.name}`}
                    title={pinned ? 'Stays open while you are in this project' : open ? 'Collapse' : 'Expand'}
                    onClick={(e) => { e.stopPropagation(); if (!pinned) toggleCollapsed(p.id) }}>
                    <Folder size={13} className="twist-folder" style={{ color: p.color }} />
                    <ChevronRight size={13} className={`twist-chevron${open ? ' rot90' : ''}`} />
                  </button>
                  <span className="project-name">{p.name}</span>
                  {open && (
                    <span className="cf-seg" role="group" aria-label={`Chats or files in ${p.name}`} onClick={(e) => e.stopPropagation()} onKeyDown={(e) => e.stopPropagation()}>
                      {(['chats', 'files'] as const).map((m) => (
                        <button key={m} className={showFiles === (m === 'files') ? 'on' : ''} aria-pressed={showFiles === (m === 'files')}
                          onClick={() => setFilesProjects((prev) => { const n = new Set(prev); if (m === 'files') n.add(p.id); else n.delete(p.id); return n })}>{m === 'files' ? 'Files' : 'Chats'}</button>
                      ))}
                    </span>
                  )}
                </div>
                {open && (
                  <div className="project-rows">
                    {showFiles ? <SidebarChatFiles scope={{ projectId: p.id }} limit={PROJECT_ROWS} jump={openConversation} /> : <>
                    {rows.length === 0 && <button className="convo-item sub muted" onClick={() => (inCanvas ? void useCanvas.getState().newChatWindow(p.id) : newChat(p.id))}><MessageSquarePlus size={12} /> New chat in project</button>}
                    {rows.slice(0, PROJECT_ROWS).map((c) => <ChatRow key={c.id} conv={c} sub active={c.id === focusedId && view === 'chat'} />)}
                    </>}
                    {(showFiles || rows.length > 0) && <button className="project-viewall" onClick={() => openProject(p.id, showFiles ? 'files' : 'chats')}>View all</button>}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}

      <div className="section-row">
        <button className="section-toggle" aria-label="Chats" aria-expanded={chatsOpen} onClick={() => setChatsOpen((o) => !o)}>
          {/* The Chats | Files switch beside it names the section, so the toggle carries no word of its own. */}
          <ChevronRight size={12} className={chatsOpen ? 'rot90' : ''} /><MessageSquare size={13} />
          {/* Counted off the desk inbox: chats working autonomously that have something unseen for you. */}
          {needsYou > 0 && <span className="count pending" title={`${needsYou} chat${needsYou === 1 ? '' : 's'} working autonomously need${needsYou === 1 ? 's' : ''} you`}>{needsYou}</span>}
        </button>
        <span className="cf-seg" role="group" aria-label="Chats or files">
          <button className={chatsTab === 'chats' ? 'on' : ''} aria-pressed={chatsTab === 'chats'} aria-label="Chat list" onClick={() => { setChatsOpen(true); setChatsTab('chats') }}>Chats</button>
          <button className={chatsTab === 'files' ? 'on' : ''} aria-pressed={chatsTab === 'files'} aria-label="Files from personal chats" onClick={() => { setChatsOpen(true); setChatsTab('files') }}>Files</button>
        </span>
        {chatsTab === 'chats' && <>
        <button className={`icon-btn sm${needsOnly ? ' on' : ''}`} aria-label="Show only what needs you" aria-pressed={needsOnly}
          title={needsOnly ? 'Showing only what needs you or is blocked' : 'Show only what needs you'}
          onClick={() => { setChatsOpen(true); setNeedsOnly((on) => { writeNeeds(!on); return !on }) }}>
          <Bell size={13} />
        </button>
        <button
          className={`icon-btn sm${searching ? ' on' : ''}`}
          aria-label="Search chats"
          aria-expanded={searching}
          title="Search chats"
          onClick={() => {
            setChatsOpen(true)
            setSearching((on) => {
              if (on) setQuery('')
              return !on
            })
          }}
        >
          <Search size={13} />
        </button>
        </>}
      </div>
      {chatsOpen && chatsTab === 'files' && <SidebarChatFiles jump={openConversation} />}
      {chatsOpen && chatsTab === 'chats' && (<>
      {searching && (
        <label className="search">
          <input
            ref={searchRef}
            placeholder="Search"
            aria-label="Search chats"
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
        {filtering && (
          <section>
            <h4>Needs you</h4>
            {wanting.length === 0 && <p className="empty-hint">Nothing needs you.</p>}
            {wanting.map((x) => ('conv' in x
              ? <ChatRow key={x.conv.id} conv={x.conv} active={x.conv.id === focusedId && view === 'chat'} lead={projectDot(x.conv)} />
              : <JobRow key={`j${x.job.id}`} job={x.job} onOpen={() => setView('home')} />))}
          </section>
        )}
        {!filtering && pinned.length > 0 && (
          <section>
            <h4>Pinned</h4>
            {pinned.map((c) => <ChatRow key={c.id} conv={c} active={c.id === focusedId && view === 'chat'} lead={projectDot(c)} trail={<Snippet hit={hitById.get(c.id)} />} />)}
          </section>
        )}
        {!filtering && groups.length === 0 && pinned.length === 0 && inMessages.length === 0 && <p className="empty-hint">{query ? 'No matches.' : 'No personal chats yet.'}</p>}
        {!filtering && groups.map((g) => (
          <section key={g.label}>
            <h4>{g.label}</h4>
            {g.items.map((c) => <ChatRow key={c.id} conv={c} active={c.id === focusedId && view === 'chat'} lead={projectDot(c)} trail={<Snippet hit={hitById.get(c.id)} />} />)}
          </section>
        ))}
        {!filtering && inMessages.length > 0 && (
          <section>
            <h4>In messages</h4>
            {inMessages.map((h) => (
              <div key={h.id} className={`convo-item ${h.id === focusedId && view === 'chat' ? 'active' : ''}`} aria-current={h.id === focusedId && view === 'chat' ? 'page' : undefined} {...rowButton(() => openConversation(h.id))}>
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
          <h4 className="archived-head"><button className="section-toggle" aria-expanded={archivedOpen} onClick={() => setArchivedOpen((o) => !o)}><ChevronRight size={11} className={archivedOpen ? 'rot90' : ''} /> Archived</button></h4>
          {archivedOpen && shownArchived.length === 0 && <p className="empty-hint">{archived.length ? 'No archived chats match.' : 'Nothing archived.'}</p>}
          {archivedOpen && shownArchived.map((c) => (
            <div key={c.id} className="convo-item archived" {...rowButton(() => void selectChat(c.id))}>
              <span className="convo-title">{c.title}</span>
              <button className="icon-btn ghost" aria-label={`Unarchive chat: ${c.title}`} title="Unarchive" onClick={(e) => { e.stopPropagation(); void archiveChat(c.id, false) }}><ArchiveRestore size={13} /></button>
              <button className="icon-btn ghost" aria-label={`Delete chat: ${c.title}`} title="Delete" onClick={(e) => { e.stopPropagation(); void deleteChat(c.id).then(() => setArchived((a) => a.filter((x) => x.id !== c.id))) }}><Trash2 size={13} /></button>
            </div>
          ))}
        </section>
      </div>
      </>)}

      {shownJobs.length > 0 && (<>
        <div className="section-row">
          <button className="section-toggle" aria-expanded={jobsOpen} onClick={() => setJobsOpen((o) => !o)}>
            <ChevronRight size={12} className={jobsOpen ? 'rot90' : ''} /><CalendarClock size={13} /> Jobs
          </button>
        </div>
        {jobsOpen && <div className="convo-list">{shownJobs.map((j) => <JobRow key={j.id} job={j} onOpen={() => setView('home')} />)}</div>}
      </>)}
      </div>

      <div className="sidebar-bottom">
        {/* Both are mounted in every view: a capture running somewhere must never be invisible. */}
        <MeetingIndicator />
        <ActivityIndicator />
        <button className="settings-btn" onClick={() => setSettingsOpen(true)}><Settings size={16} /><span>Settings</span>
          {memoryProposals > 0 && <span className="count pending" title={`${memoryProposals} memory tidy-up suggestion${memoryProposals === 1 ? '' : 's'} to review, under Knowledge base`}>{memoryProposals}</span>}
          <kbd>⌘,</kbd></button>
      </div>
    </aside>
  )
}

/** A scheduled job in the sidebar: its attention state and name. Jobs are managed on Today, so a click goes there. */
function JobRow({ job, onOpen }: { job: Job; onOpen: () => void }): JSX.Element {
  return (
    <div className="convo-item" {...rowButton(onOpen)}>
      <AttentionDot state={jobAttention(job)} detail={job.paused_reason ? 'switched off by the scheduler' : job.last_error ?? undefined} />
      <span className="convo-title">{job.name}</span>
    </div>
  )
}
