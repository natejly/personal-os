import type { CatalogCard, ExtensionConsent, ExtensionInstallRequest } from '@shared/types'

export interface Chip { label: string; warn: boolean }

const plural = (n: number, one: string, many = `${one}s`): string => `${n} ${n === 1 ? one : many}`

/** The first characters of a pinned hash, enough to compare by eye. */
export const shortHash = (sha: string | undefined, n = 12): string => (sha ? sha.slice(0, n) : '')

export type BadgeKind = 'bundled' | 'verified' | 'unverified'

export function badgeKind(card: Pick<CatalogCard, 'source' | 'verified'>): BadgeKind {
  if (card.source === 'bundled') return 'bundled'
  return card.verified ? 'verified' : 'unverified'
}

export const BADGE_TEXT: Record<BadgeKind, string> = { bundled: 'Included with Grain', verified: 'Verified', unverified: 'Unverified' }

const SOURCE_TEXT: Record<CatalogCard['source'], string> = { bundled: 'Bundled', git: 'Git', zip: 'Zip archive', folder: 'Local folder', catalog: 'Catalog' }

/** Line 2 of a card: author and where it came from. */
export function authorSource(card: Pick<CatalogCard, 'author' | 'source' | 'url'>): string {
  const host = (() => { try { return card.url ? new URL(card.url).host : '' } catch { return '' } })()
  return [card.author, SOURCE_TEXT[card.source] + (host && card.source !== 'folder' ? ` (${host})` : '')].filter(Boolean).join(' - ')
}

/** Contribution chips; hooks and background processes are the ones to look at twice. */
export function contributionChips(card: Pick<CatalogCard, 'contributes'>): Chip[] {
  const c = card.contributes
  const out: Chip[] = []
  if (c.tools.length) out.push({ label: plural(c.tools.length, 'tool'), warn: c.tools.some((t) => t.danger === 'danger') })
  if (c.views.length) out.push({ label: plural(c.views.length, 'view'), warn: false })
  if (c.slots.length) out.push({ label: plural(c.slots.length, 'slot'), warn: false })
  if (c.commands.length) out.push({ label: plural(c.commands.length, 'command'), warn: false })
  if (c.settingsFields.length) out.push({ label: plural(c.settingsFields.length, 'setting'), warn: false })
  if (c.hooks.length) out.push({ label: plural(c.hooks.length, 'hook'), warn: true })
  if (c.processes.length) out.push({ label: plural(c.processes.length, 'background process', 'background processes'), warn: true })
  return out
}

/** The capability sentences as short fragments: no trailing full stops, no empties, no repeats. */
export function capabilityLines(lines: readonly string[]): string[] {
  const out = lines.map((l) => l.trim().replace(/[.\s]+$/, '').replace(/\.{2,}/g, '.')).filter(Boolean)
  return out.filter((l, i) => out.indexOf(l) === i)
}

/** "adds about 120 tokens every turn". */
export function contextCostText(tokens: number): string {
  if (!tokens || tokens <= 0) return 'Adds nothing to the prompt'
  return `Adds about ${tokens.toLocaleString('en-US')} tokens every turn`
}

/** "Required by Calendar, Mail"; ids are shown by name when `names` knows them. */
export function requiredByText(card: Pick<CatalogCard, 'requiredBy'>, names: Record<string, string> = {}): string {
  if (!card.requiredBy.length) return ''
  return `Required by ${card.requiredBy.map((id) => names[id] ?? id).join(', ')}`
}

/** Turn what the user typed into the install request: a path when it is absolute, otherwise a URL. */
export function installRequest(input: string, ref: string, subdir: string): ExtensionInstallRequest | { error: string } {
  const text = input.trim()
  if (!text) return { error: 'Enter a git URL, a zip URL or a folder path.' }
  const isPath = text.startsWith('/') || text.startsWith('~') || /^[A-Za-z]:[\\/]/.test(text) || text.startsWith('file://')
  const req: ExtensionInstallRequest = isPath ? { path: text.replace(/^file:\/\//, '') } : { url: text }
  if (!isPath && !/^(https?:|git@|ssh:|git:)/.test(text)) return { error: 'That is not a URL or an absolute folder path.' }
  if (ref.trim()) req.ref = ref.trim()
  if (subdir.trim()) req.subdir = subdir.trim()
  return req
}

/** Findings of level error stop the confirm button. */
export const blockingFindings = (c: Pick<ExtensionConsent, 'findings'>): string[] => c.findings.filter((f) => f.level === 'error').map((f) => f.message)

/** Whether an update consent says nothing changed. */
export const alreadyUpToDate = (c: ExtensionConsent): boolean => c.card.updateAvailable === false && !c.changes?.permissionsAdded.length && !c.changes?.hooksAdded.length && !c.changes?.toolsAdded.length

/** The new-version changes that force a closer look, one line each. */
export function changeLines(changes: ExtensionConsent['changes']): string[] {
  if (!changes) return []
  return [
    ...changes.permissionsAdded.map((p) => `New permission: ${p}`),
    ...changes.hooksAdded.map((h) => `New hook: ${h}`),
    ...changes.toolsAdded.map((t) => `New tool: ${t}`)
  ]
}

/** Whether a card can be switched on from its card (invalid ones cannot). */
export const canToggle = (card: Pick<CatalogCard, 'state'>): boolean => card.state !== 'invalid'

export const UI_NOT_LOADED_TEXT = "Backend tools only; this extension's UI parts are not loaded"
