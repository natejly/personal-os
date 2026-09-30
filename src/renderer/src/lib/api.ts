import type {
  ChatEvent, ToolInfo, Todo, GoogleStatus, TodayDashboard, CalendarEvent, GmailMessage, GmailFullMessage, GmailLabel, GoogleTask, GoogleTaskList, TasksSyncStatus, DriveFile, Board, BoardCard, BoardColumn, DataSource, Dashboard, Widget, Recap, Conversation, ConversationSettings, ContextUsed, Document, GraphData, GraphEdge, GraphNode, Message,
  Memory, ModelInfo, ModelPrice, Settings, Project, UsageReport, ChatRunStarted, RunInfo,
  Canvas, CanvasWindow, Note, PopoutBounds, Rect, SnapMode, WidgetKind, WindowLayout, WindowState,
  Doc, FullDoc, DocRevision,
  ActivityConfig, ActivityContextFile, ActivityEvent, ActivityStatus, ActivitySummary
} from '@shared/types'

let base = ''
let token = ''
let tokenP: Promise<void> | null = null

export const setBase = (url: string): void => {
  base = url.replace(/\/+$/, '')
  tokenP = window.os
    .backendToken()
    .then((t) => {
      token = t
    })
    .catch(() => {
      token = ''
    })
}
export const getBase = (): string => base
/** The resolved token, for callers that cannot await (keepalive writes on unload). '' until setBase() resolves it. */
export const getToken = (): string => token

/** Sidecar shared secret. Resolved once per setBase(); every backend request carries it. */
const auth = async (): Promise<Record<string, string>> => {
  if (!token && tokenP) await tokenP
  return token ? { 'X-Personal-OS-Token': token } : {}
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  // The token wait only suspends while setBase() is still resolving it. Once it is (or when there is no
  // sidecar at all, as in tests), a req() runs synchronously up to its fetch — the canvas store's
  // flush-before-space-switch depends on that.
  if (!token && tokenP) await tokenP
  const r = await fetch(`${base}${path}`, {
    ...init,
    headers: { ...(init?.body && !(init.body instanceof FormData) ? { 'Content-Type': 'application/json' } : {}), ...(init?.headers ?? {}), ...(token ? { 'X-Personal-OS-Token': token } : {}) }
  })
  if (!r.ok) {
    let msg = `${r.status} ${r.statusText}`
    try {
      const j = await r.json()
      msg = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail ?? j)
    } catch {
      /* ignore */
    }
    throw new Error(msg)
  }
  return (await r.json()) as T
}

const json = (v: unknown): string => JSON.stringify(v)
/** Scope filter: 'all' = everything, 'personal' = items in no project, or a project id (that project only). */
export type Scope = 'all' | 'personal' | string
const scope = (s: Scope): string => `project_id=${encodeURIComponent(s)}&include_global=false`

