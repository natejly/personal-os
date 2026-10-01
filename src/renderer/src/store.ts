import { create } from 'zustand'
import type { ActivityConfig, ActivityContextFile, ActivityEvent, ActivitySignal, ActivityStatus, ActivitySummary, AgentInbox, ChatEvent, ChatRunStarted, Conversation, ConversationSettings, Doc, DocRevision, Document, FullDoc, GraphData, Memory, Message, ModelInfo, Settings, Project, RunConflict, SessionStatus, ToolInfo, Todo, GoogleStatus, TasksSyncStatus, TodayDashboard, Recap, Job } from '@shared/types'
import { api, chatStream, setBase, type Scope } from './lib/api'
import { finishStatus, mergeConversation, pickEvictions, reduceStatus, settleApprovals } from './sessionStatus'
import { viewHidden } from './modules'

/**
 * Settings as the renderer holds them: without the legacy `mode`, which only init() reads. Kept out
 * of the store so no Settings draft (the first-run modal, ⌘,) can PUT a stale `mode: 'canvas'` back
 * over the one-shot migration.
 */
const withoutLegacyMode = (s: Settings): Settings => {
  const out = { ...s }
  delete out.mode
  return out
}

/** `'canvas'` is the spaces desktop: one destination among the views, not a separate shell. */
export type View = 'home' | 'chat' | 'todos' | 'calendar' | 'mail' | 'boards' | 'dashboards' | 'memory' | 'documents' | 'docs' | 'activity' | 'project' | 'canvas'
/** Every view but the canvas: what ⌘⇧C and the sidebar's LayoutGrid button return to. */
export type ClassicView = Exclude<View, 'canvas'>
/** How the Docs editor splits its panes. */
export type DocMode = 'edit' | 'split' | 'preview'
/** How the Memory panel lays out its two halves: the memory list and the knowledge graph. */
export type MemoryMode = 'split' | 'list' | 'graph'
export type ContextTab = 'last' | 'preview' | 'trace'
export type { Scope, SessionStatus }

/** `abort` only detaches this window from the run's SSE; ending the run itself is `api.stopRun(runId)`. */
export interface Streaming { messageId: string | null; runId: string; abort: AbortController }

/** One live conversation. Store-local: a running AbortController must never cross the IPC bus. */
export interface ChatSession {
  conversation: Conversation
  streaming: Streaming | null
  status: SessionStatus
  /** epoch ms the last run finished; drives the 6 s green hold */
  finishedAt: number | null
  /** tool calls waiting on the approval card */
  pendingApprovals: number
  /** assistant replies that landed while this session was not focused */
  unread: number
  /** epoch ms of the last focus or run; only used to pick eviction victims */
  touchedAt: number
}

interface Toast { id: number; text: string; kind: 'info' | 'error' | 'learned' }

/** Live sessions kept in memory at once. Beyond this the least recently touched are dropped. */
const MAX_SESSIONS = 12
const HOLD_MS = 6000

const newSession = (conversation: Conversation): ChatSession =>
  ({ conversation, streaming: null, status: 'idle', finishedAt: null, pendingApprovals: 0, unread: 0, touchedAt: Date.now() })

const countApprovals = (c: Conversation): number =>
  (c.messages ?? []).reduce((n, m) => n + (m.tool_events ?? []).filter((t) => t.pending && t.needs_approval).length, 0)

export interface State {
  ready: boolean
  backendError: string | null
  settings: Settings
  models: ModelInfo[]
  modelsError: string | null
  tools: ToolInfo[]
  google: GoogleStatus | null
  tasksSync: TasksSyncStatus | null
  dashboard: TodayDashboard | null
  todos: Todo[]
  recap: Recap | null
  recapLoading: boolean
  /** The Agent Inbox on Today: what needs the user, and what the scheduled jobs did. */
  agentInbox: AgentInbox | null
  jobs: Job[]

  projects: Project[]
  personalStats: Project['stats']

  view: View
  /** The classic view the canvas was entered from; `leaveCanvas` (⌘⇧C) returns to it. */
  lastClassicView: ClassicView
  /** Layout of the Memory panel (list + graph live in one panel). */
  memoryMode: MemoryMode
  projectViewId: string | null
  /** Project the next new chat will be created in (null = personal). */
  draftProjectId: string | null
  /** Scope filter used by the Memory / Graph / Documents library views. */
  libraryScope: Scope
  /** Scope the memories/graph/documents arrays are currently loaded for. */
  dataScope: Scope

  sidebarOpen: boolean
  contextOpen: boolean
  contextTab: ContextTab
  /** Message whose execution trace the Trace tab shows (null = latest assistant reply). */
  traceMessageId: string | null
  settingsOpen: boolean
  projectModal: { mode: 'create' } | { mode: 'edit'; project: Project } | null
  toasts: Toast[]

  conversations: Conversation[]
  /** Loaded conversations, keyed by id. Each one streams independently. */
  sessions: Record<string, ChatSession>
  focusedConversationId: string | null

  memories: Memory[]
  graph: GraphData
  documents: Document[]

  /** Docs: the markdown the user writes. List rows, plus the one open in the editor. */
  docs: Doc[]
  activeDoc: FullDoc | null
  /** Ids of the docs open as tabs, most recent last. */
  docTabs: string[]
  docRevisions: DocRevision[]
  /** Assistant edits awaiting review, across every doc — the sidebar badge. */
  docsPending: number
  docMode: DocMode
  /** Editor buffer for the open doc: what the user has typed but autosave has not yet flushed. */
  docDraft: string | null
  docSaving: boolean
  /** Activity monitor. `null` until the first status poll lands. */
  activity: ActivityStatus | null
  activityEvents: ActivityEvent[]
  activitySummaries: ActivitySummary[]
  activityContext: ActivityContextFile | null
  activityBusy: boolean

  init: () => Promise<void>
  loadModels: () => Promise<void>
  saveSettings: (patch: Partial<Settings>) => Promise<void>
  setView: (v: View) => void
  /** Back to `lastClassicView`. */
  leaveCanvas: () => void
  setMemoryMode: (m: MemoryMode) => void
  /** Open the Memory panel, optionally focused on one half. */
  openMemory: (m?: MemoryMode) => void
  toggleSidebar: () => void
  toggleContext: () => void
  setContextTab: (t: ContextTab) => void
  openTrace: (messageId: string) => void
  setSettingsOpen: (o: boolean) => void
  setProjectModal: (m: State['projectModal']) => void
  toast: (text: string, kind?: Toast['kind']) => void

  refreshProjects: () => Promise<void>
  openProject: (id: string) => void
  createProject: (p: Pick<Project, 'name' | 'description' | 'system_prompt' | 'color'>) => Promise<void>
  updateProject: (id: string, patch: Partial<Project>) => Promise<void>
  deleteProject: (id: string) => Promise<void>

  setLibraryScope: (s: Scope) => Promise<void>
  loadScope: (s: Scope) => Promise<void>

