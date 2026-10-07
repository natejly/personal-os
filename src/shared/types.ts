export type Role = 'system' | 'user' | 'assistant'

export interface Project {
  id: string
  name: string
  description: string
  system_prompt: string
  color: string
  tools: Record<string, ToolOverride>
  /** 'isolated' = chats here see no personal memory, graph, docs, skills or voice, and save nothing personal. */
  memory_mode: 'shared' | 'isolated'
  created_at: number
  stats?: { conversations: number; memories: number; nodes: number; documents: number; docs?: number }
}

/**
 * A cited source. An excerpt has chunk_id; a read span (kind 'range': a pinned file, a read_document slice, or doc) has start/end into the text its viewer loads; a web page (source 'web') has url and opens in the browser.
 */
export interface Citation {
  name: string
  text: string
  n?: number
  source?: string
  chunk_id?: string
  document_id?: string
  doc_id?: string | null
  idx?: number
  heading?: string
  page?: number | null
  kind?: 'range'
  start?: number
  end?: number
  url?: string
  title?: string
  domain?: string
  /** Set once the reply is saved: the excerpt sentence that best matches the citing sentence, and how well. */
  quote?: string
  support?: 'ok' | 'weak' | 'invalid'
}

export interface ContextUsed {
  project: { id: string; name: string } | null
  memories: { id: string; content: string; project_id: string | null }[]
  nodes: { id: string; label: string; type: string; kind?: 'self' | 'value' }[]
  edges: { id: string; relation: string; source_id: string; target_id: string }[]
  /** Every source the reply may cite. `n` is its citation number ("[n]"); absent on messages saved before citations. */
  chunks: Citation[]
  /** Approved skills injected as procedural memory. Absent on messages written before skills existed. */
  skills?: { id: string; name: string; description: string }[]
  /** What the user was looking at when they asked, when the turn came from the page agent (⌘I). */
  page: PageContext | null
  /** The writing-style profile this reply drafted with; null when there is none or the chat opted out. */
  style: { project_id: string | null; summary: string; guidelines: string[]; block: string } | null
  /** Pinned documents carried whole this turn. Absent on older messages. */
  pinned?: { document_id: string; name: string }[]
  /** The always-on standing preferences (pinned rows plus preference and instruction rows) carried in the system prompt every turn. Absent on older messages. */
  profile?: { id: string; content: string; project_id: string | null; pinned: boolean }[]
  /** Items dropped per section because it hit its share of the model's context window. */
  trimmed?: Record<string, number>
  /** Built-in tools held out of the request until tool_search loads them (toolDeferAbove). Absent on older messages. */
  tools_deferred?: number
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
  use_count?: number
  last_used_at?: number | null
  /** Why auto-learn suggested it: the friction it would remove, or what went wrong with the skill it revises. */
  rationale?: string
}

/** Teach a task: the step draft extracted from a screen recording (teach.py normalize). */
export interface TeachStep {
  n: number
  app: string
  action: string
  detail: string
  /** The recording frame that shows this step, when the model named one. */
  frame: number | null
}
export interface TeachDraft {
  title: string
  goal: string
  inputs: { name: string; example: string }[]
  steps: TeachStep[]
}
export interface TeachRecording {
  id: string
  created_at: number
  status: 'recording' | 'ready' | 'extracted' | 'saved'
  source: 'screen' | 'import'
  frame_count: number
  steps: TeachDraft | null
  skill_id: string | null
  job_id: string | null
  /** Seconds since Start, only while recording. */
  elapsed?: number
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
/** The mode tops out at 'ask' (tools.ASK_LOCKED_DANGER): no map can switch the tool on, and no card grants it whole-tool.
 *  The backend's per-tool answer wins when known; a bare danger tier is the fallback for a tool the store has not loaded. */
export const askLocked = (t: { danger: string | undefined; ask_locked?: boolean } | string | undefined): boolean => {
  if (typeof t === 'object' && t?.ask_locked !== undefined) return t.ask_locked
  const danger = typeof t === 'string' ? t : t?.danger
  return danger === 'external' || danger === 'schedules'
}

export interface ToolInfo {
  name: string
  description: string
  group: string
  danger: 'safe' | 'writes' | 'network' | 'executes' | 'external' | 'schedules'
  available: boolean
  default_mode: ToolMode
  /** Results carry untrusted third-party content, so one call taints the rest of the reply. */
  taints?: boolean
  /** Capped at 'ask' and never granted whole-tool: an external or schedules tool under the alwaysAsk setting. */
  ask_locked?: boolean
}

/**
 * Something the chat's side panel shows: content the model wrote (the same kinds as the fenced blocks in a reply,
 * plus markdown), or a file on this Mac the panel fetches by path.
 */
export type ShowKind = 'html' | 'svg' | 'mermaid' | 'chart' | 'interactive' | 'markdown' | 'file'
export interface ShowItem {
  kind: ShowKind
  title: string
  /** Inline kinds: the source text. */
  source?: string
  /** kind=file: the resolved path the backend serves at /local/raw, with what it knows about the file. */
  path?: string
  name?: string
  mime?: string
  size?: number
  /** kind=file: an upload; its bytes come from /documents/{id}/raw instead of a path. */
  documentId?: string
  /** kind=file: a chat's output (Files → Artifacts); its bytes come from this backend route, since /local/raw refuses the data folder. */
  rawPath?: string
  /** An upload only: the original bytes were kept (false = just the extracted text is left). */
  hasOriginal?: boolean
  /** Where a split panel puts it; unset replaces the active pane (or fills the right one once split). */
  pane?: 'left' | 'right'
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

/**
 * What the user authorises on a plan card, one entry per KEPT step (POST /approvals `steps`, plans.parse_plan_edits):
 * the step by its 0-based proposed index, with `arguments` only when the user edited them. A step left out is
 * dropped; `steps: null` approves the plan as proposed.
 */
export interface PlanEdit { idx: number; arguments?: Record<string, unknown> }

export type ApprovalDecision = 'allow' | 'deny' | 'always_chat' | 'always_global' | 'always_session' | 'always_rule' | 'allow_host'

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
  /** The tool's danger tier; an 'external' or 'schedules' call offers no whole-tool standing grant. */
  danger?: string
}

/** Allow / ask / deny lists of `Tool(pattern)` rules (permrules.py). */
export interface PermissionRules { allow: string[]; ask: string[]; deny: string[] }

/** A remembered MCP tool mode (`mcp_grants`), bound to the schema it approved. scope_id is '' for global. */
export interface McpGrant {
  id: string
  tool_slug: string
  scope: 'global' | 'project' | 'chat'
  scope_id: string
  mode: ToolMode
  schema_hash: string
  granted_by: string
  created_at: number
  updated_at: number
}

/** GET /permissions/grants: every standing grant, so one view shows what runs without asking. */
/** One row of GET /approvals/history (approval_log.py): an answer to a card, a call a standing grant or plan let
 *  through, or a reviewer's verdict. */
export interface ApprovalLogEntry {
  id: number
  ts: number
  conversation_id: string | null
  conversation_title?: string
  run_id: string | null
  desk_id: string | null
  agent: string | null
  tool: string
  args_summary: string
  /** allow_once | always | deny | edited | plan | auto (ran after the review gate allowed it) | review (desk reviewer) | review-ask (sent to the user) */
  decision: string
  /** once | conversation | global | rule | plan | auto-review | allow-all */
  scope: string | null
  rule: unknown
  note: string | null
  reviewer_verdict: string | null
  reviewer_reason: string | null
  /** 'high' | 'medium' | 'low' when the automatic reviewer gave one. */
  reviewer_model: string | null
  reviewer_ms: number | null
  call_id: string | null
}

export interface PermissionGrants {
  /** 'Allow for this chat session' keys, in memory until restart. */
  session: { conversation_id: string; title: string; keys: string[] }[]
  chat_overrides: { conversation_id: string; title: string; tool: string; mode: ToolMode }[]
  project_overrides: { project_id: string; title: string; tool: string; mode: ToolMode }[]
  global: Record<string, ToolMode>
  mcp: McpGrant[]
  rules: PermissionRules
}

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
  /** The server's own annotations, as sent. Self-reported and unverified: never a reason to trust a tool. */
  annotations?: Record<string, unknown>
  /** readOnlyHint is true. The server's claim, not a guarantee. */
  read_only?: boolean
  /** destructiveHint is true and readOnlyHint is not. Turning such a tool `on` needs an explicit confirm. */
  destructive?: boolean
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
  /** The catalog entry this server was installed from; '' for a hand-added one. */
  catalog_id?: string
  enabled: boolean
  /** Remote servers only: whether a browser sign-in is stored. null for stdio. */
  signed_in?: boolean | null
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
    resources?: { uri: string; name: string; description: string; mime_type: string }[]
    prompts?: { name: string; description: string; arguments: { name: string; required?: boolean }[] }[]
  }
  tools: McpTool[]
  eval: McpEvalRecord | null
}

