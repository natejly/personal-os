/** Pure helpers for the Settings dialog: the rail's one-line status, which section a registered panel lands in, and the rail's clusters. */
import { SECTION_ALIAS, SECTION_ORDER, sectionOf, type SettingsSectionId, type SettingsTab } from './settingsTabs'

/** Sections that sit together in the rail; a divider is drawn between clusters. */
export const RAIL_CLUSTERS: readonly (readonly SettingsSectionId[])[] = [
  ['general', 'models', 'assistant', 'permissions'],
  ['extensions', 'advanced']
]

/** True when `id` opens a new cluster (a divider goes above it). */
export function startsCluster(id: SettingsSectionId): boolean {
  return RAIL_CLUSTERS.slice(1).some((c) => c[0] === id)
}

/** A group id as a section: a known section id stays, anything else (missing, misspelt) falls into Advanced. */
export function panelSection(p: { group?: string }): SettingsSectionId {
  const g = SECTION_ALIAS[p.group ?? '']?.tab ?? p.group ?? 'advanced'
  return (SECTION_ORDER as readonly string[]).includes(g) ? (g as SettingsSectionId) : 'advanced'
}

/** The registered panels of `section`, by order then registration, with unknown groups counted as Advanced. */
export function panelsFor<T extends { group?: string; order?: number }>(section: SettingsSectionId, list: readonly T[]): T[] {
  return list.map((p, i) => ({ p, i })).filter(({ p }) => panelSection(p) === section)
    .sort((a, b) => (a.p.order ?? 100) - (b.p.order ?? 100) || a.i - b.i).map(({ p }) => p)
}

/** Where a tab id opens: its section, and the anchor to scroll to when it names a registered panel. */
export function locate(tab: SettingsTab, panels: readonly { tab: string; id: string; group?: string }[]): { section: SettingsSectionId; anchor?: string } {
  if (!tab.startsWith('ext:')) return { section: tab as SettingsSectionId }
  const p = panels.find((x) => x.tab === tab)
  if (p) return { section: panelSection(p), anchor: p.id }
  return { section: sectionOf(tab), anchor: tab === 'ext:core:runtime' ? 'runtime' : undefined }
}

export interface HintInput {
  themeLabel?: string | null
  modelLabel?: string | null
  modeLabel?: string | null
  autoCompact?: boolean | null
  extensionsOn?: number | null
}

/** The line under a section's name in the rail, from data already at hand; null when there is nothing worth saying. */
export function hintOf(id: SettingsSectionId, h: HintInput): string | null {
  switch (id) {
    case 'general': return h.themeLabel ?? null
    case 'models': return h.modelLabel || null
    case 'permissions': return h.modeLabel ?? null
    case 'assistant': return h.autoCompact == null ? null : h.autoCompact ? 'Auto-compact on' : 'Auto-compact off'
    case 'extensions': return h.extensionsOn == null ? null : `${h.extensionsOn} on`
    default: return null
  }
}