  refreshConversations: () => Promise<void>
  newChat: (projectId?: string | null) => void
  /** Create a conversation without navigating to it, so a canvas can open a chat window on it. Toasts and resolves null on failure. */
  createConversation: (projectId: string | null) => Promise<Conversation | null>
  selectChat: (id: string | null) => Promise<void>
  /** Load a conversation into `sessions` without focusing it. Concurrent calls share one fetch. */
  openSession: (conversationId: string) => Promise<void>
  /** `openSession`, plus attach to a reply already in flight elsewhere so the window paints amber. Idempotent. */
  attachSession: (conversationId: string) => Promise<void>
  /** Drop a session and abort whatever it was streaming. */
  closeSession: (conversationId: string) => void
  /** Called on focus: clears unread and maps done/error back to idle, but never needs-approval. */
  clearSessionStatus: (conversationId: string) => void
  deleteChat: (id: string) => Promise<void>
  renameChat: (id: string, title: string) => Promise<void>
  setChatModel: (model: string, conversationId?: string) => Promise<void>
  setChatSettings: (patch: Partial<ConversationSettings>, conversationId?: string) => Promise<void>
  /** `false` when the text was refused, so the caller must keep it. Never rejects. */
  send: (text: string, conversationId?: string) => Promise<boolean>
  regenerate: (conversationId?: string) => Promise<void>
  stop: (conversationId?: string) => Promise<void>

  refreshMemories: (q?: string) => Promise<void>
  addMemory: (content: string, kind: string, projectId: string | null) => Promise<void>
  updateMemory: (id: string, patch: Parameters<typeof api.memories.update>[1]) => Promise<void>
  deleteMemory: (id: string) => Promise<void>

  refreshGraph: () => Promise<void>
  refreshDocuments: () => Promise<void>

  /** Status only - cheap enough to poll while the Activity panel is open. */
  refreshActivity: () => Promise<void>
  /** Status plus the event log, summaries and activity.md. */
  loadActivity: () => Promise<void>
  setActivityConfig: (patch: Partial<ActivityConfig>) => Promise<void>
  toggleActivitySignal: (signal: ActivitySignal) => Promise<void>
  startActivity: () => Promise<void>
  stopActivity: () => Promise<void>
  pauseActivity: (minutes?: number) => Promise<void>
  resumeActivity: () => Promise<void>
  rollupActivity: () => Promise<void>
  refreshActivityProfile: () => Promise<void>
  deleteActivityEvent: (id: string) => Promise<void>
  deleteActivitySummary: (id: string) => Promise<void>
  purgeActivity: (scope: 'expired' | 'events' | 'summaries' | 'all') => Promise<void>
  refreshDashboard: () => Promise<void>
  refreshRecap: (force?: boolean) => Promise<void>
  refreshAgentInbox: () => Promise<void>
  refreshJobs: () => Promise<void>
  setJobEnabled: (id: string, enabled: boolean) => Promise<void>
  runJobNow: (id: string) => Promise<void>
  decideProposal: (id: string, accept: boolean, args?: Record<string, unknown>) => Promise<void>
  approveTool: (callId: string, decision: 'allow' | 'deny' | 'always_chat' | 'always_global', conversationId?: string) => Promise<void>
  refreshGoogle: () => Promise<void>
  connectGoogle: () => Promise<void>
  disconnectGoogle: () => Promise<void>
  refreshTasksSync: () => Promise<void>
  setTasksSync: (patch: { enabled?: boolean; tasklist?: string; intervalMinutes?: number }) => Promise<void>
  runTasksSync: () => Promise<void>
  refreshTodos: (scope?: Scope, includeDone?: boolean) => Promise<void>
  addTodo: (t: { title: string; project_id?: string | null; due?: string | null; priority?: number; notes?: string }) => Promise<void>
  updateTodo: (id: string, patch: Parameters<typeof api.todos.update>[1]) => Promise<void>
  deleteTodo: (id: string) => Promise<void>
  uploadDocuments: (files: FileList | File[], projectId: string | null) => Promise<void>
  deleteDocument: (id: string) => Promise<void>

  refreshDocs: (q?: string) => Promise<void>
  refreshDocsPending: () => Promise<void>
  openDoc: (id: string) => Promise<void>
  closeDocTab: (id: string) => void
  createDoc: (d?: { title?: string; content?: string; project_id?: string | null }) => Promise<void>
  /** Type into the open doc. Buffers locally and flushes to the backend on a debounce. */
  editDoc: (content: string) => void
  /** Flush the buffer now (⌘S, switching docs, leaving the view). */
  flushDoc: () => Promise<void>
  renameDoc: (id: string, title: string) => Promise<void>
  setDocStar: (id: string, starred: boolean) => Promise<void>
  /** Move a doc into a folder; '' takes it out of any folder. */
  setDocFolder: (id: string, folder: string) => Promise<void>
  deleteDoc: (id: string) => Promise<void>
  setDocMode: (m: DocMode) => void
  refreshDocRevisions: (id?: string) => Promise<void>
  acceptRevision: (revId: string) => Promise<void>
  rejectRevision: (revId: string) => Promise<void>
  restoreRevision: (revId: string) => Promise<void>
}

let toastSeq = 0
/** Autosave debounce for the doc editor: long enough to be one history entry, short enough to trust. */
const SAVE_DEBOUNCE_MS = 1200
let saveTimer: ReturnType<typeof setTimeout> | null = null

/**
 * A pop-out renderer (`?surface=widget`). Same lookup as main.tsx: dev serves the query off
 * `location.search`, and the href fallback covers one that arrived behind a hash.
 */
const isPopout = (): boolean => {
  const q = window.location.search || (window.location.href.includes('?') ? window.location.href.slice(window.location.href.indexOf('?')) : '')
  return new URLSearchParams(q).get('surface') !== null
}

/** Pending `done` → `idle` timers, keyed by conversation id. A new run cancels its own. */
const holds = new Map<string, ReturnType<typeof setTimeout>>()
const clearHold = (convId: string): void => {
  const t = holds.get(convId)
  if (t !== undefined) {
    clearTimeout(t)
    holds.delete(convId)
  }
}

/** A 409 from `POST /chat` arrives as a `RunConflict` JSON-encoded in the error detail. */
const runConflict = (e: unknown): RunConflict | null => {
  try {
    const d = JSON.parse((e as Error).message) as RunConflict
    return typeof d?.run_id === 'string' && typeof d.seq === 'number' ? d : null
  } catch {
    return null
  }
}

/**
 * Conversations with a mounted chat surface, refcounted. The canvas view never sets
 * `focusedConversationId`, so without this the LRU would pick a victim by mount order alone and an
 * on-screen window would be a legal one.
 */
const retained = new Map<string, number>()

