/**
 * Turning a transcribed clip into text for the caret. Pure: the composer wires it to the input.
 */

/** Strip case and punctuation so "New line." and "new  paragraph," are still commands. */
const commandKey = (raw: string): string => raw.toLowerCase().replace(/[^a-z\s]/g, ' ').replace(/\s+/g, ' ').trim()

/** Characters after which no space is wanted before the next word. */
const OPENERS = /[\s(\[{“‘]$/
/** Punctuation that attaches to the word before it. */
const CLOSERS = /^[.,;:!?)\]}%”’]/

// The recogniser may already have punctuated around the word ("Thanks, period."), so absorb it. A comma counts
// anywhere; a sentence mark only ends the clip, because mid-sentence "the period of time" is a word.
const SPOKEN_COMMA = /\s*,?\s*\bcomma\b[.,!?]?/gi
const SPOKEN_END = /\s*,?\s*\b(period|question mark)\b[.,!?]?\s*$/i

/** Whether the next letter starts a sentence, judged from the text before the caret. */
function startsSentence(before: string): boolean {
  if (before.trim() === '') return true
  // A line start counts, including a markdown list or heading marker that is already typed.
  if (/\n\s*$/.test(before)) return true
  // A list or heading marker typed by a command ("- ", "## ") leaves the caret at a line start.
  if (/(^|\n)[ \t]*(?:[-*]|#{1,6}) $/.test(before)) return true
  return /[.!?]["”’)\]]*\s*$/.test(before)
}

const FILLER = /(?:^|\s)(?:um+|uh+|uhm|erm|hmm+)\b[,.]?(?=\s|$)/gi
const PUNCT: Record<string, string> = { period: '.', 'full stop': '.', comma: ',', 'question mark': '?', 'exclamation mark': '!', 'exclamation point': '!', colon: ':', semicolon: ';' }
const HEADINGS: Record<string, string> = { 'heading one': '# ', 'heading two': '## ', 'heading three': '### ' }

/**
 * The text to insert at the caret for one clip of dictation.
 *
 * - A clip that is, on its own, "new line" or "new paragraph" becomes that break. Only a whole
 *   utterance is a command: the same words inside a sentence are words the user said.
 * - Spoken "comma" becomes "," anywhere in the clip; "period" and "question mark" become the mark when they end
 *   it (mid-clip they are words: "the period of time").
 * - Otherwise one space joins it to what precedes, unless the caret already follows whitespace or
 *   an opening bracket, or the clip begins with punctuation that belongs to the previous word.
 * - The first letter is capitalised when the caret starts a sentence. A clip that continues one is
 *   left exactly as the recogniser wrote it: lower-casing it would also lower-case names.
 *
 * `before` is the text before the caret (a tail of a few dozen characters is enough).
 */
export function dictationText(raw: string, before: string): string {
  // Fillers go first, so "um, new line" is still the command. Only whole words: "umbrella" stays.
  const text = raw.replace(FILLER, ' ').trim().replace(/\s+/g, ' ')
  if (!text) return ''
  const cmd = commandKey(text)
  if (cmd === 'scratch that' || cmd === 'stop dictation') return ''
  if (PUNCT[cmd]) return PUNCT[cmd]
  const atLineStart = /(^|\n)[ \t]*$/.test(before)
  if (cmd === 'bullet' || cmd === 'next bullet') return /(^|\n)[ \t]*- $/.test(before) ? '' : atLineStart ? '- ' : '\n- '
  if (HEADINGS[cmd]) return atLineStart ? HEADINGS[cmd] : `\n${HEADINGS[cmd]}`
  if (cmd === 'new line' || cmd === 'newline') return '\n'
  if (cmd === 'new paragraph') {
    if (/\n\n\s*$/.test(before)) return ''
    return /\n\s*$/.test(before) ? '\n' : '\n\n'
  }
  // A spoken price arrives as `$12`, and two of them in one paragraph are the shape the preview
  // reads as inline maths. Speech never dictates a formula, so every amount is escaped.
  let out = text.replace(SPOKEN_COMMA, ',').replace(SPOKEN_END, (_, w: string) => (w.toLowerCase() === 'period' ? '.' : '?')).trim()
  out = out.replace(/(?<!\\)\$(?=\d)/g, '\\$')
  if (startsSentence(before)) out = out.charAt(0).toUpperCase() + out.slice(1)
  // A straight quote is ambiguous: it opens a quotation when it follows a space, a bracket or the
  // start, and closes one when it follows a word or punctuation (`point?"`).
  const openQuote = /(^|[\s(\[{])"$/.test(before)
  const needsSpace = before !== '' && !OPENERS.test(before) && !openQuote && !CLOSERS.test(out)
  return needsSpace ? ` ${out}` : out
}
