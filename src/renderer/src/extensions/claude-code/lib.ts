/** Pure helpers behind the Claude Code extension: its row types, ordering, event merging, fleet totals and status classes. No React, no network. */

export type CcStatus = 'queued' | 'starting' | 'running' | 'waiting_approval' | 'done' | 'failed' | 'stopped'

export interface CcSession {
  id: string
  name: string
  repo_path?: string | null
  worktree?: string | null
  branch?: string | null
  status: CcStatus
  model?: string | null
  cost_usd?: number | null
  num_turns?: number | null
  result_subtype?: string | null
  fleet_id?: string | null
  conversation_id?: string | null
  started_at?: number | null
  ended_at?: number | null
  pending_approval?: { tool: string; summary: string } | null
  last_text?: string | null
}

export type CcEventKind = 'init' | 'assistant' | 'tool' | 'tool_result' | 'result' | 'approval' | 'note' | 'error'

export interface CcEvent {
  seq: number
  session_id: string
  kind: CcEventKind
  text: string
  tool?: string | null
  data?: unknown
  at?: number | null
}

export interface CcFleet {
  id: string
  session_ids: string[]
  cost_usd: number
  by_model: Record<string, { cost_usd?: number; input_tokens?: number; output_tokens?: number } | number>
  counts: Record<string, number>
}

export const MAX_EVENTS = 500

/** Still occupying a slot or waiting on something: the view keeps these on top. */
export const isLive = (s: CcStatus): boolean => s === 'starting' || s === 'running' || s === 'waiting_approval'
export const isFinished = (s: CcStatus): boolean => s === 'done' || s === 'failed' || s === 'stopped'
/** Stop applies to anything not finished, including a session still queued. */
export const isStoppable = (s: CcStatus): boolean => !isFinished(s)
/** A follow-up can be sent to a session that is running or has finished; a queued one has nothing to answer. */
export const canSend = (s: CcStatus): boolean => s !== 'queued' && s !== 'starting'

export type Tone = 'ok' | 'warn' | 'bad' | 'run' | undefined
export const statusTone = (s: CcStatus): Tone =>
  s === 'running' || s === 'starting' ? 'run' : s === 'waiting_approval' ? 'warn' : s === 'failed' ? 'bad' : s === 'done' ? 'ok' : undefined
/** The `inbox-dot` class for a status. */
export const statusDot = (s: CcStatus): 'needs-you' | 'working' | 'done' | 'failed' | 'idle' =>
  s === 'waiting_approval' ? 'needs-you' : s === 'running' || s === 'starting' ? 'working' : s === 'failed' ? 'failed' : s === 'done' ? 'done' : 'idle'
export const statusLabel = (s: CcStatus): string => (s === 'waiting_approval' ? 'needs approval' : s)

const ORDER: Record<CcStatus, number> = { waiting_approval: 0, running: 1, starting: 2, queued: 3, failed: 4, done: 5, stopped: 6 }
const when = (s: CcSession): number => s.started_at ?? 0

/** Live sessions first (those needing approval on top), then the finished ones; newest first inside a group. */
export function sortSessions(list: CcSession[]): CcSession[] {
  return [...list].sort((a, b) => {
    const active = (x: CcSession): number => Number(isLive(x.status) || x.status === 'queued')
    if (active(a) !== active(b)) return active(b) - active(a)
    return (active(a) ? ORDER[a.status] - ORDER[b.status] : 0) || when(b) - when(a) || a.id.localeCompare(b.id)
  })
}

/** Add or replace a session by id; a no-op (same object back) when the row is deep-equal to the held one. */
export function upsertSession(map: Record<string, CcSession>, s: CcSession): Record<string, CcSession> {
  const held = map[s.id]
  if (held && JSON.stringify(held) === JSON.stringify(s)) return map
  return { ...map, [s.id]: s }
}

export const sessionMap = (list: CcSession[]): Record<string, CcSession> => Object.fromEntries(list.map((s) => [s.id, s]))