/** LRU by `touchedAt`, never evicting a retained, focused, streaming or unread session. */
const evict = (sessions: Record<string, ChatSession>, keepId: string | null): Record<string, ChatSession> => {
  const keep = new Set(retained.keys())
  if (keepId) keep.add(keepId)
  const victims = pickEvictions(sessions, keep, MAX_SESSIONS)
  if (!victims.length) return sessions
  const out = { ...sessions }
  for (const id of victims) {
    clearHold(id)
    delete out[id]
  }
  return out
}

/** One in-flight promise per key, so N concurrent callers share one fetch instead of racing. */
const share = (map: Map<string, Promise<void>>, key: string, fn: () => Promise<void>): Promise<void> => {
  const live = map.get(key)
  if (live) return live
  const p = fn().finally(() => { if (map.get(key) === p) map.delete(key) })
  map.set(key, p)
  return p
}

const loads = new Map<string, Promise<void>>()
const attaches = new Map<string, Promise<void>>()

/** Every conversation mutation a stream event makes, as one new session. No side effects. */
const applyEvent = (s: ChatSession, ev: ChatEvent, focused: boolean): ChatSession => {
  const c = s.conversation
  const msgs = c.messages ?? []
  const withMsgs = (messages: Message[]): ChatSession => ({ ...s, conversation: { ...c, messages } })
  const mapMsg = (mid: string, fn: (m: Message) => Message): ChatSession =>
    withMsgs(msgs.map((m) => (m.id === mid ? fn(m) : m)))
  switch (ev.event) {
    case 'user_message':
      // Merge by id: a steer is persisted and published by its endpoint, so an attach replay plus the
      // live stream (or a refetch) can both carry it.
      return msgs.some((m) => m.id === ev.data.id) ? s : withMsgs([...msgs, ev.data])
    case 'assistant_message': {
      // Merge by id: attaching to a run replays this event into a conversation row that may already
      // hold the message, and appending it twice is the duplicate the ring used to paint. An empty
      // replayed body keeps whatever content we have, so a mid-reply attach loses nothing.
      const held = msgs.some((m) => m.id === ev.data.id)
      return {
        ...(held
          ? mapMsg(ev.data.id, (m) => ({ ...ev.data, content: ev.data.content || m.content }))
          : withMsgs([...msgs, ev.data])),
        streaming: s.streaming && { ...s.streaming, messageId: ev.data.id },
        // A steered run opens a new segment after a `done`; the green hold belongs to the real end.
        finishedAt: null,
        unread: focused || held ? s.unread : s.unread + 1
      }
    }
    case 'title':
      return { ...s, conversation: { ...c, title: ev.data.title } }
    case 'removed_message':
      return withMsgs(msgs.filter((m) => m.id !== ev.data.id))
    case 'delta':
      return mapMsg(ev.data.id, (m) => ({ ...m, content: m.content + ev.data.text }))
    case 'tool_call':
      return mapMsg(ev.data.message_id, (m) => ({ ...m, tool_events: [...(m.tool_events ?? []), { id: ev.data.id, name: ev.data.name, arguments: ev.data.arguments, result_preview: '', duration_ms: 0, error: null, pending: true, needs_approval: !!ev.data.needs_approval }] }))
    case 'tool_result':
      return mapMsg(ev.data.message_id, (m) => ({ ...m, tool_events: (m.tool_events ?? []).map((t) => (t.id === ev.data.id ? { ...ev.data, pending: false } : t)) }))
    case 'span':
      return mapMsg(ev.data.message_id, (m) => {
        const trace = m.trace ?? []
        const i = trace.findIndex((sp) => sp.id === ev.data.span.id)
        return { ...m, trace: i >= 0 ? trace.map((sp, j) => (j === i ? ev.data.span : sp)) : [...trace, ev.data.span] }
      })
    case 'done':
      return { ...mapMsg(ev.data.id, (m) => ({ ...m, error: ev.data.error, context_used: ev.data.context_used, tool_events: ev.data.tool_events?.length ? ev.data.tool_events : m.tool_events, trace: ev.data.trace?.length ? ev.data.trace : m.trace })), finishedAt: Date.now() }
    default:
      return s
  }
}

