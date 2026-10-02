/** Split text around [start, end) so the middle can be wrapped in <mark>. An invalid span (-1, empty or out of range) leaves the text whole. */
export function splitHighlight(text: string, start: number, end: number): [string, string, string] {
  if (start < 0 || end <= start || start >= text.length) return [text, '', '']
  return [text.slice(0, start), text.slice(start, end), text.slice(end)]
}
