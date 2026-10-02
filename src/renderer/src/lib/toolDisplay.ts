/**
 * Pure helpers that turn a tool call into something a person can read: a plain-language title, a labelled
 * argument list, a readable result. No React in here, so it is unit-tested directly (toolDisplay.test.ts).
 *
 * Raw JSON is never the default view of a call. These helpers produce the default; the card keeps the raw call
 * behind a "Details" disclosure.
 */
import type { ToolEvent } from '@shared/types'
import { hostPath } from './browserApproval'
import { browserLine } from './toolResult'

/** What the call does, in words. `verb` is the title; `subject` is what it acts on, shown muted beside it. */
export interface Described { verb: string; subject: string }

const VERBS: Record<string, string> = {
  web_search: 'Search the web', fetch_url: 'Read web page', open_page: 'Open web page',
  youtube_search: 'Search YouTube', youtube_video: 'Read YouTube video', github_search: 'Search GitHub',
  github_read: 'Read from GitHub', read_feed: 'Read RSS feed',
  search_documents: 'Search documents', read_document: 'Read document', list_documents: 'List documents',
  doc_list: 'List docs', doc_search: 'Search docs', doc_read: 'Read doc', doc_create: 'Create doc', doc_edit: 'Edit doc',
  search_memory: 'Search memory', save_memory: 'Save to memory', graph_search: 'Search knowledge graph',
  graph_traverse: 'Explore knowledge graph', graph_add: 'Add to knowledge graph',
  run_python: 'Run Python', current_time: 'Check the time',
  gmail_search: 'Search Gmail', gmail_read: 'Read email', gmail_draft: 'Draft email', gmail_send: 'Send email',
  gmail_outbox: 'Check send queue', gmail_modify: 'Update email',
  calendar_events: 'List calendar events', calendar_get: 'Read calendar event', calendar_create: 'Create calendar event',
  calendar_update: 'Update calendar event', calendar_delete: 'Delete calendar event', calendar_respond: 'Respond to invite',
  calendar_propose: 'Propose calendar change',
  google_tasks_list: 'List Google Tasks', google_tasks_add: 'Add Google Task', google_tasks_complete: 'Complete Google Task',
  google_docs_search: 'Search Google Docs', google_docs_read: 'Read Google Doc', google_docs_create: 'Create Google Doc',
  google_docs_append: 'Append to Google Doc', google_drive_search: 'Search Google Drive', google_drive_read: 'Read Drive file',
  google_sheets_read: 'Read Google Sheet', google_sheets_write: 'Write to Google Sheet', google_sheets_create: 'Create Google Sheet',
  find_files: 'Find files', read_local_file: 'Read file', write_local_file: 'Write file', move_local_file: 'Move file',
  trash_local_file: 'Move file to Trash', list_shortcuts: 'List Shortcuts', run_shortcut: 'Run shortcut',
  todo_write: 'Update plan', todo_add: 'Add to-do', schedule_task: 'Schedule a task', cancel_scheduled_task: 'Cancel scheduled task',
  scheduled_tasks: 'List scheduled tasks', propose_plan: 'Propose a plan', read_tool_result: 'Read earlier result',
  board_list: 'List boards', board_create: 'Create board', board_add_card: 'Add board card', board_move_card: 'Move board card',
  meeting_list: 'List meetings', meeting_read: 'Read meeting', meeting_search: 'Search meetings',
  activity_recent: 'Recent activity', activity_insights: 'Activity insights', activity_pause: 'Pause activity monitor',
  activity_access: 'Check activity access',
  skill_list: 'List skills', skill_draft: 'Draft skill', skill_revise: 'Revise skill',
  save_writing_sample: 'Save writing sample', writing_style: 'Read writing style',
  desk_list_files: 'List desk files', desk_read_file: 'Read desk file', desk_write_file: 'Write desk file',
  desk_trash_file: 'Trash desk file', desk_deliver: 'Deliver to desk', desk_ask: 'Ask a question', desk_done: 'Finish desk task',
  desk_import_sandbox: 'Import from sandbox',
  sandbox_exec: 'Run in sandbox', sandbox_write_file: 'Write sandbox file', sandbox_read_file: 'Read sandbox file',
  sandbox_list_files: 'List sandbox files', sandbox_put_document: 'Copy document to sandbox', sandbox_reset: 'Reset sandbox',
  sandbox_checkpoint: 'Save sandbox checkpoint', sandbox_restore: 'Restore sandbox checkpoint',
  shell_run: 'Run command', shell_poll: 'Check command output', shell_kill: 'Stop command', python_install: 'Install Python packages',
  fs_glob: 'Find files by name', fs_grep: 'Search file contents', fs_edit: 'Edit file', fs_copy: 'Copy file', fs_mkdir: 'Create folder',
  agent_spawn: 'Start subagent', agent_wait: 'Wait for subagents', agent_stop: 'Stop subagent',
  desk_fetch_file: 'Download file to desk',
  browser_open: 'Open in browser', browser_snapshot: 'Read browser page', browser_click: 'Click in browser', browser_type: 'Type in browser',
  browser_select: 'Choose in browser', browser_press: 'Press key in browser', browser_scroll: 'Scroll browser', browser_manage: 'Manage browser',
  view_image: 'Look at image', convert_document: 'Convert document', render_preview: 'Preview document pages', doc_guide: 'Read format guide',
  browser: 'Browser approval'
}

