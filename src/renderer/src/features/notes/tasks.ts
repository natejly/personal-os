/** GFM task items: flipping one from the preview, and finding which source line a rendered checkbox belongs to. */

const TASK = /^(\s*(?:[-*+]|\d+[.)])\s+\[)([ xX])(\])(?=\s|$)/

/** Flip `[ ]` / `[x]` on a 1-based line. Null when that line is not a task, so a stale click does nothing. */
export function toggleTaskAt(source: string, line: number): string | null {
  const lines = source.split('\n')
  const cur = lines[line - 1]
  if (cur === undefined) return null
  const m = TASK.exec(cur)
  if (!m) return null
  lines[line - 1] = m[1] + (m[2] === ' ' ? 'x' : ' ') + cur.slice(m[0].length - 1)
  return lines.join('\n')
}

/** 1-based line numbers of every task-looking line, in order. */
export function taskLines(src: string): number[] {
  const out: number[] = []
  src.split('\n').forEach((l, i) => { if (TASK.test(l)) out.push(i + 1) })
  return out
}

/**
 * The preview parses `normalized` (maths blocks reshaped, so lines shift), but a toggle must edit
 * `source`. Normalising never touches a task line, so the n-th task line of one is the n-th of the
 * other: map by index. Null when the counts disagree, which leaves that checkbox read-only.
 */
export function taskLineMap(source: string, normalized: string): Map<number, number> {
  const a = taskLines(normalized)
  const b = taskLines(source)
  const map = new Map<number, number>()
  if (a.length === b.length) a.forEach((n, i) => map.set(n, b[i]))
  return map
}

export function mapTaskLine(source: string, normalized: string, normalizedLine: number): number | null {
  return taskLineMap(source, normalized).get(normalizedLine) ?? null
}
