export interface OutlineItem {
  /** 1-based source line. */
  line: number
  /** 1..6, as written. */
  level: number
  /** Nesting depth for display: a doc that jumps from # to ### does not indent twice. */
  depth: number
  text: string
}

/** ATX headings, skipping fenced code and `$$` blocks (the same state the editor's highlighter keeps). */
export function outline(source: string): OutlineItem[] {
  const out: OutlineItem[] = []
  const stack: number[] = []
  let fence: string | null = null
  let math = false
  source.split('\n').forEach((line, i) => {
    if (fence !== null) {
      if (line.trimStart().startsWith(fence)) fence = null
      return
    }
    const open = /^\s*(```+|~~~+)/.exec(line)
    if (open) { fence = open[1].slice(0, 3); return }
    if (math) { if (line.includes('$$')) math = false; return }
    if (/^\s*\$\$\s*$/.test(line)) { math = true; return }
    const m = /^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$/.exec(line)
    if (!m || !m[2].trim()) return
    const level = m[1].length
    while (stack.length && stack[stack.length - 1] >= level) stack.pop()
    out.push({ line: i + 1, level, depth: stack.length, text: m[2].trim() })
    stack.push(level)
  })
  return out
}

/** Index of the heading whose section holds `line`, or -1 before the first heading. */
export function activeHeading(items: OutlineItem[], line: number): number {
  let idx = -1
  for (let i = 0; i < items.length; i++) if (items[i].line <= line) idx = i
  return idx
}
