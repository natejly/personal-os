import type {
  ChatEvent, ToolInfo, Todo, GoogleStatus, Dashboard as DashboardData, CalendarEvent, GmailMessage, DriveFile, Board, BoardCard, BoardColumn, Recap, Conversation, ConversationSettings, ContextUsed, Document, GraphData, GraphEdge, GraphNode,
  Memory, ModelInfo, ModelPrice, Settings, Project, UsageReport
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

/** Sidecar shared secret. Resolved once per setBase(); every backend request carries it. */
const auth = async (): Promise<Record<string, string>> => {
  if (!token && tokenP) await tokenP
  return token ? { 'X-Personal-OS-Token': token } : {}
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(`${base}${path}`, {
    ...init,
    headers: { ...(init?.body && !(init.body instanceof FormData) ? { 'Content-Type': 'application/json' } : {}), ...(init?.headers ?? {}), ...(await auth()) }
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
  dashboard: () => req<DashboardData>('/dashboard'),
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
  todos: {
    list: (s: Scope = 'all', includeDone = false, q = '') => req<Todo[]>(`/todos?project_id=${encodeURIComponent(s)}&include_done=${includeDone}&q=${encodeURIComponent(q)}`),
    create: (t: { title: string; project_id?: string | null; notes?: string; due?: string | null; priority?: number }) => req<Todo>('/todos', { method: 'POST', body: json(t) }),
    update: (id: string, patch: { title?: string; notes?: string; due?: string | null; priority?: number; done?: boolean; project_id?: string | null; clear_due?: boolean; clear_project?: boolean }) =>
      req<Todo>(`/todos/${id}`, { method: 'PUT', body: json(patch) }),
    delete: (id: string) => req(`/todos/${id}`, { method: 'DELETE' })
  },
  google: {
    status: () => req<GoogleStatus>('/integrations/google/status'),
    start: () => req<{ url: string }>('/integrations/google/auth/start', { method: 'POST' }),
    disconnect: () => req<GoogleStatus>('/integrations/google/disconnect', { method: 'POST' }),
    calendar: (days = 2) => req<CalendarEvent[]>(`/integrations/google/calendar?days=${days}`),
    calendarRange: (startIso: string, days = 7) => req<CalendarEvent[]>(`/integrations/google/calendar?days=${days}&start=${encodeURIComponent(startIso)}`),
    createEvent: (e: { summary: string; start: string; end?: string; description?: string; location?: string }) => req(`/integrations/google/calendar`, { method: 'POST', body: json(e) }),
    gmail: (q = 'is:unread newer_than:3d') => req<GmailMessage[]>(`/integrations/google/gmail?q=${encodeURIComponent(q)}`),
    drive: (q = '', maxResults = 20) => req<DriveFile[]>(`/integrations/google/drive?q=${encodeURIComponent(q)}&max_results=${maxResults}`),
    driveFile: (id: string) => req<DriveFile>(`/integrations/google/drive/${id}`)
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
  }
}

/** POST to the chat endpoint and iterate its server-sent events. */
export async function* chatStream(
  convId: string,
  body: { content?: string; model?: string },
  signal?: AbortSignal
): AsyncGenerator<ChatEvent> {
  const r = await fetch(`${base}/conversations/${convId}/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...(await auth()) },
    body: JSON.stringify(body),
    signal
  })
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