/** A launch config, as the add form holds it and as /mcp/check takes it. */
export interface McpServerDraft {
  name: string
  /** A remote server is streamable HTTP, or the older SSE transport. */
  transport: 'stdio' | 'http' | 'sse'
  command: string
  args: string[]
  cwd: string
  env: Record<string, string>
  secrets: Record<string, string>
  /** Remote servers. Header values are stored as secrets; reads return the names with empty values. */
  url: string
  headers: Record<string, string>
  description: string
}

/** One field of a catalog entry's install form. A secret goes to the secret store, never into args or the URL. */
export interface McpCatalogField {
  id: string
  label: string
  secret?: boolean
  required?: boolean
  help?: string
  placeholder?: string
  default?: string
  /** Plain fields only: split on newlines/commas into several args. */
  multiple?: boolean
}

export interface McpCatalogEntry {
  id: string
  name: string
  description: string
  category: string
  /** A lucide icon name in kebab-case. */
  icon: string
  publisher: string
  /** Maintained by the vendor of the service. */
  official: boolean
  docs: string
  transport: 'stdio' | 'http' | 'sse'
  runtime: 'node' | 'python' | 'docker' | 'binary' | 'remote'
  auth: 'none' | 'api_key' | 'oauth' | 'env'
  fields: McpCatalogField[]
  /** Ids of the servers already installed from this entry. */
  installed: string[]
  /** Local program detection (e.g. a coding CLI on this Mac); null when the entry has none. */
  detected?: { found: boolean; path: string; hint: string } | null
}

/** Whether the program a local connector is launched with is on the PATH. */
export interface McpRuntime {
  command: string
  found: boolean
  path: string | null
  hint: string
}

export interface McpCatalog {
  entries: McpCatalogEntry[]
  categories: string[]
  runtimes: Record<string, McpRuntime>
}

/** A search hit from the public MCP registry. Never verified by Grain. */
export interface McpRegistryResult {
  id: string
  name: string
  description: string
  version: string
  repository: string | null
  verified: false
  transport: 'stdio' | 'http' | 'sse'
  install: { command: string; args: string[]; url: string; env: Record<string, string> }
  secret_keys: string[]
  env_keys: string[]
  docs?: string
}

/** One server found in another app's config. Env and header values never reach the renderer. */
export interface McpImportServer {
  ref: string
  key: string
  name: string
  transport: 'stdio' | 'http' | 'sse'
  command: string
  args: string[]
  url: string
  env_keys: string[]
  header_keys: string[]
  /** Env keys whose values will go to the secret store. */
  secret_keys: string[]
  installed: boolean
  /** Label of another source that already lists this same server. */
  duplicate_of?: string
  /** False when the server is switched off in the app it came from. */
  enabled?: boolean
}

export interface McpImportSource {
  id: 'claude_desktop' | 'claude_code' | 'cursor' | 'vscode' | 'windsurf' | 'codex' | 'opencode'
  label: string
  path: string
  found: boolean
  error: string | null
  servers: McpImportServer[]
}

/** The trail a deep_research call leaves on its tool event (never shown to the model). */
export interface ResearchTrail {
  plan: string[]
  steps: { q: string; status: string; sources: { url: string; title: string }[]; claims: number }[]
  sources_considered: { url: string; title: string; n?: number }[]
  dropped: number
}

/** On a connector tool's `tool_call` event. The flags are the server's own claims. */
export interface McpToolOrigin {
  server: string
  read_only: boolean
  destructive: boolean
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
  /** What the `show` tool put in the chat's side panel; the model only saw a receipt. */
  show?: ShowItem | null
  /** deep_research's plan, steps and sources considered. */
  research?: ResearchTrail | null
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
  /** The review gate's verdict on this call; 'ask' is why a card opened. */
  review?: { verdict: 'allow' | 'ask' | 'deny'; reason: string; model: string; ms: number; confidence?: string } | null
  /** Id of the proposal this call became: a background run may not complete an outward-facing call. */
  proposal?: string | null
  /** Set when this call's arguments matched an approved plan step, so it ran without its own card. */
  plan?: PlanStepRef | null
  /** Stopped mid-run or the app closed; the error text says which. */
  interrupted?: boolean
  /** Arguments were repaired before the call ran. */
  repaired?: boolean
  /** Set on a connector's tool: which connector, and what the server claims about it (unverified). */
  mcp?: McpToolOrigin | null
  /** Refused before the gate: broken JSON, unknown name or signature mismatch. */
  invalid?: 'arguments' | 'name' | 'schema'
  /** Handle of the stored full result (read_tool_result). */
  result_id?: string | null
  /** Set when a subagent made this call: its card rides the parent's stream, labelled with the child. */
  agent?: string
  /**
   * write_local_file / move_local_file: the pre-image kept so the user can undo it (id is null when too large to keep).
   * Calendar / Google Tasks writes carry `external_id` instead; `notifies` means undoing emails the guests too.
   */
  undo?: { snapshot_id?: string | null; reason?: string | null; external_id?: string; notifies?: boolean } | null
  /** Set when the user rewrote the arguments on the approval card (approval_edits.py). `arguments` is then what ran. */
  edited_by?: 'user' | null
  /** What the model originally asked for, kept beside the edit so a card can show what changed. */
  original_arguments?: Record<string, unknown> | null
  edited_arguments?: Record<string, unknown> | null
}

/** Why a reply stopped early. `rounds`, `tokens`, `time` and `cost` only appear on rows stored by an older version. */
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

export type MessageOutcome = 'stopped' | 'rounds' | 'tokens' | 'time' | 'cost' | 'loop' | 'interrupted' | 'length' | 'incomplete'
export type ErrorKind = 'rate_limit' | 'quota' | 'auth' | 'not_found' | 'overflow' | 'unsupported_param' | 'content_filter' | 'overloaded' | 'server' | 'bad_request' | 'transport' | 'cancelled' | 'timeout'

/** A transient line under a streaming reply. `retry` counts down to `until` (epoch ms); `compacting` has no end time. */
export interface MessageStatus {
  kind: 'retry' | 'compacting' | 'route'
  attempt?: number
  max?: number
  until?: number
  reason?: 'rate_limit' | 'provider_error' | 'connection'
  /** `route`: the model Auto picked and the short reason. */
  model?: string
  why?: string
}

export interface Message {
  id: string
  conversation_id: string
  role: Role
  content: string
  /** Uploaded files sent with a user turn; the server inlines their text for the model. */
  attachments?: Attachment[] | null
  model: string | null
  error: string | null
  context_used: ContextUsed | null
  tool_events: ToolEvent[] | null
  trace: Span[] | null
  /** A reasoning model's chain-of-thought. Never sent back to the model as history. */
  reasoning?: string | null
  created_at: number
  /** Live only, never persisted: what a streaming reply is waiting on (a provider retry, a history summary). Set and cleared by `status` events. */
  status?: MessageStatus | null
  /** How the reply ended when it did not end normally; null = complete, or failed with error. */
  outcome?: MessageOutcome | null
  error_kind?: ErrorKind | null
  /** Regenerate group: id of the first answer; the active member carries the group's ids. */
  variant_of?: string | null
  variants?: string[] | null
  /** Up to 3 suggested next questions, written after the reply; shown under the newest reply only. */
  followups?: string[] | null
  /** Set on a user message that replaced an earlier one (edit-and-resend). */
  edited_from?: string | null
  /** Null for something a person said. Any other value ('wake', 'nudge', ...) is a hidden control turn for the model, never rendered. */
  kind?: string | null
}

/** GET /conversations/{id}/workers: one detached background worker of a chat (the assistant's `delegate` tool). */
export type WorkerStatus = 'queued' | 'running' | 'awaiting_approval' | 'done' | 'error' | 'interrupted' | 'stopped'
export interface WorkerInfo {
  id: string
  conversation_id: string
  title: string
  goal: string
  status: WorkerStatus
  /** One-line current action while running, else ''. */
  now: string
  queue_position: number | null
  started_at: number
  ended_at: number | null
  resume_of: string | null
  /** An ended worker with a stored transcript can continue with its history. */
  resumable: boolean
  pending_approvals: { call_id: string; tool: string; args: Record<string, unknown> }[]
  depth: number
  /** Info only. */
  cost: number | null
}

