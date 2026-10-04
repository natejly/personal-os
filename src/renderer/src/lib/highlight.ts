/** Split text around [start, end) so the middle can be wrapped in <mark>. An invalid span (-1, empty or out of range) leaves the text whole. */
export function splitHighlight(text: string, start: number, end: number): [string, string, string] {
  if (start < 0 || end <= start || start >= text.length) return [text, '', '']
  return [text.slice(0, start), text.slice(start, end), text.slice(end)]
}

type Span = { start: number; end: number }
type CiteRef = { start?: number | null; end?: number | null; text: string; part?: string | null }

/** A range citation (no chunk id) carries its own span; a missing bound marks nothing. */
export function rangeSpan(ref: { start?: number | null; end?: number | null }): Span {
  return { start: ref.start ?? 0, end: ref.end ?? 0 }
}

/** A meeting citation over notes or enhanced notes marks its span in that body. A transcript (not in the meeting
 * payload) or an emptied body falls back to the citation's own preview text, marked whole. */
export function meetingView(ref: CiteRef, meeting: { notes?: string | null; enhanced?: string | null }): Span & { text: string } {
  const body = ref.part === 'notes' || ref.part === 'enhanced' ? meeting[ref.part] : ''
  return body ? { text: body, ...rangeSpan(ref) } : { text: ref.text, start: 0, end: ref.text.length }
}
