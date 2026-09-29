import { create } from 'zustand'
import type { ChatEvent, ChatRunStarted, Conversation, ConversationSettings, Document, GraphData, Memory, Message, ModelInfo, Settings, Project, RunConflict, SessionStatus, ToolInfo, Todo, GoogleStatus, TodayDashboard, Recap } from '@shared/types'
import { api, chatStream, setBase, type Scope } from './lib/api'
import { finishStatus, reduceStatus, settleApprovals } from './sessionStatus'

export type View = 'home' | 'chat' | 'todos' | 'calendar' | 'boards' | 'dashboards' | 'memory' | 'documents' | 'project'
/** How the Memory panel lays out its two halves: the memory list and the knowledge graph. */
export type MemoryMode = 'split' | 'list' | 'graph'
export type ContextTab = 'last' | 'preview' | 'trace'
/** Classic is the single-pane router; canvas is the floating-window desktop. */
export type Mode = 'classic' | 'canvas'
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
  dashboard: TodayDashboard | null
  todos: Todo[]
  recap: Recap | null
  recapLoading: boolean

  projects: Project[]
  personalStats: Project['stats']

  mode: Mode
  view: View
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

  init: () => Promise<void>
  loadModels: () => Promise<void>
  saveSettings: (patch: Partial<Settings>) => Promise<void>
  toggleMode: () => void
  setView: (v: View) => void
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
  selectChat: (id: string | null) => Promise<void>
  /** Load a conversation into `sessions` without focusing it: one canvas chat window per call. */
  openSession: (conversationId: string) => Promise<void>
  /** `openSession`, plus attach to a reply already in flight elsewhere so the window paints amber. */
  attachSession: (conversationId: string) => Promise<void>
  /** Drop a session and abort whatever it was streaming. */
  closeSession: (conversationId: string) => void
  /** Called on focus: clears unread and maps done/error back to idle, but never needs-approval. */
  clearSessionStatus: (conversationId: string) => void
  deleteChat: (id: string) => Promise<void>
  renameChat: (id: string, title: string) => Promise<void>
  setChatModel: (model: string, conversationId?: string) => Promise<void>
  setChatSettings: (patch: Partial<ConversationSettings>, conversationId?: string) => Promise<void>
  send: (text: string, conversationId?: string) => Promise<void>
  regenerate: (conversationId?: string) => Promise<void>
  stop: (conversationId?: string) => Promise<void>

  refreshMemories: (q?: string) => Promise<void>
  addMemory: (content: string, kind: string, projectId: string | null) => Promise<void>
  updateMemory: (id: string, patch: Parameters<typeof api.memories.update>[1]) => Promise<void>
  deleteMemory: (id: string) => Promise<void>

  refreshGraph: () => Promise<void>
  refreshDocuments: () => Promise<void>
  refreshDashboard: () => Promise<void>
  refreshRecap: (force?: boolean) => Promise<void>
  approveTool: (callId: string, decision: 'allow' | 'deny' | 'always_chat' | 'always_global', conversationId?: string) => Promise<void>
  refreshGoogle: () => Promise<void>
  connectGoogle: () => Promise<void>
  disconnectGoogle: () => Promise<void>
  refreshTodos: (scope?: Scope, includeDone?: boolean) => Promise<void>
  addTodo: (t: { title: string; project_id?: string | null; due?: string | null; priority?: number; notes?: string }) => Promise<void>
  updateTodo: (id: string, patch: Parameters<typeof api.todos.update>[1]) => Promise<void>
  deleteTodo: (id: string) => Promise<void>
  uploadDocuments: (files: FileList | File[], projectId: string | null) => Promise<void>
  deleteDocument: (id: string) => Promise<void>
}

let toastSeq = 0

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

/** LRU by `touchedAt`, never evicting the focused session, a live run, or unread replies. */
const evict = (sessions: Record<string, ChatSession>, keepId: string | null): Record<string, ChatSession> => {
  const ids = Object.keys(sessions)
  if (ids.length <= MAX_SESSIONS) return sessions
  const victims = ids
    .filter((id) => id !== keepId && !sessions[id].streaming && !sessions[id].unread)
    .sort((a, b) => sessions[a].touchedAt - sessions[b].touchedAt)
    .slice(0, ids.length - MAX_SESSIONS)
  if (!victims.length) return sessions
  const out = { ...sessions }
  for (const id of victims) {
    clearHold(id)
    delete out[id]
  }
  return out
}

