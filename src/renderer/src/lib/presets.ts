/** Permission presets, as the Settings panel shows them. Pure: no store import. */
import type { PermissionPreset, PresetDiff, PresetScope, ProfileHeld } from '@shared/types'
import { MODES } from './permissionMode'

export const SCOPE_LABEL: Record<PresetScope, string> = { global: 'Everywhere', project: 'This project', chat: 'This chat' }

/** The scopes that can be chosen: a project or a chat only while one is open. */
export function scopesFor(projectId: string | null, chatId: string | null): PresetScope[] {
  return ['global', ...(projectId ? ['project' as const] : []), ...(chatId ? ['chat' as const] : [])]
}

/** The id the backend wants with a scope: the project's or the chat's, empty for everywhere. */
export const scopeId = (scope: PresetScope, projectId: string | null, chatId: string | null): string =>
  scope === 'project' ? projectId ?? '' : scope === 'chat' ? chatId ?? '' : ''

const KEY_LABEL: Record<string, string> = {
  permissionMode: 'Permission mode', permissionRules: 'Permission rules', alwaysAsk: 'Always ask first', destructiveDefaults: 'Destructive actions',
  unattendedApprovals: 'Background runs that need a card', shellNetwork: 'Open network for commands', shellRegistryAccess: 'Package registries for commands',
  shellAllowedDomains: 'Domains for commands', fetchAllowlist: 'Domains fetch may read after untrusted content'
}

/** A changed key in words: `tools.shell_run` reads "Tool: shell_run". */
export function keyLabel(key: string): string {
  if (key.startsWith('tools.')) return `Tool: ${key.slice(6)}`
  if (key.startsWith('connectors.')) return `Connector: ${key.slice(11)}`
  return KEY_LABEL[key] ?? key
}

/** A value for a diff row. */
export function valueText(key: string, v: unknown): string {
  if (key === 'permissionMode') return MODES.find((m) => m.id === v)?.label ?? String(v)
  if (Array.isArray(v)) return v.length ? v.join(', ') : 'none'
  if (v && typeof v === 'object') {
    const rules = v as Record<string, unknown>
    return Object.entries(rules).map(([k, x]) => `${k}: ${Array.isArray(x) ? (x.length ? x.join(', ') : 'none') : String(x)}`).join('; ')
  }
  return typeof v === 'boolean' ? (v ? 'on' : 'off') : String(v)
}

export interface DiffRow { key: string; label: string; old: string; next: string; loosens: boolean }

/** The rows of a diff, the ones that allow more first. */
export function diffRows(diff: PresetDiff): DiffRow[] {
  return Object.entries(diff.changes)
    .map(([key, c]) => ({ key, label: keyLabel(key), old: valueText(key, c.old), next: valueText(key, c.new), loosens: c.loosens }))
    .sort((a, b) => Number(b.loosens) - Number(a.loosens) || a.key.localeCompare(b.key))
}

/** Where a preset comes from, for its row. */
export const presetOrigin = (p: PermissionPreset): string =>
  p.source === 'builtin' ? 'Built in' : p.source === 'profile' ? `From ${p.owner}` : p.source === 'import' ? 'Imported' : 'Yours'

/** What a profile's held default would change, in words: the mode, or one key of a permission preset. */
export function heldText(c: ProfileHeld): string {
  const key = c.key === 'permissions' ? 'permissionMode' : c.key.replace(/^perm:/, '')
  return `${keyLabel(key)} from ${valueText(key, c.was)} to ${valueText(key, c.set)}`
}
