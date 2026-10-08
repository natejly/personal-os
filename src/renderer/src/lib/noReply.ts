// NO_REPLY is the backend's "nothing to say" marker (workers.py is_silent). The user never sees it.

const SENTINEL = 'NO_REPLY'
const WHOLE = /^[\s`*_~>."'“”‘’]*NO_REPLY[\s`*_~.!?,;:"'“”‘’]*$/i

/** The text is only the marker (markdown, quotes or trailing punctuation aside). Empty text is not. */
export function isNoReply(text: string | null | undefined): boolean {
  return WHOLE.test(text ?? '')
}

/** A streaming reply that so far could still become the marker ("N", "NO_", "NO_REPL"): show nothing yet. */
export function isNoReplyPrefix(text: string | null | undefined): boolean {
  if (isNoReply(text)) return true
  const t = (text ?? '').replace(/^[\s`*_"']+|[`*"']+$/g, '').toUpperCase()
  return t !== '' && SENTINEL.startsWith(t)
}

/** The reply without marker lines at its start or end ('' when that was all it said). */
export function stripNoReply(text: string | null | undefined): string {
  const lines = (text ?? '').trim().split('\n')
  while (lines.length && (isNoReply(lines[0]) || !lines[0].trim())) lines.shift()
  while (lines.length && (isNoReply(lines[lines.length - 1]) || !lines[lines.length - 1].trim())) lines.pop()
  return lines.join('\n')
}

type Streamed = { content: string; held?: string }

/** A delta added to a streaming message: held back (content stays empty) while the reply so far could be the marker. */
export function appendDelta(m: Streamed, text: string): Streamed {
  const all = m.content + (m.held ?? '') + text
  return !m.content.trim() && isNoReplyPrefix(all) ? { content: '', held: all } : { content: all, held: undefined }
}

/** The reply is whole: held text that was not the marker after all ("No") becomes content; the marker itself is dropped. */
export function settleHeld(m: Streamed): Streamed {
  return { content: m.held && !isNoReply(m.held) ? m.content + m.held : m.content, held: undefined }
}
