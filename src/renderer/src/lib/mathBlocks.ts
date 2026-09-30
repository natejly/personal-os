/**
 * Make single-line display maths actually display.
 *
 * remark-math only reads `$$` as a *block* when the delimiters are alone on their lines. Written on
 * one line, `$$x^2$$` falls back to inline maths and comes out small and mid-paragraph; written as
 * `$$x^2` with the closing `$$` below, the first line is parsed as the block's "meta" and the formula
 * disappears. Both are what people and models write all the time, so the source is normalised to the
 * form remark-math wants before it is parsed:
 *
 *     $$x^2$$        ->  $$\nx^2\n$$
 *     $$x^2          ->  $$\nx^2
 *     ... x^2$$      ->  ... x^2\n$$     (only while inside a block)
 *
 * Fenced code is left exactly as written — a `$$` in a shell snippet is not a formula.
 */

const FENCE = /^\s*(```+|~~~+)/

export function normalizeMathBlocks(src: string): string {
  if (!src.includes('$$')) return src
  const out: string[] = []
  let fence: string | null = null
  let inMath = false

  for (const line of src.split('\n')) {
    // Inside a code fence nothing is maths.
    if (fence !== null) {
      out.push(line)
      if (line.trimStart().startsWith(fence)) fence = null
      continue
    }
    const f = FENCE.exec(line)
    if (f) {
      fence = f[1].slice(0, 3)
      out.push(line)
      continue
    }

    const m = /^(\s*)\$\$(.*)$/.exec(line)
    if (m && !inMath) {
      const [, indent, rest] = m
      if (rest.trim() === '') {
        // A bare `$$` opener, already the shape remark-math wants.
        inMath = true
        out.push(line)
      } else if (rest.trimEnd().endsWith('$$') && rest.trimEnd().length > 2) {
        // `$$formula$$` on one line -> a real block.
        out.push(`${indent}$$`, rest.trimEnd().slice(0, -2).trim(), `${indent}$$`)
      } else {
        // `$$formula` with the closing fence on a later line.
        inMath = true
        out.push(`${indent}$$`, rest.trim())
      }
      continue
    }

    if (inMath) {
      const trimmed = line.trimEnd()
      if (trimmed.trim() === '$$') {
        inMath = false
        out.push(line)
      } else if (trimmed.endsWith('$$')) {
        // A closing fence stuck to the end of the formula.
        inMath = false
        out.push(trimmed.slice(0, -2).trimEnd(), '$$')
      } else {
        out.push(line)
      }
      continue
    }

    out.push(line)
  }

  // An unterminated block is left unterminated: the user is probably still typing it, and inventing a
  // closing fence would make the rest of the document render as maths.
  return out.join('\n')
}
