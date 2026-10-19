/** Pure helpers for the run_python card: shape the inner-call trace a code run reports. */
import type { Fields } from './toolResult'

export type TraceMark = 'ok' | 'failed' | 'approved' | 'declined'

export interface TraceRow {
  name: string
  mark: TraceMark
  label: string
  ms: number | null
}

/** Rows shown before the "show all" toggle. */
export const TRACE_VISIBLE = 8

const LABELS: Record<TraceMark, string> = { ok: 'ok', failed: 'failed', approved: 'approved', declined: 'declined' }

/** Read `calls` from a run_python result. Malformed entries are skipped; no `calls` gives []. */
export function traceRows(data: Fields | null | undefined): TraceRow[] {
  const raw = data?.calls
  if (!Array.isArray(raw)) return []
  const rows: TraceRow[] = []
  for (const c of raw) {
    if (!c || typeof c !== 'object') continue
    const o = c as Record<string, unknown>
    if (typeof o.name !== 'string' || !o.name) continue
    const ok = o.ok === true
    const mark: TraceMark = o.approved === false ? 'declined' : !ok ? 'failed' : o.approved === true ? 'approved' : 'ok'
    rows.push({ name: o.name, mark, label: LABELS[mark], ms: typeof o.ms === 'number' && isFinite(o.ms) ? o.ms : null })
  }
  return rows
}

/** "850 ms" / "3.4 s"; empty for an unknown duration. */
export function fmtCallMs(ms: number | null): string {
  if (ms === null) return ''
  return ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(1)} s`
}

/** The rows to render: all of them when expanded or few, else the first TRACE_VISIBLE and the count hidden. */
export function visibleTrace(rows: TraceRow[], expanded: boolean, limit = TRACE_VISIBLE): { shown: TraceRow[]; hidden: number } {
  if (expanded || rows.length <= limit) return { shown: rows, hidden: 0 }
  return { shown: rows.slice(0, limit), hidden: rows.length - limit }
}

/** The `tools` argument as a short line: a name list, or "every offered tool". Null when unset. */
export function toolsArgLabel(tools: unknown): string | null {
  if (tools === 'offered') return 'every offered tool'
  if (Array.isArray(tools)) {
    const names = tools.filter((t): t is string => typeof t === 'string' && !!t)
    return names.length ? names.join(', ') : null
  }
  return null
}
