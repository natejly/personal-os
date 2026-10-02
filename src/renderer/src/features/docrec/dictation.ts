import type { MeetingSegment } from '@shared/types'
import { isSettled } from './segments'

/**
 * Turning a transcribed clip into text for the caret, and deciding which clips are ready to be
 * typed. Both are pure: the hook only wires them to the store and the editor.
 */

/** Strip case and punctuation so "New line." and "new  paragraph," are still commands. */
const commandKey = (raw: string): string => raw.toLowerCase().replace(/[^a-z\s]/g, ' ').replace(/\s+/g, ' ').trim()

/** Characters after which no space is wanted before the next word. */
const OPENERS = /[\s(\[{“‘]$/
/** Punctuation that attaches to the word before it. */
const CLOSERS = /^[.,;:!?)\]}%”’]/

/** Whether the next letter starts a sentence, judged from the text before the caret. */
function startsSentence(before: string): boolean {
  if (before.trim() === '') return true
  // A line start counts, including a markdown list or heading marker that is already typed.
  if (/\n\s*$/.test(before)) return true
  return /[.!?]["”’)\]]*\s*$/.test(before)
}

/**
 * The text to insert at the caret for one clip of dictation.
 *
 * - A clip that is, on its own, "new line" or "new paragraph" becomes that break. Only a whole
 *   utterance is a command: the same words inside a sentence are words the user said.
 * - Otherwise one space joins it to what precedes, unless the caret already follows whitespace or
 *   an opening bracket, or the clip begins with punctuation that belongs to the previous word.
 * - The first letter is capitalised when the caret starts a sentence. A clip that continues one is
 *   left exactly as the recogniser wrote it: lower-casing it would also lower-case names.
 *
 * `before` is the text before the caret (a tail of a few dozen characters is enough).
 */
export function dictationText(raw: string, before: string): string {
  const text = raw.trim().replace(/\s+/g, ' ')
  if (!text) return ''
  const cmd = commandKey(text)
  if (cmd === 'new line' || cmd === 'newline') return '\n'
  if (cmd === 'new paragraph') {
    if (/\n\n\s*$/.test(before)) return ''
    return /\n\s*$/.test(before) ? '\n' : '\n\n'
  }
  // A spoken price arrives as `$12`, and two of them in one paragraph are the shape the preview
  // reads as inline maths. Speech never dictates a formula, so every amount is escaped.
  let out = text.replace(/(?<!\\)\$(?=\d)/g, '\\$')
  if (startsSentence(before)) out = out.charAt(0).toUpperCase() + out.slice(1)
  // A straight quote is ambiguous: it opens a quotation when it follows a space, a bracket or the
  // start, and closes one when it follows a word or punctuation (`point?"`).
  const openQuote = /(^|[\s(\[{])"$/.test(before)
  const needsSpace = before !== '' && !OPENERS.test(before) && !openQuote && !CLOSERS.test(out)
  return needsSpace ? ` ${out}` : out
}

/**
 * Which clips of a dictation session may be typed now.
 *
 * Mic clips only, in recording order, and never past an unfinished one: the transcription queue is
 * FIFO today, but a retry or a slow clip must not let a later clip jump ahead and scramble the
 * sentence. `consumed` lists every clip that is now decided, including silent and failed ones, so
 * the caller can mark them seen and never look at them again.
 */
export function readyForInsert(segments: MeetingSegment[], seen: ReadonlySet<string>): { ready: MeetingSegment[]; consumed: string[] } {
  const mic = segments
    .filter((s) => s.channel === 'mic' && !seen.has(s.id))
    .sort((a, b) => a.seq - b.seq || a.t_start - b.t_start)
  const ready: MeetingSegment[] = []
  const consumed: string[] = []
  for (const s of mic) {
    if (!isSettled(s)) break
    consumed.push(s.id)
    if (s.state === 'done' && s.text.trim()) ready.push(s)
  }
  return { ready, consumed }
}

/**
 * What to do with a dictation's clips right now.
 *
 * `typed` is the set of clips already decided for THIS recording (typed, or silent/failed). `ready`
 * are the clips to type, in order, and are empty while the editor cannot take text: they stay
 * undecided so they are typed when it can. `skip` are decided clips with nothing to type, safe to
 * mark at once. A clip is only added to `typed` by the caller after its insert actually ran.
 */
export function planDictation(
  segments: MeetingSegment[], typed: ReadonlySet<string>, canInsert: boolean
): { ready: MeetingSegment[]; skip: string[] } {
  const { ready, consumed } = readyForInsert(segments, typed)
  const readyIds = new Set(ready.map((s) => s.id))
  return { ready: canInsert ? ready : [], skip: consumed.filter((id) => !readyIds.has(id)) }
}

/** Every mic clip held is decided and none is still in flight: nothing more will be typed from these. */
export function dictationDrained(segments: MeetingSegment[], typed: ReadonlySet<string>): boolean {
  return segments.every((s) => s.channel !== 'mic' || (isSettled(s) && typed.has(s.id)))
}

/**
 * Dictation recordings, by meeting id, kept for as long as the app runs rather than as long as a
 * component is mounted. The doc is fixed when the dictation is first seen, so a clip can only ever be
 * typed into the doc it was dictated into, and `typed` survives switching docs, leaving the Docs
 * view and a preview-only stretch with no editor, so the clips said meanwhile are typed on return.
 */
export interface DictationSession { docId: string; typed: Set<string>; endedAt: number }
const sessions = new Map<string, DictationSession>()

export function trackDictation(meetingId: string, docId: string): DictationSession {
  let s = sessions.get(meetingId)
  if (!s) { s = { docId, typed: new Set(), endedAt: 0 }; sessions.set(meetingId, s) }
  return s
}
export const forgetDictation = (meetingId: string): void => { sessions.delete(meetingId) }
export function dictationsFor(docId: string): Array<[string, DictationSession]> {
  return [...sessions].filter(([, s]) => s.docId === docId)
}
