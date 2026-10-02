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
  /** Approved skills injected as procedural memory. Absent on messages written before skills existed. */
  skills?: { id: string; name: string; description: string }[]
  /** What the user was looking at when they asked, when the turn came from the page agent (⌘I). */
  page: PageContext | null
  /** The writing-style profile this reply drafted with; null when there is none or the chat opted out. */
  style: { project_id: string | null; summary: string; guidelines: string[]; block: string } | null
  /** The recent-meetings block, verbatim; null when meetings are off or the chat opted out. */
  meetings: string | null
  system_prompt: string
  tokens_estimate: number
}

export type PlanStatus = 'pending' | 'in_progress' | 'done'
/** One step of a chat's plan artifact: written by the model with `todo_write`, tickable by the user. */
export interface PlanStep {
  id: string
  text: string
  status: PlanStatus
  note: string
}
export interface Plan {
  conversation_id: string
  steps: PlanStep[]
  updated_at: number | null
}

export type SkillStatus = 'candidate' | 'approved' | 'rejected'
/** Procedural memory. A candidate is inert: only an approved skill is ever injected into a prompt. */
/** One finding from the skill lint. 'error' blocks approval — it is always text claiming authority
 *  over the assistant's permissions. 'warn' is quality, for the author to weigh. */
export interface SkillFinding {
  level: 'error' | 'warn'
  code: string
  message: string
  field: 'name' | 'description' | 'procedure'
  hint?: string
  excerpt?: string
}

export interface SkillDraft {
  name: string
  description: string
  procedure: string
}

/** The result of drafting from intent. Nothing is stored: `draft` is text for the user to edit. */
export interface SkillDraftResult {
  draft: SkillDraft | null
  reason?: string
  findings?: SkillFinding[]
}

/** The real injected block, assembled by the same function the chat uses, plus what did not fit. */
export interface SkillPreview {
  block: string
  tokens_estimate: number
  included: { id: string; name: string }[]
  omitted: { id: string; name: string }[]
}

export interface Skill {
  id: string
  project_id: string | null
  name: string
  description: string
  procedure: string
  status: SkillStatus
  /** induced (from a conversation) · proposed (by the assistant mid-chat) · user */
  source: string
  source_conversation_id: string | null
  created_at: number
  updated_at: number
  approved_at: number | null
}

/** A large tool result kept out of the model's context; `read_tool_result` pages it. */
export interface ToolResultHandle {
  id: string
  tool: string
  total_chars: number
  shape: Record<string, unknown>
  message_id: string | null
  created_at: number
}

/**
 * A snapshot of the screen the user is on, published by the active view and sent with a page-agent
 * turn. It is a description of what is visible, not a fetch: `refs` name the rows so the model can
 * read or change them with the ordinary tools.
 */
export interface PageContext {
  /** The view that published it — 'docs', 'calendar', ... Matches the renderer's View union. */
  view: string
  /** One line for the panel's chip and the system prompt's heading, e.g. `Doc “Weekly notes”`. */
  label: string
  /** What is on screen, as markdown the model reads: the open doc's text, the visible events, ... */
  detail?: string
  /** The user's current selection, when the view has one. */
  selection?: string
  /** Rows the page is about, so the model can act on them by id rather than searching. */
  refs?: { kind: string; id: string; name?: string }[]
  /** Starter prompts offered in an empty panel. Three at most; the view knows its own verbs. */
  hints?: string[]
}

export type ToolMode = 'on' | 'ask' | 'off'
export type ToolOverride = 'inherit' | ToolMode

export interface ToolInfo {
  name: string
  description: string
  group: string
  danger: 'safe' | 'writes' | 'network' | 'executes' | 'external' | 'schedules'
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

/** One call a `propose_plan` card asks the user to authorise, with the arguments it will really be made with.
 *  Distinct from PlanStep, which is a step of the chat's own todo_write plan artifact. */
export interface ProposedStep {
  tool: string
  arguments: Record<string, unknown>
  why?: string
}

/** The arguments of a `propose_plan` call: what the plan card renders. */
export interface ProposedPlan {
  title?: string
  steps: ProposedStep[]
}

export type ToolDanger = 'safe' | 'network' | 'writes' | 'executes' | 'external' | 'plan' | 'schedules'

export type PlanStepStatus = 'proposed' | 'approved' | 'consumed' | 'done' | 'failed' | 'dropped' | 'rejected'

/** One step of a stored plan: what the card shows, and what became of it. */
export interface PlanRecordStep {
  step_id: string
  plan_id: string
  idx: number
  title: string
  tool: string
  /** The column is `args`; the wire key is `arguments`. Approving binds `args_digest` of exactly this. */
  arguments: Record<string, unknown>
  args_digest: string
  why: string
  danger: ToolDanger
  status: PlanStepStatus
  /** The user rewrote the arguments, so the agent is bound to theirs rather than its own. */
  edited: boolean
  call_id: string | null
  consumed_at: number | null
  result_error: string | null
}

/**
 * A stored `propose_plan` row: the approval artifact. Not `Plan`, which is the todo_write checklist,
 * and not `ProposedPlan`, which is only the tool's arguments before any of this was written down.
 */
export interface PlanRecord {
  plan_id: string
  /** The approvals row this plan is decided through. */
  call_id: string
  run_id: string | null
  conversation_id: string | null
  message_id: string | null
  desk_id: string | null
  title: string
  intent: string
  status: 'pending' | 'approved' | 'rejected'
  /** The reply had already read untrusted content when this plan was proposed. */
  tainted: boolean
  /** Tools in this plan that taint, so a later external step is not re-gated by the plan's own research. */
  expected_taint: string[]
  note: string | null
  decided_by: string | null
  created_at: number
  decided_at: number | null
  steps: PlanRecordStep[]
}

/** The approved plan step a call was matched against, instead of asking again. */
export interface PlanStepRef {
  plan_id: string
  idx: number
  title: string
}

/** What the user authorises on a plan card: the steps to keep, by their proposed index, with any edited arguments. */
export interface PlanEdit {
  idx: number
  arguments?: Record<string, unknown>
}

export type ApprovalDecision = 'allow' | 'deny' | 'always_chat' | 'always_global' | 'always_session' | 'always_rule'

/** What an approval card adds beyond the tool name: the rule that put it there and the rules it can save. */
export interface PermissionCard {
  kind: 'rule' | 'opaque' | 'external_directory' | 'doom_loop' | null
  /** The subject the card is about, e.g. `Bash(git push origin)` or `doom_loop(fs_grep)`. */
  subject: string | null
  rule: string | null
  /** Editable before saving; one per subcommand, at most five. */
  suggestions: string[]
  /** False for a forced card (taint, plan mode, doom loop): it can only be answered once. */
  session: boolean
}

/** Allow / ask / deny lists of `Tool(pattern)` rules (permrules.py). */
export interface PermissionRules { allow: string[]; ask: string[]; deny: string[] }

export interface PermissionEvaluation {
  action: 'allow' | 'ask' | 'deny' | 'none'
  hardline: boolean
  reason: string | null
  rule: string | null
  kind: string | null
  subjects: string[]
  suggestions: string[]
  external: string[]
}

/* ---- MCP connectors ---- */

/** How a tool's mode was arrived at, and whether the approved shape still matches the offered one. */
export interface McpEffective {
  slug: string
  mode: ToolMode
  source: 'default' | 'global' | 'project' | 'chat'
  /** The server changed this tool since it was approved, so an `on` has decayed back to `ask`. */
  stale: boolean
  approved_hash: string
  schema_hash: string
  missing: boolean
  known: boolean
}

export interface McpTool {
  id: string
  server_id: string
  /** The name the server exports. */
  name: string
  /** `mcp__<server>__<tool>`, derived by the backend and stable across reconnects. */
  slug: string
  description: string
  parameters: Record<string, unknown>
  schema_hash: string
  danger: ToolInfo['danger']
  first_seen_at: number
  last_seen_at: number
  /** Set when the advertised shape last changed. */
  schema_changed_at: number | null
  /** Set when the server stopped offering it; the row is kept so the slug cannot be reused. */
  missing_since: number | null
  effective: McpEffective
  /** Only on /mcp/tools: its server is connected right now. */
  ready?: boolean
  /** Set when the shape changed since the user last saw it; `quarantined` means it is withheld from the model. */
  drift?: McpDrift | null
}

export interface McpToolShape {
  description: string
  parameters: Record<string, unknown>
  schema_hash: string
  seen_at: number
}

export interface McpDrift {
  previous: McpToolShape
  current: McpToolShape
  diff: { description: string[]; added_params: string[]; removed_params: string[]; changed_params: string[]; new_required: string[] }
  new_findings: McpFinding[]
  quarantined: boolean
  changed_at: number
}

export interface McpFinding {
  code: string
  severity: 'info' | 'warn' | 'fail'
  where: string
  detail: string
  excerpt?: string
}

export interface McpEvalRecord {
  id: string
  server_id: string | null
  tool_slug: string
  status: 'pass' | 'warn' | 'fail' | 'error'
  summary: string
  findings: McpFinding[]
  created_at: number
}

/** The full report from a check; `evaluate_config`/`evaluate_server` shape. */
export interface McpReport {
  status: McpEvalRecord['status']
  summary: string
  findings: McpFinding[]
  /** What a static check cannot show. Always displayed with the verdict. */
  limits: string[]
  tools: { name: string; description: string; parameters: Record<string, unknown> }[]
  server_info: { name?: string; version?: string; protocol?: string; instructions?: string }
  stderr: string[]
  eval?: McpEvalRecord
}

export interface McpServer {
  id: string
  /** Derived backend-side from the name; never chosen by the client. */
  slug: string
  name: string
  transport: 'stdio' | 'sse' | 'http'
  command: string
  args: string[]
  cwd: string
  env: Record<string, string>
  /** Key names only — stored secret values never leave the backend. */
  secret_keys: string[]
  url: string
  headers: Record<string, string>
  description: string
  enabled: boolean
  status: string
  status_detail: string
  last_connected_at: number | null
  created_at: number
  updated_at: number
  tool_count: number
  live: {
    status: 'idle' | 'connecting' | 'ready' | 'error' | 'disabled' | string
    detail: string
    running: boolean
    ready: boolean
    attempts: number
    server_info: McpReport['server_info']
  }
  tools: McpTool[]
  eval: McpEvalRecord | null
}

/** A launch config, as the add form holds it and as /mcp/check takes it. */
export interface McpServerDraft {
  name: string
  transport: 'stdio' | 'sse' | 'http'
  command: string
  args: string[]
  cwd: string
  env: Record<string, string>
  secrets: Record<string, string>
  description: string
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
  /** Rule context for an ask card: the suggested rules to save and whether a session grant is offered. */
  permission?: PermissionCard | null
  /** Id of the proposal this call became: a background run may not complete an outward-facing call. */
  proposal?: string | null
  /** Set when this call's arguments matched an approved plan step, so it ran without its own card. */
  plan?: PlanStepRef | null
  /** Set when a subagent made this call: its card rides the parent's stream, labelled with the child. */
  agent?: string
  /** write_local_file / move_local_file: the pre-image kept so the user can undo it (id is null when too large to keep). */
  undo?: { snapshot_id: string | null; reason?: string | null } | null
  /** Set when the user rewrote the arguments on the approval card (approval_edits.py). `arguments` is then what ran. */
  edited_by?: 'user' | null
  /** What the model originally asked for, kept beside the edit so a card can show what changed. */
  original_arguments?: Record<string, unknown> | null
  edited_arguments?: Record<string, unknown> | null
  /** The artifact an artifact_create / artifact_update / artifact_edit call made. Persisted with the event, so the card survives a reload. */
  artifact?: ArtifactRef | null
}

/** Which artifact a tool call made, and what it did to it. */
export interface ArtifactRef {
  id: string
  title: string
  version: number | null
  action: 'created' | 'updated'
}

/** An AI-generated, self-contained HTML document with version history (backend/personal_os/artifacts.py). */
export interface Artifact {
  id: string
  project_id: string | null
  title: string
  kind: 'html'
  prompt: string
  version: number
  created_at: number
  updated_at: number
  conversation_id: string | null
  run_id: string | null
  message_id: string | null
  /** Signed, expiring path for the sandboxed iframe (the iframe cannot send the app token). */
  render_path: string
  /** Absent from list rows. */
  code?: string
  size?: number
  version_count?: number
  /** What the render CSP will silently break in this document: network, storage, form, ... */
  blocked?: string[]
  lint?: ArtifactLint
}

export interface ArtifactVersion {
  id: string
  artifact_id: string
  version: number
  prompt: string
  instruction: string
  source: 'llm' | 'user' | 'restore'
  created_at: number
  size?: number
  code?: string
  render_path?: string
}

/** Why a reply stopped early: a budget axis, or the repetition breaker. */
export type PartialReason = 'rounds' | 'tokens' | 'time' | 'cost' | 'loop' | 'stuck' | 'stuck_nudge'

export type SpanKind = 'context' | 'llm' | 'tool' | 'learn' | 'compact'

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
  /** The span this one nests under (a tool under its model round). Absent on older traces. */
  parent_id?: string
}