export const useStore = create<State>((set, get) => {
  /**
   * App's init effect runs twice under React.StrictMode, so both of these are latched. A second
   * `window.os.onMenu` subscription would run every menu action twice, which silently kills the
   * toggles: ⌘B/⌘I/⌘⇧C flip and flip straight back, and ⌘N opens two chats. The canvas store
   * latches its own menu/bus listeners the same way.
   */
  let menuWired = false
  let inited = false

  const wireMenu = (): void => {
    if (menuWired) return
    menuWired = true
    window.os.onMenu((action) => {
      const s = get()
      // In the canvas view ⌘N opens a chat window instead; canvas/store.ts handles it there.
      if (action === 'new-chat') {
        if (s.view !== 'canvas') s.newChat(s.view === 'project' ? s.projectViewId : selectActive(s)?.project_id ?? null)
      } else if (action === 'settings') s.setSettingsOpen(true)
      else if (action === 'toggle-sidebar') s.toggleSidebar()
      else if (action === 'toggle-context') s.toggleContext()
      else if (action === 'view:graph') s.openMemory('graph')
      else if (action.startsWith('view:')) s.setView(action.slice(5) as View)
      else if (action === 'upload') {
        s.setView('documents')
        setTimeout(() => document.getElementById('doc-upload-input')?.click(), 100)
      }
    })
  }

  const patchSession = (convId: string, fn: (s: ChatSession) => ChatSession): void =>
    set((st) => {
      const cur = st.sessions[convId]
      if (!cur) return {}
      const next = fn(cur)
      return next === cur ? {} : { sessions: { ...st.sessions, [convId]: next } }
    })
  const putSession = (conversation: Conversation): void =>
    set((st) => {
      const cur = st.sessions[conversation.id]
      // A fetch that lands among the deltas must not clobber what the stream already applied: the
      // in-flight assistant message is not persisted yet, so an overwrite blanks the visible reply.
      const next = cur
        ? { ...cur, conversation: mergeConversation(cur.conversation, conversation, !!cur.streaming), touchedAt: Date.now() }
        : newSession(conversation)
      const sessions = { ...st.sessions, [conversation.id]: next }
      return { sessions: evict(sessions, st.focusedConversationId) }
    })
  const patchConversation = (convId: string, fn: (c: Conversation) => Conversation): void =>
    patchSession(convId, (s) => ({ ...s, conversation: fn(s.conversation) }))
  const hold = (convId: string): void => {
    clearHold(convId)
    holds.set(convId, setTimeout(() => {
      holds.delete(convId)
      patchSession(convId, (s) => (s.status === 'done' ? { ...s, status: 'idle', finishedAt: null } : s))
    }, HOLD_MS))
  }
  const refreshAll = (): void => {
    void get().refreshMemories()
    void get().refreshGraph()
    void get().refreshDocuments()
    void get().refreshProjects()
  }

  /** Consume one run's events into a session. `attached` means the run was started by someone else. */
  const watchRun = async (convId: string, run: ChatRunStarted, from: { messageId: string | null; approvals: number; attached: boolean }): Promise<void> => {
    // One subscription per conversation. A second subscription to the same run would apply every
    // delta twice, since `applyEvent` appends. A different run supersedes this one, so its viewer is
    // detached first: the old loop's `finally` is abort-identity guarded and will not undo us.
    const prev = get().sessions[convId]?.streaming
    if (prev?.runId === run.run_id) return
    prev?.abort.abort()
    const abort = new AbortController()
    const attached = from.attached
    clearHold(convId)
    patchSession(convId, (s) => ({ ...s, streaming: { messageId: from.messageId, runId: run.run_id, abort }, status: settleApprovals('working', from.approvals), finishedAt: null, pendingApprovals: from.approvals, touchedAt: Date.now() }))
    try {
      for await (const ev of chatStream(convId, run.seq, abort.signal, run.run_id)) {
        const focused = get().focusedConversationId === convId
        patchSession(convId, (s) => {
          const next = applyEvent(s, ev, focused)
          // Only the approval events can move the count, and delta must stay free of any recount.
          const pendingApprovals = ev.event === 'tool_call' || ev.event === 'tool_result' ? countApprovals(next.conversation) : s.pendingApprovals
          return { ...next, pendingApprovals, status: reduceStatus(s.status, ev, pendingApprovals) }
        })
        switch (ev.event) {
          case 'done':
            if (!ev.data.error) hold(convId)
            void get().refreshConversations()
            break
          case 'learned': {
            const { memories, nodes, edges, updated = [], removed = [] } = ev.data
            const parts = [`Learned ${memories.length} memor${memories.length === 1 ? 'y' : 'ies'}`]
            if (updated.length) parts.push(`updated ${updated.length}`)
            if (removed.length) parts.push(`forgot ${removed.length}`)
            parts.push(`${nodes.length} entities, ${edges.length} relations`)
            get().toast(parts.join(', '), 'learned')
            if (memories.length + updated.length + removed.length + nodes.length + edges.length) refreshAll()
            break
          }
          case 'learn_error':
            get().toast(`Auto-learn failed: ${ev.data.message}`, 'error')
            break
          case 'error':
            get().toast(ev.data.message, 'error')
            break
        }
      }
    } catch (e) {
      // An aborted signal is the user pressing Stop, not a failure.
      if (!abort.signal.aborted) {
        get().toast((e as Error).message, 'error')
        patchSession(convId, (s) => (s.streaming?.abort === abort ? { ...s, status: 'error', finishedAt: Date.now() } : s))
      }
    } finally {
      patchSession(convId, (s) => (s.streaming?.abort === abort ? { ...s, streaming: null, status: finishStatus(s.status) } : s))
      // An attached run wrote deltas this window never saw; the persisted message is the whole reply.
      if (attached) void get().openSession(convId)
    }
  }

  /**
   * `false` means the backend never accepted `body`, so the caller still owns the text it sent.
   * Resolves on that verdict, not at the end of the run: a composer is holding a draft on it.
   */
  const runStream = async (convId: string, body: { content?: string; model?: string }): Promise<boolean> => {
    let run: ChatRunStarted
    try {
      run = await api.chat(convId, body)
    } catch (e) {
      const conflict = runConflict(e)
      if (!conflict) {
        get().toast((e as Error).message, 'error')
        patchSession(convId, (s) => ({ ...s, status: 'error', finishedAt: Date.now() }))
        return false
      }
      // Another window is already mid-reply. Adopt that run from its tail so the window paints, and
      // steer the message into it instead of dropping it.
      void watchRun(convId, { run_id: conflict.run_id, seq: conflict.seq }, { messageId: null, approvals: 0, attached: true })
      if (body.content) {
        try {
          await api.steer(convId, body.content)
          return true
        } catch {
          get().toast('That chat is already replying — your message was not sent.', 'error')
        }
      }
      return false
    }
    // Synchronous up to its first await, so `streaming` is set before this returns.
    void watchRun(convId, run, { messageId: null, approvals: 0, attached: false })
    return true
  }

  return {
    ready: false,
    backendError: null,
    settings: { baseUrl: '', apiKey: '', defaultModel: '', systemPrompt: '', extractionModel: '', autoLearn: true, theme: 'dark', gatherShortcut: '', tools: {}, maxToolRounds: 8, braveApiKey: '', tavilyApiKey: '', googleClientId: '', googleClientSecret: '', modelPrices: {} },
    models: [],
    modelsError: null,
    tools: [],
    google: null,
    tasksSync: null,
    dashboard: null,
    todos: [],
    recap: null,
    recapLoading: false,
    agentInbox: null,
    jobs: [],
    projects: [],
    personalStats: undefined,
    view: 'home',
    lastClassicView: 'home',
    memoryMode: 'split',
    projectViewId: null,
    draftProjectId: null,
    libraryScope: 'all',
    dataScope: 'all',
    docs: [],
    activeDoc: null,
    docTabs: [],
    docRevisions: [],
    docsPending: 0,
    docMode: 'split',
    docDraft: null,
    docSaving: false,
    sidebarOpen: true,
    contextOpen: false,
    contextTab: 'last',
    traceMessageId: null,
    settingsOpen: false,
    projectModal: null,
    toasts: [],
    conversations: [],
    sessions: {},
    focusedConversationId: null,
    memories: [],
    graph: { nodes: [], edges: [] },
    documents: [],

    activity: null,
    activityEvents: [],
    activitySummaries: [],
    activityContext: null,
    activityBusy: false,

    init: async () => {
      // Before the backend check and before the guard: a dead backend must still leave the menu
      // shortcuts wired, and StrictMode's second mount must not add a second listener.
      wireMenu()
      if (inited) return
      inited = true
      const status = await window.os.backendStatus()
      if (!status.url) return set({ ready: true, backendError: status.error ?? 'Backend not running' })
      setBase(status.url)
      try {
        await api.health()
      } catch (e) {
        return set({ ready: true, backendError: status.error ?? (e as Error).message })
      }
      const [settings, projects, personalStats, conversations] = await Promise.all([
        api.settings.get(), api.projects.list(), api.projects.globalStats(), api.conversations.list('all')
      ])
      // One-shot migration of the pre-spaces global mode: a user who left the app in canvas mode lands
      // in the canvas once, and the setting is reset so later launches open on Today. Only the main
      // window writes it back; a pop-out (`?surface=widget`) never renders App and must not touch settings.
      const { mode: legacyMode } = settings
      const legacyCanvas = legacyMode === 'canvas'
      set({ settings: withoutLegacyMode(settings), view: legacyCanvas ? 'canvas' : 'home', projects, personalStats, conversations, ready: true, settingsOpen: !settings.apiKey && conversations.length === 0 })
      if (legacyCanvas && !isPopout()) void get().saveSettings({ mode: 'classic' }).catch(() => undefined)
      void get().loadModels()
      void get().loadScope('all')
      void api.tools().then((t) => set({ tools: t.tools })).catch(() => undefined)
      void get().refreshDashboard()
      void get().refreshTodos()
      void get().refreshRecap()
      void get().refreshDocsPending()
      void get().refreshActivity()
    },

    loadModels: async () => {
      try {
        set({ models: await api.models(), modelsError: null })
      } catch (e) {
        set({ models: [], modelsError: (e as Error).message })
      }
    },
    saveSettings: async (patch) => {
      set({ settings: withoutLegacyMode(await api.settings.set(patch)) })
      if ('baseUrl' in patch || 'apiKey' in patch) void get().loadModels()
      if ('googleClientId' in patch || 'googleClientSecret' in patch) void get().refreshGoogle()
    },
    setView: (view) => {
      const cur = get().view
      // Leaving the editor must not drop what is still in the buffer.
      if (cur === 'docs' && view !== 'docs') void get().flushDoc()
      if (view === 'canvas' && cur !== 'canvas') set({ view, lastClassicView: cur })
      else set({ view })
      if (view === 'docs') {
        void get().refreshDocs()
        void get().refreshDocsPending()
      }
      if (view === 'home') void get().refreshDashboard()
      if (view === 'todos') void get().refreshTodos()
      if (view === 'activity') void get().loadActivity()
    },
    leaveCanvas: () => {
      const s = get()
      const v = s.lastClassicView
      // The target can have gone while in the canvas: its project deleted, or the view hidden in Settings.
      const gone = v === 'project' ? !s.projectViewId || !s.projects.some((p) => p.id === s.projectViewId) : viewHidden(s.settings, v)
      s.setView(gone ? 'home' : v)
    },
    setMemoryMode: (memoryMode) => set({ memoryMode }),
    openMemory: (memoryMode) => set(memoryMode ? { view: 'memory', memoryMode } : { view: 'memory' }),
    toggleSidebar: () => set((s) => ({ sidebarOpen: !s.sidebarOpen })),
    toggleContext: () => set((s) => ({ contextOpen: !s.contextOpen })),
    setContextTab: (contextTab) => set({ contextTab }),
    openTrace: (traceMessageId) => set({ traceMessageId, contextTab: 'trace', contextOpen: true }),
    setSettingsOpen: (settingsOpen) => set({ settingsOpen }),
    setProjectModal: (projectModal) => set({ projectModal }),
    toast: (text, kind = 'info') => {
      const id = ++toastSeq
      set((s) => ({ toasts: [...s.toasts, { id, text, kind }] }))
      setTimeout(() => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })), kind === 'error' ? 6000 : 3500)
    },

    refreshProjects: async () => {
      const [projects, personalStats] = await Promise.all([api.projects.list(), api.projects.globalStats()])
      set({ projects, personalStats })
    },
    openProject: (id) => set({ view: 'project', projectViewId: id, draftProjectId: id, settingsOpen: false }),
    createProject: async (p) => {
      const project = await api.projects.create(p)
      await get().refreshProjects()
      get().openProject(project.id)
    },
    updateProject: async (id, patch) => {
      await api.projects.update(id, patch)
      await get().refreshProjects()
    },
    deleteProject: async (id) => {
      await api.projects.delete(id)
      set((s) => {
        const sessions = Object.fromEntries(Object.entries(s.sessions).filter(([, x]) => x.conversation.project_id !== id))
        const fid = s.focusedConversationId
        return {
          view: s.view === 'project' && s.projectViewId === id ? 'chat' : s.view,
          // Deleted from the sidebar while in the canvas: ⌘⇧C must not return to its page.
          lastClassicView: s.lastClassicView === 'project' && s.projectViewId === id ? 'chat' : s.lastClassicView,
          projectViewId: s.projectViewId === id ? null : s.projectViewId,
          draftProjectId: s.draftProjectId === id ? null : s.draftProjectId,
          sessions,
          focusedConversationId: fid && s.sessions[fid] && !sessions[fid] ? null : fid
        }
      })
      await Promise.all([get().refreshProjects(), get().refreshConversations()])
    },

    setLibraryScope: async (libraryScope) => {
      set({ libraryScope })
      await get().loadScope(libraryScope)
    },
    loadScope: async (dataScope) => {
      set({ dataScope })
      await Promise.all([get().refreshMemories(), get().refreshGraph(), get().refreshDocuments(), get().refreshDocs()])
    },

    refreshConversations: async () => set({ conversations: await api.conversations.list('all') }),
    newChat: (projectId = null) => set({ focusedConversationId: null, draftProjectId: projectId, view: 'chat', settingsOpen: false }),
    createConversation: async (projectId) => {
      try {
        const c = await api.conversations.create(projectId, get().settings.defaultModel)
        c.messages = []
        putSession(c)
        set((s) => ({ conversations: [c, ...s.conversations.filter((x) => x.id !== c.id)] }))
        void get().refreshProjects()
        return c
      } catch (e) {
        get().toast((e as Error).message, 'error')
        return null
      }
    },
    selectChat: async (id) => {
      set({ view: 'chat', settingsOpen: false, traceMessageId: null })
      if (!id) return set({ focusedConversationId: null })
      set({ focusedConversationId: id })
      get().clearSessionStatus(id)
      // A session mid-run holds content the backend has not persisted yet, so never refetch over it.
      if (get().sessions[id]?.streaming) return
      const c = await api.conversations.get(id)
      if (get().focusedConversationId !== id) return
      putSession(c)
      set({ draftProjectId: c.project_id })
    },
    openSession: async (conversationId) =>
      share(loads, conversationId, async () => {
        putSession(await api.conversations.get(conversationId))
      }),
    attachSession: async (conversationId) =>
      share(attaches, conversationId, async () => {
        // A run started before this window existed: `GET /runs` is the only way it can know.
        const runs = await api.runs().catch(() => null)
        const run = runs?.find((r) => r.conversation_id === conversationId && r.live)
        await get().openSession(conversationId)
        const s = get().sessions[conversationId]
        if (!run || !s) return
        // From the run's own seq, so the tail streams live and no past delta is applied twice. Not
        // awaited: `watchRun` only resolves when the run ends, and this promise gates the dedupe.
        void watchRun(conversationId, { run_id: run.run_id, seq: run.seq }, { messageId: run.message_id, approvals: countApprovals(s.conversation), attached: true })
      }),
    closeSession: (conversationId) => {
      clearHold(conversationId)
      get().sessions[conversationId]?.streaming?.abort.abort()
      set((s) => {
        const sessions = { ...s.sessions }
        delete sessions[conversationId]
        return { sessions, focusedConversationId: s.focusedConversationId === conversationId ? null : s.focusedConversationId }
      })
    },
    clearSessionStatus: (conversationId) => {
      clearHold(conversationId)
      patchSession(conversationId, (s) => {
        const settled = s.status === 'done' || s.status === 'error'
        return { ...s, unread: 0, touchedAt: Date.now(), status: settled ? 'idle' : s.status, finishedAt: settled ? null : s.finishedAt }
      })
    },
    deleteChat: async (id) => {
      await api.conversations.delete(id)
      get().closeSession(id)
      set((s) => ({ conversations: s.conversations.filter((c) => c.id !== id) }))
      void get().refreshProjects()
    },
    renameChat: async (id, title) => {
      if (!title.trim()) return
      await api.conversations.patch(id, { title: title.trim() })
      patchConversation(id, (c) => ({ ...c, title: title.trim() }))
      await get().refreshConversations()
    },
    setChatModel: async (model, conversationId) => {
      const id = conversationId ?? get().focusedConversationId
      if (!id) return void (await get().saveSettings({ defaultModel: model }))
      await api.conversations.patch(id, { model })
      patchConversation(id, (c) => ({ ...c, model }))
    },
    setChatSettings: async (patch, conversationId) => {
      const id = conversationId ?? get().focusedConversationId
      if (!id) return
      const c = await api.conversations.patch(id, { settings: patch })
      patchConversation(id, (cur) => ({ ...cur, settings: c.settings }))
    },

    send: async (text, conversationId) => {
      if (!text.trim()) return false
      const id = conversationId ?? get().focusedConversationId
      if (id) {
        // Mid-reply sends steer the live run: the message lands in the conversation now and the
        // model folds it in at its next round boundary.
        if (get().sessions[id]?.streaming) {
          try {
            await api.steer(id, text)
            return true
          } catch {
            // The run ended in the gap; fall through to a normal send.
          }
        }
        if (!get().sessions[id]) await get().openSession(id)
        return runStream(id, { content: text })
      }
      let c: Conversation
      try {
        c = await api.conversations.create(get().draftProjectId, get().settings.defaultModel)
      } catch (e) {
        // `send` never rejects: a caller holding the user's draft needs a verdict, not an exception.
        get().toast((e as Error).message, 'error')
        return false
      }
      c.messages = []
      putSession(c)
      set({ focusedConversationId: c.id, view: 'chat' })
      void get().refreshProjects()
      return runStream(c.id, { content: text })
    },
    regenerate: async (conversationId) => {
      const id = conversationId ?? get().focusedConversationId
      if (!id || get().sessions[id]?.streaming) return
      if (!get().sessions[id]) await get().openSession(id)
      await runStream(id, {})
    },
    stop: async (conversationId) => {
      const id = conversationId ?? get().focusedConversationId
      const st = id && get().sessions[id]?.streaming
      if (!id || !st) return
      // Aborting the fetch would only detach this window, so a stop is always a request to the run.
      if (st.messageId) await api.stop(st.messageId).catch(() => undefined)
      else await api.stopRun(id, st.runId).catch(() => undefined)
    },

    refreshDocs: async (q = '') => set({ docs: await api.docs.list(get().dataScope, q) }),
    refreshDocsPending: async () => {
      try {
        set({ docsPending: (await api.docs.pending()).pending })
      } catch { /* a badge is not worth a toast */ }
    },
    openDoc: async (id) => {
      if (get().activeDoc?.id !== id) await get().flushDoc()
      set((st) => ({ view: 'docs', docTabs: st.docTabs.includes(id) ? st.docTabs : [...st.docTabs, id] }))
      try {
        const doc = await api.docs.get(id)
        // A slower fetch must not clobber a doc the user has since switched away from.
        if (get().docTabs.includes(id)) set({ activeDoc: doc, docDraft: null })
        void get().refreshDocRevisions(id)
      } catch (e) {
        get().toast((e as Error).message, 'error')
      }
    },
    closeDocTab: (id) => {
      const st = get()
      if (st.activeDoc?.id === id) void st.flushDoc()
      const tabs = st.docTabs.filter((t) => t !== id)
      set({ docTabs: tabs })
      if (st.activeDoc?.id === id) {
        const next = tabs[tabs.length - 1]
        if (next) void get().openDoc(next)
        else set({ activeDoc: null, docDraft: null, docRevisions: [] })
      }
    },
    createDoc: async (d = {}) => {
      try {
        const doc = await api.docs.create({ title: d.title ?? 'Untitled', content: d.content ?? '', project_id: d.project_id ?? null })
        await get().refreshDocs()
        set((st) => ({ view: 'docs', docTabs: [...st.docTabs, doc.id], activeDoc: doc, docDraft: null }))
        void get().refreshDocRevisions(doc.id)
      } catch (e) {
        get().toast((e as Error).message, 'error')
      }
    },
    editDoc: (content) => {
      if (!get().activeDoc) return
      set({ docDraft: content })
      if (saveTimer) clearTimeout(saveTimer)
      saveTimer = setTimeout(() => { void get().flushDoc() }, SAVE_DEBOUNCE_MS)
    },
    flushDoc: async () => {
      if (saveTimer) {
        clearTimeout(saveTimer)
        saveTimer = null
      }
      const { activeDoc: doc, docDraft } = get()
      if (!doc || docDraft === null || docDraft === doc.content) return set({ docDraft: null })
      set({ docSaving: true })
      try {
        const saved = await api.docs.save(doc.id, { content: docDraft })
        // Keep whatever was typed while the request was in flight; adopt only the server's metadata.
        set((st) => {
          if (st.activeDoc?.id !== doc.id) return { docSaving: false }
          const newer = st.docDraft !== null && st.docDraft !== docDraft
          return {
            activeDoc: newer ? { ...saved, content: st.docDraft as string } : saved,
            docDraft: newer ? st.docDraft : null,
            docSaving: false
          }
        })
        void get().refreshDocs()
        void get().refreshDocRevisions(doc.id)
      } catch (e) {
        set({ docSaving: false })
        get().toast(`Could not save: ${(e as Error).message}`, 'error')
      }
    },
    renameDoc: async (id, title) => {
      if (!title.trim()) return
      try {
        const d = await api.docs.patch(id, { title: title.trim() })
        set((st) => ({ activeDoc: st.activeDoc?.id === id ? { ...st.activeDoc, title: d.title } : st.activeDoc }))
        await get().refreshDocs()
      } catch (e) {
        get().toast((e as Error).message, 'error')
      }
    },
    setDocStar: async (id, starred) => {
      await api.docs.patch(id, { starred })
      await get().refreshDocs()
    },
    setDocFolder: async (id, folder) => {
      try {
        const d = await api.docs.patch(id, { folder: folder.trim() })
        set((st) => ({ activeDoc: st.activeDoc?.id === id ? { ...st.activeDoc, folder: d.folder } : st.activeDoc }))
        await get().refreshDocs()
      } catch (e) {
        get().toast((e as Error).message, 'error')
      }
    },
    deleteDoc: async (id) => {
      await api.docs.delete(id)
      get().closeDocTab(id)
      await Promise.all([get().refreshDocs(), get().refreshDocsPending()])
    },
    setDocMode: (docMode) => set({ docMode }),
    refreshDocRevisions: async (id) => {
      const docId = id ?? get().activeDoc?.id
      if (!docId) return
      try {
        const revs = await api.docs.revisions(docId)
        if (get().activeDoc?.id === docId) set({ docRevisions: revs })
      } catch { /* history is supplementary */ }
    },
    acceptRevision: async (revId) => {
      try {
        // Buffered typing is saved first, so accepting lands on top of it instead of losing it.
        await get().flushDoc()
        const doc = await api.docs.accept(revId)
        set({ activeDoc: doc, docDraft: null })
        get().toast('Revision applied')
        await Promise.all([get().refreshDocRevisions(doc.id), get().refreshDocs(), get().refreshDocsPending()])
      } catch (e) {
        get().toast((e as Error).message, 'error')
      }
    },
    rejectRevision: async (revId) => {
      try {
        const doc = await api.docs.reject(revId)
        set({ activeDoc: doc })
        await Promise.all([get().refreshDocRevisions(doc.id), get().refreshDocs(), get().refreshDocsPending()])
      } catch (e) {
        get().toast((e as Error).message, 'error')
      }
    },
    restoreRevision: async (revId) => {
      try {
        await get().flushDoc()
        const doc = await api.docs.restore(revId)
        set({ activeDoc: doc, docDraft: null })
        get().toast('Document restored')
        await Promise.all([get().refreshDocRevisions(doc.id), get().refreshDocs()])
      } catch (e) {
        get().toast((e as Error).message, 'error')
      }
    },

    refreshMemories: async (q = '') => set({ memories: await api.memories.list(get().dataScope, q) }),
    addMemory: async (content, kind, projectId) => {
      await api.memories.create({ project_id: projectId, content, kind })
      await Promise.all([get().refreshMemories(), get().refreshProjects()])
    },
    updateMemory: async (id, patch) => {
      const m = await api.memories.update(id, patch)
      set((s) => ({ memories: s.memories.map((x) => (x.id === id ? m : x)) }))
      void get().refreshProjects()
    },
    deleteMemory: async (id) => {
      await api.memories.delete(id)
      set((s) => ({ memories: s.memories.filter((m) => m.id !== id) }))
      void get().refreshProjects()
    },

    refreshGraph: async () => set({ graph: await api.graph.get(get().dataScope) }),
    refreshDocuments: async () => set({ documents: await api.documents.list(get().dataScope) }),

    // ---- activity monitor ----
    refreshActivity: async () => {
      try {
        set({ activity: await api.activity.status() })
      } catch {
        /* the panel shows whatever it last had; a failed poll is not worth a toast */
      }
    },
    loadActivity: async () => {
      await get().refreshActivity()
      const [events, summaries, context] = await Promise.all([
        api.activity.events(24, 300).catch(() => []),
        api.activity.summaries(7).catch(() => []),
        api.activity.context().catch(() => null)
      ])
      set({ activityEvents: events, activitySummaries: summaries, activityContext: context })
    },
    setActivityConfig: async (patch) => {
      try {
        set({ activity: await api.activity.config(patch) })
      } catch (e) {
        get().toast((e as Error).message, 'error')
      }
    },
    toggleActivitySignal: async (signal) => {
      const cur = get().activity?.config.signals
      if (!cur) return
      await get().setActivityConfig({ signals: { ...cur, [signal]: !cur[signal] } })
    },
    startActivity: async () => {
      try {
        set({ activity: await api.activity.start() })
        get().toast('Activity monitor on')
      } catch (e) {
        get().toast((e as Error).message, 'error')
      }
    },
    stopActivity: async () => {
      set({ activity: await api.activity.stop() })
      get().toast('Activity monitor off')
    },
    pauseActivity: async (minutes = 30) => set({ activity: await api.activity.pause(minutes) }),
    resumeActivity: async () => set({ activity: await api.activity.resume() }),
    rollupActivity: async () => {
      set({ activityBusy: true })
      try {
        const { summary, status } = await api.activity.rollup()
        set({ activity: status })
        get().toast(summary ? `Summarized: ${summary.headline}` : 'Nothing new to summarize')
        await get().loadActivity()
      } catch (e) {
        get().toast((e as Error).message, 'error')
      } finally {
        set({ activityBusy: false })
      }
    },
    refreshActivityProfile: async () => {
      set({ activityBusy: true })
      try {
        await api.activity.refreshProfile()
        get().toast('Rebuilt the work profile')
        await get().loadActivity()
      } catch (e) {
        get().toast((e as Error).message, 'error')
      } finally {
        set({ activityBusy: false })
      }
    },
    deleteActivityEvent: async (id) => {
      await api.activity.deleteEvent(id)
      set((s) => ({ activityEvents: s.activityEvents.filter((e) => e.id !== id) }))
    },
    deleteActivitySummary: async (id) => {
      await api.activity.deleteSummary(id)
      set((s) => ({ activitySummaries: s.activitySummaries.filter((x) => x.id !== id) }))
      set({ activityContext: await api.activity.context().catch(() => get().activityContext) })
    },
    purgeActivity: async (scope) => {
      const { deleted, status } = await api.activity.purge(scope)
      set({ activity: status })
      get().toast(`Deleted ${deleted.events} samples and ${deleted.summaries} summaries`)
      await get().loadActivity()
    },
    uploadDocuments: async (files, projectId) => {
      for (const f of Array.from(files)) {
        try {
          await api.documents.upload(projectId, f)
          get().toast(`Uploaded ${f.name}`)
        } catch (e) {
          get().toast(`${f.name}: ${(e as Error).message}`, 'error')
        }
      }
      await Promise.all([get().refreshDocuments(), get().refreshProjects()])
    },
    deleteDocument: async (id) => {
      await api.documents.delete(id)
      set((s) => ({ documents: s.documents.filter((d) => d.id !== id) }))
      void get().refreshProjects()
    },

    refreshDashboard: async () => {
      void get().refreshAgentInbox()
      try {
        const dashboard = await api.dashboard()
        set({ dashboard, google: dashboard.google })
      } catch (e) {
        get().toast(`Dashboard: ${(e as Error).message}`, 'error')
      }
    },
    refreshRecap: async (force = false) => {
      set({ recapLoading: true })
      try {
        set({ recap: await api.recap(force) })
      } catch (e) {
        if (force) get().toast(`Recap: ${(e as Error).message}`, 'error')
      } finally {
        set({ recapLoading: false })
      }
    },
    refreshAgentInbox: async () => {
      try {
        set({ agentInbox: await api.inbox() })
      } catch (e) {
        /* the inbox is a card on Today, not the shell: a failed read must not toast on every refresh */
        void e
      }
    },
    refreshJobs: async () => {
      try {
        set({ jobs: await api.jobs.list() })
      } catch (e) {
        get().toast(`Jobs: ${(e as Error).message}`, 'error')
      }
    },
    setJobEnabled: async (id, enabled) => {
      try {
        const job = await api.jobs.update(id, { enabled })
        set((s) => ({ jobs: s.jobs.map((x) => (x.id === id ? job : x)) }))
        void get().refreshAgentInbox()
      } catch (e) {
        get().toast(`Jobs: ${(e as Error).message}`, 'error')
      }
    },
    runJobNow: async (id) => {
      try {
        const { run_id } = await api.jobs.runNow(id)
        get().toast(run_id ? 'Job started. It will show up under “While you were away”.' : 'Job did not start', run_id ? 'info' : 'error')
        void get().refreshJobs()
      } catch (e) {
        get().toast(`Jobs: ${(e as Error).message}`, 'error')
      }
    },
    decideProposal: async (id, accept, args) => {
      try {
        const res = accept ? await api.proposals.accept(id, args) : await api.proposals.reject(id)
        if (accept && !res.ok) get().toast(`That did not go through: ${res.proposal.error ?? 'unknown error'}`, 'error')
        else get().toast(accept ? 'Done — that one actually ran.' : 'Dropped.', 'info')
      } catch (e) {
        get().toast((e as Error).message, 'error')
      } finally {
        void get().refreshAgentInbox()
      }
    },
    approveTool: async (callId, decision, conversationId) => {
      const id = conversationId ?? get().focusedConversationId
      if (!id) return
      try {
        await api.approve(callId, decision)
        // Mark as no longer awaiting in the UI; the tool_result event fills in the rest. The count
        // settles now rather than when the tool returns, since an external action can take seconds.
        patchSession(id, (s) => {
          const conversation = { ...s.conversation, messages: (s.conversation.messages ?? []).map((m) => ({ ...m, tool_events: (m.tool_events ?? []).map((t) => (t.id === callId ? { ...t, needs_approval: false, approval: decision } : t)) })) }
          const pendingApprovals = countApprovals(conversation)
          return { ...s, conversation, pendingApprovals, status: settleApprovals(s.status, pendingApprovals) }
        })
      } catch (e) {
        get().toast((e as Error).message, 'error')
      }
    },
    refreshGoogle: async () => {
      try {
        set({ google: await api.google.status() })
        void api.tools().then((t) => set({ tools: t.tools })).catch(() => undefined)
        void get().refreshTasksSync()
      } catch { /* ignore */ }
    },
    refreshTasksSync: async () => {
      try {
        set({ tasksSync: await api.google.tasksSync() })
      } catch { /* ignore */ }
    },
    setTasksSync: async (patch) => {
      try {
        set({ tasksSync: await api.google.tasksSyncConfig(patch) })
      } catch (e) {
        get().toast((e as Error).message, 'error')
      }
    },
    runTasksSync: async () => {
      set((s) => ({ tasksSync: s.tasksSync && { ...s.tasksSync, syncing: true } }))
      try {
        set({ tasksSync: await api.google.tasksSyncRun() })
        await get().refreshTodos()
        void get().refreshDashboard()
      } catch (e) {
        get().toast((e as Error).message, 'error')
        void get().refreshTasksSync()
      }
    },
    connectGoogle: async () => {
      try {
        // Re-authing an already-connected account looks identical unless we watch for a new
        // token, so remember which one we had before opening the browser.
        const before = get().google?.connected_at ?? null
        const { url } = await api.google.start()
        window.open(url, '_blank')
        // poll until the callback lands
        const started = Date.now()
        const timer = setInterval(async () => {
          const st = await api.google.status().catch(() => null)
          const fresh = !!st?.connected && st.connected_at !== before
          if (fresh || Date.now() - started > 180_000) {
            clearInterval(timer)
            if (fresh && st) {
              set({ google: st })
              get().toast(`Connected ${st.email ?? 'Google account'}`)
              void get().refreshDashboard()
              void api.tools().then((t) => set({ tools: t.tools })).catch(() => undefined)
            }
          }
        }, 1500)
      } catch (e) {
        get().toast((e as Error).message, 'error')
      }
    },
    disconnectGoogle: async () => {
      set({ google: await api.google.disconnect() })
      void get().refreshDashboard()
      void api.tools().then((t) => set({ tools: t.tools })).catch(() => undefined)
    },

    refreshTodos: async (scope = 'all', includeDone = false) => set({ todos: await api.todos.list(scope, includeDone) }),
    addTodo: async (t) => {
      await api.todos.create(t)
      await Promise.all([get().refreshTodos(), get().refreshDashboard()])
    },
    updateTodo: async (id, patch) => {
      const t = await api.todos.update(id, patch)
      set((s) => ({ todos: s.todos.map((x) => (x.id === id ? t : x)), dashboard: s.dashboard && { ...s.dashboard, todos: s.dashboard.todos.map((x) => (x.id === id ? t : x)).filter((x) => !x.done) } }))
    },
    deleteTodo: async (id) => {
      await api.todos.delete(id)
      set((s) => ({ todos: s.todos.filter((x) => x.id !== id), dashboard: s.dashboard && { ...s.dashboard, todos: s.dashboard.todos.filter((x) => x.id !== id) } }))
    }
  }
})

