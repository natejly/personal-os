/** Pure markdown formatting transforms shared by the keyboard handlers and the format bar. */

export interface Edit { text: string; start: number; end: number }

/** Wrap or unwrap [s, e) with a delimiter, keeping the selection on the text. */
export function wrapToggle(value: string, s: number, e: number, left: string, right = left): Edit {
  const sel = value.slice(s, e)
  if (value.slice(s - left.length, s) === left && value.slice(e, e + right.length) === right) {
    return { text: value.slice(0, s - left.length) + sel + value.slice(e + right.length), start: s - left.length, end: e - left.length }
  }
  if (sel.startsWith(left) && sel.endsWith(right) && sel.length >= left.length + right.length) {
    const inner = sel.slice(left.length, sel.length - right.length)
    return { text: value.slice(0, s) + inner + value.slice(e), start: s, end: s + inner.length }
  }
  return { text: value.slice(0, s) + left + sel + right + value.slice(e), start: s + left.length, end: e + left.length }
}

// One family per prefix kind, so switching H1 to H2 or bullet to task replaces rather than stacks.
const FAMILY: Array<[RegExp, RegExp]> = [
  [/^#/, /^#{1,6}[ \t]+/],
  [/^[-*+]/, /^[-*+][ \t]+(\[[ xX]\][ \t]+)?/],
  [/^>/, /^>[ \t]?/]
]

/**
 * Toggle a line prefix ('# ', '- ', '- [ ] ', '> ') on every line the selection touches. When all
 * non-empty lines already carry exactly the prefix it is removed; otherwise any marker of the same
 * family is replaced by it.
 */
export function toggleLinePrefix(value: string, s: number, e: number, prefix: string): Edit {
  const from = value.lastIndexOf('\n', s - 1) + 1
  const toEnd = value.indexOf('\n', e)
  const to = toEnd === -1 ? value.length : toEnd
  const lines = value.slice(from, to).split('\n')
  const marker = (FAMILY.find(([k]) => k.test(prefix)) ?? FAMILY[0])[1]
  const body = lines.filter((l) => l.trim() !== '')
  const off = body.length > 0 && body.every((l) => marker.exec(l)?.[0] === prefix)
  let firstDelta = 0
  const next = lines.map((l, i) => {
    if (!off && l.trim() === '' && lines.length > 1) return l
    const old = marker.exec(l)?.[0] ?? ''
    const out = off ? l.slice(prefix.length) : prefix + l.slice(old.length)
    if (i === 0) firstDelta = out.length - l.length
    return out
  }).join('\n')
  const total = next.length - (to - from)
  return { text: value.slice(0, from) + next + value.slice(to), start: Math.max(from, s + firstDelta), end: Math.max(from, e + total) }
}
