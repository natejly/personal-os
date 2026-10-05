import type { ResearchTrail, ToolEvent } from '@shared/types'

export interface TrailView {
  plan: string[]
  steps: { q: string; status: string; label: string }[]
  sources: { url: string; title: string; n?: number }[]
  dropped: number
}

const plural = (n: number, w: string): string => `${n} ${w}${n === 1 ? '' : 's'}`

/** The reply's research trail, shaped for display: the last finished deep_research call that carries one, else null. */
export function trailFromEvents(events: ToolEvent[] | null | undefined): TrailView | null {
  const ev = [...(events ?? [])].reverse().find((e) => e.name === 'deep_research' && e.research && Array.isArray(e.research.steps))
  const r: ResearchTrail | null | undefined = ev?.research
  if (!r) return null
  return {
    plan: r.plan ?? [],
    steps: r.steps.map((s) => ({
      q: s.q,
      status: s.status,
      label: s.status === 'done' ? plural(s.sources?.length ?? 0, 'source') : s.status === 'failed' ? 'no sourced claims' : s.status
    })),
    sources: r.sources_considered ?? [],
    dropped: r.dropped ?? 0
  }
}
