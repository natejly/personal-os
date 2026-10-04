/**
 * Pure helpers behind the desk tool cards (shell, python, browser, vision, documents, desk tools): read a
 * tool's result_preview, and turn calls and results into the short lines a card shows. No React in here, so
 * toolResult.test.ts covers it directly.
 *
 * A result_preview is at most ~1500 characters (backend `summarize_result`). A bigger result arrives as
 * `{"truncated": true, "preview": "<the JSON text, cut>"}`, so a card cannot rely on JSON.parse succeeding.
 * `parseResult` therefore reads what it can from the cut text and says it was cut.
 */
import { hostPath } from './browserApproval'

export type Fields = Record<string, unknown>

export interface Parsed {
  /** The fields found: the whole object when the preview parsed, the readable leading ones when it was cut. Null for non-JSON. */
  data: Fields | null
  /** Some of the result is missing (the backend cut it, or the preview ended mid-value). */
  cut: boolean
}

const STRING_KEYS = ['output', 'stdout', 'stderr', 'cwd', 'job_id', 'status', 'url', 'title', 'snapshot', 'path', 'output_path', 'converter', 'description',
  'text', 'note', 'model', 'error', 'answer', 'choice', 'summary', 'question', 'format']
const NUMBER_KEYS = ['exit_code', 'duration_s', 'bytes', 'width', 'height', 'tab', 'total_pages', 'total_bytes']
const BOOL_KEYS = ['timed_out', 'still_running', 'background', 'ocr', 'truncated']

/** A JSON string body that may stop mid-way: decode what is there, dropping a dangling escape. */
function decodeLoose(body: string): string {
  let b = body
  for (let i = 0; i < 6; i++) {
    try { return JSON.parse(`"${b}"`) as string } catch { b = b.slice(0, -1) }
  }
  return ''
}

/** Pull the known keys out of JSON text that may be cut anywhere. Order-independent; a missing key is simply absent. */
export function looseFields(text: string): Fields {
  const out: Fields = {}
  for (const k of STRING_KEYS) {
    const m = new RegExp(`"${k}":\\s*"((?:[^"\\\\]|\\\\.)*)`).exec(text)
    if (m) out[k] = decodeLoose(m[1])
  }
  for (const k of NUMBER_KEYS) {
    const m = new RegExp(`"${k}":\\s*(-?\\d+(?:\\.\\d+)?)`).exec(text)
    if (m) out[k] = Number(m[1])
  }
  for (const k of BOOL_KEYS) {
    const m = new RegExp(`"${k}":\\s*(true|false)`).exec(text)
    if (m) out[k] = m[1] === 'true'
  }
  return out
}

export function parseResult(preview: string | null | undefined): Parsed {
  const raw = (preview ?? '').trim()
  if (!raw) return { data: null, cut: false }
  let v: unknown
  try { v = JSON.parse(raw) } catch { return raw.startsWith('{') ? { data: looseFields(raw), cut: true } : { data: null, cut: false } }
  if (!v || typeof v !== 'object' || Array.isArray(v)) return { data: null, cut: false }
  const o = v as Fields
  if (o.truncated === true && typeof o.preview === 'string') return { data: looseFields(o.preview), cut: true }
  return { data: o, cut: o.truncated !== undefined && typeof o.truncated === 'object' }
}

export const str = (v: unknown): string => (typeof v === 'string' ? v : '')
export const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null)
export const strList = (v: unknown): string[] => (Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : [])

/** The last `max` lines of output, and how many earlier lines are not shown. */
export function tailLines(text: string, max = 12): { shown: string; hidden: number } {
  const lines = text.replace(/\s+$/, '').split('\n')
  if (lines.length <= max) return { shown: lines.join('\n'), hidden: 0 }
  return { shown: lines.slice(-max).join('\n'), hidden: lines.length - max }
}

/** "reached pypi.org · blocked example.com", from a shell result's `network` field (an object, or a plain boolean). */
export function networkLine(net: unknown): string | null {
  if (net === true) return 'network allowed'
  if (!net || typeof net !== 'object') return null
  const n = net as Fields
  const reached = strList(n.contacted)
  const blocked = strList(n.blocked)
  const bits: string[] = []
  if (reached.length) bits.push(`reached ${reached.join(', ')}`)
  if (blocked.length) bits.push(`blocked ${blocked.join(', ')}`)
  return bits.length ? bits.join(' · ') : 'no network contact'
}