export const api = {
  health: () => req<{ ok: boolean; data_dir: string }>('/health'),
  settings: {
    get: () => req<Settings>('/settings'),
    set: (patch: Partial<Settings>) => req<Settings>('/settings', { method: 'PUT', body: json(patch) })
  },
  models: () => req<ModelInfo[]>('/models'),
  tools: () => req<{ tools: ToolInfo[]; enabled: Record<string, boolean> }>('/tools'),
  dashboard: () => req<TodayDashboard>('/dashboard'),
  recap: (force = false) => req<Recap>(`/recap?force=${force}`),
  approve: (callId: string, decision: 'allow' | 'deny' | 'always_chat' | 'always_global') => req(`/approvals/${callId}`, { method: 'POST', body: json({ decision }) }),
  boards: {
    list: () => req<Board[]>('/boards'),
    get: (id: string) => req<Board>(`/boards/${id}`),
    create: (b: { name: string; project_id?: string | null; columns?: string[] }) => req<Board>('/boards', { method: 'POST', body: json(b) }),
    update: (id: string, patch: { name?: string; project_id?: string | null }) => req<Board>(`/boards/${id}`, { method: 'PUT', body: json(patch) }),
    delete: (id: string) => req(`/boards/${id}`, { method: 'DELETE' }),
    addColumn: (id: string, name: string) => req<BoardColumn>(`/boards/${id}/columns`, { method: 'POST', body: json({ name }) }),
    updateColumn: (cid: string, patch: { name?: string; position?: number; wip_limit?: number | null }) => req(`/boards/columns/${cid}`, { method: 'PUT', body: json(patch) }),
    deleteColumn: (cid: string) => req(`/boards/columns/${cid}`, { method: 'DELETE' }),
    addCard: (id: string, c: { title: string; column_id?: string | null; description?: string; due?: string | null; priority?: number; labels?: string[] }) => req<BoardCard>(`/boards/${id}/cards`, { method: 'POST', body: json(c) }),
    updateCard: (cid: string, patch: { title?: string; description?: string; due?: string; priority?: number; labels?: string[]; clear_due?: boolean }) => req<BoardCard>(`/boards/cards/${cid}`, { method: 'PUT', body: json(patch) }),
    moveCard: (cid: string, column_id: string, before_card_id: string | null = null) => req<BoardCard>(`/boards/cards/${cid}/move`, { method: 'POST', body: json({ column_id, before_card_id }) }),
    deleteCard: (cid: string) => req(`/boards/cards/${cid}`, { method: 'DELETE' })
  },
  sources: {
    list: () => req<{ sources: DataSource[]; internal: string[] }>('/sources'),
    create: (s: { name: string; kind: string; config: Record<string, unknown>; secret?: string; description?: string }) => req<DataSource>('/sources', { method: 'POST', body: json(s) }),
    update: (id: string, patch: Record<string, unknown>) => req<DataSource>(`/sources/${id}`, { method: 'PUT', body: json(patch) }),
    delete: (id: string) => req(`/sources/${id}`, { method: 'DELETE' }),
    fetch: (id: string) => req<unknown>(`/sources/${id}/fetch`)
  },
  dashboards: {
    list: () => req<Dashboard[]>('/dashboards'),
    get: (id: string) => req<Dashboard>(`/dashboards/${id}`),
    create: (d: { name: string; description?: string }) => req<Dashboard>('/dashboards', { method: 'POST', body: json(d) }),
    delete: (id: string) => req(`/dashboards/${id}`, { method: 'DELETE' }),
    addWidget: (id: string, w: { kind: string; title?: string; prompt?: string; source_ids?: string[]; code?: string; output?: string; width?: number; height?: number }) => req<Widget>(`/dashboards/${id}/widgets`, { method: 'POST', body: json(w) })
  },
  /** AI dashboard widgets (`/widgets/{id}`). Not `api.windows`, which is a canvas window. */
  widgets: {
    update: (id: string, patch: Record<string, unknown>) => req<Widget>(`/widgets/${id}`, { method: 'PUT', body: json(patch) }),
    refresh: (id: string, regenerate = false) => req<Widget>(`/widgets/${id}/refresh?regenerate=${regenerate}`, { method: 'POST' }),
    revise: (id: string, instruction: string) => req<Widget>(`/widgets/${id}/revise`, { method: 'POST', body: json({ instruction }) }),
    delete: (id: string) => req(`/widgets/${id}`, { method: 'DELETE' })
  },
  todos: {
    list: (s: Scope = 'all', includeDone = false, q = '') => req<Todo[]>(`/todos?project_id=${encodeURIComponent(s)}&include_done=${includeDone}&q=${encodeURIComponent(q)}`),
    create: (t: { title: string; project_id?: string | null; notes?: string; due?: string | null; priority?: number }) => req<Todo>('/todos', { method: 'POST', body: json(t) }),
    update: (id: string, patch: { title?: string; notes?: string; due?: string | null; priority?: number; done?: boolean; project_id?: string | null; clear_due?: boolean; clear_project?: boolean; calendar_event_id?: string | null; calendar_link?: string | null }) =>
      req<Todo>(`/todos/${id}`, { method: 'PUT', body: json(patch) }),
    delete: (id: string) => req(`/todos/${id}`, { method: 'DELETE' })
  },
  google: {
    status: () => req<GoogleStatus>('/integrations/google/status'),
    start: () => req<{ url: string }>('/integrations/google/auth/start', { method: 'POST' }),
    disconnect: () => req<GoogleStatus>('/integrations/google/disconnect', { method: 'POST' }),
    calendar: (days = 2) => req<CalendarEvent[]>(`/integrations/google/calendar?days=${days}`),
    calendarRange: (startIso: string, days = 7) => req<CalendarEvent[]>(`/integrations/google/calendar?days=${days}&start=${encodeURIComponent(startIso)}`),
    createEvent: (e: { summary: string; start: string; end?: string; description?: string; location?: string }) =>
      req<{ id: string; link: string; summary: string }>('/integrations/google/calendar', { method: 'POST', body: json(e) }),
    gmail: (q = 'is:unread in:inbox newer_than:14d', maxResults = 12) => req<GmailMessage[]>(`/integrations/google/gmail?q=${encodeURIComponent(q)}&max_results=${maxResults}`),
    tasks: (showCompleted = false) => req<GoogleTask[]>(`/integrations/google/tasks?show_completed=${showCompleted}`),
    tasklists: () => req<GoogleTaskList[]>('/integrations/google/tasklists'),
    tasksSync: () => req<TasksSyncStatus>('/integrations/google/tasks-sync'),
    tasksSyncConfig: (patch: { enabled?: boolean; tasklist?: string; intervalMinutes?: number }) =>
      req<TasksSyncStatus>('/integrations/google/tasks-sync', { method: 'PUT', body: json(patch) }),
    tasksSyncRun: () => req<TasksSyncStatus>('/integrations/google/tasks-sync/run', { method: 'POST' }),
    drive: (q = '', maxResults = 20) => req<DriveFile[]>(`/integrations/google/drive?q=${encodeURIComponent(q)}&max_results=${maxResults}`),
    gmailGet: (id: string) => req<GmailFullMessage>(`/integrations/google/gmail/${id}`),
    gmailLabels: () => req<GmailLabel[]>('/integrations/google/gmail/labels'),
    gmailModify: (id: string, patch: { mark_read?: boolean; archive?: boolean; star?: boolean }) =>
      req<{ ok: boolean }>(`/integrations/google/gmail/${id}/modify`, { method: 'POST', body: json(patch) }),
    gmailDraft: (m: { to: string; subject: string; body: string; reply_to_message_id?: string | null }) =>
      req<{ draft_id: string }>('/integrations/google/gmail/draft', { method: 'POST', body: json(m) }),
    gmailSend: (m: { to: string; subject: string; body: string; reply_to_message_id?: string | null }) =>
      req<{ sent: string }>('/integrations/google/gmail/send', { method: 'POST', body: json(m) })
  },
  assist: {
    complete: (p: { kind: string; before: string; after?: string; context?: string }) =>
      req<{ completion: string }>('/assist/complete', { method: 'POST', body: json(p) }),
    mailReview: (p: { to?: string; subject?: string; body: string; reply_context?: string }) =>
      req<{ feedback: string[]; revised: string }>('/assist/mail-review', { method: 'POST', body: json(p) })
  },
  projects: {
    list: () => req<Project[]>('/projects'),
    create: (s: Pick<Project, 'name' | 'description' | 'system_prompt' | 'color'>) => req<Project>('/projects', { method: 'POST', body: json(s) }),
    update: (id: string, patch: Partial<Project>) => req<Project>(`/projects/${id}`, { method: 'PUT', body: json(patch) }),
    delete: (id: string) => req(`/projects/${id}`, { method: 'DELETE' }),
    globalStats: () => req<NonNullable<Project['stats']>>('/projects/global/stats')
  },
  conversations: {
    list: (s: Scope = 'all') => req<Conversation[]>(`/conversations?${scope(s)}`),
    get: (id: string) => req<Conversation>(`/conversations/${id}`),
    create: (projectId: string | null, model?: string) => req<Conversation>('/conversations', { method: 'POST', body: json({ project_id: projectId, model }) }),
    patch: (id: string, patch: { title?: string; model?: string; settings?: Partial<ConversationSettings> }) =>
      req<Conversation>(`/conversations/${id}`, { method: 'PATCH', body: json(patch) }),
    delete: (id: string) => req(`/conversations/${id}`, { method: 'DELETE' }),
    deleteMessage: (id: string, mid: string) => req(`/conversations/${id}/messages/${mid}`, { method: 'DELETE' })
  },
  stop: (mid: string) => req(`/messages/${mid}/stop`, { method: 'POST' }),
  /** Starts the reply as a background task and returns at once; watch it with `chatStream(convId, seq)`. Throws a 409 carrying a `RunConflict` when that conversation already has a live run. */
  chat: (convId: string, body: { content?: string; model?: string }) => req<ChatRunStarted>(`/conversations/${convId}/chat`, { method: 'POST', body: json(body) }),
  /** Injects a user message into a live run (steering). Throws a 409 when nothing is running. */
  steer: (convId: string, content: string) => req<{ ok: boolean; run_id: string; message: Message }>(`/conversations/${convId}/steer`, { method: 'POST', body: json({ content }) }),
  runs: () => req<RunInfo[]>('/runs'),
  /** Stops a run before its assistant message exists. Detaching the stream would only drop a viewer. */
  stopRun: (convId: string, runId?: string) => req<{ ok: boolean }>(`/conversations/${convId}/stop${runId ? `?run_id=${encodeURIComponent(runId)}` : ''}`, { method: 'POST' }),
  usage: {
    report: (days = 30) => req<UsageReport>(`/usage?days=${days}`),
    setPrices: (modelPrices: Record<string, { input: number; output: number }>) =>
      req<{ repriced: number; prices: Record<string, ModelPrice> }>('/usage/prices', { method: 'PUT', body: json({ modelPrices }) })
  },
  contextPreview: (projectId: string | null, query: string, convSettings?: Partial<ConversationSettings>) =>
    req<ContextUsed>('/context/preview', { method: 'POST', body: json({ project_id: projectId, query, conv_settings: convSettings ?? {} }) }),
  memories: {
    list: (s: Scope, q = '') => req<Memory[]>(`/memories?${scope(s)}&q=${encodeURIComponent(q)}`),
    create: (m: { project_id: string | null; content: string; kind?: string; pinned?: boolean }) => req<Memory>('/memories', { method: 'POST', body: json(m) }),
    update: (id: string, patch: { content?: string; kind?: string; pinned?: boolean; project_id?: string | null; move_to_global?: boolean }) =>
      req<Memory>(`/memories/${id}`, { method: 'PUT', body: json(patch) }),
    delete: (id: string) => req(`/memories/${id}`, { method: 'DELETE' })
  },
  graph: {
    get: (s: Scope) => req<GraphData>(`/graph?${scope(s)}`),
    createNode: (n: { project_id: string | null; label: string; type?: string; properties?: Record<string, unknown> }) =>
      req<GraphNode>('/graph/nodes', { method: 'POST', body: json(n) }),
    updateNode: (id: string, patch: Partial<Pick<GraphNode, 'label' | 'type' | 'properties'>>) => req<GraphNode>(`/graph/nodes/${id}`, { method: 'PUT', body: json(patch) }),
    deleteNode: (id: string) => req(`/graph/nodes/${id}`, { method: 'DELETE' }),
    createEdge: (e: { project_id: string | null; source_id: string; target_id: string; relation: string }) => req<GraphEdge>('/graph/edges', { method: 'POST', body: json(e) }),
    updateEdge: (id: string, patch: Partial<Pick<GraphEdge, 'relation' | 'properties'>>) => req<GraphEdge>(`/graph/edges/${id}`, { method: 'PUT', body: json(patch) }),
    deleteEdge: (id: string) => req(`/graph/edges/${id}`, { method: 'DELETE' })
  },
  documents: {
    list: (s: Scope) => req<Document[]>(`/documents?${scope(s)}`),
    get: (id: string) => req<Document>(`/documents/${id}`),
    upload: (projectId: string | null, file: File) => {
      const fd = new FormData()
      fd.append('file', file)
      if (projectId) fd.append('project_id', projectId)
      return req<Document>('/documents', { method: 'POST', body: fd })
    },
    delete: (id: string) => req(`/documents/${id}`, { method: 'DELETE' })
  },
  activity: {
    status: () => req<ActivityStatus>('/activity/status'),
    config: (patch: Partial<ActivityConfig>) => req<ActivityStatus>('/activity/config', { method: 'PUT', body: json(patch) }),
    start: () => req<ActivityStatus>('/activity/start', { method: 'POST' }),
    stop: () => req<ActivityStatus>('/activity/stop', { method: 'POST' }),
    pause: (minutes = 30) => req<ActivityStatus>('/activity/pause', { method: 'POST', body: json({ minutes }) }),
    resume: () => req<ActivityStatus>('/activity/resume', { method: 'POST' }),
    events: (hours = 24, limit = 200, kind = '') => req<ActivityEvent[]>(`/activity/events?hours=${hours}&limit=${limit}&kind=${encodeURIComponent(kind)}`),
    deleteEvent: (id: string) => req(`/activity/events/${id}`, { method: 'DELETE' }),
    summaries: (days = 7) => req<ActivitySummary[]>(`/activity/summaries?days=${days}`),
    deleteSummary: (id: string) => req(`/activity/summaries/${id}`, { method: 'DELETE' }),
    /** Summarize what is pending now instead of waiting for the interval. */
    rollup: () => req<{ summary: ActivitySummary | null; status: ActivityStatus }>('/activity/rollup', { method: 'POST' }),
    refreshProfile: () => req<{ profile: string }>('/activity/profile', { method: 'POST' }),
    context: () => req<ActivityContextFile>('/activity/context'),
    devices: () => req<{ index: string; name: string }[]>('/activity/devices'),
    purge: (scope: 'expired' | 'events' | 'summaries' | 'all') => req<{ deleted: { events: number; summaries: number }; status: ActivityStatus }>('/activity/purge', { method: 'POST', body: json({ scope }) })
  },
  canvases: {
    list: () => req<Canvas[]>('/canvases'),
    get: (id: string) => req<Canvas>(`/canvases/${id}`),
    create: (c: { name?: string; project_id?: string | null; copy_from?: string | null }) => req<Canvas>('/canvases', { method: 'POST', body: json(c) }),
    update: (id: string, patch: { name?: string; project_id?: string | null; position?: number; snap_mode?: SnapMode; grid_size?: number; zoom?: number; pan_x?: number; pan_y?: number; wallpaper?: string; clear_project?: boolean }) =>
      req<Canvas>(`/canvases/${id}`, { method: 'PUT', body: json(patch) }),
    delete: (id: string) => req(`/canvases/${id}`, { method: 'DELETE' }),
    addWindow: (id: string, w: { kind: WidgetKind; ref_id?: string | null; project_id?: string | null; title?: string; x?: number; y?: number; w?: number; h?: number; config?: Record<string, unknown> }) =>
      req<CanvasWindow>(`/canvases/${id}/windows`, { method: 'POST', body: json(w) }),
    /** Bulk geometry write, debounced on pointerup. Returns the rowcount, not the canvas. */
    layout: (id: string, windows: WindowLayout[]) => req<{ ok: boolean; updated: number }>(`/canvases/${id}/layout`, { method: 'PUT', body: json({ windows }) })
  },
  /** Canvas windows (`/windows/{id}`). Not `api.widgets`, which is an AI dashboard widget. */
  windows: {
    get: (id: string) => req<CanvasWindow>(`/windows/${id}`),
    /** `config` merges server-side, so one key is safe to send on its own. */
    update: (id: string, patch: { title?: string; ref_id?: string; config?: Record<string, unknown>; state?: WindowState; pinned?: boolean; x?: number; y?: number; w?: number; h?: number; z?: number; canvas_id?: string; restore_bounds?: Rect; popout_bounds?: PopoutBounds; clear_restore_bounds?: boolean; clear_popout_bounds?: boolean }) =>
      req<CanvasWindow>(`/windows/${id}`, { method: 'PUT', body: json(patch) }),
    raise: (id: string) => req<CanvasWindow>(`/windows/${id}/raise`, { method: 'POST' }),
    delete: (id: string) => req(`/windows/${id}`, { method: 'DELETE' })
  },
  docs: {
    list: (s: Scope = 'all', q = '') => req<Doc[]>(`/docs?project_id=${encodeURIComponent(s)}&q=${encodeURIComponent(q)}`),
    get: (id: string) => req<FullDoc>(`/docs/${id}`),
    create: (d: { title?: string; content?: string; folder?: string; project_id?: string | null }) => req<FullDoc>('/docs', { method: 'POST', body: json(d) }),
    /** Autosave. Records a revision, folding a burst of keystrokes into one history entry. */
    save: (id: string, patch: { content?: string; title?: string; summary?: string }) => req<FullDoc>(`/docs/${id}`, { method: 'PUT', body: json(patch) }),
    /** Title, folder, star and project moves — metadata, so it stays out of the history. */
    patch: (id: string, patch: { title?: string; folder?: string; starred?: boolean; project_id?: string | null; clear_project?: boolean }) =>
      req<FullDoc>(`/docs/${id}`, { method: 'PATCH', body: json(patch) }),
    delete: (id: string) => req(`/docs/${id}`, { method: 'DELETE' }),
    pending: () => req<{ pending: number }>('/docs/pending'),
    revisions: (id: string, limit = 100) => req<DocRevision[]>(`/docs/${id}/revisions?limit=${limit}`),
    revision: (revId: string) => req<DocRevision>(`/docs/revisions/${revId}`),
    accept: (revId: string) => req<FullDoc>(`/docs/revisions/${revId}/accept`, { method: 'POST' }),
    reject: (revId: string) => req<FullDoc>(`/docs/revisions/${revId}/reject`, { method: 'POST' }),
    restore: (revId: string) => req<FullDoc>(`/docs/revisions/${revId}/restore`, { method: 'POST' })
  },
  notes: {
    list: (s: Scope = 'all', q = '') => req<Note[]>(`/notes?project_id=${encodeURIComponent(s)}&q=${encodeURIComponent(q)}`),
    get: (id: string) => req<Note>(`/notes/${id}`),
    create: (n: { body?: string; color?: string; project_id?: string | null }) => req<Note>('/notes', { method: 'POST', body: json(n) }),
    update: (id: string, patch: { body?: string; color?: string; project_id?: string | null; clear_project?: boolean }) => req<Note>(`/notes/${id}`, { method: 'PUT', body: json(patch) }),
    delete: (id: string) => req(`/notes/${id}`, { method: 'DELETE' })
  }
}

/** Attach to a conversation's run and iterate its server-sent events from `since`. Any number of clients may. */
export async function* chatStream(convId: string, since = 0, signal?: AbortSignal): AsyncGenerator<ChatEvent> {
  const r = await fetch(`${base}/conversations/${convId}/stream?since=${since}`, { signal, headers: await auth() })
  if (!r.ok || !r.body) throw new Error(`${r.status} ${r.statusText}`)
  const reader = r.body.getReader()
  const dec = new TextDecoder()
  let buf = ''
  while (true) {
    const { value, done } = await reader.read()
    if (done) break
    buf += dec.decode(value, { stream: true })
    let idx: number
    while ((idx = buf.indexOf('\n\n')) >= 0) {
      const block = buf.slice(0, idx)
      buf = buf.slice(idx + 2)
      let event = 'message'
      let data = ''
      for (const line of block.split('\n')) {
        if (line.startsWith('event:')) event = line.slice(6).trim()
        else if (line.startsWith('data:')) data += line.slice(5).trim()
      }
      if (data) yield { event, data: JSON.parse(data) } as ChatEvent
    }
  }
}