/** Every conversation mutation a stream event makes, as one new session. No side effects. */
const applyEvent = (s: ChatSession, ev: ChatEvent, focused: boolean): ChatSession => {
  const c = s.conversation
  const msgs = c.messages ?? []
  const withMsgs = (messages: Message[]): ChatSession => ({ ...s, conversation: { ...c, messages } })
  const mapMsg = (mid: string, fn: (m: Message) => Message): ChatSession =>
    withMsgs(msgs.map((m) => (m.id === mid ? fn(m) : m)))
  switch (ev.event) {
    case 'user_message':
      return withMsgs([...msgs, ev.data])
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
      const sessions = { ...st.sessions, [conversation.id]: cur ? { ...cur, conversation } : newSession(conversation) }
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
    const abort = new AbortController()
    const attached = from.attached
    clearHold(convId)
    patchSession(convId, (s) => ({ ...s, streaming: { messageId: from.messageId, runId: run.run_id, abort }, status: settleApprovals('working', from.approvals), finishedAt: null, pendingApprovals: from.approvals, touchedAt: Date.now() }))
    try {
      for await (const ev of chatStream(convId, run.seq, abort.signal)) {
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
            const { memories, nodes, edges } = ev.data
            get().toast(`Learned ${memories.length} memor${memories.length === 1 ? 'y' : 'ies'}, ${nodes.length} entities, ${edges.length} relations`, 'learned')
            if (memories.length + nodes.length + edges.length) refreshAll()
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

  const runStream = async (convId: string, body: { content?: string; model?: string }): Promise<void> => {
    let run: ChatRunStarted
    let attached = false
    try {
      run = await api.chat(convId, body)
    } catch (e) {
      const conflict = runConflict(e)
      if (!conflict) {
        get().toast((e as Error).message, 'error')
        patchSession(convId, (s) => ({ ...s, status: 'error', finishedAt: Date.now() }))
        return
      }
      // Another window already started this reply: watch it from its tail rather than erroring.
      run = { run_id: conflict.run_id, seq: conflict.seq }
      attached = true
    }
    await watchRun(convId, run, { messageId: null, approvals: 0, attached })
  }

  return {
    ready: false,
    backendError: null,
    settings: { baseUrl: '', apiKey: '', defaultModel: '', systemPrompt: '', extractionModel: '', autoLearn: true, theme: 'dark', gatherShortcut: '', tools: {}, maxToolRounds: 8, braveApiKey: '', tavilyApiKey: '', googleClientId: '', googleClientSecret: '', modelPrices: {} },
    models: [],
    modelsError: null,
    tools: [],
    google: null,
    dashboard: null,
    todos: [],
    recap: null,
    recapLoading: false,
    projects: [],
    personalStats: undefined,
    mode: 'classic',
    view: 'home',
    memoryMode: 'split',
    projectViewId: null,
    draftProjectId: null,
    libraryScope: 'all',
    dataScope: 'all',
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

    init: async () => {
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
      set({ settings, projects, personalStats, conversations, ready: true, settingsOpen: !settings.apiKey && conversations.length === 0 })
      void get().loadModels()
      void get().loadScope('all')
      void api.tools().then((t) => set({ tools: t.tools })).catch(() => undefined)
      void get().refreshDashboard()
      void get().refreshTodos()
      void get().refreshRecap()
      window.os.onMenu((action) => {
        const s = get()
        if (action === 'new-chat') s.newChat(s.view === 'project' ? s.projectViewId : selectActive(s)?.project_id ?? null)
        else if (action === 'toggle-mode') s.toggleMode()
        else if (action === 'settings') s.setSettingsOpen(true)
        else if (action === 'toggle-sidebar') s.toggleSidebar()
        else if (action === 'toggle-context') s.toggleContext()
        else if (action === 'view:graph') s.openMemory('graph')
        else if (action.startsWith('view:')) s.setView(action.slice(5) as View)
        else if (action === 'upload') {
          s.setView('documents')
          setTimeout(() => document.getElementById('doc-upload-input')?.click(), 100)
        }
      })
    },

    loadModels: async () => {
      try {
        set({ models: await api.models(), modelsError: null })
      } catch (e) {
        set({ models: [], modelsError: (e as Error).message })
      }
    },
    saveSettings: async (patch) => {
      set({ settings: await api.settings.set(patch) })
      if ('baseUrl' in patch || 'apiKey' in patch) void get().loadModels()
      if ('googleClientId' in patch || 'googleClientSecret' in patch) void get().refreshGoogle()
    },
    toggleMode: () => set((s) => ({ mode: s.mode === 'canvas' ? 'classic' : 'canvas' })),
    setView: (view) => {
      set({ view })
      if (view === 'home') void get().refreshDashboard()
      if (view === 'todos') void get().refreshTodos()
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
      await Promise.all([get().refreshMemories(), get().refreshGraph(), get().refreshDocuments()])
    },

    refreshConversations: async () => set({ conversations: await api.conversations.list('all') }),
    newChat: (projectId = null) => set({ focusedConversationId: null, draftProjectId: projectId, view: 'chat', settingsOpen: false }),
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
    openSession: async (conversationId) => {
      if (get().sessions[conversationId]?.streaming) return
      putSession(await api.conversations.get(conversationId))
    },
    attachSession: async (conversationId) => {
      if (get().sessions[conversationId]?.streaming) return
      // A run started before this window existed: `GET /runs` is the only way it can know.
      const runs = await api.runs().catch(() => null)
      const run = runs?.find((r) => r.conversation_id === conversationId && r.live)
      await get().openSession(conversationId)
      const s = get().sessions[conversationId]
      if (!run || !s || s.streaming) return
      // From the run's own seq, so the tail streams live and no past delta is applied twice.
      await watchRun(conversationId, { run_id: run.run_id, seq: run.seq }, { messageId: run.message_id, approvals: countApprovals(s.conversation), attached: true })
    },
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
      if (!text.trim()) return
      const id = conversationId ?? get().focusedConversationId
      if (id) {
        if (get().sessions[id]?.streaming) return
        if (!get().sessions[id]) await get().openSession(id)
        return runStream(id, { content: text })
      }
      const c = await api.conversations.create(get().draftProjectId, get().settings.defaultModel)
      c.messages = []
      putSession(c)
      set({ focusedConversationId: c.id, view: 'chat' })
      void get().refreshProjects()
      await runStream(c.id, { content: text })
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
      } catch { /* ignore */ }
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
