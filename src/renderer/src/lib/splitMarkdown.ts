/**
 * Cut markdown into top-level blocks so a streaming reply re-parses only its tail.
 *
 * A cut is made only where re-parsing the pieces separately gives the same result as parsing the whole:
 * before a column-0 line that follows a blank line, outside any code fence or `$$` block, and that is
 * neither a list item nor a quote (those may continue across blank lines). Sources whose meaning can
 * reach across blocks (link-reference and footnote definitions, raw HTML that may span blank lines)
 * are returned whole. The pieces always concatenate back to the source. Run it on the output of
 * `normalizeMathBlocks`.
 */

const FENCE = /^ {0,3}(`{3,}|~{3,})/
const LIST_OR_QUOTE = /^(?:[-*+](?:\s|$)|\d{1,9}[.)](?:\s|$)|>)/
const REFERENCE = /^ {0,3}\[[^\]]+\]:/m
const RAW_HTML = /<(?:pre|script|style|textarea)\b|<!--/i

export function splitMarkdown(src: string): string[] {
  if (REFERENCE.test(src) || RAW_HTML.test(src)) return [src]
  const lines = src.split('\n')
  const out: string[] = []
  let start = 0
  let fence: { ch: string; len: number } | null = null
  let math = false
  let blankBefore = false

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i]
    const outside = fence === null && !math
    if (outside && blankBefore && i > start && line !== '' && !/^\s/.test(line) && !LIST_OR_QUOTE.test(line)) {
      out.push(lines.slice(start, i).join('\n') + '\n')
      start = i
    }
    if (fence) {
      const m = FENCE.exec(line)
      if (m && m[1][0] === fence.ch && m[1].length >= fence.len && line.trim() === m[1]) fence = null
    } else if (math) {
      if (line.trim() === '$$') math = false
    } else {
      const m = FENCE.exec(line)
      if (m) fence = { ch: m[1][0], len: m[1].length }
      else if (line.trim() === '$$') math = true
    }
    blankBefore = line.trim() === ''
  }
  out.push(lines.slice(start).join('\n'))
  return out
}

/** 1-based line (within the concatenated source) on which block `idx` starts. */
export function blockStartLine(blocks: readonly string[], idx: number): number {
  let line = 1
  for (let k = 0; k < idx; k++) line += (blocks[k].match(/\n/g) ?? []).length
  return line
}
