/**
 * A small diff engine for the doc editor: line diff, then word-level refinement inside lines that
 * were merely reworded. No dependency — the shapes here are exactly what DiffView draws.
 */

export type Op = 'same' | 'add' | 'del'

export interface WordPart {
  text: string
  changed: boolean
}

export interface DiffLine {
  op: Op
  text: string
  /** 1-based line numbers in the old and new document; null where the line does not exist. */
  oldNo: number | null
  newNo: number | null
  /** Set on a del/add pair that is a rewording, so the row can highlight just the words that moved. */
  parts?: WordPart[]
}

export interface Hunk {
  oldStart: number
  newStart: number
  lines: DiffLine[]
}

export interface DiffStat {
  added: number
  removed: number
  /** del/add pairs counted once: how many lines were reworded rather than wholly new. */
  changed: number
}

/**
 * Longest common subsequence over lines, as a backtrace matrix.
 * O(n·m) memory, which is fine for prose: a 5k-line doc against itself is 25M cells only if it shares
 * nothing, and the common-prefix/suffix trim below removes that case in practice.
 */
function lcsDiff(a: string[], b: string[]): DiffLine[] {
  // Trim the matching head and tail first: most edits touch a small middle.
  let head = 0
  while (head < a.length && head < b.length && a[head] === b[head]) head++
  let tail = 0
  while (tail < a.length - head && tail < b.length - head && a[a.length - 1 - tail] === b[b.length - 1 - tail]) tail++

  const aMid = a.slice(head, a.length - tail)
  const bMid = b.slice(head, b.length - tail)

  const out: DiffLine[] = []
  for (let i = 0; i < head; i++) out.push({ op: 'same', text: a[i], oldNo: i + 1, newNo: i + 1 })

  const n = aMid.length
  const m = bMid.length
  if (n === 0 || m === 0) {
    // Pure insertion or pure deletion in the middle.
    for (let i = 0; i < n; i++) out.push({ op: 'del', text: aMid[i], oldNo: head + i + 1, newNo: null })
    for (let j = 0; j < m; j++) out.push({ op: 'add', text: bMid[j], oldNo: null, newNo: head + j + 1 })
  } else {
    const w = m + 1
    const dp = new Uint32Array((n + 1) * w)
    for (let i = n - 1; i >= 0; i--) {
      for (let j = m - 1; j >= 0; j--) {
        dp[i * w + j] = aMid[i] === bMid[j] ? dp[(i + 1) * w + j + 1] + 1 : Math.max(dp[(i + 1) * w + j], dp[i * w + j + 1])
      }
    }
    let i = 0
    let j = 0
    while (i < n && j < m) {
      if (aMid[i] === bMid[j]) {
        out.push({ op: 'same', text: aMid[i], oldNo: head + i + 1, newNo: head + j + 1 })
        i++
        j++
      } else if (dp[(i + 1) * w + j] >= dp[i * w + j + 1]) {
        out.push({ op: 'del', text: aMid[i], oldNo: head + i + 1, newNo: null })
        i++
      } else {
        out.push({ op: 'add', text: bMid[j], oldNo: null, newNo: head + j + 1 })
        j++
      }
    }
    while (i < n) out.push({ op: 'del', text: aMid[i], oldNo: head + i++ + 1, newNo: null })
    while (j < m) out.push({ op: 'add', text: bMid[j], oldNo: null, newNo: head + j++ + 1 })
  }

  for (let k = 0; k < tail; k++) {
    const oi = a.length - tail + k
    const ni = b.length - tail + k
    out.push({ op: 'same', text: a[oi], oldNo: oi + 1, newNo: ni + 1 })
  }
  return out
}

const WORD = /(\s+|[^\s\w]+|\w+)/g
const tokenize = (s: string): string[] => s.match(WORD) ?? []

/** Word-level diff of one reworded line, so the row can dim what stayed and mark what moved. */
export function wordDiff(before: string, after: string): { before: WordPart[]; after: WordPart[] } {
  const a = tokenize(before)
  const b = tokenize(after)
  const n = a.length
  const m = b.length
  const w = m + 1
  const dp = new Uint32Array((n + 1) * w)
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      dp[i * w + j] = a[i] === b[j] ? dp[(i + 1) * w + j + 1] + 1 : Math.max(dp[(i + 1) * w + j], dp[i * w + j + 1])
    }
  }
  const bp: WordPart[] = []
  const ap: WordPart[] = []
  const push = (arr: WordPart[], text: string, changed: boolean): void => {
    const last = arr[arr.length - 1]
    if (last && last.changed === changed) last.text += text
    else arr.push({ text, changed })
  }
  let i = 0
  let j = 0
  while (i < n && j < m) {
    if (a[i] === b[j]) {
      push(bp, a[i], false)
      push(ap, b[j], false)
      i++
      j++
    } else if (dp[(i + 1) * w + j] >= dp[i * w + j + 1]) {
      push(bp, a[i++], true)
    } else {
      push(ap, b[j++], true)
    }
  }
  while (i < n) push(bp, a[i++], true)
  while (j < m) push(ap, b[j++], true)
  return { before: bp, after: ap }
}

