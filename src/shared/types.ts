export type Role = 'system' | 'user' | 'assistant'

export interface Project {
  id: string
  name: string
  description: string
  system_prompt: string
  color: string
  tools: Record<string, ToolOverride>
  created_at: number
  stats?: { conversations: number; memories: number; nodes: number; documents: number }
}

export interface ContextUsed {
  project: { id: string; name: string } | null
  memories: { id: string; content: string; project_id: string | null }[]
  nodes: { id: string; label: string; type: string }[]
  edges: { id: string; relation: string; source_id: string; target_id: string }[]
  chunks: { chunk_id: string; document_id: string; name: string; idx: number; text: string }[]
  system_prompt: string
  tokens_estimate: number
}

export type ToolMode = 'on' | 'ask' | 'off'
export type ToolOverride = 'inherit' | ToolMode

export interface ToolInfo {
  name: string
  description: string
  group: string
  danger: 'safe' | 'writes' | 'network' | 'executes' | 'external'
  available: boolean
  default_mode: ToolMode
  /** Results carry untrusted third-party content, so one call taints the rest of the reply. */
  taints?: boolean
}

export interface ToolImage {
  name: string
  mime: string
  bytes: number
  /** data: URI */
  data: string
}

export interface ToolEvent {
  id: string
  name: string
  arguments: Record<string, unknown>
  result_preview: string
  duration_ms: number
  error: string | null
  /** Images the tool produced (e.g. matplotlib figures from run_python). */
  images?: ToolImage[] | null
  pending?: boolean
  needs_approval?: boolean
  approval?: string | null
  /** This result carried untrusted third-party content. */
  tainted?: boolean
  /** Set when a circuit breaker refused the call instead of running it. */
  blocked?: string
  breaker?: PartialReason
  /** Approval was forced by taint even though the tool is set to 'on'. */
  forced?: boolean
}

/** Why a reply stopped early: a budget axis, or the repetition breaker. */
export type PartialReason = 'rounds' | 'tokens' | 'time' | 'cost' | 'loop'

export type SpanKind = 'context' | 'llm' | 'tool' | 'learn'

/** One timed step in the execution trace of an assistant reply. */
export interface Span {
  id: string
  kind: SpanKind
  name: string
  /** ms since epoch */
  start: number
  /** null while still running */
  end: number | null
  meta: Record<string, unknown>
  error: string | null
}

export interface Message {
  id: string
  conversation_id: string
  role: Role
  content: string
  model: string | null
  error: string | null
  context_used: ContextUsed | null
  tool_events: ToolEvent[] | null
  trace: Span[] | null
  created_at: number
  /** Set when the reply ran out of budget or hit a breaker; not persisted. */
  partial?: PartialReason | null
}

export interface ConversationSettings {
  useMemory: boolean
  useGraph: boolean
  useDocuments: boolean
  autoLearn: boolean
  useTools: boolean
  tools: Record<string, ToolOverride>
  /** Sticky: a reply read untrusted content, so external tools keep asking and fetch_url stays restricted. */
  tainted?: boolean
  taint_sources?: string[]
}

export interface Conversation {
  id: string
  project_id: string | null
  title: string
  model: string
  settings: ConversationSettings
  created_at: number
  updated_at: number
  messages?: Message[]
}

export interface Memory {
  id: string
  project_id: string | null
  content: string
  kind: string
  source: 'user' | 'auto'
  pinned: number
  created_at: number
  updated_at: number
}

export interface GraphNode {
  id: string
  project_id: string | null
  label: string
  type: string
  properties: Record<string, unknown>
  created_at: number
  updated_at: number
}

export interface GraphEdge {
  id: string
  project_id: string | null
  source_id: string
  target_id: string
  relation: string
  properties: Record<string, unknown>
  created_at: number
}

export interface GraphData {
  nodes: GraphNode[]
  edges: GraphEdge[]
}

export interface Document {
  id: string
  project_id: string | null
  name: string
  mime: string
  size: number
  chunk_count: number
  created_at: number
  preview?: string
  text?: string
}

export interface Todo {
  id: string
  project_id: string | null
  title: string
  notes: string
  due: string | null
  priority: number
  done: number
  source: string
  external_id: string | null
  created_at: number
  updated_at: number
  completed_at: number | null
}

export interface GoogleStatus {
  configured: boolean
  /** Where the OAuth client came from: the app's .env, the user's Settings, or nowhere yet. */
  source: 'env' | 'settings' | null
  connected: boolean
  email: string | null
  /** Unix seconds the current token was minted; changes on every fresh sign-in. */
  connected_at: number | null
  scopes: string[]
  /** Scopes the app needs that this token was not granted. */
  missing_scopes: string[]
  /** Connected, but the token is expired, revoked or short on scopes: sign in again. */
  needs_reauth: boolean
  reauth_reason: string | null
}

export interface CalendarEvent {
  id: string
  summary: string
  start: string
  end: string
  all_day: boolean
  location: string | null
  link: string | null
  attendees: string[]
  description: string
  meet: string
}