export type Effort = 'default' | 'low' | 'medium' | 'high' | 'xhigh' | 'max'

/** What a new chat starts on. `'default'` is a different choice: it omits `reasoning_effort`. */
export const DEFAULT_EFFORT: Effort = 'low'

export interface ConversationSettings {
  /** A chat opened on an agent (Library > Agents > Chat): its prompt leads the system prompt and its tools bound the chat's. */
  agent?: string
  /** Reasoning effort passed through as `reasoning_effort`. 'default' sends nothing; 'xhigh' and 'max' are the rungs above high. */
  effort: Effort
  /** Priority processing (`service_tier: priority`). Off sends nothing, so a model that rejects it is unaffected. */
  fast?: boolean
  /** Set only when the chat is created. Memory, graph, voice and auto-learn are then forced off for good,
   *  and the chat is left out of chat search. */
  private?: boolean
  useMemory: boolean
  useGraph: boolean
  useDocuments: boolean
  /** Inject the writing-style profile, so drafts sound like the user. */
  useStyle: boolean
  /** Explicit draft turn: the voice block is only injected while this is on (never on a tainted chat). Defaults off. */
  draftMode?: boolean
  /** This chat's reply style; absent reads as default. `responseStyleText` is the user's own wording for 'custom'. */
  responseStyle?: string
  responseStyleText?: string
  /** Per-chat plan mode. Absent reads as the global default; a desk writes it when it is created. */
  planMode?: 'off' | 'auto' | 'always'
  /** Absent inherits Settings.skipPermissions. True runs tool calls that would have asked, in this chat. */
  skipPermissions?: boolean
  autoLearn: boolean
  /** False: the chat stays in history and search, but auto-learn, skill drafting and graph extraction skip it. Unlike `private`, it can be switched at any time. */
  learn?: boolean
  /** Who wrote the title: the user (never overwritten) or the model. Absent on chats that predate it. */
  titleSource?: 'auto' | 'user'
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
  /** Set when this conversation is a desk's transcript. Desk and job transcripts cannot be branched. */
  deskId?: string
  /** The doc this chat is bound to: opening the doc brings the chat back in the page agent panel. */
  docId?: string
  /** The chat this one was branched from (POST /conversations/{id}/fork). */
  forkedFrom?: string
  /** Set on the Telegram bridge's conversation: the sidebar pins it first under the label "Telegram". */
  telegram?: boolean
}

/** One conversation matched by GET /conversations/search. Matched words in `text` sit between \x02 and \x03. */
export interface ChatSearchHit {
  id: string
  title: string
  project_id: string | null
  updated_at: number
  hits: number
  snippets: { message_id: string; role: string; created_at: number; text: string }[]
}

export interface Conversation {
  id: string
  project_id: string | null
  title: string
  model: string
  settings: ConversationSettings
  created_at: number
  updated_at: number
  /** Set while the chat is pinned (epoch seconds); archiving clears it. */
  pinned_at?: number | null
  /** Set while the chat is archived: hidden from the list, still opens by id. */
  archived_at?: number | null
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
  /** Unix seconds after which the row is expired: out of the live list and the prompt, kept in history. Null or absent: never expires. */
  expires_at?: number | null
  source_conversation_id?: string | null
  source_message_id?: string | null
}

/** The message a memory was learned from, with the quoted passage (GET /memories/{id}/source). */
export interface MemorySource { conversation_id: string; message_id: string; title: string; quote: string }

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

/** A rebuild of the graph from existing chat messages (`/graph/backfill`). */
export interface GraphBackfillStatus {
  running: boolean
  done: number
  total: number
  errors: number
  started_at: number | null
  finished_at: number | null
  cancelled: boolean
  project_id: string | null
}

export interface GraphEdge {
  id: string
  project_id: string | null
  source_id: string
  target_id: string
  relation: string
  properties: Record<string, unknown>
  created_at: number
  fact?: string
  confidence?: number | null
  source_message_id?: string | null
  valid_at?: number | null
  invalid_at?: number | null
}

export interface GraphData {
  nodes: GraphNode[]
  edges: GraphEdge[]
}

/** One uploaded file attached to a message or a draft: the documents row it points at. */
export interface Attachment {
  id: string
  name: string
  mime: string
  size?: number
}

export interface Document {
  id: string
  project_id: string | null
  name: string
  mime: string
  size: number
  chunk_count: number
  created_at: number
  pinned?: number
  preview?: string
  text?: string
  /** The uploaded bytes are still stored; false for rows from before originals were kept. */
  has_original?: boolean
}

/** POST /documents: the stored row plus what the server could make of the file. */
export interface UploadResult extends Document {
  /** These exact bytes were already stored in this scope; the existing row came back. */
  duplicate?: boolean
  /** Some text came out of the file (false for a picture with no OCR text or a scan with no text layer). */
  extracted?: boolean
  /** False when a chat cannot read the file: it is stored, but the assistant cannot see what is in it. */
  readable?: boolean
  reason?: string | null
}

/** Recurring todo: completing it spawns the next instance (backend todo_rules.py). */
export interface TodoRepeat {
  every: number
  unit: 'day' | 'week' | 'month' | 'year'
  mode: 'from_due' | 'from_completion'
}