/** Merge events into a timeline: deduped by seq, ascending, and capped to the newest `cap`. Returns `cur` itself when nothing changed. */
export function mergeEvents(cur: CcEvent[], incoming: CcEvent[], cap = MAX_EVENTS): CcEvent[] {
  const have = new Set(cur.map((e) => e.seq))
  const fresh = incoming.filter((e) => !have.has(e.seq))
  if (!fresh.length) return cur
  const seen = new Set<number>()
  const add = fresh.filter((e) => (seen.has(e.seq) ? false : (seen.add(e.seq), true)))
  let next = [...cur, ...add]
  if (next.some((e, i) => i > 0 && e.seq < next[i - 1].seq)) next.sort((a, b) => a.seq - b.seq)
  if (next.length > cap) next = next.slice(next.length - cap)
  return next
}

export const addEvents = (all: Record<string, CcEvent[]>, id: string, incoming: CcEvent[], cap = MAX_EVENTS): Record<string, CcEvent[]> => {
  const cur = all[id] ?? []
  const next = mergeEvents(cur, incoming, cap)
  return next === cur ? all : { ...all, [id]: next }
}

export interface FleetSummary { cost: number; counts: Record<string, number>; total: number; live: number; waiting: number }

/** Totals over a set of sessions; the cost sums what each reports. */
export function summarize(list: CcSession[]): FleetSummary {
  const counts: Record<string, number> = {}
  let cost = 0
  for (const s of list) {
    counts[s.status] = (counts[s.status] ?? 0) + 1
    cost += Number.isFinite(s.cost_usd) ? (s.cost_usd as number) : 0
  }
  return {
    cost, counts, total: list.length,
    live: list.filter((s) => isLive(s.status)).length,
    waiting: counts.waiting_approval ?? 0
  }
}

/** A fleet's own totals, recomputed from the live session rows once they are known (the fleet row can lag behind them). */
export function summarizeFleet(f: CcFleet, sessions: Record<string, CcSession>): FleetSummary {
  const rows = f.session_ids.map((id) => sessions[id]).filter((s): s is CcSession => !!s)
  if (!rows.length) {
    const counts = f.counts ?? {}
    return { cost: f.cost_usd ?? 0, counts, total: Object.values(counts).reduce((a, b) => a + b, 0), live: (counts.starting ?? 0) + (counts.running ?? 0) + (counts.waiting_approval ?? 0), waiting: counts.waiting_approval ?? 0 }
  }
  return summarize(rows)
}

/** Sessions grouped by fleet (fleets in order of their newest member), then the ones that belong to none. */
export function groupByFleet(list: CcSession[]): { fleet: string | null; sessions: CcSession[] }[] {
  const groups = new Map<string | null, CcSession[]>()
  for (const s of sortSessions(list)) {
    const k = s.fleet_id ?? null
    groups.set(k, [...(groups.get(k) ?? []), s])
  }
  const named = [...groups.entries()].filter(([k]) => k !== null)
  const solo = groups.get(null)
  return [...named.map(([fleet, sessions]) => ({ fleet, sessions })), ...(solo ? [{ fleet: null, sessions: solo }] : [])]
}

export const formatCost = (usd: number | null | undefined): string => {
  const n = Number(usd)
  if (!Number.isFinite(n) || n <= 0) return '$0.00'
  return n < 0.01 ? '<$0.01' : `$${n.toFixed(2)}`
}

export const pathBase = (p: string | null | undefined): string => (p ? p.replace(/\/+$/, '').split('/').pop() ?? '' : '')

/** Tool results arrive as JSON text or as an object; anything else gives null. */
export function parseJson(raw: unknown): Record<string, unknown> | null {
  if (raw && typeof raw === 'object' && !Array.isArray(raw)) return raw as Record<string, unknown>
  if (typeof raw !== 'string') return null
  try {
    const v: unknown = JSON.parse(raw)
    return v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : null
  } catch { return null }
}