/** Opt-in OpenTelemetry export of finished replies (otel_export.py). */
export interface OtelExportConfig {
  enabled: boolean
  endpoint: string
  headers: Record<string, string>
  includeContent: boolean
  allowRemote: boolean
  timeoutSeconds: number
}

/** GET /conversations/{id}/context-meter: the replayed history against the model window (estimates, len/4). */
export interface ContextMeter {
  window: number
  estimated_tokens: number
  compact_at: number
  summary: { summary: string; summarized_messages: number; tokens_before: number; tokens_after: number; updated_at: number } | null
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
  /** A reasoning model's chain-of-thought. Never sent back to the model as history. */
  reasoning?: string | null
  created_at: number
  /** Set when the reply ran out of budget or hit a breaker; not persisted. */
  partial?: PartialReason | null
}

export type Effort = 'default' | 'low' | 'medium' | 'high' | 'xhigh' | 'max'

/** What a new chat starts on. `'default'` is a different choice: it omits `reasoning_effort`. */
export const DEFAULT_EFFORT: Effort = 'low'

export interface ConversationSettings {
  /** Reasoning effort passed through as `reasoning_effort`. 'default' sends nothing; 'xhigh' and 'max' are the rungs above high. */
  effort: Effort
  /** Priority processing (`service_tier: priority`). Off sends nothing, so a model that rejects it is unaffected. */
  fast?: boolean
  useMemory: boolean
  useGraph: boolean
  useDocuments: boolean
  /** Inject what the activity monitor observed. Defaults on, but only ever has an effect while the
   *  monitor is running and its own `injectContext` is left on. */
  useActivity: boolean
  /** Inject the writing-style profile, so drafts sound like the user. */
  useStyle: boolean
  /** Per-chat plan mode. Absent reads as the global default; a desk writes it when it is created. */
  planMode?: 'off' | 'auto' | 'always'
  /** Inject the recent-meetings block. Optional because stored conversations predate the key; a
   *  missing value reads as on, the way the backend's `.get(..., True)` does. */
  useMeetings?: boolean
  autoLearn: boolean
  useTools: boolean
  /** Inject the skills the user approved. Defaults on; only approved ones are ever eligible. */
  useSkills?: boolean
  tools: Record<string, ToolOverride>
  /** Sticky: a reply read untrusted content, so external tools keep asking and fetch_url stays restricted. */
  tainted?: boolean
  taint_sources?: string[]
  /** Set when this conversation is a scheduled job's transcript. Such chats are indexed by the Agent
   *  Inbox and left out of the sidebar list (GET /conversations?include_jobs=true includes them). */
  job_id?: string
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
  /** Validity interval: `invalid_at` set means superseded or forgotten (history), `superseded_by` is its replacement. */
  valid_from?: number | null
  invalid_at?: number | null
  superseded_by?: string | null
  source_conversation_id?: string | null
  source_message_id?: string | null
}

/** A pending tidy-up the user can apply or dismiss (backend consolidate.py). Nothing applies by itself. */
export interface MemoryProposal {
  id: string
  project_id: string | null
  kind: 'merge_memories' | 'rewrite_memory' | 'merge_entities'
  payload: { ids: string[]; text?: string; label?: string; snapshot: Record<string, string> }
  rationale: string
  status: 'pending' | 'applied' | 'dismissed' | 'stale'
  created_at: number
  decided_at: number | null
}

/** How the user writes, learned from samples of their own writing. One per scope. See backend style.py. */
export interface StyleProfile {
  id: string
  project_id: string | null
  summary: string
  guidelines: string[]
  traits: Record<string, string>
  phrases: string[]
  avoid: string[]
  enabled: number
  /** Hand-edited: auto-relearn leaves it alone until the user asks for a fresh read. */
  edited: number
  sample_count: number
  sample_chars: number
  model: string
  created_at: number
  updated_at: number
}

/** One passage of the user's own writing, kept so a profile can be re-derived and audited. */
export interface StyleSample {
  id: string
  project_id: string | null
  text: string
  source: 'chat' | 'doc' | 'paste' | string
  ref: string
  chars: number
  /** Already folded into the current profile. */
  folded: number
  created_at: number
}