export interface TodoFilter {
  id: string
  name: string
  tag?: string
  q?: string
  project_id?: string
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
  /** Lowercase labels; filter with `?tag=`. */
  tags?: string[]
  /** Set on a subtask; the list nests it under this todo. */
  parent_id?: string | null
  /** The named list (what a kanban board used to be) this todo sits on; null = none. */
  list_name: string | null
  /** Board column: Backlog, To do, In progress, Done, or any column a migrated board had. Done completes the todo. */
  status: string
  /** Order within a board column. */
  position: number
  /** Ids of open todos this one waits on. */
  depends_on?: string[]
  blocked_count?: number
  blocking_count?: number
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

/** Which account Mail, Calendar and the agent's mail/calendar tools read from. One at a time. */
export type PimProvider = 'google' | 'microsoft'

export interface MicrosoftStatus extends GoogleStatus {
  /** Entra object id: the identity sync keys on. */
  oid: string | null
  tenant: string | null
  name: string | null
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

export interface DriveFile {
  id: string
  name: string
  mime_type: string
  modified: string | null
  link: string | null
  size: number | null
  owner: string | null
}

export interface GmailAttachment {
  id: string
  name: string
  mime: string
  size: number
}

export interface GmailFullMessage {
  id: string
  thread_id: string
  from: string | null
  to: string | null
  subject: string | null
  date: string | null
  body: string
  attachments?: GmailAttachment[]
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
  /** Mail-watch counts, from the table; absent without the module. */
  mail_watch?: { to_reply: number; awaiting_reply_overdue: number }
  /** The day plan the planner proposes from the saved calendar; nothing is written until confirmed. */
  planner_blocks?: PlannerBlock[]
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
  /** The provider preset in use (a /setup/providers id); null or missing means the address was typed by hand. */
  provider?: string | null
  /** Which providers have a key saved. Never the keys. Missing on an older backend. */
  providerKeysSet?: Record<string, boolean>
  firecrawlApiKeySet?: boolean
  /** FIRECRAWL_API_KEY is set in the backend's environment; used when the field is empty. Never the value. */
  firecrawlEnvKey?: boolean
  braveApiKeySet?: boolean
  tavilyApiKeySet?: boolean
  exaApiKeySet?: boolean
  githubTokenSet?: boolean
  googleClientSecretSet?: boolean
  defaultModel: string
  /** Auto sends short plain messages here; empty means Auto uses the default model. */
  fastModel: string
  autoRoute: boolean
  systemPrompt: string
  extractionModel: string
  autoLearn: boolean
  /** New uploads and Rebuild index write a model-made context blurb per chunk (one call each). */
  contextualChunks?: boolean
  /** Document search: 'hybrid' (keywords + embeddings) or 'bm25' (keywords only). */
  retrievalMode?: 'hybrid' | 'bm25'
  /** Reorder fused candidates with retrievalRerankModel before trimming. */
  retrievalRerank?: boolean
  /** Also reorder recalled memories with the rerank model. On unless false. */
  memoryRerank?: boolean
  /** Rerank model shared by document search and memory recall; blank means the provider's default. */
  retrievalRerankModel?: string
  /** 0-1: vector-only hits below this similarity are dropped. */
  retrievalMinSimilarity?: number
  /** 1-10 passages per document in one search. */
  retrievalPerDocCap?: number
  /** 5-50 candidates per ranker before fusion. */
  retrievalCandidates?: number
  /** Also retrieve from the user's own Docs, not just uploaded files. */
  useDocsInContext?: boolean
  /** Write a short model title after the first reply (uses the extraction model). */
  autoTitle: boolean
  /** Suggest up to 3 next questions as chips under the latest reply (uses the extraction model). */
  followUps: boolean
  /** Bank long messages and saved docs as writing samples, and keep the voice profile current. */
  learnStyle: boolean
  /** Default type for Files; a doc with its own `typography` ignores it. */
  docTypography?: DocTypography
  theme: 'dark' | 'light' | 'system'
  /** Pastel highlight colour. Missing on older settings rows means sage. */
  accent?: 'sage' | 'lilac' | 'sky' | 'rose' | 'mint' | 'fog'
  /** Legacy, pre-spaces global mode. Read once by init() (→ initial view 'canvas') and reset to 'classic'; nothing else reads it. */
  mode?: 'classic' | 'canvas'
  /** Electron accelerator for the global Gather/Scatter shortcut. */
  gatherShortcut: string
  /** Electron accelerator for the global quick-capture window (appends to today's daily note). */
  quickCaptureShortcut?: string
  /** Electron accelerator for the global quick-ask bar (a one-line prompt that starts a new chat). */
  quickAskShortcut?: string
  /** Read-aloud voice (a speechSynthesis voice URI); empty is the system default. */
  ttsVoice?: string
  /** Read-aloud speaking rate, 0.8 to 1.5. */
  ttsRate?: number
  /** Voice chat ends itself after this many replies. */
  voiceLoopMaxTurns?: number
  /** Hold-to-talk dictation chord for the chat composer mic, e.g. 'Control+Alt+D'. */
  dictationChord?: string
  /** Today-screen cards, keyed by module (see modules.ts); a missing key means shown. Cowork defaults off. */
  homeWidgets?: Record<string, boolean>
  /** Sidebar views the user removed. Missing means every view is shown. */
  hiddenViews?: string[]
  tools: Record<string, ToolMode | boolean>
  /** How assistant edits to docs land. Missing means review: show the diff and wait. */
  docEditMode?: 'review' | 'apply'
  /** Connector tool count above which schemas are deferred behind tool search; 0 keeps every schema in the request. */
  mcpDeferAbove?: number
  /** Built-in tool count above which only the core tools plus tool_search are sent; 0 sends every schema. */
  toolDeferAbove?: number
  /** Put the notes each connected connector server sends at initialize into the prompt, fenced and scanned. Default on. */
  mcpServerNotes?: boolean
  /** Embedding model id used by memory and document retrieval. Changing it re-embeds both stores. */
  embeddingModel?: string
  /** Fuse keyword, embedding, recency and graph signals for memories; false = keyword only. */
  hybridRetrieval?: boolean
  /** Propose a memory tidy-up after this many new auto memories; 0 = manual only. */
  consolidateEvery?: number
  /** Argument-pattern rules over the per-tool modes. Deny beats ask beats allow; forced approvals are never lifted. */
  permissionRules?: PermissionRules
  /** 'deny': a background run that would have to ask is refused instead of waiting for someone. */
  unattendedApprovals?: 'ask' | 'deny'
  /** Review gate: a second model looks at a risky call that would run unasked and may turn it into a card. */
  autoReview?: 'off' | 'risky' | 'all-writes'
  /** Model for the review gate; empty = the extraction model, else the chat model. */
  autoReviewModel?: string
  /** External and schedules tools that always show a card. Every other tool that acts outside the app runs on a plain yes. */
  alwaysAsk?: string[]
  /** Legacy; the UI no longer shows it. permissionMode decides. */
  skipPermissions?: boolean
  /** auto: a second model checks risky actions; manual: ask before each; allow_all: no checks, no cards. Default auto. */
  permissionMode?: 'auto' | 'manual' | 'allow_all'
  /** Lifts the host allow-lists (fetch, browse, shell network) and the per-tool approval for connector tools. Independent of permissionMode. */
  allowAllConnections?: boolean
  /** Keep the system prompt stable and put per-turn retrieval beside the newest message (prompt caching). Default on. */
  cacheLayout?: boolean
  /** Show traces, the context preview, the full system prompt and OTLP export. Off by default; traces are recorded either way. */
  devTools?: boolean
  otelExport?: OtelExportConfig
  /** Context management (compaction.py): window in tokens, thresholds as fractions of it. */
  contextWindow?: number
  autoCompact?: boolean
  compactAt?: number
  compactKeepRecent?: number
  microKeep?: number
  microAt?: number
  /** Coding sessions: how many run at once (1-20, default 3). */
  codingSessionMaxConcurrent?: number
  /** Background workers. Past `delegationAfterRounds` tool rounds in one reply the assistant hands remaining work to a worker (default on, 2, 1-20). */
  delegationForce?: boolean
  delegationAfterRounds?: number
  /** Workers running at once (1-16, default 4); the rest queue. */
  workerMaxConcurrent?: number
  /** Send the assistant's reply to a finished worker to Telegram (default off). */
  telegramPushWorkerResults?: boolean
  /** Provider resilience and retention (backend llm.py / retention.py); missing means the shipped default. */
  llmRetries?: number
  llmIdleSeconds?: number
  retainUsageDays?: number
  retainTraceDays?: number
  retainToolResultDays?: number
  retainApprovalDays?: number
  /** Hosts fetch_url may still read once the reply has seen untrusted content. */
  fetchAllowlist?: string[]
  /** Snapshot granted folders before a reply changes them, so Undo covers shell effects. Missing means on. */
  snapshotsEnabled?: boolean
  /** Reported by GET /settings, never stored: folder snapshots need a version-control binary on this Mac. */
  snapshotsAvailable?: boolean
  /** Grain works anywhere on the Mac, so this no longer limits anything. The backend still accepts and stores it. */
  workspaceRoots?: string[]
  /** New chats start working autonomously (Ask as it goes) unless switched off per chat. Missing means on. */
  autonomousByDefault?: boolean
  /** Mount the active desk's workspace at /workspace/desk in its sandbox container. Missing means on. */
  sandboxMountDesk?: boolean
  /** Linux sandbox containers: the image a fresh one starts from, the CLI. */
  sandboxImage?: string
  sandboxRuntime?: 'docker' | 'podman' | 'nerdctl'
  /** A stopped sandbox nobody came back to is removed after this many days (0 = never). */
  sandboxKeepDays?: number
  /** The Linux sandbox's network: none, an allowlisting proxy (registries plus shellAllowedDomains), or open. A legacy stored true reads as open. */
  sandboxNetwork?: 'off' | 'proxy' | 'open' | boolean
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
  /** The model generate_image calls; the provider must offer /images/generations. */
  imageModel?: string
  /** The agent's own browser. */
  browserEnabled?: boolean
  browserMaxTabs?: number
  browserIdleSeconds?: number
  browserAllowlist?: string[]
  /** Extra packages for the shared work environment. */
  workEnvPackages?: string[]
  /** fs_edit and an overwriting write refuse a file this chat has not read. Missing means on. */
  requireReadBeforeWrite?: boolean
  /** When set, Firecrawl answers web search and page reads first; the other engines are the fallback. Empty = the FIRECRAWL_API_KEY environment variable, if any. */
  firecrawlApiKey?: string
  /** Texting channel: Telegram bot, owner-only. */
  telegramEnabled?: boolean
  /** Also send approvals and finish notices for chat runs not started from Telegram. */
  telegramNotifyLongRuns?: boolean
  telegramLongRunMinutes?: number
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
  deskMaxLive?: number
  /** Relaunch desks a restart interrupted mid-turn. Never one with an unknown-outcome call or a pending card. Off by default. */
  deskAutoResume?: boolean
  /** How long a desk waits on a card nobody is watching before the run lets go. 0 = wait forever. */
  parkAfterSeconds?: number
  /** A native notification when a desk stops and cannot go on without you. Missing reads as on. */
  deskNotify?: boolean
  /** A system notification when a reply finishes, fails or needs approval in a chat that is not in front of you. */
  chatNotify?: boolean
  /** Spaces: a chat window shows the chat's face instead of the transcript until switched. Off by default. */
  compactChats?: boolean
  /** A small Explain / Summarize / Verify / Ask bubble over text selected in a chat, note, mail or the page agent. Missing reads as on. */
  selectionToolbar?: boolean
  /** Interface zoom in percent, 80-160. Missing reads as 100. */
  uiZoom?: number
  /** A native notification when a scheduled job fails, is auto-paused or leaves proposals, while the window is hidden. Missing reads as on. */
  notifyJobs?: boolean
  /** Default plan mode for a new chat: off, auto (the first mutating call arms it), or always. */
  planMode?: 'off' | 'auto' | 'always'
  /** How a new chat's replies are shaped: default, concise, formal, tutor, thorough or custom (then `responseStyleText`). */
  responseStyle?: string
  responseStyleText?: string
  googleClientId: string
  googleClientSecret: string
  /** Entra app (public client, no secret). Tenant is `common` when blank. */
  microsoftClientId: string
  microsoftTenant: string
  pimProvider: PimProvider
  /** Undo window on outgoing mail. `seconds` is clamped to 60-120 by the backend. */
  gmailSendHold?: { enabled: boolean; seconds: number }
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

/** What one chat has spent: the `usage_log` rows tagged with its id. `cost` leaves out `unpriced` calls. */
export interface ConversationUsage {
  totals: UsageBucket
  by_kind: (UsageBucket & { kind: string })[]
  /** First recorded call (epoch seconds); rows older than the retention window are gone. */
  since: number | null
  /** Calls whose token counts were estimated, not reported. */
  estimated: number
}

export interface UsageReport {
  days: number
  totals: UsageBucket
  daily: (UsageBucket & { day: string })[]
  hourly: { hour: number; calls: number }[]
  weekday: { weekday: string; calls: number }[]
  /** Today, this week (from Monday) and this month, local time, whatever `days` is. `since` is the first day. */
  periods?: Record<UsagePeriod, UsageBucket & { since: string }>
  /** One row per model: the proxy alias and the provider's full id (`ids`) are merged under the short name. */
  by_model: (UsageBucket & { model: string; ids?: string[] })[]
  /** What the calls were for (chat, jobs, memory, embeddings...), in display order. */
  by_feature?: (UsageBucket & { feature: string; label: string })[]
  by_kind: (UsageBucket & { kind: string })[]
  by_project: (UsageBucket & { project: string })[]
  by_tag: (UsageBucket & { tag: string })[]
  prices: Record<string, ModelPrice>
}

export type UsagePeriod = 'today' | 'week' | 'month'

export interface ModelInfo {
  id: string
  mode?: string | null
  reasoning?: boolean | null
}

export type ChatEvent =
  | { event: 'user_message'; data: Message }
  | { event: 'assistant_message'; data: Message }
  | { event: 'removed_message'; data: { id: string } }
  | { event: 'restored_message'; data: { message: Message; reason: string | null } }
  | { event: 'title'; data: { id: string; title: string } }
  | { event: 'delta'; data: { id: string; text: string } }
  | { event: 'reasoning'; data: { id: string; text: string } }
  | { event: 'tool_call'; data: { message_id: string; id: string; name: string; arguments: Record<string, unknown>; needs_approval?: boolean; forced?: boolean; permission?: PermissionCard | null; review?: ToolEvent['review']; plan?: PlanStepRef | null; agent?: string; mcp?: McpToolOrigin | null } }
  | { event: 'tool_result'; data: ToolEvent & { message_id: string } }
  /** The card was answered (by this window, another one, or a steer): settles a replayed card so it is not asked twice. */
  | { event: 'tool_decision'; data: { message_id: string; id: string; decision: ApprovalDecision } }
  | { event: 'span'; data: { message_id: string; span: Span } }
  /** Transient progress for a reply that has no tokens yet: a provider retry (`until` is epoch ms) or a history summary. `kind: null` clears it. */
  | { event: 'status'; data: { id: string; kind: MessageStatus['kind'] | null; attempt?: number; max?: number; until?: number; reason?: MessageStatus['reason']; model?: string; why?: string } }
  | { event: 'done'; data: { id: string | null; error: string | null; context_used: ContextUsed | null; tool_events: ToolEvent[]; trace: Span[]; stopped: boolean; partial?: PartialReason | null; segment?: boolean; tainted?: boolean; taint_sources?: string[]; reasoning?: string | null; outcome?: MessageOutcome | null; error_kind?: ErrorKind | null; notice?: string | null; attachments?: Attachment[] | null } }
  | { event: 'taint'; data: { message_id: string; source: string } }
  | { event: 'subagent'; data: SubagentInfo & { message_id: string | null } }
  | { event: 'plan'; data: { conversation_id: string; steps: PlanStep[] } }
  /** propose_plan opened a card. `plan` above is the todo_write checklist — a different thing. */
  | { event: 'plan_card'; data: { message_id: string; call_id: string; plan: PlanRecord } }
  /** How it was answered, so a window that was watching sees a decision made somewhere else. */
  | { event: 'plan_decision'; data: { message_id: string; call_id: string; plan: PlanRecord } }
  /** The reply let go of a card nobody was watching. The row stays pending and decidable. */
  | { event: 'parked'; data: { message_id: string; call_id: string; name: string } }
  /** A desk's row changed: the rail's label, its status, its counters. */
  | { event: 'desk_status'; data: Desk }
  /** This turn is handing over to another one, announced before `done` so the UI can re-attach. */
  | { event: 'desk_handoff'; data: { desk_id: string; conversation_id: string; turn: number } }
  | { event: 'learned'; data: Learned }
  | { event: 'style_learned'; data: { project_id: string | null; profile: StyleProfile | null; sample_id: string } }
  | { event: 'learn_error'; data: { message: string } }
  | { event: 'error'; data: { message: string; interrupted?: boolean; run_id?: string; pending_approvals?: string[] } }

/** What one auto-learn pass (or the `remember` tool) put away. The ids are set only off `/events`. */
export interface Learned {
  memories: Memory[]
  /** Durable preferences auto-learn superseded or dropped, rather than adding a near-duplicate. */
  updated?: Memory[]
  /** The rows auto-learn dropped; their ids are the ones Undo restores. */
  removed?: Memory[]
  /** Each update that replaced a row: `old_id` is the wording Undo brings back. A pinned row is rewritten in place and has no entry. */
  superseded?: { old_id: string; new_id: string }[]
  nodes: GraphNode[]
  edges: GraphEdge[]
  /** Candidate skills auto-learn drafted: a friction fix, or a revised copy of an approved one that failed. */
  skill_candidates?: { id: string; name: string; why: string }[]
  skill_revisions?: { id: string; name: string; why: string; revises: string }[]
  conversation_id?: string
  message_id?: string
  /** Ids of memories the model suggested pinning to the standing preferences. */
  pin_suggested?: string[]
}

/**
 * `GET /events`: app-wide work no single run is waiting on. Auto-learn runs here, after its reply's
 * run has already ended, so these never arrive on a conversation stream.
 */
export type BackgroundEvent =
  | { event: 'learned'; data: Learned }
  /** Suggested next questions for a finished reply, written off the run. */
  | { event: 'followups'; data: { conversation_id: string; message_id: string; followups: string[] } }
  | { event: 'learn_error'; data: { conversation_id?: string; message_id?: string; message: string } }
  /** Auto tidy-up queued memory proposals. `count` is only the new ones: re-read the pending list for the badge. */
  | { event: 'proposals'; data: { count: number } }
  /** The learn worker re-read a scope's writing samples into a new voice profile. */
  | { event: 'style_learned'; data: { project_id: string | null; profile: StyleProfile } }
  | { event: 'job_finished'; data: { run_id: string; job_id: string } }
  /** Every desk write, for desks nobody is watching: the rail, the badge and the Today card stay live. */
  | { event: 'desk_status'; data: Desk }
  /** A run's answering / status state moved: lets every window know about a reply it did not start. */
  | { event: 'run_state'; data: RunInfo }
  /** A conversation's title was rewritten off the run (model title or regenerate). */
  | { event: 'conversation_changed'; data: { id: string; title?: string; /** A message was added outside a run (a desk's report): re-read the chat. */ reload?: boolean } }
  /** A shell job started, ended or was killed: the Running list refetches. */
  | { event: 'shell_jobs'; data: { live: number } }
  /** A background worker changed status or asked for approval. */
  | { event: 'workers'; data: { conversation_id: string; worker: WorkerInfo } }
  | { event: 'todos_changed'; data: Record<string, never> }
  /** A workflow run or one of its steps moved (payloads stripped): crew windows and the run list refetch. */
  | { event: 'workflow_run'; data: WorkflowRun }
  /** A ship checklist moved (ship.py): the whole row, so the card and the job row update without a refetch. */
  | { event: 'ship_checklist'; data: ShipChecklist }
  /** A coding session moved (codingagents.py): the whole summary row. */
  | { event: 'coding_session'; data: CodingSession }

/** GET /coding-sessions: one background Claude Code or OpenCode session started through the coding_session_* tools. */
export interface CodingSession {
  id: string; agent: 'claude' | 'opencode'; name: string
  status: 'starting' | 'working' | 'needs_you' | 'blocked' | 'done' | 'stopped' | 'failed'
  attention: Attention; detail: string | null; repo_path: string; worktree: string; branch: string | null
  external_id: string | null; model: string | null; permission_mode: string | null; log_tail: string
  /** `claude attach <id>`, set while a Claude Code session waits on a permission prompt. */
  attach_hint?: string | null
  created_at: number; updated_at: number; ended_at: number | null
}
/** GET /coding-sessions/{id}/diff: what the session changed in its worktree. `diff` only with ?full=1. */
export interface CodingSessionDiff { worktree: string; branch: string | null; status: string; diff_stat: string; log: string; diff?: string; truncated: boolean }

/** One step of a ship checklist. awaiting_confirm is only ever the merge step. */
export type ShipStepStatus = 'pending' | 'running' | 'green' | 'red' | 'skipped' | 'awaiting_confirm'
export interface ShipStep {
  name: 'tests' | 'push' | 'pr' | 'merge'; status: ShipStepStatus
  started_at: number | null; ended_at: number | null; log_tail: string; link: string | null
}
/** GET /ship/{id}: tests -> push -> PR -> merge for one branch; the merge runs only after the user confirms. */
export interface ShipChecklist {
  id: string; job_id: string | null; run_id: string | null; repo_path: string; branch: string; base: string
  test_command: string | null; status: 'running' | 'awaiting_confirm' | 'done' | 'failed' | 'cancelled'
  steps: ShipStep[]; pr_url: string | null; merged_sha: string | null; created_at: number; updated_at: number
}

/** A shell command the agent started (GET /shell/jobs). `orphaned` = left by an earlier run of the app. */
export interface ShellJobInfo {
  job_id: string; pid: number | null; pgid: number | null; cwd: string; run_id: string | null
  conversation_id: string | null; command: string
  status: 'running' | 'exited' | 'killed' | 'timed_out' | 'orphaned' | 'failed'
  exit_code: number | null; started: number; finished: number | null; background: boolean; total: number
}
export interface ShellJobTail extends ShellJobInfo { output: string; note?: string }

/** One sandbox container (GET /sandboxes). conversation_id is null for one made before containers were labelled. */
export interface SandboxInfo {
  name: string; conversation_id: string | null; title: string | null; status: string; created: string
  last_used: number | null; networked: boolean | null; holds_import: boolean; checkpoints: string[]
}
export interface TelegramStatus {
  enabled: boolean
  has_token: boolean
  bot_username: string | null
  paired: boolean
  owner_name: string | null
  status: 'disabled' | 'no_token' | 'not_paired' | 'connected' | 'error' | 'bad_token' | 'conflict' | 'locked'
  last_error: string | null
  last_poll_at: number | null
  pairing: { code: string; link: string; expires_at: number } | null
}

export interface SandboxStatus { available: boolean; runtime: string; reason: string; items: SandboxInfo[] }

export interface BackupInfo {
  name: string; kind: 'daily' | 'manual' | 'premigrate' | 'prerestore'; created_at: number; size: number
  app_version: string | null; schema_version: number | null
}
export interface DataOverview {
  data_dir: string; backups: BackupInfo[]; last_backup: number | null
  pending_restore: { name: string } | null; schema_version: number; app_version: string
  /** A staged restore that could not be applied at the last start; the live data was left as it was. */
  restore_failed?: { name: string | null; error: string; at: number } | null
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
  /** Store an HTML fence's source for the preview scheme; returns the id for `previewUrl`. */
  previewPut: (source: string) => Promise<string>
  /** Supervisor state and restart history; `restartBackend` also works from `failed`. */
  backendInfo: () => Promise<BackendInfo>
  restartBackend: () => Promise<BackendInfo>
  onBackendState: (cb: (info: BackendInfo) => void) => () => void
  /** Reveal the log folder in Finder. */
  openLogs: () => Promise<string>
  platform: NodeJS.Platform
  onMenu: (cb: (action: string) => void) => () => void
  /** Page zoom of this window, in percent. */
  setZoom: (percent: number) => Promise<void>
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
    capture: () => Promise<ShortcutState>
    setCapture: (accelerator: string) => Promise<ShortcutState>
    ask: () => Promise<ShortcutState>
    setAsk: (accelerator: string) => Promise<ShortcutState>
    onFailure: (cb: (s: ShortcutState) => void) => () => void
  }
  /** Data folder helpers for Settings → Data (native dialog, Finder, restart to apply a restore). */
  data: {
    chooseExportPath: () => Promise<string | null>
    /** Native open dialog for files to hand a desk as inputs; [] when cancelled. */
    chooseInputFiles: () => Promise<string[]>
    /** One folder from the system dialog, or null when cancelled. */
    chooseFolder: () => Promise<string | null>
    reveal: (path: string) => Promise<boolean>
    /** A file the side panel shows: select it in Finder, or open it in its default app (only types that cannot run). */
    fileAction: (path: string, action: 'reveal' | 'open') => Promise<boolean>
    relaunch: () => Promise<void>
  }
  print: {
    payload: () => Promise<{ title: string; content: string } | null>
    ready: () => void
    /** 'save': the path of the PDF written into Downloads. 'bytes': the PDF itself. */
    exportPdf: (title: string, content: string, filename: string, mode: 'save' | 'bytes') => Promise<string | Uint8Array | null>
  }
  /** Closes the BrowserWindow this renderer lives in: the Cmd-W fall-through when no canvas window has focus. */
  /** The quick-ask bar's own window only: clipboard text, grow to content height, show a chat in the main window. */
  quickAsk: {
    clipboard: () => Promise<string>
    resize: (height: number) => Promise<void>
    openChat: (conversationId: string) => Promise<void>
  }
  closeSelf: () => void
  minimizeSelf: () => void
  /** A native notification about a desk, shown by main only while the window is unfocused; clicking opens that desk. */
  deskNotify: (payload: { title: string; body: string; deskId?: string }) => void
  /** macOS microphone access for this app, asking once when it was never decided. Always 'granted' off macOS. */
  micAccess: () => Promise<'granted' | 'denied' | 'restricted' | 'not-determined' | 'unknown'>
  /** Opens Terminal on `claude attach <id>` for a coding session waiting on the user; false when the id is invalid or it failed. */
  codingAttach: (externalId: string) => Promise<boolean>
  /** System access wizard: side-effect-free status reads, one grant per row, and an allowlisted Settings pane opener. */
  sysAccess: {
    status: () => Promise<import('./systemAccess').MainStatus>
    grant: (id: string) => Promise<{ state: import('./systemAccess').AccessState; note?: string }>
    openPane: (url: string) => Promise<boolean>
  }
  /** The agent's interactive browser (hidden windows owned by main). The renderer never gets the bridge secret. */
  agentBrowser: {
    list: () => Promise<AgentBrowserSession[]>
    show: (session: string) => Promise<void>
    hide: (session: string) => Promise<void>
    /** Live JPEG frames after each action and at most every ~1.5 s while subscribed; returns the unsubscribe. */
    subscribe: (session: string, cb: (frame: AgentBrowserFrame) => void) => () => void
    /** Sites with cookies saved in the agent browser (sign-ins from handoffs; page fetches share the same store). */
    signIns: () => Promise<AgentBrowserSignIn[]>
    /** Forgets one site's cookies and storage, or with no domain everything, closing open agent browsers first. */
    clearSignIns: (domain?: string) => Promise<void>
  }
}

export interface AgentBrowserSignIn {
  domain: string
  count: number
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

export interface Recap { day: string; content: string; created_at: number; cached?: boolean }

// ---------------- Cowork desks ----------------
/**
 * Named `Desk*` throughout, never a bare `Plan` / `PlanStep`: `Plan`/`PlanStep` is the todo_write
 * checklist and `ProposedPlan`/`ProposedStep` is propose_plan's approval record, which is the one a
 * desk carries. The CSS mirrors are `.cowork-` and `.desk-`.
 */

export type DeskStatus = 'draft' | 'planning' | 'awaiting_plan' | 'working' | 'needs_approval' | 'blocked'
  | 'paused' | 'interrupted' | 'review' | 'done' | 'failed' | 'stopped' | 'queued'

/** Mirrors `cowork.NEEDS_YOU`: the statuses that put a desk in the rail's "Needs you" section. */
export const NEEDS_YOU: DeskStatus[] = ['awaiting_plan', 'needs_approval', 'blocked', 'interrupted', 'review']
/** Mirrors `cowork.LIVE`: something is driving the desk right now. Also what `Desk.live` is computed from. */
export const DESK_LIVE: DeskStatus[] = ['planning', 'working', 'needs_approval']

export type DeskAutonomy = 'plan' | 'ask' | 'propose'
export type PlanDecision = 'approve' | 'edit' | 'reject'



/** Something the user hands a desk: a doc, an uploaded document, or a local file under the home folder. */
export type DeskInputRef = { kind: 'doc'; id: string } | { kind: 'document'; id: string } | { kind: 'path'; path: string }

export type DeskAction = 'start' | 'pause' | 'resume' | 'stop' | 'message' | 'delete'

export interface Desk {
  id: string
  attention?: Attention
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
  last_error: string | null
  archived: boolean
  /** Derived: the status is in DESK_LIVE. */
  live: boolean
  /** Derived: unseen `needs_you` events on this desk. */
  unseen: number
  /** Derived from the backend's transition tables: what may be done to the desk in this status. */
  actions: DeskAction[]
  created_at: number
  updated_at: number
  ended_at: number | null
  /** Set while the desk waits for a free slot under `deskMaxLive` (status `queued`); the queue is oldest first. */
  queued_at?: number | null
}

/** What start/resume/message/create answer for a desk that joined the queue instead of starting. */
export interface DeskQueued { queued: true; position: number; live: number; max: number }

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
export type PromotionKind = 'doc' | 'doc_append' | 'document' | 'download' | 'todo' | 'mail_draft'

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
  /** Undecided rows only: the file on disk no longer matches `sha256`. Survives a refused promotion. */
  stale?: boolean
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

/** GET /conversations/{id}/outputs: what a plain chat's tools saved for the user. `folder` is for Show in Finder. */
export interface ChatOutputs extends DeskFileTree { folder: string }

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
  | 'chat' | 'todos' | 'calendar'
  | 'memory' | 'graph' | 'documents' | 'recap' | 'project' | 'usage' | 'doc' | 'face' | 'crew'

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
 * uploaded, for retrieval).
 */
export interface Doc {
  id: string
  project_id: string | null
  title: string
  folder: string
  starred: number
  /** Pinned docs sit in their own group above the folders; absent on older payloads. */
  pinned?: number
  created_at: number
  updated_at: number
  words: number
  /** Distinct lowercase #tags in the body (list rows only). */
  tags?: string[]
  /** List rows carry a preview and a pending count; a fetched doc carries the body and the pending revisions. */
  preview?: string
  size?: number
  content?: string
  pending?: number | DocRevision[]
  /** This doc's own type; null or absent follows Settings → docTypography. */
  typography?: DocTypography | null
}

/** A font choice for the rendered and edit views: family, px size, and measure (line width) in ch. */
export interface DocTypography {
  font?: 'serif' | 'sans' | 'mono' | 'book'
  size?: number
  measure?: number
}

/**
 * One row of a doc's comments. A thread row (parent_id null) anchors to `quote` in the rendered text, with
 * ~32 chars of context either side and the offset it was made at; a reply carries its thread's id and no
 * anchor. `resolved` is read off the thread row.
 */
export interface DocComment {
  id: string
  doc_id: string
  parent_id: string | null
  author: 'user' | 'agent'
  body: string
  quote: string
  prefix: string
  suffix: string
  offset_hint: number
  resolved: number
  created_at: number
  updated_at: number
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
  /** An append proposal: the section to add. While pending, `before`/`after` are
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

export type DragKind =
  | 'conversation' | 'todo' | 'document' | 'memory' | 'project' | 'file' | 'nav'
  /** A Files doc: opens as a doc window editing it in place. */
  | 'doc'
  /** A desk, a saved workflow or one run of it: each opens as a crew window showing its agents. */
  | 'desk' | 'workflow' | 'workflow_run'

export interface DragPayload {
  kind: DragKind
  /** The underlying object's id. For kind 'nav' this is a WidgetKind; for 'file' it is ''. */
  id: string
  label: string
  projectId?: string | null
}

/** Run state of one chat session. Travels the cross-window bus, so it is a shared type, not a store-local one. */
export type SessionStatus = 'idle' | 'working' | 'done' | 'error' | 'needs-approval'

/** What a chat, desk or job asks of the user right now. Derived in one table (backend attention.py, mirrored by lib/attention.ts). */
export type Attention = 'idle' | 'working' | 'needs_you' | 'blocked'

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
  /** What it is doing right now (a tool call, or "thinking"); empty once finished. */
  now?: string
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
  /** Tape seq of the latest assistant_message; a window attaching mid-reply replays from just before it. */
  message_seq?: number | null
  kind?: string
  started_at: number
  live: boolean
  /** Still producing a reply. `live` outlasts it by the auto-learn tail that follows the last `done`. */
  answering: boolean
  /** Durable status from agent_runs. */
  status?: 'running' | 'awaiting_approval' | 'done' | 'error' | 'interrupted'
  ended_at?: number | null
  error?: string | null
  attention?: Attention
}

// ---------------- scheduled jobs + the Agent Inbox ----------------

export type JobKind = 'cron' | 'once' | 'watch' | 'mail' | 'calendar'
/** Inbox run rows. */
export type RunKind = JobKind

/** One scheduled job (`jobs` table). `cron` is read in `timezone`, so it follows the wall clock through DST. */
export interface Job {
  id: string
  /** From GET /jobs only: rows returned by create/update leave it out. */
  attention?: Attention
  name: string
  /** 'cron' repeats on `cron` forever; 'once' fires at `run_at` and then switches itself off; 'watch' fires when
   *  files appear or change in `watch_dir` (and on `cron` too, when it has one); 'mail' fires when a thread
   *  matching `mail_query` is new or has a new message. */
  kind: JobKind
  /** Empty for a one-off, for a mail job, and for a folder job with no clock. */
  cron: string
  /** A Gmail search, for kind 'mail'. Polled at most every five minutes. */
  mail_query?: string | null
  /** kind 'calendar': words that must all appear in an event's title or attendees; the job fires `minutes_before`
   *  the start of each matching event in the next 24 h, once per event. `calendar_id` null = the primary calendar. */
  calendar_query?: string | null
  calendar_id?: string | null
  minutes_before?: number
  /** A run whose result matches the previous run's is recorded as unchanged and not announced. */
  only_on_change?: boolean
  /** When a run's result last differed from the one before it (only kept while `only_on_change` is on). */
  last_change_at?: number | null
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
  /** When a run is worth an OS notification: failures, pauses and proposals ('problems'), also plain successes, or never. */
  notify: JobNotifyMode
  /** The folder a 'watch' job watches; null for the others. */
  watch_dir: string | null
  /** The model this job's runs use. null = the default model. */
  model: string | null
  /** 'desk': each fire opens a desk with `prompt` as its brief, instead of a proposal-only chat run. */
  target: 'run' | 'desk'
  /** A scheduled desk plans first or proposes at the end; 'ask' is refused (nobody is there to answer). */
  desk_autonomy: Exclude<DeskAutonomy, 'ask'> | null
  /** The agent definition this routine runs as (its prompt, skills, boundaries and tool overrides). null = plain. */
  agent_id?: string | null
}

export type JobNotifyMode = 'problems' | 'always' | 'never'

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
  /** 'expired': left pending past settings.proposalExpireDays; it can no longer be accepted. */
  status: 'pending' | 'accepted' | 'rejected' | 'expired'
  result: unknown
  error: string | null
  /** The user changed the arguments before accepting. */
  edited: boolean
  /** Accept takes edited arguments for this tool (approval_edits.EDITABLE_TOOLS); otherwise it runs as proposed. */
  editable?: boolean
  created_at: number
  decided_at: number | null
  /** Who proposed it (GET /inbox only): the job, or the desk whose run did. */
  source?: { kind: 'job' | 'desk'; id: string; run_id: string | null; name: string } | null
}

/** One job run as "While you were away" shows it. Every field but `summary` comes from a row, not from prose. */
export interface JobRunSummary {
  run_id: string
  conversation_id: string | null
  status: 'running' | 'awaiting_approval' | 'done' | 'error' | 'interrupted'
  job_id: string | null
  job: string
  kind: RunKind
  due_at: number | null
  fired_at: number
  late: boolean
  late_seconds: number
  missed_slots: number
  manual: boolean
  /** A test run before enabling: a manual run labelled as such. */
  test?: boolean
  /** 1 for the first launch of a slot; 2+ for a retry of the run `retry_of`. */
  attempt: number
  retry_of: string | null
  started_at: number
  ended_at: number | null
  error: string | null
  tool_calls: number
  proposals: number
  pending_proposals: number
  /** Marked read in the Agent Inbox (inbox_seen). A read card collapses to one line. */
  seen: boolean
  /** The run's own report, from the event tape. Shown as the body; headed sections are split out for display only. */
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
  /** The result matched the previous run's on a job that notifies only on change. */
  unchanged?: boolean
  /** A read-only preview, not a real run; left out of the stats. */
  dry_run: boolean
  /** A test run before enabling; counts toward neither retries nor the failure streak. */
  test?: boolean
  tool_calls: number
  proposals: { pending: number; accepted: number; rejected: number }
  cost: number | null
  error: string | null
  summary: string
}

/** A slot the job turned away (job_skips), listed among its runs. `started_at` is when it was skipped. */
export interface JobSkipRecord {
  run_id: string
  conversation_id: null
  status: 'skipped'
  reason: string
  due_at: number | null
  started_at: number
}

export interface JobStats {
  /** Skipped slots in the window; never counted in `runs` or `success_rate`. */
  skipped?: number
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
  kind: 'job_failed' | 'job_done' | 'job_done_with_proposals' | 'job_paused' | 'proposal_pending'
  title: string
  body: string
  at: number
  /** Where a click goes: 'inbox' (Today's Agent Inbox) or 'run:<conversation_id>' (that run's transcript). */
  target: string
}

export type InboxQueueKey = 'doc_edits' | 'skills' | 'workflows' | 'memory'

export interface AgentInbox {
  needs_you: {
    approvals: (PendingApproval & { run_kind?: string | null; job?: string | null })[]
    proposals: AgentProposal[]
    paused_jobs: { id: string; name: string; reason: string; paused_at: number; consecutive_failures: number }[]
    /** One unseen needs-you row per Cowork desk (its latest), unless an approval of that desk is already listed. */
    desks?: DeskEvent[]
    /** Every other review queue, as a count and a key the renderer maps to the place it is decided. */
    elsewhere?: { key: InboxQueueKey; label: string; count: number }[]
  }
  while_you_were_away: JobRunSummary[]
  counts: { needs_you: number; approvals: number; proposals: number; paused_jobs: number; runs: number; unseen_runs: number; late: number; failed: number }
  /** wake_unavailable: the OS refused to book a wake, so jobs only run while the Mac is awake and the app is open. */
  scheduler: { last_tick: number | null; fires: number; next_due_at: number | null; timezone: string; wake_unavailable?: boolean }
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
  /** What the user said with the answer. */
  note?: string | null
  conversation_title?: string | null
}

/** 409 detail of POST /conversations/{id}/chat when that conversation already has a live run. */
export interface RunConflict { message: string; run_id: string; seq: number; message_id?: string | null; message_seq?: number | null }

/** One detached widget window as the main process sees it. */
export interface PopoutInfo { windowId: string; bounds: PopoutBounds; pinned: boolean; opacity: number }

export interface PopoutOpenRequest { bounds?: Partial<PopoutBounds>; minWidth?: number; minHeight?: number; title?: string; pinned?: boolean; opacity?: number }

export interface PopoutChange { windowId: string; event: 'opened' | 'closed'; bounds: PopoutBounds | null }

export interface GatherState { gathered: boolean; popped: string[] }

export interface ShortcutState { accelerator: string; ok: boolean; message: string | null; which?: 'gather' | 'capture' | 'ask' }

export type BusKind = 'window-bounds' | 'window-state' | 'window-config' | 'chat-status' | 'todo-changed' | 'note-changed' | 'canvas-invalidate'

/**
 * Optimistic cross-window hint relayed renderer -> main -> every other renderer.
 * The backend stays authoritative; a bus message never creates state.
 */
export interface BusMessage { kind: BusKind; windowId?: string; canvasId?: string; refId?: string; data?: Record<string, unknown> }

/** What came back from asking macOS for a grant. `prompted` is false when macOS refuses to ask at all. */
export interface PermissionGrantResult {
  id: string
  state: 'granted' | 'denied' | 'unasked' | 'unknown' | 'n/a'
  prompted: boolean
  note: string
}

/** Voice input (settings key `voice`), read and patched through /voice/config. */
export interface VoiceConfig {
  sttBackend: 'auto' | 'speech' | 'whistle' | 'proxy' | 'local' | 'off'
  /** Speech-to-text model on the configured LLM base URL (the proxy backend). */
  sttModel: string
  /** whisper.cpp ggml model file for the local backend; blank takes the first .bin in the data dir's models folder. */
  whisperModelPath: string
  whisperVadModelPath: string
  /** Drop known silence hallucinations and repeated-word runs. */
  hallucinationFilter: boolean
  /** Run each transcribed clip through a model that only fixes punctuation, case and fillers. */
  dictationCleanup: boolean
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
  /** The subagent runs an agent / fan_out step spawned, in order; null for a tool step. */
  agents: string[] | null
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
// ---- The crew tree (GET /crew/{id}): a desk or workflow run and the subagents under it ----
export interface CrewAgent {
  id: string
  /** Another agent's id, or null when it hangs straight off the root. */
  parent_id: string | null
  role: string
  task: string
  /** The run row's status: running | done | error | interrupted | awaiting_approval. */
  status: string
  /** Live only: running | completed | partial | error. */
  state: string | null
  /** Live only: the tool call in flight, or "thinking". */
  now: string
  exit_reason: string | null
  rounds: number
  calls: number
  cost: number
  started_at: number | null
  ended_at: number | null
  error: string | null
}
export interface CrewRoot {
  kind: 'desk' | 'workflow_run' | 'workflow' | 'chat'
  id: string
  title: string
  /** A DeskStatus, a WorkflowRunStatus, or 'idle' for a workflow that has never run. */
  status: string
  /** What it is on right now: the desk's headline, the live step ids, or the error. */
  now: string
  ended_at?: number | null
  run_id?: string | null
  workflow_id?: string | null
  cost?: number | null
}
export interface CrewView {
  root: CrewRoot
  run: WorkflowRun | null
  workflow: Workflow | null
  agents: CrewAgent[]
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

/** A user-authored agent definition (inert until approved). Built-ins come back separately, without these fields. */
export interface AgentDef {
  id: string
  name: string
  description: string
  model: string | null
  tools: string[]
  /** Approved skill names folded into its prompt. */
  skills: string[]
  /** Face colour in degrees; null lets the name pick one. */
  hue: number | null
  hidden: boolean
  approved: boolean
  body: string
  /** One line naming its job ("Inbox triage"). */
  label: string
  /** What it must ask before doing and what it never does; fenced into its system prompt. */
  boundaries: string
  /** "What this agent should remember": fenced into its system prompt after the boundaries. */
  notes: string
  /** Its own working folder; empty = none. */
  workspace: string
  /** Tool modes for this agent only: above the project's, below the chat's. Empty = inherit. */
  tool_modes: Record<string, ToolMode>
}
export interface BuiltinAgent { name: string; description: string; tools: string[]; hue: number | null }
/** The editable fields of a definition: what the editor holds and what a draft returns. */
export type AgentFields = Pick<AgentDef, 'name' | 'description' | 'model' | 'tools' | 'skills' | 'hue' | 'hidden' | 'body'>
  & Partial<Pick<AgentDef, 'label' | 'boundaries' | 'notes' | 'workspace' | 'tool_modes'>>
/** The fields that ride beside the definition text (PATCH /agents/defs/{id}/scope keeps the approval). */
export type AgentScope = Partial<Pick<AgentDef, 'label' | 'boundaries' | 'notes' | 'workspace' | 'tool_modes' | 'skills'>>
/** GET /agents/defs/{id}/home: one agent's page. */
export interface AgentHomeData {
  agent: AgentDef
  chats: { id: string; title: string; project_id: string | null; updated_at: number }[]
  routines: Job[]
  runs: { run_id: string; conversation_id: string | null; kind: string; status: string; error: string | null; started_at: number; ended_at: number | null; title: string | null }[]
  working: number
  needs_you: number
}
/** GET /subagents/{id}: the run row, live state while it runs, and its history (OpenAI-shaped messages). */
export interface SubagentView {
  run: { run_id: string; status: string; parent_run_id: string | null; input?: Record<string, unknown>; budget?: Record<string, number> | null }
  agent: SubagentInfo | null
  messages: { role: 'system' | 'user' | 'assistant' | 'tool'; content: string | null; tool_calls?: { function: { name: string; arguments: string } }[] }[]
}