/** The argument that best names what a call acts on, in order of preference. */
const SUBJECT_KEYS = ['title', 'summary', 'subject', 'name', 'path', 'query', 'url', 'to', 'command', 'entity', 'document_id', 'repo', 'task_id']

function clip(s: string, n: number): string {
  const one = s.replace(/\s+/g, ' ').trim()
  return one.length > n ? one.slice(0, n - 1) + '…' : one
}

/** `snake_case_name` -> `Snake case name`. MCP names (`mcp__server__tool`) read as `Tool (server)`. */
export function humanizeName(name: string): string {
  const parts = name.split('__').filter(Boolean)
  const base = (parts.length >= 3 && parts[0] === 'mcp' ? parts[parts.length - 1] : name).replace(/[_-]+/g, ' ').trim()
  const label = base ? base[0].toUpperCase() + base.slice(1) : name
  return parts.length >= 3 && parts[0] === 'mcp' ? `${label} (${parts[1].replace(/[_-]+/g, ' ')})` : label
}

export function describeCall(name: string, args: Record<string, unknown> | null | undefined): Described {
  const a = args ?? {}
  const str = (k: string): string => (typeof a[k] === 'string' ? (a[k] as string) : '')
  const verb = VERBS[name] ?? humanizeName(name)
  switch (name) {
    case 'run_shortcut': return { verb, subject: str('name') ? `'${str('name')}'` : '' }
    case 'propose_plan': {
      const steps = Array.isArray(a.steps) ? a.steps.length : 0
      return { verb, subject: `${steps} ${steps === 1 ? 'action' : 'actions'}${str('title') ? ` · ${clip(str('title'), 60)}` : ''}` }
    }
    case 'move_local_file': return { verb, subject: str('path') && str('to') ? `${str('path')} → ${str('to')}` : str('path') }
    case 'run_python': return { verb, subject: clip(str('code').split('\n')[0] ?? '', 70) }
    case 'shell_run': return { verb, subject: clip(str('command'), 80) }
    case 'shell_poll':
    case 'shell_kill': return { verb, subject: str('job_id') ? `job ${str('job_id')}` : '' }
    case 'python_install': return { verb, subject: Array.isArray(a.packages) ? clip(a.packages.map(String).join(', '), 80) : '' }
    case 'fs_glob':
    case 'fs_grep': return { verb, subject: [clip(str('pattern'), 50), str('path') ? `in ${clip(str('path'), 40)}` : ''].filter(Boolean).join(' ') }
    case 'fs_copy': return { verb, subject: str('src') && str('dst') ? `${clip(str('src'), 40)} → ${clip(str('dst'), 40)}` : clip(str('src') || str('dst'), 80) }
    case 'agent_spawn': return { verb, subject: clip(str('task'), 80) }
    case 'agent_stop': return { verb, subject: str('id') }
    case 'agent_wait': return { verb, subject: Array.isArray(a.ids) && a.ids.length ? a.ids.map(String).join(', ') : 'all' }
    case 'desk_fetch_file': return { verb, subject: hostPath(str('url')) }
    case 'desk_ask': return { verb, subject: clip(str('question'), 80) }
    case 'desk_done': return { verb, subject: clip(str('summary'), 80) }
    case 'view_image': return { verb, subject: str('path') }
    case 'convert_document': return { verb, subject: str('path') && str('to') ? `${str('path')} → ${str('to')}` : str('path') }
    case 'render_preview': return { verb, subject: str('path') }
    case 'doc_guide': return { verb, subject: str('format') }
    case 'browser_open': return { verb, subject: hostPath(str('url')) }
    case 'browser_snapshot': return { verb, subject: str('query') ? `for “${clip(str('query'), 40)}”` : '' }
    case 'browser_click':
    case 'browser_select':
    case 'browser_scroll':
    case 'browser_manage':
    case 'browser_press':
    case 'browser_type': {
      const b = browserLine(name, a, null)
      return { verb, subject: clip(name === 'browser_manage' ? `${b.action} ${b.subject}`.trim() : b.subject, 80) }
    }
    case 'gmail_send':
    case 'gmail_draft': return { verb, subject: str('to') ? `to ${clip(str('to'), 50)}` : '' }
  }
  for (const k of SUBJECT_KEYS) {
    const v = a[k]
    if (typeof v === 'string' && v.trim()) return { verb, subject: clip(v, 80) }
  }
  return { verb, subject: '' }
}

