import type { ModelInfo } from '@shared/types'
import { providerHead, modelLabel } from './modelLabel'
import type { PickKind } from './providerModels'

export interface SelectGroup { provider: string; name: string; rows: { value: string; label: string }[] }
export interface SelectView {
  groups: SelectGroup[]
  /** The value shown as selected: the saved one, or the listed ref it names when it was saved as a bare id. */
  value: string
  /** The saved model is not offered: one disabled option says why. */
  stale: { value: string; label: string } | null
}

const MODES: Record<PickKind, string[]> = { chat: [], embedding: ['embedding'], rerank: ['rerank'], image: ['image_generation', 'image'] }

/** The rows a field of this kind may offer. Rerank and image models may also be chat models (the backend falls back to a completion). */
function rowsFor(models: ModelInfo[], kind: PickKind): ModelInfo[] {
  const chat = models.filter((m) => m.mode == null || m.mode === 'chat')
  if (kind === 'chat') return chat
  const own = models.filter((m) => m.mode != null && MODES[kind].includes(m.mode))
  return kind === 'embedding' ? own.sort((a, b) => Number(!!b.preset) - Number(!!a.preset)) : [...own, ...chat]
}

/**
 * What a model <select> shows. Rows come from providers with a key, grouped by provider in list order.
 * The search model is stored as a bare id (its vectors are keyed by it); every other field stores `<provider>/<model>`.
 * A saved value that no listed row matches stays visible, disabled, with the reason.
 */
export function selectView(models: ModelInfo[], kind: PickKind, value: string, providers: { id: string; name: string }[]): SelectView {
  const names = new Map(providers.map((p) => [p.id, p.name]))
  const bare = kind === 'embedding'
  const val = (m: ModelInfo): string => (bare ? m.model : m.id)
  const seen = new Set<string>()
  const groups: SelectGroup[] = []
  const rows = rowsFor(models, kind)
  for (const m of rows) {
    if (seen.has(val(m))) continue
    seen.add(val(m))
    const provider = m.provider ?? ''
    let g = groups.find((x) => x.provider === provider)
    if (!g) groups.push((g = { provider, name: names.get(provider) ?? provider, rows: [] }))
    g.rows.push({ value: val(m), label: modelLabel(m.model) })
  }
  // Providers in the order they were added, whatever order the rows came in.
  const order = providers.map((p) => p.id)
  groups.sort((a, b) => (order.indexOf(a.provider) + 1 || order.length + 1) - (order.indexOf(b.provider) + 1 || order.length + 1))
  if (!value) return { groups, value, stale: null }
  const hit = rows.find((m) => val(m) === value) ?? rows.find((m) => m.model === value || m.id === value)
  if (hit) return { groups, value: val(hit), stale: null }
  const head = providerHead(value)
  const why = !head ? 'not available' : names.has(head) ? `add a key for ${names.get(head)}` : 'provider removed'
  return { groups, value, stale: { value, label: `${modelLabel(value)} — ${why}` } }
}