export type ShellTone = 'ok' | 'warn' | 'bad' | 'run'
/** The one-phrase outcome of a shell_run / shell_poll result, with how a card should colour it. */
export function shellState(name: string, d: Fields | null, pending: boolean): { label: string; tone: ShellTone } {
  if (pending) return { label: 'running', tone: 'run' }
  if (!d) return { label: 'finished', tone: 'ok' }
  const job = str(d.job_id)
  const code = num(d.exit_code)
  if (d.still_running === true) return { label: `timed out, moved to background${job ? ` (job ${job})` : ''}`, tone: 'warn' }
  if (d.timed_out === true) return { label: 'timed out and stopped', tone: 'bad' }
  if (name === 'shell_poll') {
    const s = str(d.status)
    if (s === 'running') return { label: `still running${job ? ` (job ${job})` : ''}`, tone: 'run' }
    if (s === 'killed') return { label: 'stopped', tone: 'warn' }
    if (s === 'exited' || s === 'timed_out') return { label: code === null ? 'finished' : `exited ${code}`, tone: code === 0 ? 'ok' : 'bad' }
    return { label: s || 'finished', tone: 'warn' }
  }
  if (d.background === true && code === null) return { label: `running in background${job ? ` (job ${job})` : ''}`, tone: 'run' }
  if (code === null) return { label: 'finished', tone: 'ok' }
  return { label: code === 0 ? 'exit 0' : `exit ${code}`, tone: code === 0 ? 'ok' : 'bad' }
}

/** A sandbox_list_files row. */
export interface SandboxEntry { type: 'dir' | 'file'; bytes: number; path: string }

/** The rows of a sandbox_list_files result (the backend drops trailing rows rather than cutting one). */
export function sandboxEntries(d: Fields | null): SandboxEntry[] {
  const rows: unknown[] = Array.isArray(d?.entries) ? d.entries : []
  return rows.flatMap((r) => {
    const e = (r && typeof r === 'object' ? r : {}) as Fields
    return str(e.path) ? [{ type: e.type === 'dir' ? 'dir' as const : 'file' as const, bytes: num(e.bytes) ?? 0, path: str(e.path) }] : []
  })
}

/** The one-line outcome of a sandbox write, export or checkpoint call; '' when the result says nothing readable. */
export function sandboxLine(name: string, d: Fields | null): string {
  if (!d) return ''
  const bytes = num(d.bytes)
  const size = bytes === null ? '' : `${bytes.toLocaleString('en-US')} bytes `
  switch (name) {
    case 'sandbox_write_file':
    case 'sandbox_put_document':
      return str(d.written) ? `${d.appended === true ? 'appended' : 'wrote'} ${size}to ${str(d.written)}` : ''
    case 'sandbox_export_file': return str(d.saved) ? `saved ${size}to ${str(d.saved)}` : ''
    case 'sandbox_checkpoint': {
      const kept = strList(d.kept)
      return str(d.checkpoint) ? `saved checkpoint “${str(d.checkpoint)}”${kept.length > 1 ? ` (keeping ${kept.join(', ')})` : ''}` : ''
    }
    case 'sandbox_restore': return str(d.restored) ? `restored checkpoint “${str(d.restored)}”` : ''
    case 'sandbox_reset': return d.reset === true ? 'reset; the next call starts from a fresh container' : ''
    default: return ''
  }
}

/** "1.4 s", "2 min 5 s". */
export function fmtSeconds(s: number | null): string {
  if (s === null) return ''
  if (s < 10) return `${Math.round(s * 10) / 10} s`
  const t = Math.round(s)
  return t < 60 ? `${t} s` : `${Math.floor(t / 60)} min ${t % 60} s`
}

/** The snapshot line carrying `[ref]`, so the card can tell what a ref names. */
export function snapshotLine(snapshot: string, ref: string): string {
  if (!ref) return ''
  // Interactive lines are rendered `e5 textbox "Name" [flags] = "value"` (axSnapshot.ts render).
  return snapshot.split('\n').map((l) => l.trim()).find((l) => l.startsWith(`${ref} `)) ?? ''
}

/** A field that holds a secret: typed text must never be echoed into the transcript for these. */
export const looksSecret = (line: string): boolean => /password|passcode|one-time|cvv|cvc|card number|credit card|\bcc-/i.test(line)

/** The page-state facts every browser result shares. */
export interface BrowserView { action: string; subject: string; url: string; title: string }

/**
 * One line for a browser_* call. A typed value is shown only when the snapshot proves the field is not a
 * secret; otherwise (password, payment, or the field is not in the cut preview) only its length appears.
 */
