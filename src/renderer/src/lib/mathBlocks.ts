/**
 * Make maths and prices render the way they were meant, before remark-math sees the source.
 *
 * Three jobs, all render-only (stored text and Copy are never rewritten):
 *
 * 1. Display blocks. remark-math only reads `$$` as a *block* when the delimiters are alone on their
 *    lines. Written on one line, `$$x^2$$` falls back to inline maths; written as `$$x^2` with the
 *    closing `$$` below, the formula disappears into the block's "meta". So:
 *
 *        $$x^2$$        ->  $$\nx^2\n$$
 *        $$x^2          ->  $$\nx^2
 *        ... x^2$$      ->  ... x^2\n$$     (only while inside a block)
 *
 * 2. Bracket delimiters. Many models write `\(x\)` and `\[ ... \]`, which markdown reads as escaped
 *    punctuation. They become `$x$` and `$$` blocks.
 * 3. Currency. The parser closes an inline `$` at the next raw single `$`, so "$5 and $10" would be a
 *    formula. A `$` followed by a digit whose pair looks like another price is escaped to `\$`.
 *
 * Fenced code and inline code spans are left exactly as written.
 */

const FENCE = /^\s*(```+|~~~+)/
// A bracket pair only counts as maths when its inner text looks like a formula; "\[1\] Smith" is a citation.
const MATH_SIGNAL = /[\\^_={}+*/<>]/

const isDigit = (c: string | undefined): boolean => c !== undefined && c >= '0' && c <= '9'
const isSpace = (c: string | undefined): boolean => c !== undefined && /\s/.test(c)

/** The length of the run of `ch` starting at `i`. */
function runLen(s: string, i: number, ch: string): number {
  let n = 0
  while (s[i + n] === ch) n++
  return n
}

/** One left-to-right scan over a paragraph that mirrors how the parser pairs `$`. */
function fixInline(s: string): string {
  let out = ''
  let i = 0
  while (i < s.length) {
    const c = s[i]
    if (c === '\\') {
      const n = s[i + 1]
      if (n === '(') {
        const end = s.indexOf('\\)', i + 2)
        const inner = end > 0 ? s.slice(i + 2, end) : ''
        if (end > 0 && inner.trim() && !inner.includes('$')) {
          out += `$${inner.trim()}$`
          i = end + 2
          continue
        }
      } else if (n === '[' && s[i + 2] !== '(') {
        const end = s.indexOf('\\]', i + 2)
        const inner = end > 0 ? s.slice(i + 2, end) : ''
        if (end > 0 && s[end + 2] !== '(' && inner.trim() && MATH_SIGNAL.test(inner)) {
          out += `$$${inner.trim()}$$`
          i = end + 2
          continue
        }
      }
      out += n === undefined ? c : c + n
      i += n === undefined ? 1 : 2
      continue
    }
    if (c === '`') {
      const n = runLen(s, i, '`')
      let j = i + n
      let close = -1
      while (j < s.length) {
        if (s[j] !== '`') { j++; continue }
        const m = runLen(s, j, '`')
        if (m === n) { close = j; break }
        j += m
      }
      if (close < 0) { out += s.slice(i, i + n); i += n; continue }
      out += s.slice(i, close + n)
      i = close + n
      continue
    }
    if (c === '$') {
      const n = runLen(s, i, '$')
      if (n >= 2) {
        const end = s.indexOf('$$', i + n)
        const stop = end < 0 ? i + n : end + 2
        out += s.slice(i, stop)
        i = stop
        continue
      }
      // The candidate closer is the next single `$` run, found on raw characters like the parser does.
      let j = i + 1
      let close = -1
      while (j < s.length) {
        if (s[j] !== '$') { j++; continue }
        const m = runLen(s, j, '$')
        if (m === 1) { close = j; break }
        j += m
      }
      if (close < 0) { out += c; i++; continue }
      if (isDigit(s[i + 1]) && (isSpace(s[close - 1]) || isDigit(s[close + 1]))) {
        // A price: keep it literal and let the would-be closer be read as an opener of its own.
        out += '\\$'
        i++
        continue
      }
      out += s.slice(i, close + 1)
      i = close + 1
      continue
    }
    out += c
    i++
  }
  return out
}

export function normalizeMathBlocks(src: string): string {
  if (!src.includes('$') && !src.includes('\\(') && !src.includes('\\[')) return src
  const out: string[] = []
  let fence: string | null = null
  let math: '$$' | '\\]' | null = null
  let prose: string[] = []
  const flush = (): void => {
    if (prose.length) out.push(fixInline(prose.join('\n')))
    prose = []
  }

  for (const line of src.split('\n')) {
    // Inside a code fence nothing is maths.
    if (fence !== null) {
      out.push(line)
      if (line.trimStart().startsWith(fence)) fence = null
      continue
    }
    if (math === null) {
      const f = FENCE.exec(line)
      if (f) {
        flush()
        fence = f[1].slice(0, 3)
        out.push(line)
        continue
      }
      if (line.trim() === '') {
        flush()
        out.push(line)
        continue
      }
      const m = /^(\s*)\$\$(.*)$/.exec(line)
      if (m) {
        flush()
        const [, indent, rest] = m
        if (rest.trim() === '') {
          // A bare `$$` opener, already the shape remark-math wants.
          math = '$$'
          out.push(line)
        } else if (rest.trimEnd().endsWith('$$') && rest.trimEnd().length > 2) {
          // `$$formula$$` on one line -> a real block.
          out.push(`${indent}$$`, rest.trimEnd().slice(0, -2).trim(), `${indent}$$`)
        } else {
          // `$$formula` with the closing fence on a later line.
          math = '$$'
          out.push(`${indent}$$`, rest.trim())
        }
        continue
      }
      const b = /^(\s*)\\\[(.*)$/.exec(line)
      if (b) {
        const [, indent, rest] = b
        const t = rest.trim()
        if (t === '') {
          flush()
          math = '\\]'
          out.push(`${indent}$$`)
          continue
        }
        if (t.endsWith('\\]') && t.indexOf('\\]') === t.length - 2 && MATH_SIGNAL.test(t.slice(0, -2))) {
          flush()
          out.push(`${indent}$$`, t.slice(0, -2).trim(), `${indent}$$`)
          continue
        }
      }
      prose.push(line)
      continue
    }

    const trimmed = line.trimEnd()
    if (math === '$$') {
      if (trimmed.trim() === '$$') {
        math = null
        out.push(line)
      } else if (trimmed.endsWith('$$')) {
        // A closing fence stuck to the end of the formula.
        math = null
        out.push(trimmed.slice(0, -2).trimEnd(), '$$')
      } else {
        out.push(line)
      }
    } else if (trimmed.trim() === '\\]') {
      math = null
      out.push('$$')
    } else if (trimmed.endsWith('\\]')) {
      math = null
      out.push(trimmed.slice(0, -2).trimEnd(), '$$')
    } else {
      out.push(line)
    }
  }
  flush()

  // An unterminated block is left unterminated: the user is probably still typing it, and inventing a
  // closing fence would make the rest of the document render as maths.
  return out.join('\n')
}
