import { create } from 'zustand'
import type { Conversation, ConversationSettings, Document, GraphData, Memory, Message, ModelInfo, Settings, Project, ToolInfo, Todo, GoogleStatus, TodayDashboard, Recap } from '@shared/types'
import { api, chatStream, setBase, type Scope } from './lib/api'

export type View = 'home' | 'chat' | 'todos' | 'calendar' | 'boards' | 'dashboards' | 'memory' | 'documents' | 'project'
/** How the Memory panel lays out its two halves: the memory list and the knowledge graph. */
export type MemoryMode = 'split' | 'list' | 'graph'
export type ContextTab = 'last' | 'preview' | 'trace'
export type { Scope }

interface Streaming { conversationId: string; messageId: string | null; abort: AbortController }
interface Toast { id: number; text: string; kind: 'info' | 'error' | 'learned' }

interface State {
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
  activeId: string | null
  active: Conversation | null
  streaming: Streaming | null

  memories: Memory[]
  graph: GraphData
  documents: Document[]

  init: () => Promise<void>
  loadModels: () => Promise<void>
  saveSettings: (patch: Partial<Settings>) => Promise<void>
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
  deleteChat: (id: string) => Promise<void>
  renameChat: (id: string, title: string) => Promise<void>
  setChatModel: (model: string) => Promise<void>
  setChatSettings: (patch: Partial<ConversationSettings>) => Promise<void>
  send: (text: string) => Promise<void>
  regenerate: () => Promise<void>
  stop: () => Promise<void>

  refreshMemories: (q?: string) => Promise<void>
  addMemory: (content: string, kind: string, projectId: string | null) => Promise<void>
  updateMemory: (id: string, patch: Parameters<typeof api.memories.update>[1]) => Promise<void>
  deleteMemory: (id: string) => Promise<void>

