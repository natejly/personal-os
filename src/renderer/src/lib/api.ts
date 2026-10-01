import type {
  BackgroundEvent, ChatEvent, ToolInfo, Todo, GoogleStatus, TodayDashboard, CalendarEvent, CalendarColors, EventPayload, GoogleCalendar, GmailMessage, GmailFullMessage, GmailLabel, GoogleTask, GoogleTaskList, TasksSyncStatus, TodoCalendarStatus, DriveFile, Board, BoardCard, BoardColumn, DataSource, Dashboard, Widget, Recap, Conversation, ConversationSettings, ContextUsed, Document, GraphData, GraphEdge, GraphNode, Message,
  ApprovalDecision, PlanEdit,
  Memory, ModelInfo, ModelPrice, PageContext, Settings, Project, UsageReport, ChatRunStarted, RunInfo,
  Plan, PlanStep, Skill, SkillStatus, ToolResultHandle,
  Canvas, CanvasPreset, CanvasWindow, InstantiatedCanvas, Note, PopoutBounds, Rect, SnapMode, WidgetKind, WindowLayout, WindowState,
  AgentInbox, AgentProposal, Job,
  Doc, DocFolder, FullDoc, DocRevision,
  McpEffective, McpReport, McpServer, McpServerDraft, McpTool, ToolMode,
  ActivityCapability, ActivityConfig, ActivityContextFile, ActivityEvent, ActivityGrantResult, ActivityStatus, ActivitySummary,
  PendingSend, SendHoldConfig, Verification, Verified
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

/** Human sentence for a read-back that did not prove the write (mirrors verify.summary_text). */
export function verificationMessage(v: Verification): string {
  if (v.status === 'mismatch' && v.reason === 'still_present') return `It may not have been deleted: ${v.what} is still there.`
  if (v.status === 'mismatch') return `Stored differently than requested: ${v.what} disagrees on ${Object.keys(v.differences ?? {}).join(', ') || 'a field'}.`
  if (v.reason === 'read_failed') return `Could not confirm it: reading ${v.what} back failed (${v.detail ?? 'unknown error'}).`
  return `Could not confirm it: ${v.what} was not there after ${v.attempts} read-backs. It may not have happened at all.`
}

/** Every external write re-reads the remote state to prove it landed (backend verify.py). An
 *  unproven write is thrown, not returned, so no caller can toast success over it by forgetting
 *  to look — the write may still have happened, which is what the message says. */
async function proven<T extends Verified>(p: Promise<T>): Promise<T> {
  const r = await p
  if (r.verification && r.verified === false) throw new Error(`${verificationMessage(r.verification)} Check Google before relying on it.`)
  return r
}

/** Skips the backend's short-lived Google read cache; for user-initiated reloads only. */
const fresh = (refresh: boolean): string => (refresh ? '&refresh=true' : '')
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
  // `steps` / `note` are for a propose_plan card: the steps the user is authorising (with any edited arguments,
  // whose digests the backend re-derives), and one line back to the model.
  approve: (callId: string, decision: ApprovalDecision, opts?: { steps?: PlanEdit[] | null; note?: string }) =>
    req(`/approvals/${callId}`, { method: 'POST', body: json({ decision, ...(opts?.steps ? { steps: opts.steps } : {}), ...(opts?.note ? { note: opts.note } : {}) }) }),
  /** The Agent Inbox: pending approvals and proposals, plus what the scheduled jobs did. Built from journal rows. */
  inbox: (hours = 72) => req<AgentInbox>(`/inbox?hours=${hours}`),
  jobs: {
    list: () => req<Job[]>('/jobs'),
    /** A repeating job passes `cron`; a one-off passes kind:'once' and `run_at` (unix seconds, must be future). */
    create: (j: { name: string; prompt: string; kind?: 'cron' | 'once'; cron?: string; run_at?: number | null; timezone?: string; enabled?: boolean; project_id?: string | null }) =>
      req<Job>('/jobs', { method: 'POST', body: json(j) }),
    update: (id: string, patch: Partial<Pick<Job, 'name' | 'kind' | 'cron' | 'run_at' | 'prompt' | 'timezone' | 'enabled' | 'project_id'>>) =>
      req<Job>(`/jobs/${id}`, { method: 'PATCH', body: json(patch) }),
    delete: (id: string) => req(`/jobs/${id}`, { method: 'DELETE' }),
    /** Fire it now by hand. Still proposal-only and on the job budget; the cron schedule is untouched. */
    runNow: (id: string) => req<{ ok: boolean; run_id: string | null; conversation_id: string | null }>(`/jobs/${id}/run`, { method: 'POST' })
  },
  proposals: {
    list: (status: 'pending' | 'accepted' | 'rejected' | 'all' = 'pending') => req<AgentProposal[]>(`/proposals?status=${status}`),
    /** Executes it, as the user. `args` replaces the call's arguments first. Accepting twice is a 409, never a resend. */
    accept: (id: string, args?: Record<string, unknown>) =>
      req<{ ok: boolean; proposal: AgentProposal; replayed: boolean; result: string }>(`/proposals/${id}/accept`, { method: 'POST', body: json({ args: args ?? null }) }),
    reject: (id: string) => req<{ ok: boolean; proposal: AgentProposal }>(`/proposals/${id}/reject`, { method: 'POST' })
  },
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
    update: (id: string, patch: { title?: string; notes?: string; due?: string | null; priority?: number; done?: boolean; project_id?: string | null; clear_due?: boolean; clear_project?: boolean; calendar_event_id?: string | null; calendar_link?: string | null; calendar_id?: string | null }) =>
      req<Todo>(`/todos/${id}`, { method: 'PUT', body: json(patch) }),
    delete: (id: string) => req(`/todos/${id}`, { method: 'DELETE' })
  },
  mcp: {
    servers: () => req<McpServer[]>('/mcp/servers'),
    create: (s: Partial<McpServerDraft> & { name: string; enabled?: boolean }) => req<McpServer>('/mcp/servers', { method: 'POST', body: json(s) }),
    /** A secret left as '' keeps the stored value; `clear_secrets` removes keys outright. */
    update: (id: string, patch: Partial<McpServerDraft> & { enabled?: boolean; clear_secrets?: string[] }) =>
      req<McpServer>(`/mcp/servers/${id}`, { method: 'PATCH', body: json(patch) }),
    remove: (id: string) => req<{ ok: boolean }>(`/mcp/servers/${id}`, { method: 'DELETE' }),
    restart: (id: string) => req<McpServer>(`/mcp/servers/${id}/restart`, { method: 'POST' }),
    logs: (id: string) => req<{ server_id: string; stderr: string[] }>(`/mcp/servers/${id}/logs`),
    /** Probe + static check of a saved server; files the report as its latest. */
    check: (id: string) => req<McpReport>(`/mcp/servers/${id}/check`, { method: 'POST' }),
    /** The same check on a config that has not been saved, so trust can be decided first. */
    checkDraft: (d: Partial<McpServerDraft>) => req<McpReport>('/mcp/check', { method: 'POST', body: json(d) }),
    tools: () => req<{ tools: McpTool[] }>('/mcp/tools'),
    setGrant: (slug: string, mode: ToolMode, scope: 'global' | 'project' | 'chat' = 'global', scopeId?: string) =>
      req<McpEffective>(`/mcp/tools/${encodeURIComponent(slug)}/grant`, { method: 'PUT', body: json({ mode, scope, scope_id: scopeId ?? null }) }),
    clearGrant: (slug: string, scope: 'global' | 'project' | 'chat' = 'global') =>
      req<McpEffective>(`/mcp/tools/${encodeURIComponent(slug)}/grant?scope=${scope}`, { method: 'DELETE' })
  },
  google: {
    status: () => req<GoogleStatus>('/integrations/google/status'),
    start: () => req<{ url: string }>('/integrations/google/auth/start', { method: 'POST' }),
    disconnect: () => req<GoogleStatus>('/integrations/google/disconnect', { method: 'POST' }),
    calendar: (days = 2, calendars = 'primary', refresh = false) =>
      req<CalendarEvent[]>(`/integrations/google/calendar?days=${days}&calendars=${encodeURIComponent(calendars)}${fresh(refresh)}`),
    calendarRange: (startIso: string, days = 7, calendars = 'primary', refresh = false) =>
      req<CalendarEvent[]>(`/integrations/google/calendar?days=${days}&start=${encodeURIComponent(startIso)}&calendars=${encodeURIComponent(calendars)}${fresh(refresh)}`),
    calendars: (refresh = false) => req<GoogleCalendar[]>(`/integrations/google/calendars${refresh ? '?refresh=true' : ''}`),
    calendarColors: () => req<CalendarColors>('/integrations/google/calendar/colors'),
    getEvent: (id: string, calendarId = 'primary') => req<CalendarEvent>(`/integrations/google/calendar/${encodeURIComponent(id)}?calendar_id=${encodeURIComponent(calendarId)}`),
    // The four calendar writes go through proven(): an unverified write rejects, so the callers'
    // existing catch → error-toast path is also the "we could not confirm that" path.
    createEvent: (e: EventPayload & { summary: string; start: string }) =>
      proven(req<CalendarEvent>('/integrations/google/calendar', { method: 'POST', body: json(e) })),
    updateEvent: (id: string, patch: EventPayload) =>
      proven(req<CalendarEvent>(`/integrations/google/calendar/${encodeURIComponent(id)}`, { method: 'PATCH', body: json(patch) })),
    deleteEvent: (id: string, calendarId = 'primary', sendUpdates: 'none' | 'all' | 'externalOnly' = 'none') =>
      proven(req<{ deleted: string } & Verified>(`/integrations/google/calendar/${encodeURIComponent(id)}?calendar_id=${encodeURIComponent(calendarId)}&send_updates=${sendUpdates}`, { method: 'DELETE' })),
    respondEvent: (id: string, response: 'accepted' | 'declined' | 'tentative', calendarId = 'primary') =>
      proven(req<CalendarEvent>(`/integrations/google/calendar/${encodeURIComponent(id)}/respond`, { method: 'POST', body: json({ response, calendar_id: calendarId }) })),
    gmail: (q = 'is:unread in:inbox newer_than:14d', maxResults = 12, refresh = false) =>
      req<GmailMessage[]>(`/integrations/google/gmail?q=${encodeURIComponent(q)}&max_results=${maxResults}${fresh(refresh)}`),
    tasks: (showCompleted = false, refresh = false) =>
      req<GoogleTask[]>(`/integrations/google/tasks?show_completed=${showCompleted}${fresh(refresh)}`),
    tasklists: () => req<GoogleTaskList[]>('/integrations/google/tasklists'),
    tasksSync: () => req<TasksSyncStatus>('/integrations/google/tasks-sync'),
    tasksSyncConfig: (patch: { enabled?: boolean; tasklist?: string; intervalMinutes?: number }) =>
      req<TasksSyncStatus>('/integrations/google/tasks-sync', { method: 'PUT', body: json(patch) }),
    tasksSyncRun: () => req<TasksSyncStatus>('/integrations/google/tasks-sync/run', { method: 'POST' }),
    todoCalendar: () => req<TodoCalendarStatus>('/integrations/google/todo-calendar'),
    todoCalendarConfig: (patch: { enabled?: boolean; calendarId?: string; calendarName?: string; intervalMinutes?: number; keepCompleted?: boolean }) =>
      req<TodoCalendarStatus>('/integrations/google/todo-calendar', { method: 'PUT', body: json(patch) }),
    todoCalendarRun: () => req<TodoCalendarStatus>('/integrations/google/todo-calendar/run', { method: 'POST' }),
    drive: (q = '', maxResults = 20) => req<DriveFile[]>(`/integrations/google/drive?q=${encodeURIComponent(q)}&max_results=${maxResults}`),
    gmailGet: (id: string) => req<GmailFullMessage>(`/integrations/google/gmail/${id}`),
    gmailLabels: () => req<GmailLabel[]>('/integrations/google/gmail/labels'),
    gmailModify: (id: string, patch: { mark_read?: boolean; archive?: boolean; star?: boolean }) =>
      proven(req<{ ok: boolean } & Verified>(`/integrations/google/gmail/${id}/modify`, { method: 'POST', body: json(patch) })),
    gmailDraft: (m: { to: string; subject: string; body: string; reply_to_message_id?: string | null }) =>
      proven(req<{ draft_id: string } & Verified>('/integrations/google/gmail/draft', { method: 'POST', body: json(m) })),
    /** Queues the send behind its undo hold; it has NOT gone out when this resolves. */
    gmailSend: (m: { to: string; subject: string; body: string; reply_to_message_id?: string | null }) =>
      req<PendingSend>('/integrations/google/gmail/send', { method: 'POST', body: json(m) }),
    clearCache: (namespace?: string) =>
      req<{ dropped: number }>(`/integrations/google/cache/clear${namespace ? `?namespace=${namespace}` : ''}`, { method: 'POST' })
  },
  /** Emails waiting out their undo hold (backend outbox.py). */
  outbox: {
    list: () => req<{ sends: PendingSend[]; config: SendHoldConfig }>('/outbox/gmail'),
    cancel: (id: string) => req<PendingSend>(`/outbox/gmail/${encodeURIComponent(id)}/cancel`, { method: 'POST' }),
    sendNow: (id: string) => req<PendingSend>(`/outbox/gmail/${encodeURIComponent(id)}/send-now`, { method: 'POST' })
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
  /** The chat's plan artifact: the model writes it with `todo_write`, the user ticks steps off here. */
  plan: {
    get: (convId: string) => req<Plan>(`/conversations/${convId}/plan`),
    set: (convId: string, steps: PlanStep[]) => req<Plan>(`/conversations/${convId}/plan`, { method: 'PUT', body: json({ steps }) }),
    clear: (convId: string) => req<{ ok: boolean }>(`/conversations/${convId}/plan`, { method: 'DELETE' })
  },
  /** Large tool results that were stored instead of inlined into the model's context. */
  toolResults: {
    list: (convId: string, limit = 20) => req<ToolResultHandle[]>(`/conversations/${convId}/tool-results?limit=${limit}`),
    read: (id: string, offset = 0, limit = 20000) => req<{ text: string; total_chars: number; offset: number; has_more: boolean }>(`/tool-results/${id}?offset=${offset}&limit=${limit}`)
  },
  /** Procedural memory. Nothing here is injected until its status is 'approved'. */
  skills: {
    list: (status?: SkillStatus) => req<Skill[]>(`/skills${status ? `?status=${status}` : ''}`),
    create: (s: { name: string; description?: string; procedure?: string; project_id?: string | null }) => req<Skill>('/skills', { method: 'POST', body: json(s) }),
    update: (id: string, patch: { name?: string; description?: string; procedure?: string; status?: SkillStatus }) =>
      req<Skill>(`/skills/${id}`, { method: 'PATCH', body: json(patch) }),
    delete: (id: string) => req<{ ok: boolean }>(`/skills/${id}`, { method: 'DELETE' }),
    /** Distil a conversation into a candidate for review. Never enables anything. */
    induce: (convId: string) => req<{ candidate: Skill | null; reason: string | null }>(`/conversations/${convId}/skills/induce`, { method: 'POST' })
  },
  stop: (mid: string) => req(`/messages/${mid}/stop`, { method: 'POST' }),
  /** Starts the reply as a background task and returns at once; watch it with `chatStream(convId, seq)`. Throws a 409 carrying a `RunConflict` when that conversation already has a live run. */
  chat: (convId: string, body: { content?: string; model?: string; page_context?: PageContext }) => req<ChatRunStarted>(`/conversations/${convId}/chat`, { method: 'POST', body: json(body) }),
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
    permissions: () => req<ActivityCapability[]>('/activity/permissions'),
    requestPermission: (id: string, browser = '') => req<{ result: ActivityGrantResult; status: ActivityStatus }>('/activity/permissions/request', { method: 'POST', body: json({ id, browser }) }),
    openPermissionSettings: (id: string) => req<{ ok: boolean }>('/activity/permissions/open', { method: 'POST', body: json({ id }) }),
    palantir: (on: boolean) => req<ActivityStatus>('/activity/palantir', { method: 'POST', body: json({ on }) }),
    purge: (scope: 'expired' | 'events' | 'summaries' | 'all') => req<{ deleted: { events: number; summaries: number }; status: ActivityStatus }>('/activity/purge', { method: 'POST', body: json({ scope }) })
  },
  canvases: {
    list: () => req<Canvas[]>('/canvases'),
    get: (id: string) => req<Canvas>(`/canvases/${id}`),
    create: (c: { name?: string; project_id?: string | null; copy_from?: string | null }) => req<Canvas>('/canvases', { method: 'POST', body: json(c) }),
    update: (id: string, patch: { name?: string; project_id?: string | null; position?: number; snap_mode?: SnapMode; grid_size?: number; zoom?: number; pan_x?: number; pan_y?: number; wallpaper?: string; locked?: boolean; clear_project?: boolean }) =>
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
    update: (id: string, patch: { title?: string; ref_id?: string; config?: Record<string, unknown>; state?: WindowState; pinned?: boolean; opacity?: number; x?: number; y?: number; w?: number; h?: number; z?: number; canvas_id?: string; restore_bounds?: Rect; popout_bounds?: PopoutBounds; clear_restore_bounds?: boolean; clear_popout_bounds?: boolean }) =>
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
    folders: () => req<DocFolder[]>('/docs/folders'),
    createFolder: (path: string) => req<DocFolder[]>('/docs/folders', { method: 'POST', body: json({ path }) }),
    /** Rename and move are one call: both rewrite the path of a folder and everything under it. */
    renameFolder: (path: string, newPath: string) => req<DocFolder[]>('/docs/folders', { method: 'PATCH', body: json({ path, new_path: newPath }) }),
    /** Without `deleteDocs` the folder's docs move up to its parent rather than disappearing with it. */
    deleteFolder: (path: string, deleteDocs = false) =>
      req<DocFolder[]>(`/docs/folders?path=${encodeURIComponent(path)}&delete_docs=${deleteDocs}`, { method: 'DELETE' }),
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
  },
  /** Space presets (`/canvas-presets`): named templates of a canvas. */
  presets: {
    list: () => req<CanvasPreset[]>('/canvas-presets'),
    get: (id: string) => req<CanvasPreset>(`/canvas-presets/${id}`),
    /** The server snapshots the canvas; a blank name takes the canvas's name. */
    create: (p: { canvas_id: string; name?: string }) => req<CanvasPreset>('/canvas-presets', { method: 'POST', body: json(p) }),
    update: (id: string, patch: { name?: string }) => req<CanvasPreset>(`/canvas-presets/${id}`, { method: 'PUT', body: json(patch) }),
    delete: (id: string) => req<{ ok: boolean }>(`/canvas-presets/${id}`, { method: 'DELETE' }),
    /** Creates a NEW canvas; `skipped` counts windows whose referent no longer exists. */
    instantiate: (id: string, opts: { name?: string } = {}) => req<InstantiatedCanvas>(`/canvas-presets/${id}/instantiate`, { method: 'POST', body: json(opts) })
  }
}

