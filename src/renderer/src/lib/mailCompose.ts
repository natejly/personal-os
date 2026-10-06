/**
 * Pure helpers behind the EmailCard: address parsing, "did the person change the model's draft?",
 * the arguments an edited card posts back, and a lenient reader for the (possibly truncated) JSON
 * preview a persisted tool event keeps. No React in here so node:test can run it.
 */

export interface Recipient {
  /** The display name, '' when the address stands alone. */
  name: string
  email: string
}

const ADDR = /^[\w.!#$%&'*+/=?^`{|}~-]+@[\w-]+(?:\.[\w-]+)+$/
const NAMED = /^(.*?)<([^<>]+)>$/

export function isValidEmail(s: string): boolean {
  const t = s.trim()
  return t.length <= 254 && ADDR.test(t)
}

/** One entry the way Gmail would show it: `Name <a@b.c>` or the bare address. */
export function formatRecipient(r: Recipient): string {
  return r.name ? `${r.name} <${r.email}>` : r.email
}

/** Splits on commas, semicolons and newlines (not spaces: a display name has them). Entries that are
 *  not addresses come back in `invalid`, untouched, so the card can show them as fixable chips. */
export function parseAddressList(raw: string): { ok: Recipient[]; invalid: string[] } {
  const ok: Recipient[] = []
  const invalid: string[] = []
  // A quoted display name may hold a comma ("Doe, Jane" <j@d.co>): keep quoted runs whole.
  const parts = raw.match(/(?:"[^"]*"|[^,;\n"])+/g) ?? []
  for (const part of parts) {
    const t = part.trim()
    if (!t) continue
    const m = NAMED.exec(t)
    const email = (m ? m[2] : t).trim()
    const name = m ? m[1].trim().replace(/^"|"$/g, '').trim() : ''
    if (isValidEmail(email) && !/[<>"\r\n]/.test(name)) ok.push({ name, email })
    else invalid.push(t)
  }
  return { ok, invalid }
}

/** The To value stored in the tool arguments, as chips. Bad entries keep their text. */
export function recipientsFromArg(v: unknown): { ok: Recipient[]; invalid: string[] } {
  if (Array.isArray(v)) return parseAddressList(v.map(String).join(','))
  return parseAddressList(typeof v === 'string' ? v : '')
}

export function recipientsToArg(list: Recipient[]): string {
  return list.map(formatRecipient).join(', ')
}

const norm = (s: unknown): string => String(s ?? '').replace(/\r\n/g, '\n').replace(/[ \t]+$/gm, '').trim()

export interface ComposeDraft {
  to: Recipient[]
  subject: string
  body: string
}

export type DraftField = 'to' | 'subject' | 'body'

/** Which of the model's fields the person changed. Whitespace-at-line-end and case in the domain part
 *  of an address do not count; everything else does. */
export function editedFields(original: Record<string, unknown>, draft: ComposeDraft): DraftField[] {
  const out: DraftField[] = []
  const was = recipientsFromArg(original.to).ok.map((r) => `${r.name}|${r.email.toLowerCase()}`)
  const now = draft.to.map((r) => `${r.name}|${r.email.toLowerCase()}`)
  if (was.length !== now.length || was.some((x, i) => x !== now[i])) out.push('to')
  if (norm(original.subject) !== norm(draft.subject)) out.push('subject')
  if (norm(original.body) !== norm(draft.body)) out.push('body')
  return out
}

/** The arguments a decision posts. Keeps everything the model sent (reply_to_message_id included) and
 *  overlays what the card edits; `asDraft` switches a send to Save-as-draft (gmail_send only). */
export function composeArgs(original: Record<string, unknown>, draft: ComposeDraft, asDraft = false): Record<string, unknown> {
  const out: Record<string, unknown> = { ...original, to: recipientsToArg(draft.to), subject: draft.subject.trim(), body: draft.body }
  delete out.as_draft
  if (asDraft) out.as_draft = true
  return out
}

/** Why the draft cannot go yet, or null. */
export function composeProblem(draft: ComposeDraft, invalid: string[]): string | null {
  if (invalid.length) return `"${invalid[0]}" is not a valid email address.`
  if (!draft.to.length) return 'Add at least one recipient.'
  if (/[\r\n]/.test(draft.subject)) return 'The subject must be a single line.'
  return null
}

/** Does this body look like markdown the recipient would see as stray symbols? */
export function looksMarkdown(s: string): boolean {
  return /(^|\n)\s{0,3}#{1,6}\s|\*\*[^*\n]+\*\*|(^|\n)\s*[-*]\s+\S.*\n\s*[-*]\s+\S|\[[^\]\n]+\]\([^)\s]+\)|`[^`\n]+`/.test(s)
}

/** Markdown to the plain text a mail client would show. Line breaks are the model's own and are kept. */
export function markdownToPlain(s: string): string {
  return s
    .replace(/```[^\n]*\n([\s\S]*?)```/g, '$1')
    .replace(/^\s{0,3}#{1,6}\s+/gm, '')
    .replace(/\*\*([^*\n]+)\*\*/g, '$1')
    .replace(/(^|[\s(])__([^_\n]+)__(?=[\s).,;:!?]|$)/g, '$1$2')
    .replace(/(^|[\s(])\*([^*\n]+)\*(?=[\s).,;:!?]|$)/g, '$1$2')
    .replace(/`([^`\n]+)`/g, '$1')
    .replace(/\[([^\]\n]+)\]\(([^)\s]+)\)/g, (_m, t: string, u: string) => (t === u ? u : `${t} (${u})`))
    .replace(/^\s*[*]\s+/gm, '- ')
}

// ---------- reading a tool event's result ----------

/** The preview is capped server-side; a big result arrives as {truncated, preview: "<cut json>"}. Parse what
 *  parses, and otherwise pull the plain string fields out of the cut text so a long email still reads. */
export function readPreview(preview: string | null | undefined): Record<string, unknown> | null {
  if (!preview) return null
  let o: unknown
  try {
    o = JSON.parse(preview)
  } catch {
    return null
  }
  if (!o || typeof o !== 'object' || Array.isArray(o)) return null
  const rec = o as Record<string, unknown>
  if (rec.truncated === true && typeof rec.preview === 'string') return salvage(rec.preview)
  return rec
}

function salvage(cut: string): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  const re = /"([a-z_]+)"\s*:\s*"((?:[^"\\]|\\.)*)\\?("|$)/g
  for (let m = re.exec(cut); m; m = re.exec(cut)) {
    try {
      out[m[1]] = JSON.parse(`"${m[2]}"`)
    } catch {
      out[m[1]] = m[2]
    }
  }
  return out
}

export interface MailRow {
  id: string
  threadId: string | null
  from: string
  subject: string
  snippet: string
  date: string | null
  unread: boolean
}

export function parseMailRows(preview: string | null | undefined): { rows: MailRow[]; more: number } {
  const o = readPreview(preview)
  const list = o && Array.isArray(o.messages) ? (o.messages as Record<string, unknown>[]) : []
  const t = o?.truncated as { of?: number; kept?: number } | undefined
  return {
    rows: list.filter((m) => m && typeof m === 'object').map((m) => ({
      id: String(m.id ?? ''), threadId: m.thread_id ? String(m.thread_id) : null,
      from: String(m.from ?? ''), subject: String(m.subject ?? ''), snippet: String(m.snippet ?? ''),
      date: m.date ? String(m.date) : null, unread: Boolean(m.unread)
    })),
    more: t && typeof t.of === 'number' && typeof t.kept === 'number' ? Math.max(0, t.of - t.kept) : 0
  }
}

export interface MailMessage {
  id: string
  from: string
  to: string
  subject: string
  date: string | null
  body: string
  clipped: boolean
}

export function parseMailMessage(preview: string | null | undefined): MailMessage | null {
  const o = readPreview(preview)
  if (!o || (o.body === undefined && o.subject === undefined && o.from === undefined)) return null
  return {
    id: String(o.id ?? ''), from: String(o.from ?? ''), to: String(o.to ?? ''), subject: String(o.subject ?? ''),
    date: o.date ? String(o.date) : null, body: String(o.body ?? ''),
    clipped: String(preview ?? '').includes('"truncated"')
  }
}

/** "Ana Ruiz <ana@x.co>" to its name, falling back to the address. */
export function senderName(from: string): string {
  const m = NAMED.exec(from.trim())
  const name = m ? m[1].trim().replace(/^"|"$/g, '').trim() : ''
  return name || (m ? m[2].trim() : from.trim()) || 'Unknown'
}

export function senderInitial(from: string): string {
  const c = senderName(from).replace(/[^\p{L}\p{N}]/gu, '').charAt(0)
  return c ? c.toUpperCase() : '?'
}

/** Gmail-style short time: clock today, "Mon 12" this year, date otherwise. */
export function shortTime(iso: string | null, now: Date = new Date()): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  if (d.toDateString() === now.toDateString()) return d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
  const opts: Intl.DateTimeFormatOptions = d.getFullYear() === now.getFullYear() ? { month: 'short', day: 'numeric' } : { year: 'numeric', month: 'short', day: 'numeric' }
  return d.toLocaleDateString([], opts)
}

/** m:ss for the undo countdown. */
export function clock(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds))
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}

export function gmailLink(kind: 'message' | 'drafts', id?: string | null): string {
  return kind === 'drafts' || !id ? 'https://mail.google.com/mail/u/0/#drafts' : `https://mail.google.com/mail/u/0/#all/${encodeURIComponent(id)}`
}