  refreshGraph: () => Promise<void>
  refreshDocuments: () => Promise<void>
  refreshDashboard: () => Promise<void>
  refreshRecap: (force?: boolean) => Promise<void>
  approveTool: (callId: string, decision: 'allow' | 'deny' | 'always_chat' | 'always_global') => Promise<void>
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

export const useStore = create<State>((set, get) => {
  const patchActive = (fn: (c: Conversation) => Conversation): void => {
    const a = get().active
    if (a) set({ active: fn(a) })
  }
  const patchMessage = (mid: string, fn: (m: Message) => Message): void =>
    patchActive((c) => ({ ...c, messages: (c.messages ?? []).map((m) => (m.id === mid ? fn(m) : m)) }))
  const refreshAll = (): void => {
    void get().refreshMemories()
    void get().refreshGraph()
    void get().refreshDocuments()
    void get().refreshProjects()
  }

  const runStream = async (convId: string, body: { content?: string; model?: string }): Promise<void> => {
    const abort = new AbortController()
    set({ streaming: { conversationId: convId, messageId: null, abort } })
    try {
      for await (const ev of chatStream(convId, body, abort.signal)) {
        if (get().active?.id !== convId) continue
        switch (ev.event) {
          case 'user_message':
          case 'assistant_message':
            patchActive((c) => ({ ...c, messages: [...(c.messages ?? []), ev.data] }))
            if (ev.event === 'assistant_message') set((s) => ({ streaming: s.streaming && { ...s.streaming, messageId: ev.data.id } }))
            break
          case 'title':
            patchActive((c) => ({ ...c, title: ev.data.title }))
            break
          case 'removed_message':
            patchActive((c) => ({ ...c, messages: (c.messages ?? []).filter((m) => m.id !== ev.data.id) }))
            break
          case 'delta':
            patchMessage(ev.data.id, (m) => ({ ...m, content: m.content + ev.data.text }))
            break
          case 'tool_call':
            patchMessage(ev.data.message_id, (m) => ({ ...m, tool_events: [...(m.tool_events ?? []), { id: ev.data.id, name: ev.data.name, arguments: ev.data.arguments, result_preview: '', duration_ms: 0, error: null, pending: true, needs_approval: !!ev.data.needs_approval }] }))
            break
          case 'tool_result':
            patchMessage(ev.data.message_id, (m) => ({ ...m, tool_events: (m.tool_events ?? []).map((t) => (t.id === ev.data.id ? { ...ev.data, pending: false } : t)) }))
            break
          case 'span':
            patchMessage(ev.data.message_id, (m) => {
              const trace = m.trace ?? []
              const i = trace.findIndex((s) => s.id === ev.data.span.id)
              return { ...m, trace: i >= 0 ? trace.map((s, j) => (j === i ? ev.data.span : s)) : [...trace, ev.data.span] }
            })
            break
          case 'done':
            patchMessage(ev.data.id, (m) => ({ ...m, error: ev.data.error, context_used: ev.data.context_used, tool_events: ev.data.tool_events?.length ? ev.data.tool_events : m.tool_events, trace: ev.data.trace?.length ? ev.data.trace : m.trace }))
            set({ streaming: null })
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
            set({ streaming: null })
            break
        }
      }
    } catch (e) {
      if (!abort.signal.aborted) get().toast((e as Error).message, 'error')
    } finally {
      if (get().streaming?.abort === abort) set({ streaming: null })
    }
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
    activeId: null,
    active: null,
    streaming: null,
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
        if (action === 'new-chat') s.newChat(s.view === 'project' ? s.projectViewId : s.active?.project_id ?? null)
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
      set((s) => ({
        view: s.view === 'project' && s.projectViewId === id ? 'chat' : s.view,
        projectViewId: s.projectViewId === id ? null : s.projectViewId,
        draftProjectId: s.draftProjectId === id ? null : s.draftProjectId,
        activeId: s.active?.project_id === id ? null : s.activeId,
        active: s.active?.project_id === id ? null : s.active
      }))
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
    newChat: (projectId = null) => set({ activeId: null, active: null, draftProjectId: projectId, view: 'chat', settingsOpen: false }),
    selectChat: async (id) => {
      set({ view: 'chat', settingsOpen: false, traceMessageId: null })
      if (!id) return set({ activeId: null, active: null })
      set({ activeId: id })
      const c = await api.conversations.get(id)
      if (get().activeId === id) set({ active: c, draftProjectId: c.project_id })
    },
    deleteChat: async (id) => {
      await api.conversations.delete(id)
      set((s) => ({
        conversations: s.conversations.filter((c) => c.id !== id),
        activeId: s.activeId === id ? null : s.activeId,
        active: s.activeId === id ? null : s.active
      }))
      void get().refreshProjects()
    },
    renameChat: async (id, title) => {
      if (!title.trim()) return
      await api.conversations.patch(id, { title: title.trim() })
      patchActive((c) => ({ ...c, title: title.trim() }))
      await get().refreshConversations()
    },
    setChatModel: async (model) => {
      const a = get().active
      if (!a) return void (await get().saveSettings({ defaultModel: model }))
      await api.conversations.patch(a.id, { model })
      patchActive((c) => ({ ...c, model }))
    },
    setChatSettings: async (patch) => {
      const a = get().active
      if (!a) return
      const c = await api.conversations.patch(a.id, { settings: patch })
      patchActive((cur) => ({ ...cur, settings: c.settings }))
    },

    send: async (text) => {
      if (get().streaming || !text.trim()) return
      let a = get().active
      if (!a) {
        a = await api.conversations.create(get().draftProjectId, get().settings.defaultModel)
        a.messages = []
        set({ active: a, activeId: a.id, view: 'chat' })
        void get().refreshProjects()
      }
      await runStream(a.id, { content: text })
    },
    regenerate: async () => {
      const a = get().active
      if (!a || get().streaming) return
      await runStream(a.id, {})
    },
    stop: async () => {
      const st = get().streaming
      if (!st) return
      if (st.messageId) await api.stop(st.messageId).catch(() => undefined)
      else st.abort.abort()
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
    approveTool: async (callId, decision) => {
      try {
        await api.approve(callId, decision)
        // mark as no longer awaiting in the UI; the tool_result event will fill in the rest
        patchActive((c) => ({ ...c, messages: (c.messages ?? []).map((m) => ({ ...m, tool_events: (m.tool_events ?? []).map((t) => (t.id === callId ? { ...t, needs_approval: false, approval: decision } : t)) })) }))
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
