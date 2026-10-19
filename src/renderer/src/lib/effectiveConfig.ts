/** The effective configuration as a table: rows, filters and the tab that edits each. Pure: no store import. */
import type { EffectiveConfig } from '@shared/types'
import type { SettingsTab } from './settingsTabs'

export interface EffectiveRow {
  key: string
  value: string
  source: string
  scope: string
  shadowed: string[]
  legacy: boolean
}

const show = (v: unknown): string => (typeof v === 'string' ? v : JSON.stringify(v) ?? 'null')

/** Every setting, then every tool mode, one row each. */
export function effectiveRows(cfg: EffectiveConfig): EffectiveRow[] {
  const values = Object.entries(cfg.values).map(([key, e]) => ({
    key, value: show(e.value), source: e.source, scope: e.scope, legacy: !!e.legacy,
    shadowed: e.shadowed.map((s) => `${s.source}: ${show(s.value)}`)
  }))
  const tools = Object.entries(cfg.tools).map(([name, t]) => ({
    key: `tools.${name}`, value: t.mode, source: t.source, scope: t.source === 'project' || t.source === 'chat' ? t.source : 'global', legacy: false, shadowed: []
  }))
  return [...values, ...tools]
}

/** `preset:builtin:auto` and `profile:lean` are filtered as `preset` and `profile`. */
export const sourceKind = (source: string): string => source.split(':')[0]

/** The source kinds present, for the filter chips, `default` first. */
export function sourceKinds(rows: EffectiveRow[]): string[] {
  return [...new Set(rows.map((r) => sourceKind(r.source)))].sort((a, b) => Number(b === 'default') - Number(a === 'default') || a.localeCompare(b))
}

/** `kind`: a source kind, 'all', or 'changed' (anything but the default). */
export function filterRows(rows: EffectiveRow[], query: string, kind: string): EffectiveRow[] {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean)
  return rows.filter((r) => {
    if (kind === 'changed' ? r.source === 'default' : kind !== 'all' && sourceKind(r.source) !== kind) return false
    const hay = `${r.key} ${r.value} ${r.source}`.toLowerCase()
    return words.every((w) => hay.includes(w))
  })
}

const PERMISSION_KEYS = /^(tools\.|permission|alwaysAsk|destructiveDefaults|unattendedApprovals|shell(Network|RegistryAccess|AllowedDomains)|fetchAllowlist|browser(Enabled|Allowlist)|docEditMode|allowAllConnections|trustExternalContent|desk(ShellAuto|DoneGate|SelfReview)|codingRoute|autoReviewModel)/
const MODEL_KEYS = /^(default(Model|Effort)|model(High|Medium|Low)|workerModel|visionModel|imageModel|embeddingModel|retrieval)/
const LOOK_KEYS = /^(theme|accent|font|codeFont|zoom)/

/** The Settings tab where a key is edited, or null for a key with no panel of its own. */
export function tabFor(key: string): SettingsTab | null {
  if (PERMISSION_KEYS.test(key)) return 'permissions'
  if (MODEL_KEYS.test(key)) return 'models'
  if (LOOK_KEYS.test(key)) return 'general'
  if (key === 'hiddenViews' || key.startsWith('sidebar')) return 'layout'
  return null
}
