/**
 * LiteLLM and Fireworks ids often carry a routing path
 * (`accounts/fireworks/models/kimi-k3`, `fireworks_ai/accounts/…/routers/…`).
 * The picker shows the model. The id sent to the proxy stays whole.
 */
const ROUTE = /^(?:[\w.-]+\/)?accounts\/[^/]+\/(?:models|routers)\//

export function modelLabel(id: string): string {
  return id.replace(ROUTE, '')
}

/** Prefer the id the user already picked, then a bare alias, then the shorter path. */
function prefer(a: string, b: string, selected: string): string {
  if (a === selected) return a
  if (b === selected) return b
  const aBare = a === modelLabel(a)
  const bBare = b === modelLabel(b)
  if (aBare !== bBare) return aBare ? a : b
  return a.length <= b.length ? a : b
}

/** Search matches the short name and the full id. One row per short name, sorted by that name. */
export function modelChoices(ids: string[], query: string, selected: string): string[] {
  const q = query.trim().toLowerCase()
  const matched = ids.filter((id) => !q || modelLabel(id).toLowerCase().includes(q) || id.toLowerCase().includes(q))
  const byLabel = new Map<string, string>()
  for (const id of matched) {
    const label = modelLabel(id)
    const prev = byLabel.get(label)
    byLabel.set(label, prev ? prefer(prev, id, selected) : id)
  }
  return [...byLabel.values()].sort((a, b) => modelLabel(a).localeCompare(modelLabel(b)) || a.localeCompare(b))
}