/**
 * Pin a session for as long as a surface is showing it, and release it on unmount. A retained
 * session is never an LRU victim, which is the only protection a canvas window has: the canvas view
 * does not focus conversations, and a widget's loader effect only re-runs when its `ref_id` changes.
 */
export const retainSession = (conversationId: string): (() => void) => {
  retained.set(conversationId, (retained.get(conversationId) ?? 0) + 1)
  return () => {
    const n = (retained.get(conversationId) ?? 0) - 1
    if (n > 0) return void retained.set(conversationId, n)
    retained.delete(conversationId)
    // A window that was open until now is the most recently touched, not the stalest.
    useStore.setState((s) => (s.sessions[conversationId] ? { sessions: { ...s.sessions, [conversationId]: { ...s.sessions[conversationId], touchedAt: Date.now() } } } : {}))
  }
}

export const useProject = (id: string | null | undefined): Project | undefined =>
  useStore((s) => (id ? s.projects.find((p) => p.id === id) : undefined))

const pick = (s: State, convId?: string): ChatSession | undefined => s.sessions[convId ?? s.focusedConversationId ?? '']

/** The focused conversation: what `active` used to be. Every selector below returns state as-is. */
export const selectActive = (s: State): Conversation | null => pick(s)?.conversation ?? null

export const useSession = (convId?: string): ChatSession | undefined => useStore((s) => pick(s, convId))
export const useConversation = (convId?: string): Conversation | null => useStore((s) => pick(s, convId)?.conversation ?? null)
export const useSessionStatus = (convId?: string): SessionStatus => useStore((s) => pick(s, convId)?.status ?? 'idle')
export const useIsStreaming = (convId?: string): boolean => useStore((s) => !!pick(s, convId)?.streaming)
export const useStreamingMessageId = (convId?: string): string | null => useStore((s) => pick(s, convId)?.streaming?.messageId ?? null)
export const useUnread = (convId?: string): number => useStore((s) => pick(s, convId)?.unread ?? 0)
