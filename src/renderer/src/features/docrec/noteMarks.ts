/** Characters of a line kept as its mark; the full line never leaves the doc. */
export const MARK_CHARS = 40

/**
 * The lines of `next` that are new or changed against `prev`, trimmed to MARK_CHARS. Blank lines and
 * lines that merely moved are skipped (a line present anywhere in `prev` is unchanged).
 */
export function diffTouchedLines(prev: string, next: string): string[] {
  const before = new Set(prev.split('\n').map((l) => l.trim()))
  const out: string[] = []
  for (const raw of next.split('\n')) {
    const l = raw.trim()
    if (l && !before.has(l)) {
      const snip = l.slice(0, MARK_CHARS)
      if (!out.includes(snip)) out.push(snip)
    }
  }
  return out
}
