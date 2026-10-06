import type { Meeting } from '@shared/types'
import { modeLabel } from './format'

/**
 * The line a recording leaves in its doc: an ordinary markdown link with a private scheme, so it
 * survives save, copy/paste and export as plain text. The label is written once and never
 * rewritten; the preview's chip reads the recording's live state by id.
 */
export const REC_SCHEME = 'grain-recording:'

export function recordingBlockLabel(row: Pick<Meeting, 'started_at' | 'updated_at' | 'doc_mode'>, now = Date.now()): string {
  const d = new Date((row.started_at ?? row.updated_at) * 1000 || now)
  const hm = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
  return `${modeLabel(row.doc_mode ?? null)} ${hm}`
}

export const recordingBlockLine = (id: string, label: string): string => `[${label}](${REC_SCHEME}${id})`

/** The recording id in an href, or null when it is not one. */
export const recordingIdFromHref = (href?: string): string | null => {
  if (!href?.startsWith(REC_SCHEME)) return null
  const id = href.slice(REC_SCHEME.length)
  return /^[\w-]+$/.test(id) ? id : null
}

/** What to type at the caret so the block sits on a line of its own. `before`/`after` are the text around the caret. */
export function blockInsertText(line: string, before: string, after: string): string {
  return `${before === '' || before.endsWith('\n') ? '' : '\n'}${line}${after.startsWith('\n') ? '' : '\n'}`
}
