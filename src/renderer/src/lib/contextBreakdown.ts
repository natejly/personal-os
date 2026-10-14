import type { ContextUsed } from '@shared/types'

export interface UsageSlice { key: string; label: string; tokens: number; count: number }
export interface UsageBreakdown { slices: UsageSlice[]; used: number; free: number; window: number; pct: number }

const est = (s: string): number => Math.ceil(s.length / 4)
const sum = (xs: string[]): number => xs.reduce((n, x) => n + est(x), 0)

/**
 * The chat's context window split by what fills it, all estimates (len/4): the replayed history from the
 * context meter plus each injected block of the last reply (memory, graph, skills, workspace map, files, voice).
 * The system prompt and tool schemas are not itemised here, so the total reads slightly low.
 */
export function contextBreakdown(meter: { window: number; estimated_tokens: number }, ctx: ContextUsed | null): UsageBreakdown {
  const items: UsageSlice[] = [
    { key: 'messages', label: 'Messages', tokens: meter.estimated_tokens, count: 0 },
    ...(ctx ? [
      { key: 'memory', label: 'Memories', tokens: sum([...(ctx.profile ?? []), ...ctx.memories].map((m) => m.content)), count: (ctx.profile?.length ?? 0) + ctx.memories.length },
      { key: 'graph', label: 'Graph', tokens: sum([...ctx.nodes.map((n) => n.label), ...ctx.edges.map((e) => e.relation)]), count: ctx.nodes.length },
      { key: 'skills', label: 'Skills', tokens: sum((ctx.skills ?? []).map((s) => `${s.name} ${s.description}`)), count: ctx.skills?.length ?? 0 },
      { key: 'map', label: 'Workspace map', tokens: (ctx.map?.tokens ?? 0) + (ctx.map?.volatile?.tokens ?? 0), count: ctx.map ? 1 : 0 },
      { key: 'files', label: 'Files', tokens: sum([...ctx.chunks.map((c) => c.text ?? ''), ...(ctx.pinned ?? []).map((p) => p.name)]), count: ctx.chunks.length + (ctx.pinned?.length ?? 0) },
      { key: 'style', label: 'Writing style', tokens: est(ctx.style?.block ?? ''), count: ctx.style ? 1 : 0 }
    ] : [])
  ]
  const slices = items.filter((s) => s.tokens > 0 || s.key === 'messages')
  const used = slices.reduce((n, s) => n + s.tokens, 0)
  const window = Math.max(1, meter.window)
  return { slices, used, free: Math.max(0, window - used), window, pct: Math.min(100, (used / window) * 100) }
}

/** 44200 -> "44.2k", 1000000 -> "1M". */
export const fmtTok = (n: number): string => {
  if (n >= 1e6) return `${+(n / 1e6).toFixed(1)}M`
  if (n >= 1e3) return `${+(n / 1e3).toFixed(1)}k`
  return String(Math.round(n))
}
