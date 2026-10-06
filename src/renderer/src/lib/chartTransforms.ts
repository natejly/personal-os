/**
 * Declarative row transforms for chat charts: the same op shape as the backend's widget_spec.apply_transforms
 * (sort | limit | filter | group). Pure data in, data out; a spec only names ops, nothing is evaluated.
 */
type Row = Record<string, unknown>
export type Transform = Record<string, unknown>

export const MAX_ROWS = 500
const CMPS = ['==', '!=', '>', '<', '>=', '<=', 'contains']
const GROUP_AGGS = ['sum', 'mean', 'count', 'min', 'max']

const toNum = (v: unknown): number | null => {
  if (typeof v === 'number') return Number.isFinite(v) ? v : null
  if (typeof v === 'string') {
    const s = v.replace(/[\s$€£¥,]/g, '').replace(/%$/, '')
    if (s !== '' && !Number.isNaN(Number(s))) return Number(s)
  }
  return null
}

const sortKey = (v: unknown): [number, number | string] => {
  const n = toNum(v)
  return n !== null ? [0, n] : [1, v == null ? '' : String(v).toLowerCase()]
}

function cmp(a: unknown, op: string, b: unknown): boolean {
  if (op === 'contains') return a != null && String(a).toLowerCase().includes(String(b).toLowerCase())
  const na = toNum(a), nb = toNum(b)
  const [x, y] = na !== null && nb !== null ? [na, nb] : [String(a), String(b)]
  switch (op) {
    case '==': return x === y
    case '!=': return x !== y
    case '>': return x > y
    case '<': return x < y
    case '>=': return x >= y
    default: return x <= y
  }
}

function agg(op: string, vals: unknown[]): number | null {
  if (op === 'count') return vals.length
  const nums = vals.map(toNum).filter((n): n is number => n !== null)
  if (!nums.length) return null
  if (op === 'sum') return nums.reduce((a, b) => a + b, 0)
  if (op === 'mean') return nums.reduce((a, b) => a + b, 0) / nums.length
  return op === 'min' ? Math.min(...nums) : Math.max(...nums)
}

/** Throws on an unknown op, comparison or aggregate, so a bad spec shows as a chart error rather than silently ignoring it. */
export function applyTransforms(rows: Row[], transforms: unknown): Row[] {
  if (!Array.isArray(transforms)) return rows
  let out = [...rows]
  for (const t of transforms as Transform[]) {
    const op = t && typeof t === 'object' ? t.op : undefined
    if (op === 'sort') {
      const by = String(t.by ?? ''), dir = String(t.dir ?? 'asc').toLowerCase() === 'desc' ? -1 : 1
      out.sort((a, b) => {
        const [ka, va] = sortKey(a[by]), [kb, vb] = sortKey(b[by])
        return dir * (ka - kb || (va < vb ? -1 : va > vb ? 1 : 0))
      })
    } else if (op === 'limit') {
      out = out.slice(0, Math.max(0, Math.min(Number(t.n ?? MAX_ROWS), MAX_ROWS)))
    } else if (op === 'filter') {
      const c = String(t.cmp ?? '==')
      if (!CMPS.includes(c)) throw new Error(`unknown filter comparison '${c}'`)
      const f = String(t.field ?? '')
      out = out.filter((r) => cmp(r[f], c, t.value))
    } else if (op === 'group') {
      const by = String(t.by ?? '')
      const aggs = (t.agg && typeof t.agg === 'object' ? t.agg : {}) as Record<string, string>
      for (const a of Object.values(aggs)) if (!GROUP_AGGS.includes(a)) throw new Error(`unknown aggregate '${a}'`)
      const groups = new Map<string, Row[]>()
      for (const r of out) { const k = String(r[by]); groups.set(k, [...(groups.get(k) ?? []), r]) }
      out = [...groups].map(([k, rs]) => ({ [by]: k, ...Object.fromEntries(Object.entries(aggs).map(([f, a]) => [f, agg(a, rs.map((r) => r[f]))])) }))
    } else throw new Error(`unknown transform op '${String(op)}'`)
  }
  return out
}

const ISO = /^\d{4}-\d{2}-\d{2}([T ][\d:.]+(Z|[+-]\d{2}:?\d{2})?)?$/

/** True when every non-empty x value is an ISO date or datetime, so the axis can use short date ticks. */
export const isIsoDateColumn = (rows: Row[], x: string): boolean => {
  const vals = rows.map((r) => r[x]).filter((v) => v != null && v !== '')
  return vals.length > 1 && vals.every((v) => typeof v === 'string' && ISO.test(v) && !Number.isNaN(Date.parse(v)))
}

export const fmtIsoDate = (v: unknown): string => {
  const d = new Date(String(v))
  if (Number.isNaN(d.getTime())) return String(v)
  const dateOnly = /^\d{4}-\d{2}-\d{2}$/.test(String(v))
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', ...(dateOnly ? { timeZone: 'UTC' } : {}) })
}

/** Rows beyond this get a brush to zoom into a range. */
export const BRUSH_ABOVE = 60