const STREAM_RETRIES = 8

/** One SSE connection, parsed. `seq` is the event's `id:` line, which only the app topic sends. */
async function* sseStream(path: string, signal?: AbortSignal): AsyncGenerator<{ event: string; data: unknown; seq: number | null }> {
  const r = await fetch(`${base}${path}`, { signal, headers: await auth() })
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
      let id = ''
      for (const line of block.split('\n')) {
        if (line.startsWith('event:')) event = line.slice(6).trim()
        else if (line.startsWith('data:')) data += line.slice(5).trim()
        else if (line.startsWith('id:')) id = line.slice(3).trim()
      }
      if (data) yield { event, data: JSON.parse(data), seq: id ? Number(id) : null }
    }
  }
}

/**
 * Attach to a conversation's run and iterate its server-sent events from `since`. Any number of clients may.
 * The stream is a tail on the run's stored tape, and every event carries its seq (`id:`), so with a `runId` a
 * dropped connection (a backend restart, the Mac waking up) reconnects from the last seq it saw instead of failing.
 */
export async function* chatStream(convId: string, since = 0, signal?: AbortSignal, runId?: string): AsyncGenerator<ChatEvent> {
  let last = since
  let failures = 0
  while (true) {
    try {
      const q = `since=${last}${runId ? `&run_id=${encodeURIComponent(runId)}` : ''}`
      for await (const { event, data, seq } of sseStream(`/conversations/${convId}/stream?${q}`, signal)) {
        failures = 0
        if (seq !== null && Number.isFinite(seq)) last = seq
        yield { event, data } as ChatEvent
      }
      return
    } catch (e) {
      if (signal?.aborted || !runId || ++failures > STREAM_RETRIES) throw e
      await new Promise((res) => setTimeout(res, Math.min(5000, 500 * 2 ** (failures - 1))))
    }
  }
}

/**
 * Follow the app topic: background work (auto-learn) that finishes after its run has ended. The
 * stream never completes on its own, so the caller reconnects: each event carries the seq to
 * resume from, and the server's ring replays whatever happened while the socket was down.
 */
export async function* backgroundStream(since = 0, signal?: AbortSignal): AsyncGenerator<BackgroundEvent & { seq: number | null }> {
  for await (const { event, data, seq } of sseStream(`/events?since=${since}`, signal)) {
    yield { event, data, seq } as BackgroundEvent & { seq: number | null }
  }
}