export function browserLine(name: string, args: Fields | null | undefined, d: Fields | null): { action: string; subject: string } {
  const a = args ?? {}
  const ref = str(a.ref)
  const snap = str(d?.snapshot)
  switch (name) {
    case 'browser_open': return { action: 'Open', subject: hostPath(str(a.url)) + (a.new_tab === true ? ' (new tab)' : '') }
    case 'browser_snapshot': return { action: 'Read the page', subject: str(a.query) ? `for “${str(a.query)}”` : a.full === true ? '(full)' : '' }
    case 'browser_click': return { action: a.double === true ? 'Double-click' : 'Click', subject: ref }
    case 'browser_type': {
      const text = str(a.text)
      const line = snapshotLine(snap, ref)
      const visible = !!line && !looksSecret(line)
      const typed = visible ? `“${text.length > 60 ? text.slice(0, 59) + '…' : text}”` : `${text.length} characters`
      return { action: 'Type', subject: `${typed} into ${ref}${a.submit === true ? ' and submit' : ''}` }
    }
    case 'browser_select': return { action: 'Choose', subject: `${strList(a.values).join(', ')} in ${ref}` }
    case 'browser_press': return { action: 'Press', subject: `${str(a.key)}${ref ? ` in ${ref}` : ''}` }
    case 'browser_scroll': return { action: 'Scroll', subject: `${str(a.direction) || 'down'}${num(a.amount) !== null ? ` ${num(a.amount)} screen${a.amount === 1 ? '' : 's'}` : ''}` }
    case 'browser_manage': {
      const act = str(a.action)
      const label: Record<string, string> = { back: 'Go back', forward: 'Go forward', reload: 'Reload', tabs: 'List tabs', wait: 'Wait', dialog: 'Answer a dialog',
        screenshot: 'Take a screenshot', upload: 'Upload files', handoff: 'Hand the browser to you', close: 'Close the browser', close_tab: 'Close a tab', switch_tab: 'Switch tab' }
      return { action: label[act] ?? (act ? act.replace(/_/g, ' ') : 'Manage the browser'), subject: act === 'wait' && num(a.ms) !== null ? `${num(a.ms)} ms` : act === 'handoff' ? str(a.reason) : '' }
    }
    default: return { action: name, subject: '' }
  }
}

/** The open problems in a desk_done refusal ("... not finished yet.\n1. ...\n2. ..."), one per item. Null when it is not a gate refusal. */
export function gateProblems(error: string | null | undefined): { lead: string; problems: string[] } | null {
  const e = (error ?? '').trim()
  if (!/^desk_done refused/i.test(e)) return null
  const [lead, ...rest] = e.split('\n')
  const items = rest.map((l) => l.replace(/^\s*\d+[.)]\s*/, '').trim()).filter(Boolean)
  return { lead: lead.replace(/^desk_done refused:?\s*/i, '').replace(/[:.]\s*$/, ''), problems: items }
}

/** One file a plain chat's tool saved for the user (backend `Workspace.output_entry`); path is relative to the chat's files. */
export interface OutputFile { name: string; size: number; path: string }

const OUTPUT_ENTRY = /\{\s*"name":\s*"((?:[^"\\]|\\.)*)",\s*"size":\s*(\d+),\s*"path":\s*"((?:[^"\\]|\\.)*)"\s*\}/g

const isOutput = (f: unknown): f is OutputFile => !!f && typeof f === 'object' && typeof (f as Fields).name === 'string' &&
  typeof (f as Fields).size === 'number' && typeof (f as Fields).path === 'string'

/**
 * The `outputs` a result lists (sandbox_export_file, run_python, a browser download in a chat). The backend puts the key
 * first, so when the preview was cut the complete entries at its start are still read.
 */
export function outputFiles(preview: string | null | undefined): OutputFile[] {
  let text = (preview ?? '').trim()
  try {
    const o = JSON.parse(text) as Fields
    if (!o || typeof o !== 'object' || Array.isArray(o)) return []
    if (Array.isArray(o.outputs)) return o.outputs.filter(isOutput)
    if (o.truncated !== true || typeof o.preview !== 'string') return []
    text = o.preview
  } catch { /* cut JSON: read it loosely below */ }
  const head = /^\{\s*"outputs":\s*\[/.exec(text)
  if (!head) return []
  const body = text.slice(head[0].length)
  const out: OutputFile[] = []
  let at = 0
  for (const m of body.matchAll(OUTPUT_ENTRY)) {
    if (body.slice(at, m.index).replace(/[\s,]/g, '')) break // past the array: something else sits between entries
    out.push({ name: decodeLoose(m[1]), size: Number(m[2]), path: decodeLoose(m[3]) })
    at = (m.index ?? 0) + m[0].length
  }
  return out
}
