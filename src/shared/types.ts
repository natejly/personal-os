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
  /** The activity-monitor block, verbatim; null when the monitor is off or the chat opted out. */
  activity: string | null
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

export type Effort = 'default' | 'low' | 'medium' | 'high'

export interface ConversationSettings {
  /** Reasoning effort passed through as `reasoning_effort`; 'default' sends nothing. */
  effort: Effort
  useMemory: boolean
  useGraph: boolean
  useDocuments: boolean
  /** Inject what the activity monitor observed. Defaults on, but only ever has an effect while the
   *  monitor is running and its own `injectContext` is left on. */
  useActivity: boolean
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

export interface GoogleTask {
  id: string
  title: string | null
  notes: string | null
  due: string | null
  status: string | null
}

export interface DriveFile {
  id: string
  name: string
  mime_type: string
  modified: string | null
  link: string | null
  size: number | null
  owner: string | null
}

export interface GmailFullMessage {
  id: string
  thread_id: string
  from: string | null
  to: string | null
  subject: string | null
  date: string | null
  body: string
}

export interface GmailLabel {
  id: string
  name: string
  type: 'system' | 'user'
}

export interface TodayDashboard {
  google: GoogleStatus
  todos: Todo[]
  todo_stats: { open: number; overdue: number; today: number }
  projects: Project[]
  recent_memories: Memory[]
  recent_conversations: Conversation[]
  calendar: CalendarEvent[] | null
  gmail: GmailMessage[] | null
  tasks: GoogleTask[] | null
  /** null until the token has the Drive scope (older sign-ins need a Reconnect). */
  drive: DriveFile[] | null
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
  /** Which shell the app opens in: the single-pane router, or the window canvas. */
  mode: 'classic' | 'canvas'
  /** Electron accelerator for the global Gather/Scatter shortcut. */
  gatherShortcut: string
  /** Today-screen cards, keyed by module (see modules.ts); a missing key means shown. */
  homeWidgets?: Record<string, boolean>
  /** Sidebar views the user removed. */
  hiddenViews?: string[]
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

export interface GrainApi {
  backendUrl: () => Promise<string>
  backendStatus: () => Promise<{ running: boolean; url: string; error: string | null }>
  backendToken: () => Promise<string>
  platform: NodeJS.Platform
  onMenu: (cb: (action: string) => void) => () => void
  popout: {
    open: (windowId: string, req?: PopoutOpenRequest) => Promise<boolean>
    close: (windowId: string) => Promise<boolean>
    focus: (windowId: string) => Promise<boolean>
    setPinned: (windowId: string, pinned: boolean) => Promise<boolean>
    setMinSize: (windowId: string, minWidth: number, minHeight: number) => Promise<boolean>
    list: () => Promise<PopoutInfo[]>
    gather: () => Promise<GatherState>
    scatter: () => Promise<GatherState>
    onChanged: (cb: (c: PopoutChange) => void) => () => void
  }
  bus: {
    send: (msg: BusMessage) => void
    on: (cb: (msg: BusMessage) => void) => () => void
  }
  shortcuts: {
    gather: () => Promise<ShortcutState>
    setGather: (accelerator: string) => Promise<ShortcutState>
    onFailure: (cb: (s: ShortcutState) => void) => () => void
  }
  /** Closes the BrowserWindow this renderer lives in: the Cmd-W fall-through when no canvas window has focus. */
  closeSelf: () => void
  minimizeSelf: () => void
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

// ---------------- Canvas Mode ----------------

/** Every widget a canvas window can host. Source of truth for `WIDGET_KINDS` in backend/personal_os/canvas.py. */
export type WidgetKind =
  | 'chat' | 'todos' | 'calendar' | 'board' | 'note' | 'dashboard-widget'
  | 'memory' | 'graph' | 'documents' | 'recap' | 'project' | 'usage' | 'activity'

export type WindowState = 'normal' | 'minimized' | 'maximized' | 'popped'
export type SnapMode = 'off' | 'grid' | 'guides' | 'both'

/** Canvas-space rect in canvas points. Never screen pixels: those are PopoutBounds. */
export interface Rect { x: number; y: number; w: number; h: number }

/** Screen bounds of a detached window: Electron's Rectangle plus the Display.id it was last seen on. */
export interface PopoutBounds { x: number; y: number; width: number; height: number; display?: number }

export interface CanvasWindow {
  id: string; canvas_id: string; kind: WidgetKind; ref_id: string | null
  project_id: string | null
  /** '' = derive the title from the underlying object */
  title: string
  x: number; y: number; w: number; h: number; z: number
  state: WindowState
  /** bounds to restore when un-maximizing */
  restore_bounds: Rect | null
  popout_bounds: PopoutBounds | null
  /** 0 | 1 — SQLite has no boolean. Always-on-top while popped. */
  pinned: number
  config: Record<string, unknown>
  created_at: number; updated_at: number
}

export interface Canvas {
  id: string; name: string; project_id: string | null; position: number
  snap_mode: SnapMode; grid_size: number; zoom: number; pan_x: number; pan_y: number
  wallpaper: string; created_at: number; updated_at: number
  windows: CanvasWindow[]
}

/** One row of the bulk `PUT /canvases/{id}/layout` body; every field but `id` is optional. */
export interface WindowLayout { id: string; x?: number; y?: number; w?: number; h?: number; z?: number; state?: WindowState }

export interface Note { id: string; project_id: string | null; body: string; color: string; created_at: number; updated_at: number }

/**
 * A doc: long-form markdown the user writes in the Docs editor. Distinct from `Document` (a file they
 * uploaded, for retrieval) and from `Note` (canvas mode's sticky note).
 */
export interface Doc {
  id: string
  project_id: string | null
  title: string
  folder: string
  starred: number
  created_at: number
  updated_at: number
  words: number
  /** List rows carry a preview and a pending count; a fetched doc carries the body and the pending revisions. */
  preview?: string
  size?: number
  content?: string
  pending?: number | DocRevision[]
}

/** A doc with its body loaded — what GET /docs/{id} returns. */
export interface FullDoc extends Doc {
  content: string
  pending: DocRevision[]
}

export type DocRevisionStatus = 'applied' | 'pending' | 'rejected'

/** One entry in a doc's history. An assistant edit stays `pending` until the user accepts it. */
export interface DocRevision {
  id: string
  doc_id: string
  before: string
  after: string
  title_before: string | null
  title_after: string | null
  summary: string
  author: 'user' | 'assistant'
  tool: string | null
  status: DocRevisionStatus
  created_at: number
  resolved_at: number | null
  stat: { added: number; removed: number }
  /** Pending only: the doc moved since this was proposed, so it is reviewed against the current body. */
  stale?: boolean
  stat_vs_current?: { added: number; removed: number } | null
  /** GET /docs/revisions/{id} only: a unified diff, for copying out. */
  patch?: string
}

export type DragKind = 'conversation' | 'todo' | 'document' | 'memory' | 'board-card' | 'project' | 'widget' | 'note' | 'file' | 'nav'

export interface DragPayload {
  kind: DragKind
  /** The underlying object's id. For kind 'nav' this is a WidgetKind; for 'file' it is ''. */
  id: string
  label: string
  projectId?: string | null
  /** kind 'widget' only: the dashboard the widget belongs to, since there is no GET /widgets/{id}. */
  dashboardId?: string
}

/** Run state of one chat session. Travels the cross-window bus, so it is a shared type, not a store-local one. */
export type SessionStatus = 'idle' | 'working' | 'done' | 'error' | 'needs-approval'

/** 200 body of POST /conversations/{id}/chat once the run is a background task. */
export interface ChatRunStarted {
  run_id: string
  /** seq of the last event already produced; open the stream with ?since=<seq> */
  seq: number
}

export interface RunInfo {
  run_id: string
  conversation_id: string
  message_id: string | null
  seq: number
  started_at: number
  live: boolean
}

/** 409 detail of POST /conversations/{id}/chat when that conversation already has a live run. */
export interface RunConflict { message: string; run_id: string; seq: number }

/** One detached widget window as the main process sees it. */
export interface PopoutInfo { windowId: string; bounds: PopoutBounds; pinned: boolean }

export interface PopoutOpenRequest { bounds?: Partial<PopoutBounds>; minWidth?: number; minHeight?: number; title?: string; pinned?: boolean }

export interface PopoutChange { windowId: string; event: 'opened' | 'closed'; bounds: PopoutBounds | null }

export interface GatherState { gathered: boolean; popped: string[] }

export interface ShortcutState { accelerator: string; ok: boolean; message: string | null }

export type BusKind = 'window-bounds' | 'window-state' | 'window-config' | 'chat-status' | 'todo-changed' | 'note-changed' | 'canvas-invalidate'

/**
 * Optimistic cross-window hint relayed renderer -> main -> every other renderer.
 * The backend stays authoritative; a bus message never creates state.
 */
export interface BusMessage { kind: BusKind; windowId?: string; canvasId?: string; refId?: string; data?: Record<string, unknown> }

/** ---- activity monitor ---------------------------------------------------
 *  Observed computer activity, summarized locally and fed back as chat context.
 *  Every signal is opt-in and off until switched on. */

/** The signals that can be collected, most benign first. */
export type ActivitySignal = 'apps' | 'browserUrls' | 'input' | 'text' | 'micAudio' | 'outputAudio'

export interface ActivityAudioConfig {
  /** ffmpeg avfoundation device index, as a string. Empty means "not chosen yet". */
  micDevice: string
  /** A loopback device (BlackHole/Loopback) - macOS cannot record its own output without one. */
  outputDevice: string
  chunkSeconds: number
  /** Speech-to-text model on the configured LLM base URL. */
  model: string
  /** Transcripts shorter than this are dropped as noise. */
  minChars: number
}

export interface ActivityConfig {
  enabled: boolean
  signals: Record<ActivitySignal, boolean>
  sampleSeconds: number
  /** No input for this long counts as away from the machine. */
  idleSeconds: number
  rollupMinutes: number
  /** How long raw samples live before they are deleted. */
  retentionHours: number
  summaryRetentionDays: number
  /** Days of detail kept in activity.md. */
  contextDays: number
  /** Feed the summaries into chats at all. */
  injectContext: boolean
  /** Scrub credential- and PII-shaped strings before anything is stored. */
  redact: boolean
  /** Apps never recorded, not even by name. */
  excludeApps: string[]
  /** Window titles / URLs containing any of these are skipped. */
  excludeTitlePatterns: string[]
  audio: ActivityAudioConfig
  /** Blank falls back to the extraction model, then the default model. */
  summaryModel: string
  profileEveryHours: number
}

/** One row of the capability checklist: what this machine can do, and how to fix what it can't. */
export interface ActivityCapability {
  id: string
  label: string
  ok: boolean
  detail: string
  /** Empty when `ok`. */
  fix: string
}

export interface ActivityStatus {
  running: boolean
  paused: boolean
  /** Unix seconds the pause lifts itself. */
  pause_until: number | null
  platform_supported: boolean
  config: ActivityConfig
  capabilities: ActivityCapability[]
  collectors: { id: string; alive: boolean; error: string }[]
  counts: { events: number; pending: number; summaries: number }
  /** One live sentence about the current window, computed without the LLM. */
  now: string
  last_rollup: number | null
  last_error: string
  profile_updated_at: number | null
  /** Where activity.md lives on disk. */
  md_path: string
  audio_devices: { index: string; name: string }[]
  /** True while macOS reports a password field focused; keystrokes are dropped meanwhile. */
  secure_input: boolean
}

export type ActivityEventKind = 'focus' | 'input' | 'idle' | 'audio' | 'note'

export interface ActivityEvent {
  id: string
  ts: number
  kind: ActivityEventKind
  app: string
  bundle: string
  title: string
  url: string
  /** Redacted typed text or transcript; empty for count-only rows. */
  text: string
  meta: Record<string, unknown>
  duration_ms: number
  rolled_up: number
  expires_at: number
}

export interface ActivitySummary {
  id: string
  /** Local YYYY-MM-DD. */
  day: string
  period_start: number
  period_end: number
  headline: string
  body: string
  apps: string[]
  event_count: number
  created_at: number
}

export interface ActivityContextFile {
  path: string
  /** The whole activity.md. */
  markdown: string
  /** The trimmed block chats actually receive. */
  injected: string
}
