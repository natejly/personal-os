/**
 * LiteLLM and Fireworks ids often carry a routing path
 * (`accounts/fireworks/models/kimi-k3`, `fireworks_ai/accounts/…/routers/…`).
 * The picker shows the model. The id sent to the proxy stays whole.
 */
const ROUTE = /^(?:[\w.-]+\/)?accounts\/[^/]+\/(?:models|routers)\//

/** A model setting is `<providerId>/<model>`; these are the ids a provider can have (custom endpoints are custom, custom-2, ...). */
const PROVIDER_HEAD = /^(anthropic|fireworks|openai|openrouter|litellm|ollama|custom(?:-\d+)?)\//

/** The provider id a ref starts with, or '' for a bare model id. */
export function providerHead(id: string): string {
  return PROVIDER_HEAD.exec(id)?.[1] ?? ''
}

export function modelLabel(id: string): string {
  if (id === 'auto') return 'Auto'
  return id.replace(PROVIDER_HEAD, '').replace(ROUTE, '')
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
  // One row per short name within a provider: the same model on two providers stays two rows.
  const byLabel = new Map<string, string>()
  for (const id of matched) {
    const key = `${providerHead(id)}|${modelLabel(id)}`
    const prev = byLabel.get(key)
    byLabel.set(key, prev ? prefer(prev, id, selected) : id)
  }
  return [...byLabel.values()].sort((a, b) => modelLabel(a).localeCompare(modelLabel(b)) || a.localeCompare(b))
}

interface ModelCaps { id: string; mode?: string | null; reasoning?: boolean | null }

/** Ids the picker may offer as chat models. A model whose kind is unknown stays: only an explicit non-chat mode drops it. */
export function chatModelIds(models: ModelCaps[]): string[] {
  return models.filter((m) => m.mode == null || m.mode === 'chat').map((m) => m.id)
}

/** The reasoning control shows unless the model is known to have no reasoning (an unknown model keeps it). */
export function showsEffort(models: ModelCaps[], id: string): boolean {
  return models.find((m) => m.id === id)?.reasoning !== false
}

/** At least one provider is set up: it has a key, or needs none. */
export function hasModelKey(s: { apiKeySet?: boolean; providers?: unknown[] }): boolean {
  return !!s.apiKeySet || !!s.providers?.length
}
