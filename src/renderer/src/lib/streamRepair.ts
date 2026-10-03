/**
 * Close half-written markdown before a *streaming* message is parsed, so a marker whose partner has not
 * arrived yet never flashes as literal text and then jumps. Render-only: a finished message is never
 * repaired and stored text is never rewritten.
 *
 * Only the tail is touched. Whole-source rules: a source that ends inside an open code fence or `$$`
 * block is returned as is. Trailing holds: a lone `-` / `=` line under text (it would turn that text
 * into a heading), a one or two character fence start, and a pipe-table header whose delimiter row has
 * not finished. The last remaining line then gets its inline openers closed: backticks, `**`, `*`, `~~`,
 * and a `[text](partial` link (reduced to its text). Single `$` and `_` are left alone on purpose: closing
 * `$` turns prices into formulas, and `_` is common inside identifiers.
 */

const FENCE = /^\s*(```+|~~~+)/
const SETEXT = /^ {0,3}(-+|=+)[ \t]*$/
const DELIM_PREFIX = /^ {0,3}\|?[ \t:|-]*$/
const BARE_FENCE = /^\s*(`{1,2}|~{1,2})$/

const isPipe = (l: string | undefined): boolean => l !== undefined && l.trimStart().startsWith('|')
const isBlank = (l: string | undefined): boolean => l === undefined || l.trim() === ''

/** Whether the text ends inside a code fence or a `$$` block. */
function endsOpen(lines: string[]): boolean {
  let fence: { ch: string; len: number } | null = null
  let math = false
  for (const l of lines) {
    if (fence) {
      const m = /^\s*(`+|~+)\s*$/.exec(l)
      if (m && m[1][0] === fence.ch && m[1].length >= fence.len) fence = null
      continue
    }
    const f = FENCE.exec(l)
    if (f) { fence = { ch: f[1][0], len: f[1].length }; continue }
    if (((l.match(/\$\$/g) ?? []).length & 1) === 1) math = !math
  }
  return fence !== null || math
}

const spaceAt = (s: string, i: number): boolean => i < 0 || i >= s.length || /\s/.test(s[i])

/** Close the open inline markers of one line. */
function repairLine(line: string): string {
  // A link whose destination is still arriving shows as its text; a half image is dropped.
  let s = line.replace(/!\[[^\]]*\]\([^)]*$/, '').replace(/\[([^[\]]*)\]\([^)]*$/, '$1')
  const stack: string[] = []
  let i = 0
  let tail = ''
  while (i < s.length) {
    const c = s[i]
    if (c === '\\') { i += 2; continue }
    if (c === '`') {
      let n = 1
      while (s[i + n] === '`') n++
      const run = s.slice(i, i + n)
      const close = s.indexOf(run, i + n)
      // a longer run is not the closer for a shorter opener
      let at = close
      while (at !== -1 && (s[at + n] === '`' || s[at - 1] === '`')) at = s.indexOf(run, at + 1)
      if (at !== -1) { i = at + n; continue }
      if (s.slice(i + n).trim() === '') s = s.slice(0, i)
      else tail = run
      i = s.length
      break
    }
    if (c === '*' || c === '~') {
      let n = 1
      while (s[i + n] === c) n++
      if (c === '~' && n !== 2) { i += n; continue }
      const canOpen = !spaceAt(s, i + n)
      const canClose = !spaceAt(s, i - 1)
      let rem = n
      if (canClose) {
        while (rem > 0) {
          const top = stack[stack.length - 1]
          if (c === '~' && top === '~~') { stack.pop(); rem -= 2 }
          else if (c === '*' && top === '**' && rem >= 2) { stack.pop(); rem -= 2 }
          else if (c === '*' && top === '*') { stack.pop(); rem -= 1 }
          else break
        }
      }
      if (rem > 0 && canOpen) {
        if (c === '~') stack.push('~~')
        else { while (rem >= 2) { stack.push('**'); rem -= 2 } if (rem === 1) stack.push('*') }
        rem = 0
      }
      // a trailing double marker with nothing after it is only the start of one
      if (rem >= 2 && i + n >= s.length) { s = s.slice(0, i) + s.slice(i + n); break }
      i += n
      continue
    }
    i++
  }
  if (!stack.length && !tail) return s
  const trimmed = s.replace(/\s+$/, '')
  const space = s.slice(trimmed.length)
  return trimmed + tail + stack.reverse().join('') + space
}

export function repairStreamingMarkdown(src: string): string {
  if (!src) return src
  let lines = src.split('\n')
  if (endsOpen(lines)) return src

  const last = lines[lines.length - 1]
  const drop = (n: number): void => { lines = lines.slice(0, lines.length - n) }
  const hasPrev = (idx: number): boolean => idx > 0
  // The index of a pipe line that starts a table (the line above it is not a pipe line).
  const header = (idx: number): boolean => idx >= 0 && isPipe(lines[idx]) && !isPipe(lines[idx - 1])

  if (last === '' && lines.length > 1 && header(lines.length - 2)) {
    drop(2) // header complete, delimiter row not started
    return lines.join('\n')
  }
  if (last !== '') {
    const idx = lines.length - 1
    if (isPipe(last) && !isPipe(lines[idx - 1])) { drop(1); return lines.join('\n') }
    if (DELIM_PREFIX.test(last) && header(idx - 1)) { drop(2); return lines.join('\n') }
    if (SETEXT.test(last) && hasPrev(idx) && !isBlank(lines[idx - 1])) { drop(1); return lines.join('\n') }
    if (BARE_FENCE.test(last)) { drop(1); return lines.join('\n') }
  }
  return lines.slice(0, -1).concat(repairLine(last)).join('\n')
}