/** One line: "Write file ~/Notes/x.md". */
export function fullTitle(name: string, args: Record<string, unknown> | null | undefined): string {
  const d = describeCall(name, args)
  return d.subject ? `${d.verb} ${d.subject}` : d.verb
}

/** A readable label for an argument key: `reply_to_message_id` -> `Reply to message`. */
export function labelFor(key: string): string {
  const k = key.replace(/_id$/, '').replace(/[_-]+/g, ' ').trim()
  return k ? k[0].toUpperCase() + k.slice(1) : key
}

/** Values longer than this are collapsed behind "Show more". */
export const COLLAPSE_AT = 160

export interface ArgRow {
  key: string
  label: string
  /** The value as readable text: strings as-is, booleans Yes/No, lists comma-joined, objects one `k: v` per line. */
  text: string
  /** Long enough to collapse. */
  long: boolean
}

export function formatValue(v: unknown): string {
  if (v === null || v === undefined) return ''
  if (typeof v === 'string') return v
  if (typeof v === 'boolean') return v ? 'Yes' : 'No'
  if (typeof v === 'number') return String(v)
  if (Array.isArray(v)) return v.every((x) => ['string', 'number', 'boolean'].includes(typeof x)) ? v.map(formatValue).join(', ') : v.map(formatValue).join('\n')
  if (typeof v === 'object') {
    return Object.entries(v as Record<string, unknown>)
      .filter(([, x]) => x !== null && x !== undefined && x !== '')
      .map(([k, x]) => `${labelFor(k)}: ${formatValue(x)}`).join('\n')
  }
  return String(v)
}

/** The arguments as a labelled list. Empty values are dropped; order is the call's own. */
export function argRows(args: Record<string, unknown> | null | undefined): ArgRow[] {
  return Object.entries(args ?? {})
    .map(([key, v]) => ({ key, label: labelFor(key), text: formatValue(v) }))
    .filter((r) => r.text !== '')
    .map((r) => ({ ...r, long: r.text.length > COLLAPSE_AT || r.text.split('\n').length > 4 }))
}

/** The first `n` characters/lines of a long value, for the collapsed form. */
export function preview(text: string, n = COLLAPSE_AT): string {
  const lines = text.split('\n')
  const head = lines.slice(0, 4).join('\n')
  return head.length > n ? head.slice(0, n).trimEnd() + '…' : lines.length > 4 ? head + '…' : head
}