/** How similar two lines are, 0..1, by shared word tokens — decides whether to refine a del/add pair. */
function similarity(a: string, b: string): number {
  if (!a.trim() || !b.trim()) return 0
  const at = tokenize(a).filter((t) => t.trim())
  const bt = tokenize(b).filter((t) => t.trim())
  if (!at.length || !bt.length) return 0
  const pool = new Map<string, number>()
  for (const t of at) pool.set(t, (pool.get(t) ?? 0) + 1)
  let shared = 0
  for (const t of bt) {
    const c = pool.get(t)
    if (c) {
      shared++
      pool.set(t, c - 1)
    }
  }
  return (2 * shared) / (at.length + bt.length)
}

const REWORD_THRESHOLD = 0.4

/**
 * Line diff with intra-line refinement. A run of deletions immediately followed by additions is
 * paired up where the lines are similar enough to be a rewording rather than a replacement.
 */
export function diffLines(before: string, after: string): DiffLine[] {
  // An empty document is no lines, not one blank one: writing into an empty doc should read as pure
  // insertion rather than as replacing a phantom blank line. ('a\n' really does end in a blank line.)
  const split = (s: string): string[] => (s === '' ? [] : s.split('\n'))
  const lines = lcsDiff(split(before), split(after))
  for (let i = 0; i < lines.length; i++) {
    if (lines[i].op !== 'del') continue
    let dEnd = i
    while (dEnd < lines.length && lines[dEnd].op === 'del') dEnd++
    let aEnd = dEnd
    while (aEnd < lines.length && lines[aEnd].op === 'add') aEnd++
    // Pair the runs positionally; only refine pairs that really look like the same sentence.
    for (let k = 0; i + k < dEnd && dEnd + k < aEnd; k++) {
      const del = lines[i + k]
      const add = lines[dEnd + k]
      if (similarity(del.text, add.text) >= REWORD_THRESHOLD) {
        const { before: bp, after: ap } = wordDiff(del.text, add.text)
        del.parts = bp
        add.parts = ap
      }
    }
    i = aEnd - 1
  }
  return lines
}

export function diffStat(lines: DiffLine[]): DiffStat {
  let added = 0
  let removed = 0
  let changed = 0
  for (const l of lines) {
    if (l.op === 'add') added++
    else if (l.op === 'del') removed++
    if (l.parts) changed++
  }
  return { added, removed, changed: Math.floor(changed / 2) }
}

/** Group a line diff into hunks with `context` unchanged lines around each change. */
export function hunks(lines: DiffLine[], context = 3): Hunk[] {
  const keep = new Array<boolean>(lines.length).fill(false)
  let any = false
  for (let i = 0; i < lines.length; i++) {
    if (lines[i].op === 'same') continue
    any = true
    for (let j = Math.max(0, i - context); j <= Math.min(lines.length - 1, i + context); j++) keep[j] = true
  }
  if (!any) return []
  const out: Hunk[] = []
  let cur: Hunk | null = null
  for (let i = 0; i < lines.length; i++) {
    if (!keep[i]) {
      cur = null
      continue
    }
    if (!cur) {
      cur = { oldStart: lines[i].oldNo ?? 0, newStart: lines[i].newNo ?? 0, lines: [] }
      out.push(cur)
    }
    cur.lines.push(lines[i])
  }
  return out
}

/** A unified patch, for copying a revision out of the app. */
export function toUnified(before: string, after: string, context = 3): string {
  const hs = hunks(diffLines(before, after), context)
  return hs
    .map((h) => {
      const oldCount = h.lines.filter((l) => l.op !== 'add').length
      const newCount = h.lines.filter((l) => l.op !== 'del').length
      const head = `@@ -${h.oldStart},${oldCount} +${h.newStart},${newCount} @@`
      const body = h.lines.map((l) => (l.op === 'add' ? '+' : l.op === 'del' ? '-' : ' ') + l.text).join('\n')
      return `${head}\n${body}`
    })
    .join('\n')
}
