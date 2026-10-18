import type { HookId } from '@shared/extensionHooks'
import type { ExtensionInfo } from '@shared/types'

/** A handler latency for the dev panel: "—" before the first call, whole ms from 10, one decimal below. */
export function formatMs(ms: number | null): string {
  if (ms === null) return '—'
  return ms >= 10 ? `${Math.round(ms)} ms` : `${ms.toFixed(1)} ms`
}

/** A job's interval: whole minutes from a minute up, whole hours when they divide evenly. */
export function formatEvery(seconds: number): string {
  const minutes = Math.round(seconds / 60)
  return minutes >= 60 && minutes % 60 === 0 ? `${minutes / 60} h` : `${minutes} min`
}

/** What an extension registered, one phrase per kind ("2 tools", "routes /notes", "1 view"). */
export function contributionSummary(ext: Pick<ExtensionInfo, 'contributes' | 'tools'>): string[] {
  const c = ext.contributes as { routes?: { prefix?: string }; views?: unknown[] }
  const plural = (n: number, word: string): string => `${n} ${word}${n === 1 ? '' : 's'}`
  return [
    ...(ext.tools.length ? [plural(ext.tools.length, 'tool')] : []),
    ...(c.routes?.prefix ? [`routes ${c.routes.prefix}`] : []),
    ...(c.views?.length ? [plural(c.views.length, 'view')] : [])
  ]
}

/** Handlers that have been switched off after repeated failures. */
export function mutedHooks(ext: Pick<ExtensionInfo, 'hooks'>): HookId[] {
  return ext.hooks.filter((h) => h.muted).map((h) => h.hook)
}

/** The permission that gives an extension raw CSS and DOM access. */
export const UNSUPPORTED_CSS = 'ui:unsupported-css'

/** The line an extension card shows for an extension with raw CSS or DOM access, or null. */
export const unsupportedWarning = (permissions: readonly string[]): string | null =>
  permissions.includes(UNSUPPORTED_CSS) ? 'Uses raw CSS and DOM access, which is unsupported and can break with any update.' : null