export type ResultView =
  | { kind: 'empty' }
  | { kind: 'text'; text: string }
  | { kind: 'facts'; rows: ArgRow[] }
  | { kind: 'list'; items: string[]; total: number }

/** Keys the backend adds for its own bookkeeping; a person does not need them in a result. */
const RESULT_NOISE = new Set(['verification', 'replayed', 'ok', 'next_offset', 'has_more', 'truncated', 'handle'])

function oneLine(v: unknown): string {
  if (v && typeof v === 'object' && !Array.isArray(v)) {
    const o = v as Record<string, unknown>
    for (const k of SUBJECT_KEYS) if (typeof o[k] === 'string' && o[k]) return clip(String(o[k]), 120)
    const parts = Object.entries(o).filter(([, x]) => typeof x === 'string' || typeof x === 'number').slice(0, 3).map(([, x]) => String(x))
    return clip(parts.join(' · '), 120)
  }
  return clip(formatValue(v), 120)
}

/** A tool's result_preview (usually JSON text) as plain facts, a list, or text. Never the raw JSON. */
export function resultView(preview: string | null | undefined): ResultView {
  const raw = (preview ?? '').trim()
  if (!raw) return { kind: 'empty' }
  let parsed: unknown
  try { parsed = JSON.parse(raw) } catch { return { kind: 'text', text: raw } }
  if (Array.isArray(parsed)) {
    return parsed.length ? { kind: 'list', items: parsed.slice(0, 20).map(oneLine), total: parsed.length } : { kind: 'empty' }
  }
  if (parsed && typeof parsed === 'object') {
    const o = parsed as Record<string, unknown>
    const list = Object.entries(o).find(([, v]) => Array.isArray(v) && v.length > 0 && typeof v[0] === 'object')
    const rest = Object.fromEntries(Object.entries(o).filter(([k]) => !RESULT_NOISE.has(k) && k !== list?.[0]))
    if (list && Object.keys(rest).length <= 2) {
      const arr = list[1] as unknown[]
      return { kind: 'list', items: arr.slice(0, 20).map(oneLine), total: arr.length }
    }
    const rows = argRows(rest)
    return rows.length ? { kind: 'facts', rows } : { kind: 'empty' }
  }
  return { kind: 'text', text: formatValue(parsed) }
}

/** The lifecycle state a card shows. Derived only from persisted event fields, so a reload renders the same. */
export type CardStatus = 'awaiting' | 'running' | 'denied' | 'failed' | 'unverified' | 'verified' | 'done'

interface Verdict { status?: string }

export function readVerdict(preview: string | null | undefined): Verdict | null {
  if (!preview) return null
  try {
    const v = (JSON.parse(preview) as { verification?: Verdict }).verification
    return v && typeof v.status === 'string' ? v : null
  } catch { return null }
}

export function cardStatus(t: Pick<ToolEvent, 'pending' | 'needs_approval' | 'error' | 'approval' | 'result_preview'>): CardStatus {
  if (t.pending) return t.needs_approval ? 'awaiting' : 'running'
  if (t.approval === 'deny') return 'denied'
  if (t.error) return /unverified/i.test(t.error) ? 'unverified' : /declined by the user|just declined/i.test(t.error) ? 'denied' : 'failed'
  const v = readVerdict(t.result_preview)
  return v?.status === 'verified' ? 'verified' : 'done'
}

/** Did the user rewrite this call's arguments on its card? Read from persisted fields. */
export function wasEdited(t: Pick<ToolEvent, 'edited_by' | 'edited_arguments'>): boolean {
  return t.edited_by === 'user' && !!t.edited_arguments
}

/** Which keys differ between what the model proposed and what the user ran. */
export function changedKeys(original: Record<string, unknown> | null | undefined, edited: Record<string, unknown> | null | undefined): string[] {
  const keys = new Set([...Object.keys(original ?? {}), ...Object.keys(edited ?? {})])
  return [...keys].filter((k) => JSON.stringify((original ?? {})[k] ?? null) !== JSON.stringify((edited ?? {})[k] ?? null))
}
