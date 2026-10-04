/** Split text around [start, end) so the middle can be wrapped in <mark>. An invalid span (-1, empty or out of range) leaves the text whole. */
export function splitHighlight(text: string, start: number, end: number): [string, string, string] {
  if (start < 0 || end <= start || start >= text.length) return [text, '', '']
  return [text.slice(0, start), text.slice(start, end), text.slice(end)]
}

/** The 1-based line an offset falls on, for opening a cited span in the editor. */
export function lineAt(text: string, offset: number): number {
  return text.slice(0, Math.max(0, offset)).split('\n').length
}
