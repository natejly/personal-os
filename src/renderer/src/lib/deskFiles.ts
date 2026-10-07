import type { Desk, PromotionKind, RunChanges, RunInfo } from '@shared/types'

/**
 * Pure helpers behind the desk's Files / Changes / Browser tabs, kept out of the components so
 * they can be tested without a renderer.
 */

export type FileKind = 'image' | 'markdown' | 'text' | 'document' | 'other'

const IMAGE = /\.(png|jpe?g|gif|webp|bmp)$/i
const DOCUMENT = /\.(pdf|docx?|xlsx?|pptx?|odt|ods|odp|rtf|epub)$/i
const MARKDOWN = /\.(md|markdown)$/i

/**
 * What a file probably is, from its name and the tree's `is_text` flag. The preview route has the
 * final say (it answers with its own `kind`); this only decides things before it answers, such as
 * the icon and whether markdown gets rendered. SVG is text on purpose: it is agent-written and is
 * never rendered.
 */
export function fileKind(path: string, isText: boolean): FileKind {
  if (IMAGE.test(path)) return 'image'
  if (MARKDOWN.test(path)) return 'markdown'
  if (DOCUMENT.test(path)) return 'document'
  return isText ? 'text' : 'other'
}

const TEXT = /\.(md|markdown|txt|text|csv|tsv|json|ya?ml|html?|xml|rst|org|log)$/i
const PROMOTIONS: PromotionKind[] = ['doc', 'doc_append', 'document', 'download']

/**
 * Where an output goes unless the user picks otherwise. A retried row re-offers the destination
 * that failed, so a retry means the same thing it did; otherwise text becomes a doc, an office file
 * or PDF an uploaded document, and anything else (an image, an archive) is handed over as a file —
 * `doc` reads text only, so sending a workbook there could only fail.
 */
export function defaultDest(o: { path: string; promoted_kind: string | null }): PromotionKind {
  if (PROMOTIONS.includes(o.promoted_kind as PromotionKind)) return o.promoted_kind as PromotionKind
  if (TEXT.test(o.path)) return 'doc'
  if (DOCUMENT.test(o.path)) return 'document'
  return 'download'
}

export function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`
  return `${(n / 1024 / 1024 / 1024).toFixed(1)} GB`
}

/** "just now", "3 min ago", "2 h ago", then a date. `modified` is epoch seconds, as the backend sends it. */
export function fmtAgo(epochSeconds: number, nowMs: number = Date.now()): string {
  const s = Math.max(0, Math.round(nowMs / 1000 - epochSeconds))
  if (s < 10) return 'just now'
  if (s < 60) return `${s} s ago`
  if (s < 3600) return `${Math.floor(s / 60)} min ago`
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`
  return new Date(epochSeconds * 1000).toLocaleDateString()
}

/** Delivered files are the ones a desk_deliver call recorded as an output row; matched by path. */
export function deliveredPaths(outputs: { path: string; status: string }[]): Map<string, string> {
  const m = new Map<string, string>()
  for (const o of outputs) m.set(o.path, o.status)
  return m
}

/** The wording for an output row's status, as a file-row badge. */
export function deliveryLabel(status: string | undefined): string | null {
  if (!status) return null
  if (status === 'promoted' || status === 'accepted') return 'accepted'
  if (status === 'rejected') return 'rejected'
  return 'delivered'
}

export interface TurnChanges {
  runId: string
  startedAt: number
  files: RunChanges['files']
  state: RunChanges['state']
  skipped: string[]
}

/**
 * One entry per desk turn that actually changed files, newest first. A turn with no changes (the
 * agent only read things, or snapshots were unavailable) is left out: an empty row has nothing to
 * undo. Only the most recent `limit` runs are considered, because each costs a request.
 */
export function groupChangesByTurn(runs: Pick<RunInfo, 'run_id' | 'started_at'>[], changes: Record<string, RunChanges | undefined>, limit = 8): TurnChanges[] {
  return [...runs]
    .sort((a, b) => b.started_at - a.started_at)
    .slice(0, limit)
    .flatMap((r) => {
      const c = changes[r.run_id]
      return c && c.count > 0 ? [{ runId: r.run_id, startedAt: r.started_at, files: c.files, state: c.state, skipped: c.skipped }] : []
    })
}

/** The runs worth asking for changes: most recent first, capped. */
export function recentRunIds(runs: Pick<RunInfo, 'run_id' | 'started_at'>[], limit = 8): string[] {
  return [...runs].sort((a, b) => b.started_at - a.started_at).slice(0, limit).map((r) => r.run_id)
}

/** One run in GET /conversations/{id}/changes: the run's summary with the ids Undo and the reply footer use. */
export interface ChatRunChanges extends RunChanges { run_id: string; message_id: string | null; started_at: number }

export const STATUS_WORD: Record<'A' | 'M' | 'D', string> = { A: 'created', M: 'modified', D: 'deleted' }

/** The files an undo/redo left alone because they were edited after the turn. */
export function undoNote(edited: string[], max = 3): string {
  if (edited.length === 0) return ''
  const shown = edited.slice(0, max).join(', ')
  return `${edited.length} left alone (edited since): ${shown}${edited.length > max ? ', …' : ''}`
}

/** Each queued desk's 1-based place in line, oldest first — the order the backend launches them in. */
export const queuePositions = (desks: Desk[]): Map<string, number> =>
  new Map(desks.filter((d) => d.status === 'queued')
    .sort((a, b) => (a.queued_at ?? 0) - (b.queued_at ?? 0))
    .map((d, i) => [d.id, i + 1]))

/**
 * How attached files read in the chat bubble: a chip naming them, not a sentence the user never
 * typed. `[report.pdf]`, `[notes.md, data.csv]`, `[3 files]`. Paths are what addInputs answered
 * with (`inputs/<name>`, already de-duplicated by the workspace).
 */
export function inputChip(paths: string[]): string {
  const names = paths.map((p) => p.replace(/^inputs\//, ''))
  if (!names.length) return ''
  return `[${names.length > 2 ? `${names.length} files` : names.join(', ')}]`
}

/** The host of a URL for emphasis, with the rest split off. Falls back to the raw string. */
export function splitUrl(url: string): { host: string; rest: string } {
  try {
    const u = new URL(url)
    return { host: u.host, rest: `${u.pathname === '/' ? '' : u.pathname}${u.search}` }
  } catch {
    return { host: url, rest: '' }
  }
}
