import type { ModelInfo } from '@shared/types'
import { chatModelIds } from './modelLabel'

/** Chat rows only: embedding, rerank and other kinds never appear in the picker or its settings. */
export function chatRows(models: ModelInfo[]): ModelInfo[] {
  const ids = new Set(chatModelIds(models))
  return models.filter((m) => ids.has(m.id))
}

/**
 * The chat rows the composer picker offers. A saved choice (`pickerModels`) lists exactly those ids; ids no longer
 * in /models are ignored. Empty, or matching nothing, means the suggested rows (`preset`); a backend that marks
 * none keeps every chat row so the picker is never empty.
 */
export function pickerVisible(models: ModelInfo[], pickerModels: string[] | undefined): ModelInfo[] {
  const rows = chatRows(models)
  const chosen = new Set(pickerModels ?? [])
  const picked = rows.filter((m) => chosen.has(m.id))
  if (picked.length) return picked
  const preset = rows.filter((m) => m.preset)
  return preset.length ? preset : rows
}

/** The new `pickerModels` after switching one id. The first change from the default materialises the default set. */
export function togglePicker(models: ModelInfo[], pickerModels: string[] | undefined, id: string, on: boolean): string[] {
  const cur = pickerModels?.length ? pickerModels : pickerVisible(models, []).map((m) => m.id)
  const rest = cur.filter((x) => x !== id)
  return on ? [...rest, id] : rest
}

/** Rows grouped by provider, the active provider first, the rest in order of first appearance. */
export function groupByProvider<T extends { provider?: string }>(rows: T[], active?: string | null): { provider: string; rows: T[] }[] {
  const groups = new Map<string, T[]>()
  for (const r of rows) {
    const p = r.provider ?? ''
    groups.set(p, [...(groups.get(p) ?? []), r])
  }
  const out = [...groups].map(([provider, rs]) => ({ provider, rows: rs }))
  return [...out.filter((g) => g.provider === active), ...out.filter((g) => g.provider !== active)]
}

/** The search model is fixed once vectors exist for it (the backend reports it): they would no longer match. */
export const embeddingLocked = (s: { embeddingLocked?: boolean }): boolean => !!s.embeddingLocked

/** Embedding rows from providers that have a key, suggested ones first. */
export function embeddingRows(models: ModelInfo[], keys: Record<string, boolean> | undefined): ModelInfo[] {
  return models
    .filter((m) => m.mode === 'embedding' && (!m.provider || keys?.[m.provider]))
    .sort((a, b) => Number(!!b.preset) - Number(!!a.preset))
}