export interface StyleState {
  /** This scope's own profile, null if it has none. */
  profile: StyleProfile | null
  stats: { samples: number; chars: number; pending: number }
  /** True when a project scope is falling back to the personal voice. */
  inherited: boolean
  /** What a chat in this scope would actually draft with. */
  effective: StyleProfile | null
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

/** Recurring todo: completing it spawns the next instance (backend todo_rules.py). */
export interface TodoRepeat {
  every: number
  unit: 'day' | 'week' | 'month' | 'year'
  mode: 'from_due' | 'from_completion'
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
  calendar_event_id: string | null
  calendar_link: string | null
  /** Which Google calendar the mirrored event lives on; null means the primary one. */
  calendar_id: string | null
  /** Internal: todo fields as last mirrored to the calendar. */
  calendar_sig: string | null
  repeat?: TodoRepeat | null
  /** Expected minutes of work; the planner time-blocks with it. */
  estimate_min?: number | null
  /** Weighted urgency score; only present on `?sort=urgency` lists. */
  urgency?: number
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

/** Verdict of the read-back that every external write goes through (backend verify.py).
 *  Anything other than 'verified' must not be rendered as success. */
export interface Verification {
  status: 'verified' | 'unverified' | 'mismatch'
  /** What was re-read, e.g. "calendar event ev1 on primary". */
  what: string
  /** The field names that were compared. */
  compared: string[]
  /** How many read-backs it took (bounded retry for eventual consistency). */
  attempts: number
  ms?: number
  reason?: 'not_visible' | 'read_failed' | 'field_mismatch' | 'still_present'
  detail?: string
  differences?: Record<string, { expected: unknown; actual: unknown }>
}

/** Any write result that carries a read-back verdict. */
export interface Verified {
  verified?: boolean
  verification?: Verification
}

/** One email waiting out its undo hold before Gmail sends it (backend outbox.py). */
export interface PendingSend {
  id: string
  to: string
  subject: string
  status: 'holding' | 'sending' | 'sent' | 'cancelled' | 'failed' | 'expired'
  origin: 'app' | 'assistant'
  conversation_id: string | null
  hold_seconds: number
  created_at: number
  send_after: number
  /** Counts down while holding, 0 otherwise. */
  seconds_left: number
  message_id: string | null
  thread_id: string | null
  error: string | null
  /** null until it has been sent. */
  verified: boolean | null
  verification?: Verification | null
  /** Only on the response to a send: false when the hold was off and it went straight out. */
  held?: boolean
}

export interface SendHoldConfig {
  enabled: boolean
  seconds: number
  min: number
  max: number
}

export interface CalendarEvent extends Verified {
  id: string
  calendar_id: string | null
  summary: string
  start: string
  end: string
  all_day: boolean
  location: string | null
  link: string | null
  attendees: string[]
  description: string
  meet: string
  color_id: string | null
  /** Set on instances of a recurring series; edit/delete via this id to touch the whole series. */
  recurring_event_id: string | null
  /** 'opaque' = busy, 'transparent' = free. */
  transparency: string
  status: string | null
  /** Editor-grade fields, present when fetched via getEvent / returned from create/update. */
  time_zone?: string | null
  recurrence?: string[] | null
  visibility?: string
  reminders?: { useDefault: boolean; overrides?: { method: string; minutes: number }[] } | null
  organizer?: string | null
  attendee_details?: EventAttendee[]
  guests_can_invite_others?: boolean
  guests_can_modify?: boolean
  guests_can_see_other_guests?: boolean
}

export interface EventAttendee {
  email: string
  optional: boolean
  /** accepted | declined | tentative | needsAction */
  response: string | null
  organizer: boolean
  self: boolean
}

export interface GoogleCalendar {
  id: string
  summary: string
  primary: boolean
  /** owner | writer | reader | freeBusyReader — only the first two can hold new events. */
  access_role: string
  color: string | null
  time_zone: string | null
  hidden: boolean
  selected: boolean
}

/** Google's fixed palettes, id -> hex. Events reference `event` ids via color_id. */
export interface CalendarColors { event: Record<string, string>; calendar: Record<string, string> }

/** Create/patch body for a calendar event; on update only the fields sent change. */
export interface EventPayload {
  summary?: string
  start?: string
  end?: string
  time_zone?: string
  description?: string
  location?: string
  attendees?: { email: string; optional?: boolean; response?: string }[]
  recurrence?: string[]
  reminders?: { use_default: boolean; overrides?: { method: string; minutes: number }[] }
  color_id?: string
  visibility?: string
  transparency?: string
  guests_can_invite_others?: boolean
  guests_can_modify?: boolean
  guests_can_see_other_guests?: boolean
  create_meet?: boolean
  clear_meet?: boolean
  calendar_id?: string
  move_to_calendar_id?: string
  send_updates?: 'none' | 'all' | 'externalOnly'
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

export interface GoogleTaskList {
  id: string
  title: string
}

/** Two-way todos <-> Google Tasks sync (`/integrations/google/tasks-sync`). */
export interface TasksSyncStatus {
  config: { enabled: boolean; tasklist: string; intervalMinutes: number }
  /** Unix seconds of the last successful pass. */
  last_sync: number | null
  last_error: string | null
  last_result: Record<string, number> | null
  syncing: boolean
}

/** One-way todos -> Google Calendar mirror (`/integrations/google/todo-calendar`). */
export interface TodoCalendarStatus {
  config: {
    enabled: boolean
    /** Empty until the first pass resolves or creates the calendar. */
    calendarId: string
    calendarName: string
    intervalMinutes: number
    keepCompleted: boolean
  }
  /** Unix seconds of the last successful pass. */
  last_sync: number | null
  last_error: string | null
  last_result: Record<string, number> | null
  syncing: boolean
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

/** A tracked health metric (`/health/metrics`). `agg` turns one day's readings into the day's value. */
export interface HealthMetric {
  key: string
  label: string
  unit: string
  /** 'scale' is 1-5; 'check' is yes (1) / no (0). */
  kind: 'number' | 'scale' | 'check'
  agg: 'sum' | 'last' | 'avg'
  goal: number | null
  goal_dir: 'at_least' | 'at_most' | null
  decimals: number
  builtin: boolean
  hidden: boolean
  position: number
}

/** One reading (`/health/entries`), filed under a local calendar day. */
export interface HealthEntry {
  id: string
  metric: string
  value: number
  day: string
  note: string
  source: string
  created_at: number
}

/** `/health/summary`: a metric with its daily series (oldest first, null where nothing was logged). */
export interface HealthSummary extends HealthMetric {
  today: number | null
  series: { day: string; value: number | null }[]
  avg: number | null
  prev_avg: number | null
  logged_days: number
  met_days: number
  streak: number
  last: { value: number; day: string } | null
}

/** A fitness service Health can pull from (`/health/providers`), connected through an MCP server. */
export interface HealthProvider {
  key: string
  label: string
  /** Connect-form fields: a select when `options` is set, a password box when `secret`. */
  needs: { key: string; label: string; options?: [string, string][]; default?: string; secret?: boolean }[]
  setup: string
  metrics: string[]
}

export interface HealthSyncResult {
  from: string
  to: string
  written: Record<string, number>
  unchanged: number
  problems: { tool: string; error: string; sample?: string }[]
}

/** A connected service (`/health/sources`). `pinned` is the tools the user approved, by schema hash. */
export interface HealthSource {
  id: string
  provider: string
  label: string
  server_id: string
  enabled: boolean
  pinned: Record<string, string>
  days_back: number
  last_sync_at: number | null
  last_error: string
  last_result: HealthSyncResult | Record<string, never>
  server: { id: string; name: string; transport: string; status: string; detail: string } | null
}

export interface HealthSourcePlan {
  source: HealthSource
  tools: { wants: string[]; tool: string | null; metrics: string[]; description: string; pinned: boolean; changed: boolean }[]
}

/** A remote MCP server's browser sign-in (`/mcp/servers/{id}/sign-in`). */
export interface McpSignIn {
  signed_in: boolean
  status: 'idle' | 'starting' | 'waiting' | 'done' | 'error'
  error: string
  auth_url: string
}

/** The Today card's slice of `/dashboard`. */
export type HealthToday = Pick<HealthMetric, 'key' | 'label' | 'unit' | 'kind' | 'goal' | 'goal_dir' | 'decimals'> & { today: number | null }

export interface TodayDashboard {
  google: GoogleStatus
  todos: Todo[]
  todo_stats: { open: number; overdue: number; today: number }
  /** Visible health metrics with today's value; missing from a backend without the health module. */
  health?: HealthToday[]
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
  /** The backend never returns secret values: apiKey etc. arrive blank and these say whether one is saved. */
  apiKeySet?: boolean
  braveApiKeySet?: boolean
  tavilyApiKeySet?: boolean
  exaApiKeySet?: boolean
  githubTokenSet?: boolean
  googleClientSecretSet?: boolean
  defaultModel: string
  systemPrompt: string
  extractionModel: string
  autoLearn: boolean
  /** Bank long messages and saved docs as writing samples, and keep the voice profile current. */
  learnStyle: boolean
  theme: 'dark' | 'light' | 'system'
  /** Pastel highlight colour. Missing on older settings rows means sage. */
  accent?: 'sage' | 'lilac' | 'sky' | 'rose' | 'mint' | 'fog'
  /** Legacy, pre-spaces global mode. Read once by init() (→ initial view 'canvas') and reset to 'classic'; nothing else reads it. */
  mode?: 'classic' | 'canvas'
  /** Electron accelerator for the global Gather/Scatter shortcut. */
  gatherShortcut: string
  /** Today-screen cards, keyed by module (see modules.ts); a missing key means shown. Cowork and meetings default off. */
  homeWidgets?: Record<string, boolean>
  /** Sidebar views the user removed. Missing means library, cowork and meetings are hidden. */
  hiddenViews?: string[]
  tools: Record<string, ToolMode | boolean>
  /** How assistant edits to docs land. Missing means review: show the diff and wait. */
  docEditMode?: 'review' | 'apply'
  maxToolRounds: number
  /** Argument-pattern rules over the per-tool modes. Deny beats ask beats allow; forced approvals are never lifted. */
  permissionRules?: PermissionRules
  /** 'deny': a background run that would have to ask is refused instead of waiting for someone. */
  unattendedApprovals?: 'ask' | 'deny'
  /** Keep the system prompt stable and put per-turn retrieval beside the newest message (prompt caching). Default on. */
  cacheLayout?: boolean
  otelExport?: OtelExportConfig
  /** Context management (compaction.py): window in tokens, thresholds as fractions of it. */
  contextWindow?: number
  autoCompact?: boolean
  compactAt?: number
  compactKeepRecent?: number
  microKeep?: number
  microAt?: number
  /** Per-reply budgets; 0 means unlimited. */
  maxRunTokens?: number
  maxRunSeconds?: number
  maxRunCost?: number
  /** Provider resilience and retention (backend llm.py / retention.py); missing means the shipped default. */
  llmRetries?: number
  llmIdleSeconds?: number
  retainUsageDays?: number
  retainTraceDays?: number
  retainToolResultDays?: number
  retainApprovalDays?: number
  /** Hosts fetch_url may still read once the reply has seen untrusted content. */
  fetchAllowlist?: string[]
  /** Folders where fs_edit / fs_copy / fs_mkdir run without asking (absolute paths inside the home folder). */
  workspaceRoots?: string[]
  /** Mount the active desk's workspace at /workspace/desk in its sandbox container. Missing means on. */
  sandboxMountDesk?: boolean
  /** Host shell. shellNetwork opens the network entirely; off, only the allowlist below is reachable. */
  shellNetwork?: boolean
  shellTimeoutSec?: number
  shellMaxBackground?: number
  shellRegistryAccess?: boolean
  shellAllowedDomains?: string[]
  /** In a desk, a sandboxed shell command inside the desk's own workspace runs without a card. Missing means on. */
  deskShellAuto?: boolean
  /** A desk may not finish with open plan steps or missing deliverables; a reviewer checks it against the brief. */
  deskDoneGate?: boolean
  deskSelfReview?: boolean
  /** The model pictures are sent to. Empty = the chat model when it reads images. */
  visionModel?: string
  /** The agent's own browser. */
  browserEnabled?: boolean
  browserMaxTabs?: number
  browserIdleSeconds?: number
  browserAllowlist?: string[]
  /** Extra packages for the shared work environment. */
  workEnvPackages?: string[]
  /** fs_edit and an overwriting write refuse a file this chat has not read. Missing means on. */
  requireReadBeforeWrite?: boolean
  braveApiKey: string
  tavilyApiKey: string
  /** Without a Brave/Tavily key, web search uses Exa (keyless, rate-limited); a key lifts the limit. */
  exaApiKey?: string
  /** Base URL of your own SearXNG; searched beside Exa and merged. Empty = off. */
  searxngUrl?: string
  /** Seconds fetch_url reuses a fetched page (0 = never). */
  fetchCacheSeconds?: number
  /** fetch_url retries a blocked or JavaScript-only page through Jina Reader (which then sees the URL). Default on. */
  readerFallback?: boolean
  /** github_search/github_read; empty uses the gh CLI's login. */
  githubToken?: string
  /** Per-model cost overrides, $ per million tokens. Proxy prices are used for models not listed. */
  modelPrices: Record<string, ModelPrice>
  /** Cowork desk budgets. 0 on either axis means unlimited; a desk may tighten them, never loosen. */
  deskMaxTurns?: number
  deskMaxCost?: number
  deskMaxLive?: number
  /** How long a desk waits on a card nobody is watching before the run lets go. 0 = wait forever. */
  parkAfterSeconds?: number
  /** A native notification when a desk stops and cannot go on without you. Missing reads as on. */
  deskNotify?: boolean
  /** A native notification when a scheduled job fails, is auto-paused or leaves proposals, while the window is hidden. Missing reads as on. */
  notifyJobs?: boolean
  /** Default plan mode for a new chat: off, auto (the first mutating call arms it), or always. */
  planMode?: 'off' | 'auto' | 'always'
  googleClientId: string
  googleClientSecret: string
  /** Undo window on outgoing mail. `seconds` is clamped to 60-120 by the backend. */
  gmailSendHold?: { enabled: boolean; seconds: number }
  /** Read-only here: the full shape is MeetingConfig, patched through /meetings/config so the merge is a deep one. */
  meetings?: { enabled: boolean }
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
  /** Input tokens served from the provider's prompt cache, and reasoning tokens inside completion_tokens. */
  cached_tokens?: number
  reasoning_tokens?: number
  cache_hit_rate?: number
  reasoning_share?: number
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
  | { event: 'reasoning'; data: { id: string; text: string } }
  | { event: 'tool_call'; data: { message_id: string; id: string; name: string; arguments: Record<string, unknown>; needs_approval?: boolean; forced?: boolean; permission?: PermissionCard | null; plan?: PlanStepRef | null; agent?: string } }
  | { event: 'tool_result'; data: ToolEvent & { message_id: string } }
  | { event: 'span'; data: { message_id: string; span: Span } }
  | { event: 'done'; data: { id: string; error: string | null; context_used: ContextUsed; tool_events: ToolEvent[]; trace: Span[]; stopped: boolean; partial?: PartialReason | null; segment?: boolean; tainted?: boolean; taint_sources?: string[]; reasoning?: string | null } }
  | { event: 'taint'; data: { message_id: string; source: string } }
  | { event: 'subagent'; data: SubagentInfo & { message_id: string | null } }
  /** artifact_create / artifact_update landed. Also on the run tape, so a reload replays it. */
  | { event: 'artifact'; data: ArtifactRef & { message_id: string; call_id: string; conversation_id: string } }
  | { event: 'plan'; data: { conversation_id: string; steps: PlanStep[] } }
  /** propose_plan opened a card. `plan` above is the todo_write checklist — a different thing. */
  | { event: 'plan_card'; data: { message_id: string; call_id: string; plan: PlanRecord } }
  /** How it was answered, so a window that was watching sees a decision made somewhere else. */
  | { event: 'plan_decision'; data: { message_id: string; call_id: string; plan: PlanRecord } }
  /** The reply let go of a card nobody was watching. The row stays pending and decidable. */
  | { event: 'parked'; data: { message_id: string; call_id: string; name: string } }
  /** A desk's row changed: the rail's label, its status, its counters. */
  | { event: 'desk_status'; data: Desk }
  /** A doc recording's segment, status or summary moved; see `RecordingEvent`. */
  | { event: 'recording'; data: RecordingEvent }
  /** This turn is handing over to another one, announced before `done` so the UI can re-attach. */
  | { event: 'desk_handoff'; data: { desk_id: string; conversation_id: string; turn: number } }
  | { event: 'learned'; data: Learned }
  | { event: 'style_learned'; data: { project_id: string | null; profile: StyleProfile | null; sample_id: string } }
  | { event: 'learn_error'; data: { message: string } }
  | { event: 'error'; data: { message: string } }

/** What one auto-learn pass (or the `remember` tool) put away. The ids are set only off `/events`. */
export interface Learned {
  memories: Memory[]
  /** Durable preferences auto-learn superseded or dropped, rather than adding a near-duplicate. */
  updated?: Memory[]
  removed?: Memory[]
  nodes: GraphNode[]
  edges: GraphEdge[]
  conversation_id?: string
  message_id?: string
}

/**
 * `GET /events`: app-wide work no single run is waiting on. Auto-learn runs here, after its reply's
 * run has already ended, so these never arrive on a conversation stream.
 */
export type BackgroundEvent =
  | { event: 'learned'; data: Learned }
  | { event: 'learn_error'; data: { conversation_id?: string; message_id?: string; message: string } }
  | { event: 'job_finished'; data: { run_id: string; job_id: string } }
  /** Every desk write, for desks nobody is watching: the rail, the badge and the Today card stay live. */
  | { event: 'desk_status'; data: Desk }
  /** A doc recording's segment, status or summary moved. */
  | { event: 'recording'; data: RecordingEvent }

export interface BackupInfo {
  name: string; kind: 'daily' | 'manual' | 'premigrate' | 'prerestore'; created_at: number; size: number
  app_version: string | null; schema_version: number | null
}
export interface DataOverview {
  data_dir: string; backups: BackupInfo[]; last_backup: number | null
  pending_restore: { name: string } | null; schema_version: number; app_version: string
}
/** The sidecar's lifecycle, as the main process supervises it. */
export type BackendState = 'starting' | 'ready' | 'restarting' | 'failed'
export interface BackendRestart {
  at: string
  reason: string
  outcome: 'restarted' | 'gave-up' | 'manual'
}
export interface BackendInfo {
  state: BackendState
  url: string
  error: string | null
  restarts: BackendRestart[]
  logDir: string
  appVersion: string
  electron: string
}

export interface GrainApi {
  backendUrl: () => Promise<string>
  backendStatus: () => Promise<{ running: boolean; url: string; error: string | null }>
  backendToken: () => Promise<string>
  /** Supervisor state and restart history; `restartBackend` also works from `failed`. */
  backendInfo: () => Promise<BackendInfo>
  restartBackend: () => Promise<BackendInfo>
  onBackendState: (cb: (info: BackendInfo) => void) => () => void
  /** Reveal the log folder in Finder. */
  openLogs: () => Promise<string>
  platform: NodeJS.Platform
  onMenu: (cb: (action: string) => void) => () => void
  popout: {
    open: (windowId: string, req?: PopoutOpenRequest) => Promise<boolean>
    close: (windowId: string) => Promise<boolean>
    focus: (windowId: string) => Promise<boolean>
    setPinned: (windowId: string, pinned: boolean) => Promise<boolean>
    setOpacity: (windowId: string, opacity: number) => Promise<boolean>
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
  /** Data folder helpers for Settings → Data (native dialog, Finder, restart to apply a restore). */
  data: {
    chooseExportPath: () => Promise<string | null>
    reveal: (path: string) => Promise<boolean>
    relaunch: () => Promise<void>
  }
  /** Closes the BrowserWindow this renderer lives in: the Cmd-W fall-through when no canvas window has focus. */
  closeSelf: () => void
  minimizeSelf: () => void
  /** A native notification about a desk, shown by main only while the window is unfocused; clicking opens that desk. */
  deskNotify: (payload: { title: string; body: string; deskId?: string }) => void
  /** The agent's interactive browser (hidden windows owned by main). The renderer never gets the bridge secret. */
  agentBrowser: {
    list: () => Promise<AgentBrowserSession[]>
    show: (session: string) => Promise<void>
    hide: (session: string) => Promise<void>
    /** Live JPEG frames after each action and at most every ~1.5 s while subscribed; returns the unsubscribe. */
    subscribe: (session: string, cb: (frame: AgentBrowserFrame) => void) => () => void
  }
}

export interface AgentBrowserSession {
  session: string
  url: string
  title: string
  tabs: number
  /** Seconds since the agent last used the session (it is torn down after its idle limit). */
  idleSeconds: number
  visible: boolean
}

export interface AgentBrowserFrame {
  dataUrl: string
  url: string
  title: string
  at: number
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
  id: string; dashboard_id: string; title: string; kind: 'html' | 'summary' | 'markdown' | 'chart' | 'stat' | 'table' | string; prompt: string; source_ids: string[]
  code: string; output: string; refresh_minutes: number; refreshed_at: number | null; position: number; width: number; height: number
  created_at: number; updated_at: number
  /** chart | stat | table only (widget_spec.py): the binding, the cached rows {rows, stat}, and why binding failed */
  spec?: Record<string, unknown>; data?: unknown; data_error?: string
}
/** What the render CSP would break, or an empty document (artifacts.lint). */
export interface ArtifactLint { blocked: string[]; empty: boolean; repaired?: boolean }
export interface Dashboard { id: string; name: string; description: string; created_at: number; widget_count?: number; widgets: Widget[] }
export interface Recap { day: string; content: string; created_at: number; cached?: boolean }

// ---------------- Cowork desks ----------------
/**
 * Named `Desk*` throughout, never a bare `Plan` / `PlanStep`: `Plan`/`PlanStep` is the todo_write
 * checklist and `ProposedPlan`/`ProposedStep` is propose_plan's approval record, which is the one a
 * desk carries. The CSS mirrors are `.cowork-` and `.desk-`.
 */

export type DeskStatus = 'draft' | 'planning' | 'awaiting_plan' | 'working' | 'needs_approval' | 'blocked'
  | 'paused' | 'interrupted' | 'review' | 'done' | 'failed' | 'stopped'

/** Mirrors `cowork.NEEDS_YOU`: the statuses that put a desk in the rail's "Needs you" section. */
export const NEEDS_YOU: DeskStatus[] = ['awaiting_plan', 'needs_approval', 'blocked', 'interrupted', 'review']
/** Mirrors `cowork.LIVE`: something is driving the desk right now. Also what `Desk.live` is computed from. */
export const DESK_LIVE: DeskStatus[] = ['planning', 'working', 'needs_approval']

export type DeskAutonomy = 'plan' | 'ask' | 'propose'
export type PlanDecision = 'approve' | 'edit' | 'reject'

/** One entry of POST /cowork/plans/{id}'s `steps`. `idx` is 1-based, exactly as the card numbers it. */
export interface PlanEdit { idx: number; arguments?: Record<string, unknown>; drop?: boolean }



export interface DeskBudget { maxTurns?: number; maxCost?: number }

export interface Desk {
  id: string
  conversation_id: string
  project_id: string | null
  title: string
  brief: string
  status: DeskStatus
  status_reason: string
  /** The rail's live "now" line, debounced server-side; never written per delta. */
  headline: string
  /** Set while a `desk_ask` card is open (or after it ran unanswered); cleared by the answer. */
  question: string
  autonomy: DeskAutonomy
  plan_id: string | null
  run_id: string | null
  /** "cowork/<id>", RELATIVE to the backend data dir. Never an absolute path. */
  workspace: string
  turn: number
  cost: number
  budget: DeskBudget
  last_error: string | null
  archived: boolean
  /** Derived: the status is in DESK_LIVE. */
  live: boolean
  /** Derived: unseen `needs_you` events on this desk. */
  unseen: number
  created_at: number
  updated_at: number
  ended_at: number | null
}

/** GET /cowork/desks/{id}: the desk plus everything the detail pane opens with. */
export interface FullDesk extends Desk {
  /** The plan the user approved for this desk: propose_plan's record, not the todo checklist. */
  plan: PlanRecord | null
  outputs: DeskOutput[]
  events: DeskEvent[]
  runs: RunInfo[]
  /** Every non-plan card this desk is waiting on, live or parked; answered in the desk pane. */
  approvals?: PendingApproval[]
}

export type DeskOutputStatus = 'proposed' | 'stale' | 'accepted' | 'promoted' | 'promote_failed' | 'rejected'
export type PromotionKind = 'doc' | 'doc_append' | 'document' | 'download'

export interface DeskOutput {
  id: string
  desk_id: string
  /** Workspace-relative, always under `outputs/`. */
  path: string
  title: string
  summary: string
  /** The digest recorded at delivery; `stale` means the file has moved on since. */
  sha256: string
  bytes: number
  run_id: string | null
  status: DeskOutputStatus
  promoted_kind: PromotionKind | string | null
  promoted_id: string | null
  /** The promoted copy was read back and matched. Read from the response, never assumed. */
  verified: boolean
  created_at: number
  updated_at: number
  decided_at: number | null
  /** Set by GET .../outputs when the file could not be re-hashed at all. */
  error?: string
}

export type DeskFileState = 'new' | 'modified' | 'unchanged'

export interface DeskFile {
  path: string
  bytes: number
  modified: number
  is_dir: boolean
  is_text: boolean
  state: DeskFileState
}

/** GET /cowork/desks/{id}/files */
export interface DeskFileTree { files: DeskFile[]; usage: { files: number; bytes: number } }

/** GET /cowork/desks/{id}/file — a character window, or a named-and-sized stub for a binary. */
export interface DeskFilePreview {
  path: string
  text: string
  bytes: number
  truncated: boolean
  binary: boolean
  state: DeskFileState
  /** Absent on the binary stub, which has no window. */
  offset?: number
  chars?: number
  /** Present only when truncated: where the next page starts. */
  next_offset?: number
}

/** GET /cowork/desks/{id}/preview — a scaled picture, a page of text, a page of extracted document text, or a reason. */
export type DeskRichPreview =
  | { kind: 'image'; data_url: string; width: number; height: number; bytes: number }
  | { kind: 'text'; text: string; offset: number; next_offset: number | null; total_chars: number }
  | { kind: 'document'; text: string; offset: number; next_offset: number | null; total_chars: number; note: string }
  | { kind: 'none'; reason: string }

/** GET /cowork/desks/{id}/diff — the unified diff against the pre-desk baseline. */
export interface DeskDiff {
  path: string
  state: DeskFileState
  diff: string
  truncated: boolean
  added: number
  removed: number
  /** False for a file the desk created: it diffs as all additions against empty. */
  has_baseline: boolean
}

export interface DeskEvent {
  id: string
  desk_id: string
  run_id: string | null
  /** status|plan|step|output|question|blocked|review|failed|promoted|interrupted|note */
  kind: string
  body: string
  data: Record<string, unknown>
  needs_you: boolean
  seen: boolean
  created_at: number
  /** GET /cowork/inbox only, so the Today card can name the desk without a second fetch. */
  desk_title?: string
}

/** One row of POST /cowork/desks/{id}/accept's response. */
export interface PromotionResult {
  output_id: string
  ok: boolean
  verified: boolean
  kind: string
  ref: string | null
  error?: string
}

// ---------------- Canvas Mode ----------------

/** Every widget a canvas window can host. Source of truth for `WIDGET_KINDS` in backend/personal_os/canvas.py. */
export type WidgetKind =
  | 'chat' | 'todos' | 'calendar' | 'board' | 'note' | 'dashboard-widget'
  | 'memory' | 'graph' | 'documents' | 'recap' | 'project' | 'usage' | 'activity' | 'web' | 'artifact'

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
  /** Window alpha while popped out, 0.2..1. 1 is opaque; the canvas ignores it. */
  opacity: number
  config: Record<string, unknown>
  created_at: number; updated_at: number
}

export interface Canvas {
  id: string; name: string; project_id: string | null; position: number
  snap_mode: SnapMode; grid_size: number; zoom: number; pan_x: number; pan_y: number
  wallpaper: string
  /** 0 | 1 — SQLite has no boolean. 1 freezes the view: no pan, no zoom, no window geometry. */
  locked: number
  created_at: number; updated_at: number
  windows: CanvasWindow[]
}

/** One row of the bulk `PUT /canvases/{id}/layout` body; every field but `id` is optional. */
export interface WindowLayout { id: string; x?: number; y?: number; w?: number; h?: number; z?: number; state?: WindowState }

export interface Note { id: string; project_id: string | null; body: string; color: string; created_at: number; updated_at: number }

/** One row in the trash (GET /trash). Deleting is soft: it sits here for `retention_days`, then is purged. */
export type TrashKind = 'project' | 'conversation' | 'doc' | 'document' | 'memory' | 'todo'
export interface TrashItem {
  type: TrashKind
  id: string
  title: string
  deleted_at: number
  /** When the automatic purge will erase it. */
  purge_at: number
  project_id: string | null
  project_name: string | null
  /** Projects only: how many chats / memories / uploads went into the trash with it. */
  contents?: Record<string, number>
}
export interface TrashListing {
  groups: { projects: TrashItem[]; conversations: TrashItem[]; docs: TrashItem[]; documents: TrashItem[]; memories: TrashItem[]; todos: TrashItem[] }
  total: number
  retention_days: number
}

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

/**
 * A folder in the Docs tree. `path` is the whole path ('Work/Research'); folders are rows of their
 * own so an empty one survives a reload, and so a rename can carry a subtree.
 */
export interface DocFolder {
  /** Which tree it is in: '' is personal, otherwise a project id. Paths are unique per scope only. */
  scope: string
  path: string
  name: string
  parent: string
  /** Docs filed directly in it. */
  docs: number
  /** Docs anywhere beneath it, itself included — what a collapsed row shows. */
  docs_deep: number
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
  /** An append proposal (a recording summary): the section to add. While pending, `before`/`after` are
   *  resolved against the doc as it stands, so the diff is just this section; null for ordinary edits. */
  append?: string | null
  /** GET /docs/revisions/{id} only: a unified diff, for copying out. */
  patch?: string
}
/** One window captured in a space preset: content + geometry, no live state (state/popout are not kept). */
export interface PresetWindow {
  kind: WidgetKind
  ref_id: string | null
  project_id: string | null
  title: string
  x: number; y: number; w: number; h: number
  z: number
  pinned: number /** 0 | 1 */
  /** window alpha while popped, 0.2..1 */
  opacity: number
  config: Record<string, unknown>
}
/** A named, user-saved template of a space. Instantiating it creates a new Canvas. */
export interface CanvasPreset {
  id: string
  name: string
  project_id: string | null
  snap_mode: SnapMode
  grid_size: number
  zoom: number; pan_x: number; pan_y: number
  wallpaper: string
  windows: PresetWindow[]
  created_at: number
  updated_at: number
}
/** POST /canvas-presets/{id}/instantiate: the new canvas plus how many preset windows were dropped (dangling refs). */
export type InstantiatedCanvas = Canvas & { skipped: number }

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

/** A subagent's live state (backend subagents.Subagents.info). */
export interface SubagentInfo {
  id: string
  parent_run_id: string
  role: string
  state: 'running' | 'completed' | 'partial' | 'error'
  exit_reason: string | null
  task: string
  rounds: number
  calls: number
  cost: number
  depth: number
  background: boolean
}

/** One row of a subagent's recorded tape (GET /runs/{id}/events). */
export interface RunTapeEvent {
  seq: number
  event: string
  data: Record<string, unknown>
}

export interface RunInfo {
  run_id: string
  conversation_id: string
  message_id: string | null
  seq: number
  started_at: number
  live: boolean
  /** Still producing a reply. `live` outlasts it by the auto-learn tail that follows the last `done`. */
  answering: boolean
  /** Durable status from agent_runs. */
  status?: 'running' | 'awaiting_approval' | 'done' | 'error' | 'interrupted'
  ended_at?: number | null
  error?: string | null
}

// ---------------- scheduled jobs + the Agent Inbox ----------------

/** One scheduled job (`jobs` table). `cron` is read in `timezone`, so it follows the wall clock through DST. */
export interface Job {
  id: string
  name: string
  /** 'cron' repeats on `cron` forever; 'once' fires at `run_at` and then switches itself off. */
  kind: 'cron' | 'once'
  /** Empty for a one-off. */
  cron: string
  /** The single instant a one-off runs at; null for a repeating job. */
  run_at: number | null
  timezone: string
  enabled: boolean
  prompt: string
  project_id: string | null
  /** When the last fire actually started, and the slot it was *for*: apart means it ran late. */
  last_fired_at: number | null
  last_due_at: number | null
  last_run_id: string | null
  last_error: string | null
  /** The slot the scheduler is waiting for. null when the job is disabled. */
  next_due_at: number | null
  created_at: number
  updated_at: number
  /** Re-launches of a run that ended in an error, with backoff. 0 = never retry. */
  max_retries: number
  /** Fires in a row that ended in failure; at the streak limit the job is paused. */
  consecutive_failures: number
  /** Why the scheduler switched this job off by itself. null for a job the user turned off. */
  paused_reason: string | null
  last_skip_at: number | null
  last_skip_reason: string | null
  /** The only tools this job's runs may use. null = every tool (the default); it can only narrow, never widen. */
  allowed_tools: string[] | null
}

/** An outward-facing call a background run recorded instead of making. Accepting it is what runs it. */
export interface AgentProposal {
  id: string
  run_id: string | null
  job_id: string | null
  conversation_id: string | null
  message_id: string | null
  call_id: string | null
  tool: string
  args: Record<string, unknown>
  args_digest: string
  status: 'pending' | 'accepted' | 'rejected'
  result: unknown
  error: string | null
  /** The user changed the arguments before accepting. */
  edited: boolean
  created_at: number
  decided_at: number | null
}

/** One job run as "While you were away" shows it. Every field but `summary` comes from a row, not from prose. */
export interface JobRunSummary {
  run_id: string
  conversation_id: string | null
  status: 'running' | 'awaiting_approval' | 'done' | 'error' | 'interrupted'
  job_id: string | null
  job: string
  kind: 'cron' | 'once'
  due_at: number | null
  fired_at: number
  late: boolean
  late_seconds: number
  missed_slots: number
  manual: boolean
  /** 1 for the first launch of a slot; 2+ for a retry of the run `retry_of`. */
  attempt: number
  retry_of: string | null
  started_at: number
  ended_at: number | null
  error: string | null
  tool_calls: number
  proposals: number
  pending_proposals: number
  /** The run's own report, from the event tape. Shown as the body; nothing is parsed out of it. */
  summary: string
}

/** One run in a job's History drawer (GET /jobs/{id}/runs). Derived from rows; `summary` is display text only. */
export interface JobRunRecord {
  run_id: string
  conversation_id: string | null
  status: 'running' | 'done' | 'error' | 'interrupted' | 'timed_out'
  started_at: number
  ended_at: number | null
  duration_s: number | null
  due_at: number | null
  late: boolean
  missed_slots: number
  attempt: number
  retry_of: string | null
  manual: boolean
  tool_calls: number
  proposals: { pending: number; accepted: number; rejected: number }
  cost: number | null
  error: string | null
  summary: string
}

export interface JobStats {
  runs: number
  ok: number
  failed: number
  success_rate: number | null
  median_duration_s: number | null
  last_ok_at: number | null
  total_cost: number
}

/** One OS-notification-worthy job event (GET /inbox/notify). Names and counts only, never reply text. */
export interface JobNotifyEvent {
  id: string
  kind: 'job_failed' | 'job_done_with_proposals' | 'job_paused' | 'proposal_pending'
  title: string
  body: string
  at: number
}

export interface AgentInbox {
  needs_you: {
    approvals: (PendingApproval & { run_kind?: string | null; job?: string | null })[]
    proposals: AgentProposal[]
    paused_jobs: { id: string; name: string; reason: string; paused_at: number; consecutive_failures: number }[]
  }
  while_you_were_away: JobRunSummary[]
  counts: { needs_you: number; approvals: number; proposals: number; paused_jobs: number; runs: number; late: number; failed: number }
  scheduler: { last_tick: number | null; fires: number; next_due_at: number | null; timezone: string }
}

/** A tool call waiting on the user (`approvals` table). */
export interface PendingApproval {
  call_id: string
  run_id: string | null
  conversation_id: string | null
  message_id: string | null
  tool: string
  args: Record<string, unknown>
  args_digest: string
  forced: boolean
  status: 'pending' | 'approved' | 'denied'
  decision: string | null
  decided_by: string | null
  created_at: number
  decided_at: number | null
  /** The tool's danger tier, copied onto the row when the card opened. */
  danger?: string
  desk_id?: string | null
  /** Set when a desk's run let go of the card; it stays decidable and wakes the desk. */
  parked_at?: number | null
  /** A run in this process is waiting on it right now. */
  live?: boolean
}

/** 409 detail of POST /conversations/{id}/chat when that conversation already has a live run. */
export interface RunConflict { message: string; run_id: string; seq: number }

/** One detached widget window as the main process sees it. */
export interface PopoutInfo { windowId: string; bounds: PopoutBounds; pinned: boolean; opacity: number }

export interface PopoutOpenRequest { bounds?: Partial<PopoutBounds>; minWidth?: number; minHeight?: number; title?: string; pinned?: boolean; opacity?: number }

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
  /** Strings or /regex/ that are never scrubbed. */
  redactAllow: string[]
  /** Strings or /regex/ that are always scrubbed. */
  redactDeny: string[]
  /** Score a candidate needs before it is scrubbed (0.2-0.9). */
  redactThreshold: number
  /** Category rules; null means the shipped default tree. */
  categories: ActivityCategoryRule[] | null
  audio: ActivityAudioConfig
  /** Blank falls back to the extraction model, then the default model. */
  summaryModel: string
  profileEveryHours: number
  /** Palantir mode: every signal on, redaction off, both exclusion lists emptied. */
  palantir: boolean
  insights: ActivityInsightConfig
}

export interface ActivityInsightConfig {
  enabled: boolean
  /** How often the habit/suggestion pass runs on its own. 0 turns the schedule off. */
  everyHours: number
  lookbackDays: number
  /** A pattern has to recur on at least this many days before it counts. */
  minDays: number
  maxSuggestions: number
  /** Write confident habits into the app's memory, where chats already read from. */
  autoMemory: boolean
  memoryConfidence: number
}

/** One thing the miner noticed, computed locally with no model. This is the evidence. */
export interface ActivityPattern {
  id: string
  /** app_routine | site_habit | thrash | deep_work | day_shape | after_hours | input_load |
   *  recurring_window | topic | switch_rate */
  kind: string
  title: string
  detail: string
  support: number
  days: number
  confidence: number
  evidence: Record<string, unknown>
}

/** A durable statement about how the user works. Owns at most one row in the memory panel. */
export interface ActivityHabit {
  id: string
  key: string
  statement: string
  kind: string
  confidence: number
  /** How many passes have seen it. */
  support: number
  evidence: string[]
  /** The memory this habit wrote; `''` when it was not confident enough, or autoMemory is off. */
  memory_id: string
  first_seen: number
  last_seen: number
}

export type InsightKind = 'automation' | 'platform' | 'hygiene'
export type InsightStatus = 'new' | 'accepted' | 'done' | 'dismissed' | 'snoozed'
/** `prompt` is the common one and it acts on nothing: it hands back a message to send. */
export type InsightActionType = 'prompt' | 'todo' | 'memory' | 'setting' | 'none'

export interface InsightAction {
  type: InsightActionType
  prompt?: string
  title?: string
  content?: string
  how?: string
}

/** A proposal, never a change. Dismissing one is permanent; a refresh will not raise it again. */
export interface ActivitySuggestion {
  id: string
  key: string
  kind: InsightKind
  title: string
  detail: string
  why: string
  impact: string
  effort: 'low' | 'medium' | 'high'
  action: InsightAction
  /** Pattern ids this rests on. */
  evidence: string[]
  confidence: number
  status: InsightStatus
  status_note: string
  snooze_until: number
  created_at: number
  updated_at: number
}

export interface ActivityInsights {
  enabled: boolean
  generated_at: number
  last_run: number
  next_run: number
  last_error: string
  window: { days?: number; first_day?: string; last_day?: string }
  totals: { focus_seconds?: number; idle_seconds?: number; keys?: number; clicks?: number; scrolls?: number; switches?: number }
  apps: { app: string; seconds: number; days: number }[]
  /** Host only - never a path or a query string. */
  hosts: { host: string; visits: number; days: number }[]
  hours: { hour: number; seconds: number }[]
  patterns: ActivityPattern[]
  habits: ActivityHabit[]
  suggestions: ActivitySuggestion[]
  counts: { open: number; accepted: number; dismissed: number; habits: number; days: number }
}

/** What came back from applying one suggestion. `prompt` means nothing happened yet - send it. */
export interface ActivityApplyResult {
  type: InsightActionType
  prompt?: string
  how?: string
  todo?: Todo
  memory?: Memory
  suggestion: ActivitySuggestion
}

/** What macOS currently thinks about one permission. `n/a` means nothing on this Mac needs it. */
export type ActivityPermissionState = 'granted' | 'denied' | 'unasked' | 'unknown' | 'n/a' | ''

/** One row of the capability checklist: what this machine can do, and how to fix what it can't. */
export interface ActivityCapability {
  id: string
  label: string
  ok: boolean
  detail: string
  /** Empty when `ok`. */
  fix: string
  /** Set only for the macOS permissions; `''` for rows that are just a yes/no about this machine. */
  state: ActivityPermissionState
  /** True when pressing Grant can make macOS ask for this one. */
  requestable: boolean
  /** Deep link into the matching Privacy & Security pane; `''` when there isn't one. */
  settings_url: string
  /** Which signals this row gates. */
  signals: ActivitySignal[]
  /** Missing this only costs one optional signal, never the monitor as a whole. */
  optional: boolean
  /** The grant only reaches a running process after a restart. */
  restart: boolean
  /** Per-browser Automation states on the `automation` row. */
  extra: { name: string; state: ActivityPermissionState }[]
}

/** What came back from pressing Grant. `prompted` is false when macOS refuses to ask at all. */
export interface ActivityGrantResult {
  id: string
  state: ActivityPermissionState
  prompted: boolean
  note: string
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
  /** Palantir mode is on: every signal recording and the gate's filters down. */
  palantir: boolean
  /** Redactions so far today, by entity. Counts only. */
  redactions?: Record<string, number>
}

export interface ActivityCategoryRule {
  name: string[]
  rule?: { type: 'regex' | 'none'; pattern?: string; fields?: ('app' | 'title')[]; hosts?: string[] }
  /** Productivity, -2 (distracting) to 2 (productive); inherited from the parent when absent. */
  score?: number
}

export interface ActivityCategoryReport {
  days: { day: string; total_seconds: number; cats: Record<string, number> }[]
  totals: Record<string, number>
  productivity: number | null
  top_uncategorized_apps: { app: string; seconds: number }[]
}

export interface ActivityRedactTest {
  redacted: string
  active: boolean
  spans: { entity: string; score: number; start: number; end: number }[]
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

/** ---- meetings -----------------------------------------------------------
 *  A recorded conversation plus the notes taken during it. The fourth text-bearing type, and
 *  distinct from the other three: `Doc` is markdown the user writes, `Document` is a file they
 *  uploaded and had chunked for retrieval, `Note` is canvas mode's sticky. A meeting is the only
 *  one whose body is partly machine-made, so it keeps the two apart — `notes` is what the user
 *  typed and has exactly one writer, `enhanced` is only ever set by accepting a MeetingRevision.
 *  Nothing here expires: unlike ActivityEvent there is no `expires_at`, so /activity/purge cannot
 *  reach a meeting. */

export type MeetingStatus =
  | 'scheduled' | 'recording' | 'stopped' | 'transcribing' | 'enhancing' | 'ready' | 'failed' | 'notes_only'

/** Shapes the enhance prompt and the notes skeleton; keys into meeting_notes.TEMPLATES. */
export type MeetingTemplate = 'general' | 'standup' | 'one_on_one' | 'user_interview' | 'sales_call' | 'lecture'

/** A list row: counts and a preview, never a body. */
export interface Meeting {
  id: string
  title: string
  project_id: string | null
  status: MeetingStatus
  template: MeetingTemplate
  /** First 240 characters of the notes, for the list rail. */
  notes_preview: string
  words: number
  segment_count: number
  /** An enhance proposal is waiting to be accepted or rejected. */
  has_pending: boolean
  duration_ms: number
  attendee_count: number
  started_at: number | null
  scheduled_start: number | null
  ended_at: number | null
  updated_at: number
  /** Last recorder/stt/enhance failure, shown as a banner; empty when fine. */
  error: string
  /** The doc this recording belongs to; null for an ordinary meeting. */
  doc_id: string | null
  doc_mode: DocRecordingMode | null
  /** The `doc_revisions.id` of the latest proposed summary, if one was made. */
  summary_revision_id: string | null
}

/** How a recording relates to its doc: `record` keeps a transcript and proposes a summary,
 *  `dictate` types what is said into the note and keeps no summary. */
export type DocRecordingMode = 'record' | 'dictate'
/** Where a recording's summary stands in the doc it was proposed into. */
export type SummaryState = 'none' | 'pending' | 'applied' | 'rejected'
/** GET /docs/{id}/recordings row. */
export interface DocRecording extends Meeting { summary_state: SummaryState }
/** The app-wide `recording` event: a segment settled, the recorder changed state, or a summary landed. */
export interface RecordingEvent {
  kind: 'segment' | 'status' | 'summary'
  meeting_id: string
  doc_id: string | null
  doc_mode: DocRecordingMode | null
  segment?: MeetingSegment
  status?: string
  revision_id?: string | null
  error?: string | null
}

/** A meeting with its bodies loaded — what GET /meetings/{id} returns. */
export interface FullMeeting extends Omit<Meeting, 'notes_preview'> {
  /** What the user typed. No model ever writes this. */
  notes: string
  /** The accepted enhanced markdown; empty until a revision is applied. */
  enhanced: string
  summary: string
  attendees: MeetingAttendee[]
  /** What was actually captured, e.g. ['mic'] on a machine with no loopback device. */
  sources: string[]
  calendar_event_id: string | null
  calendar_id: string | null
  calendar_link: string
  /** Meet/Zoom/Teams URL; a calendar event's own `meet` field is hangoutLink only. */
  conference_link: string
  keep_audio: boolean
  /** Display names for diarized speaker ids, e.g. { S1: 'Dana' }. */
  speaker_names: Record<string, string>
  /** Retained wav bytes, against the disk ceiling. */
  audio_bytes: number
  conversation_id: string | null
  /** The newest unresolved enhance proposal, if any. */
  pending: MeetingRevision | null
  actions: MeetingActionItem[]
}

export interface MeetingAttendee {
  email: string
  name: string
  /** accepted | declined | tentative | needsAction */
  response: string
  organizer: boolean
  self: boolean
}

/** One closed ffmpeg segment and its transcription. */
export interface MeetingSegment {
  id: string
  meeting_id: string
  /** Attribution is channel-level only: mic = you, output/import = everyone else. */
  channel: 'mic' | 'output' | 'import'
  /** ffmpeg's segment number, so ordering survives a restart. */
  seq: number
  /** Seconds from the start of the meeting, off the recording clock rather than when transcription returned. */
  t_start: number
  t_end: number
  /** Absolute epoch seconds of the segment's first sample. */
  started_at: number
  duration_ms: number
  text: string
  /** '' until diarized; 'me' by convention for mic. */
  speaker: string
  /** recorded | transcribing | done | failed | empty | discarded */
  state: string
  /** 'proxy' | 'local', for the usage/debug line. */
  backend: string
  error: string
  /** GET /meetings/{id}/segments?since= only: the rowid to poll from next. */
  cursor?: number
}

/**
 * An enhance proposal. EXTENDS DocRevision on purpose: <DiffView> is typed `revision: DocRevision`
 * (DiffView.tsx:101) and the backend's `_rev_view` emits those exact field names with `doc_id` set
 * to the meeting id, so the existing diff UI renders a meeting revision with no adapter.
 */
export interface MeetingRevision extends DocRevision {
  meeting_id: string
  template: string
  model: string
  /** The LLM failed and this is the mechanical fallback. */
  degraded: boolean
  decisions: string[]
  topics: string[]
}

/** Recorded here first and promoted into a Todo on demand, so the review screen can show which already are tasks. */
export interface MeetingActionItem {
  id: string
  meeting_id: string
  text: string
  /** Attendee email or display name; empty when unassigned. */
  owner: string
  /** YYYY-MM-DD, empty when none. */
  due: string
  status: 'proposed' | 'added' | 'dismissed'
  /** The todo it became. No FK on purpose: deleting the task must not erase that this meeting produced the item. */
  todo_id: string | null
}

/** Mirrors meetings.DEFAULT_CONFIG. Patched through /meetings/config rather than /settings, so the merge is a deep one. */
export interface MeetingConfig {
  enabled: boolean
  /** Unix seconds the consent modal was acknowledged; 0 means never, and recording stays blocked. */
  consentedAt: number
  /** Start capturing when a calendar meeting begins instead of only offering to. */
  autoRecord: boolean
  /** How early a calendar event is offered as a candidate. */
  nudgeSeconds: number
  /** AVFoundation uniqueID, or ffmpeg avfoundation index as a string. Empty means default / not chosen. */
  micDevice: string
  /** The name that index had when it was chosen, so a reshuffled device list is refused rather than recorded. */
  micDeviceName: string
  /** Loopback device when the Core Audio tap is unavailable. Empty when the tap is used. */
  outputDevice: string
  outputDeviceName: string
  /** Channels to capture; validated against meetings.SOURCES ('mic', 'output'). */
  sources: string[]
  /** Segment-muxer length: how far behind live the transcript runs. */
  segmentSeconds: number
  /** Hard cap so no capture can run unbounded. */
  maxMeetingSeconds: number
  /** How long stop() waits for the transcription queue to drain. */
  drainSeconds: number
  /** 'off' blocks Start outright rather than recording audio nothing will read. */
  sttBackend: 'auto' | 'speech' | 'proxy' | 'local' | 'off'
  /** Speech-to-text model on the configured LLM base URL. */
  sttModel: string
  /** whisper.cpp ggml model file, for the local backend. */
  whisperModelPath: string
  template: MeetingTemplate
  enhanceOnStop: boolean
  /** Blank falls back to the extraction model, then the default model. */
  enhanceModel: string
  /** Head-and-tail cap on the transcript sent to the model; decisions land at the end. */
  maxTranscriptChars: number
  keepAudio: boolean
  /** Disk ceiling for retained wavs, oldest failed segment evicted first. */
  maxAudioBytes: number
  /** Scrub credential-shaped strings before anything is stored. Never activity's identity rules, which
   *  replace every email with [email] and every phone number with [phone]. */
  redactSecrets: boolean
  /** Feed recent meetings into chats at all. */
  injectContext: boolean
  /** Auto-stop this long after the scheduled end. Purely time-based: there is no voice-activity detection. */
  autoStopGraceSeconds: number
  calendarIds: string[]
  /** Events with fewer attendees than this are never offered. */
  minAttendees: number
  /** Skip STT for segments with no speech, and drop known silence hallucinations. */
  vadGate: boolean
  vadMinSpeechRatio: number
  hallucinationFilter: boolean
  whisperVadModelPath: string
  /** Longest audio file an import accepts. */
  maxImportSeconds: number
  /** Separate remote speakers on retained audio (needs the optional sherpa-onnx backend). */
  diarize: boolean
  diarizeBackend: 'auto' | 'none' | 'sherpa'
  diarizeSegmentationModel: string
  diarizeEmbeddingModel: string
  diarizeThreshold: number
  diarizeSpeakers: number
  /** Run each dictated clip through a model that only fixes punctuation, case and fillers. */
  dictationCleanup: boolean
}

/** One row of the capability checklist: what this machine can do, and how to fix what it can't. */
export interface MeetingCapability {
  id: string
  label: string
  ok: boolean
  detail: string
  /** Empty when `ok`. */
  fix: string
}

/** GET /meetings/status. Cheap enough to poll: unlike /activity/status it never spawns a subprocess. */
export interface MeetingStatusInfo {
  enabled: boolean
  /** The consent modal has been acknowledged. */
  consented: boolean
  config: MeetingConfig
  active: {
    meeting_id: string
    status: MeetingStatus
    started_at: number
    elapsed_ms: number
    segments_done: number
    segments_pending: number
    /** Waiting on the transcription queue, for the honest "~20s behind · N queued" line. */
    queued: number
    /** Pause keeps capture running and throws the audio away, so `channels[].alive` stays true while
     *  paused. This flag is the only honest source of pausedness; never infer it from the channels. */
    paused: boolean
    channels: { channel: string; alive: boolean; error: string }[]
    error: string
    /** Set when the live recording belongs to a doc; null for an ordinary meeting. */
    doc_id: string | null
    doc_mode: DocRecordingMode | null
    /** The clip length this session was started with, so "N s behind" is per recording. */
    segment_seconds: number
  } | null
  upcoming: MeetingCandidate[]
  /** `loopback` marks the devices that can carry system audio. */
  devices: { index: string; name: string; loopback: boolean }[]
  stt: { backend: string; ok: boolean; detail: string }
  counts: { total: number; pending: number }
}

/** POST /meetings/preflight. `ok` false blocks Start rather than warning. */
export interface MeetingPreflight {
  ok: boolean
  blockers: MeetingCapability[]
  capabilities: MeetingCapability[]
  /** A real round trip: a synthesized silent wav, recorded and transcribed. */
  selftest: { ok: boolean; backend: string; record_ms: number; transcribe_ms: number; text: string; error: string }
}

/** A calendar event the 45s tick offers to take notes on. No LLM is involved. */
export interface MeetingCandidate {
  event_id: string
  calendar_id: string
  title: string
  /** Google's ISO timestamps, as CalendarEvent carries them. */
  start: string
  end: string
  attendee_count: number
  /** Someone other than the user is invited. */
  has_external: boolean
  conference_link: string
  /** Set once a meeting row exists for this event, so the nudge is not offered twice. */
  meeting_id: string | null
}

/** One frame of the per-meeting SSE stream. */
export interface MeetingStreamEvent {
  event: 'segment' | 'status' | 'error' | 'revision' | 'end'
  data: unknown
}

/** Reply tracker row (`/mail/watch`): who owes whom an answer. */
export interface MailWatchThread {
  thread_id: string
  subject: string
  status: 'to_reply' | 'awaiting_reply' | 'fyi' | 'actioned'
  reason: string
  last_from: string
  last_date: string | null
  age_days: number
  dismissed: number
  followup_todo_id: string | null
}
export interface MailWatchList {
  threads: MailWatchThread[]
  counts: { to_reply: number; awaiting_reply_overdue: number }
  followups: { thread_id: string; title: string; notes: string; due: string }[]
}

/** One proposed calendar block from `/planner/suggest`; nothing is written until it is applied. */
export interface PlannerBlock {
  todo_id: string
  title: string
  start: string
  end: string
  score: number
  part: [number, number]
  why?: { due: number; priority: number; energy: number; time: number }
}
export interface PlannerSuggestion {
  blocks: PlannerBlock[]
  unplaced: { id: string; reason: string }[]
  already_planned: string[]
  generated_at: string
}
export interface PlannerApplyResult {
  results: { todo_id: string | null; ok: boolean; event_id?: string; link?: string; error?: string }[]
}

/** What a reply changed in the granted folders (snapshots.py), and whether Undo / Redo is on offer. */
export interface RunChanges {
  run_id?: string
  available: boolean
  count: number
  state: 'applied' | 'undone'
  files: { root: string; status: 'A' | 'M' | 'D'; path: string }[]
  skipped: string[]
}
export interface RunUndoResult { ok: boolean; direction: 'undo' | 'redo'; reverted: string[]; edited_since: string[] }
// ---- Workflows and commands (backend workflows.py / commands.py) ----
export type WorkflowParamType = 'string' | 'number' | 'integer' | 'boolean' | 'list' | 'object'
export interface WorkflowParam { type: WorkflowParamType; required: boolean; default: unknown }
export type WorkflowStepKind = 'tool' | 'agent' | 'fan_out'
export interface Workflow {
  id: string
  name: string
  description: string
  /** The text as the user wrote it (JSON, or YAML when the backend can read it). */
  text: string
  /** Hash of the normalized definition; any edit changes it and withdraws the approval of runs proposed earlier. */
  digest: string
  params: Record<string, WorkflowParam>
  created_at: number
  updated_at: number
}
export type WorkflowRunStatus =
  | 'awaiting_approval' | 'running' | 'waiting_approval' | 'done' | 'failed' | 'cancelled' | 'interrupted' | 'stale'
export type WorkflowStepStatus = 'pending' | 'running' | 'waiting_approval' | 'done' | 'failed' | 'skipped' | 'blocked'
export interface WorkflowStepRow {
  step_id: string
  idx: number
  kind: WorkflowStepKind
  status: WorkflowStepStatus
  result: unknown
  items: Record<string, unknown> | null
  error: string | null
  approval_call_id: string | null
  idempotency_key: string
}
/** One step of the expanded plan the user approves: parameters filled in, step results still shown as {{step.result}}. */
export interface WorkflowPlanStep {
  id: string
  kind: WorkflowStepKind
  needs: string[]
  approval: 'required' | null
  tool?: string
  args?: Record<string, unknown>
  agent?: Record<string, unknown>
  fan_out?: Record<string, unknown>
  when?: unknown
}
export interface WorkflowRun {
  id: string
  workflow_id: string | null
  name: string
  params: Record<string, unknown>
  plan_digest: string
  approved_digest: string | null
  status: WorkflowRunStatus
  error: string | null
  result: unknown
  source: string
  created_at: number
  updated_at: number
  started_at: number | null
  ended_at: number | null
  steps: WorkflowStepRow[]
  /** Not sent in the run list. */
  plan?: WorkflowPlanStep[]
}
export interface Command {
  id: string
  name: string
  description: string
  body: string
  subtask: boolean
  role: string | null
  text: string
}
