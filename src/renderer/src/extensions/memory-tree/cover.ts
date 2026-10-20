/** Pure helpers for the History digest: the cover's wire shapes, labels, ordering and the expand/collapse state. */

export interface TreeNode {
  /** `lo-hi` over leaf sequence numbers, inclusive low and exclusive high; a leaf is `n-n+1`. */
  range: string
  level: number
  kind: 'leaf' | 'node'
  text: string
  /** Epoch seconds: the leaf's date, or the newest in the block. */
  ts: number | null
  pending: boolean
  tainted: boolean
}

export interface Cover {
  scope: string
  budget_tokens: number
  used_tokens: number
  counts: { leaves: number; nodes: number; pending: number }
  nodes: TreeNode[]
}

export interface RebuildResult { scope: string; leaves: number; nodes: number }

/** The digest settings, as stored (`ext.memory-tree.<name>`) and their accepted ranges. */
export const SETTING_PREFIX = 'ext.memory-tree.'
export const DIGEST_TOKENS = { min: 300, max: 6000, fallback: 1500 } as const
export const MERGES_PER_TURN = { min: 0, max: 10, fallback: 2 } as const

export const clampInt = (v: unknown, min: number, max: number, fallback: number): number => {
  const n = Math.round(Number(v))
  return Number.isFinite(n) && v !== '' && v !== null ? Math.min(max, Math.max(min, n)) : fallback
}

/** `lo-hi` as numbers, or null when malformed. */
export function parseRange(range: string): [number, number] | null {
  const m = /^(\d+)-(\d+)$/.exec(range)
  if (!m) return null
  const [lo, hi] = [Number(m[1]), Number(m[2])]
  return hi > lo ? [lo, hi] : null
}

/** How many leaves a range covers. */
export const rangeSize = (range: string): number => {
  const r = parseRange(range)
  return r ? r[1] - r[0] : 0
}

/** "#4" for a single entry, "#4 to #9" for a block (the exclusive high end is shown as the last entry in it). */
export function formatRange(range: string): string {
  const r = parseRange(range)
  if (!r) return range
  const [lo, hi] = r
  return hi - lo === 1 ? `#${lo + 1}` : `#${lo + 1} to #${hi}`
}

const DAY = 86400
const dayStart = (ts: number): number => Math.floor(ts / DAY)
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

/** "Oct 19" (with the year when it is not `now`'s year), or an em dash for no date. */
export function dateLabel(ts: number | null, now: number = Date.now() / 1000): string {
  if (ts === null) return '—'
  const d = new Date(ts * 1000)
  const base = `${MONTHS[d.getUTCMonth()]} ${d.getUTCDate()}`
  return d.getUTCFullYear() === new Date(now * 1000).getUTCFullYear() ? base : `${base}, ${d.getUTCFullYear()}`
}

/** What a row shows besides its text: the date of a leaf, the newest date and size of a block. */
export function spanLabel(node: TreeNode, now: number = Date.now() / 1000): string {
  const when = dateLabel(node.ts, now)
  const n = rangeSize(node.range)
  if (node.kind === 'leaf' || n <= 1) return when
  return `${n} entries, to ${when}`
}

/** Oldest first: by the low end of the range, then the larger block first. */
export function sortCover(nodes: readonly TreeNode[]): TreeNode[] {
  return [...nodes].sort((a, b) => {
    const [ra, rb] = [parseRange(a.range), parseRange(b.range)]
    if (!ra || !rb) return ra ? -1 : rb ? 1 : 0
    return ra[0] - rb[0] || rb[1] - ra[1]
  })
}

/** A block can be opened into its two halves. */
export const canZoom = (node: TreeNode): boolean => node.level > 0 || node.kind === 'node'

/** Share of the budget in use, 0 to 100, whole percent; 0 for no budget. */
export function tokenUse(used: number, budget: number): number {
  if (!(budget > 0) || !(used > 0)) return 0
  return Math.min(100, Math.round((used / budget) * 100))
}

/** "1.2k of 1.5k tokens (80%)" */
export function tokenLabel(used: number, budget: number): string {
  const k = (n: number): string => (n >= 1000 ? `${Math.round(n / 100) / 10}k` : String(n))
  return `${k(used)} of ${k(budget)} tokens (${tokenUse(used, budget)}%)`
}

export const isEmpty = (c: Cover | null | undefined): boolean => !c || c.nodes.length === 0

/** The scope the tab shows: the active project's digest, or the personal one. */
export const scopeFor = (projectId: string | null | undefined): string => (projectId ? `project:${projectId}` : 'personal')

/** Which blocks are open, keyed by range. */
export type Open = Readonly<Record<string, true>>
export type OpenAction = { type: 'toggle'; range: string } | { type: 'collapse'; range: string } | { type: 'reset' }

/** Collapsing a block closes whatever is open under it too. */
export function openReducer(open: Open, action: OpenAction): Open {
  switch (action.type) {
    case 'reset': return {}
    case 'toggle': return open[action.range] ? openReducer(open, { type: 'collapse', range: action.range }) : { ...open, [action.range]: true }
    case 'collapse': {
      const r = parseRange(action.range)
      const next: Record<string, true> = {}
      for (const k of Object.keys(open)) {
        const p = parseRange(k)
        const inside = !!r && !!p && p[0] >= r[0] && p[1] <= r[1]
        if (!inside) next[k] = true
      }
      return next
    }
  }
}

export const zoomKey = (scope: string, range: string): string => `${scope}|${range}`

/** The rows to draw: each cover node followed, when open and zoomed, by its children, indented one step per depth. */
export function visibleRows(nodes: readonly TreeNode[], open: Open, zoomed: Readonly<Record<string, TreeNode[]>>, scope: string): { node: TreeNode; depth: number }[] {
  const out: { node: TreeNode; depth: number }[] = []
  const walk = (list: readonly TreeNode[], depth: number): void => {
    for (const node of sortCover(list)) {
      out.push({ node, depth })
      const kids = open[node.range] ? zoomed[zoomKey(scope, node.range)] : undefined
      if (kids) walk(kids, depth + 1)
    }
  }
  walk(nodes, 0)
  return out
}