export interface GmailMessage {
  id: string
  thread_id: string
  from: string | null
  subject: string | null
  date: string | null
  snippet: string
  unread: boolean
  labels: string[]
}

export interface Dashboard {
  google: GoogleStatus
  todos: Todo[]
  todo_stats: { open: number; overdue: number; today: number }
  projects: Project[]
  recent_memories: Memory[]
  recent_conversations: Conversation[]
  calendar: CalendarEvent[] | null
  gmail: GmailMessage[] | null
  errors: Record<string, string>
}

export interface Settings {
  baseUrl: string
  apiKey: string
  defaultModel: string
  systemPrompt: string
  extractionModel: string
  autoLearn: boolean
  theme: 'dark' | 'light' | 'system'
  tools: Record<string, ToolMode | boolean>
  maxToolRounds: number
  /** Per-reply budgets; 0 means unlimited. */
  maxRunTokens?: number
  maxRunSeconds?: number
  maxRunCost?: number
  /** Hosts fetch_url may still read once the reply has seen untrusted content. */
  fetchAllowlist?: string[]
  braveApiKey: string
  tavilyApiKey: string
  /** Per-model cost overrides, $ per million tokens. Proxy prices are used for models not listed. */
  modelPrices: Record<string, ModelPrice>
  googleClientId: string
  googleClientSecret: string
}

export interface ModelPrice {
  input: number
  output: number
  /** 'proxy' = read from the LiteLLM price map, 'override' = set by hand here. */
  source?: 'proxy' | 'override'
}

/** Aggregated counters shared by every slice of the usage report. */
export interface UsageBucket {
  calls: number
  prompt_tokens: number
  completion_tokens: number
  tokens: number
  cost: number
  /** Calls whose model had no known price, so they are missing from `cost`. */
  unpriced: number
  avg_ms: number
  chat_calls: number
  learn_calls: number
  other_calls: number
}

export interface UsageReport {
  days: number
  totals: UsageBucket
  daily: (UsageBucket & { day: string })[]
  hourly: { hour: number; calls: number }[]
  weekday: { weekday: string; calls: number }[]
  by_model: (UsageBucket & { model: string })[]
  by_kind: (UsageBucket & { kind: string })[]
  by_project: (UsageBucket & { project: string })[]
  prices: Record<string, ModelPrice>
}

export interface ModelInfo {
  id: string
}

export type ChatEvent =
  | { event: 'user_message'; data: Message }
  | { event: 'assistant_message'; data: Message }
  | { event: 'removed_message'; data: { id: string } }
  | { event: 'title'; data: { id: string; title: string } }
  | { event: 'delta'; data: { id: string; text: string } }
  | { event: 'tool_call'; data: { message_id: string; id: string; name: string; arguments: Record<string, unknown>; needs_approval?: boolean; forced?: boolean } }
  | { event: 'tool_result'; data: ToolEvent & { message_id: string } }
  | { event: 'span'; data: { message_id: string; span: Span } }
  | { event: 'done'; data: { id: string; error: string | null; context_used: ContextUsed; tool_events: ToolEvent[]; trace: Span[]; stopped: boolean; partial?: PartialReason | null; tainted?: boolean; taint_sources?: string[] } }
  | { event: 'taint'; data: { message_id: string; source: string } }
  | { event: 'learned'; data: { memories: Memory[]; nodes: GraphNode[]; edges: GraphEdge[] } }
  | { event: 'learn_error'; data: { message: string } }
  | { event: 'error'; data: { message: string } }

export interface PersonalOSApi {
  backendUrl: () => Promise<string>
  backendStatus: () => Promise<{ running: boolean; url: string; error: string | null }>
  backendToken: () => Promise<string>
  platform: NodeJS.Platform
  onMenu: (cb: (action: string) => void) => () => void
}

export interface BoardColumn { id: string; board_id: string; name: string; position: number; wip_limit: number | null }
export interface BoardCard {
  id: string; board_id: string; column_id: string; title: string; description: string; position: number
  due: string | null; priority: number; labels: string[]; created_at: number; updated_at: number
}
export interface Board { id: string; project_id: string | null; name: string; created_at: number; card_count?: number; columns: BoardColumn[]; cards: BoardCard[] }

export interface DataSource {
  id: string; name: string; kind: 'http' | 'rss' | 'internal' | string; config: Record<string, unknown>; description: string
  has_secret: boolean; last_status: string | null; last_fetched_at: number | null; created_at: number
}
export interface Widget {
  id: string; dashboard_id: string; title: string; kind: 'html' | 'summary' | 'markdown' | string; prompt: string; source_ids: string[]
  code: string; output: string; refresh_minutes: number; refreshed_at: number | null; position: number; width: number; height: number
  created_at: number; updated_at: number
}
export interface Dashboard { id: string; name: string; description: string; created_at: number; widget_count?: number; widgets: Widget[] }
export interface Recap { day: string; content: string; created_at: number; cached?: boolean }
