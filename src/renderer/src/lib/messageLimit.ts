/**
 * How long one chat message may be. The backend enforces the same bound (`_message_too_long` in
 * app.py) before it stores anything; the formulas must stay identical, or the user sees a pass here
 * and a 413 at send. Pure: no store import.
 */
const DEFAULT_WINDOW = 128000
/** Matches the backend's context.estimate_tokens (len // 4). */
const CHARS_PER_TOKEN = 4
const WINDOW_FRACTION = 0.5

/** A pasted block longer than this becomes a .txt attachment instead of inline text. */
export const PASTE_AS_FILE_CHARS = 8000

export function messageCharLimit(contextWindow?: number): number {
  const w = typeof contextWindow === 'number' && Number.isFinite(contextWindow) && contextWindow > 0 ? contextWindow : DEFAULT_WINDOW
  return Math.floor(w * CHARS_PER_TOKEN * WINDOW_FRACTION)
}

/** A plain sentence when `length` is over `limit`, else null. */
export function tooLongNotice(length: number, limit: number): string | null {
  if (length <= limit) return null
  return `That message is about ${length.toLocaleString('en-US')} characters. One message can hold ${limit.toLocaleString('en-US')} with the current context window. Attach it as a file instead.`
}

export type PasteAction =
  | { kind: 'native' }
  | { kind: 'block'; notice: string }
  | { kind: 'file'; name: string; text: string }
  | { kind: 'files'; files: File[] }

/**
 * What a paste into the composer should do. A text payload wins over files: spreadsheets and rich
 * editors put an image rendition next to the text. Long text becomes a file (its own draft stays
 * small and the model reads it through the upload path); text that stays inline must still fit.
 */
export function classifyPaste(args: { text: string; files: File[]; draftLength: number; selectionLength: number; limit: number; stamp?: string }): PasteAction {
  const { text, files, draftLength, selectionLength, limit } = args
  if (text) {
    if (text.length > PASTE_AS_FILE_CHARS) return { kind: 'file', name: `pasted-${args.stamp ?? 'text'}.txt`, text }
    const notice = tooLongNotice(draftLength - selectionLength + text.length, limit)
    return notice ? { kind: 'block', notice } : { kind: 'native' }
  }
  return files.length ? { kind: 'files', files } : { kind: 'native' }
}
