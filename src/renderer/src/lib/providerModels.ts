import type { Settings } from '@shared/types'
import type { ProviderInfo } from '../components/onboarding/steps'

/** The last path segment of a model id: `accounts/fireworks/models/ember-1` is `ember-1`. */
export const baseName = (id: string): string => id.slice(id.lastIndexOf('/') + 1)

/** Providers that route to whatever the user runs, so their ids are not a fixed catalogue. */
const OPEN = new Set(['custom', 'litellm', 'ollama'])

/**
 * The id to use for the same model on another provider. `live` is that provider's own model list when known.
 * A model with no counterpart on a catalogued provider becomes its default (chat) or empty (anything else),
 * so a Fireworks id is never sent to a server that has no such model.
 */
export function remapModel(id: string, to: ProviderInfo, live?: string[], field: 'chat' | 'other' = 'other'): string {
  if (!id) return ''
  const candidates = [...(live ?? []), ...to.models]
  if (candidates.includes(id)) return id
  const name = baseName(id).toLowerCase()
  const same = candidates.find((c) => baseName(c).toLowerCase() === name)
  if (same) return same
  if (!to.models.length || OPEN.has(to.id)) return id.includes('/') ? baseName(id) : id
  return field === 'chat' ? to.defaultModel : ''
}

/** The model fields of Settings, in the order the Model tab shows them. */
export const MODEL_FIELDS = [
  { key: 'defaultModel', label: 'Chat model', field: 'chat' },
  { key: 'fastModel', label: 'Fast model' },
  { key: 'extractionModel', label: 'Helper model' },
  { key: 'embeddingModel', label: 'Search model' },
  { key: 'visionModel', label: 'Vision model' },
  { key: 'imageModel', label: 'Image model' }
] as const

export type ModelKey = (typeof MODEL_FIELDS)[number]['key']

/** The edits that move every model field to provider `to`, and the labels of fields that had a value and lost it. */
export function remapSettings(draft: Partial<Pick<Settings, ModelKey>>, to: ProviderInfo, live?: string[]): { patch: Partial<Settings>; cleared: string[] } {
  const patch: Record<string, string> = {}
  const cleared: string[] = []
  for (const f of MODEL_FIELDS) {
    const before = draft[f.key] ?? ''
    const after = remapModel(before, to, live, 'field' in f ? f.field : 'other')
    if (after !== before) patch[f.key] = after
    if (before && !after) cleared.push(f.label)
  }
  return { patch: patch as Partial<Settings>, cleared }
}
