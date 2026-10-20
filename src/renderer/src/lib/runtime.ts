import { ApiError } from './apiError'
import type { RuntimeProvider, RuntimeResolution, RuntimeSelection, RuntimeSlot, RuntimeView } from '@shared/types'

const NOUN: Record<string, string> = {
  'model.client': 'Model client', 'model.router': 'Model router', 'tools.source': 'Tool source', 'sandbox.backend': 'Sandbox backend', 'agent.loop': 'Agent loop', 'memory.system': 'Memory system'
}
export const slotNoun = (slot: string): string => NOUN[slot] ?? slot

const name = (provider: string): string => provider.split('/').pop() ?? provider

/** The footer line of a reply: each slot a provider other than the built-in served, e.g. "Model client: acme". Empty when none did. */
export function runtimeChip(used: RuntimeResolution[] | undefined): string {
  return (used ?? []).map((r) => `${slotNoun(r.slot)}: ${r.extra.length ? r.extra.map(name).join(', ') : name(r.provider)}`).join(' · ')
}

/** The sentences behind the chip, one per slot, for its tooltip. */
export const runtimeReasons = (used: RuntimeResolution[] | undefined): string => (used ?? []).map((r) => r.reason).join('\n')

/** Where a resolution's choice came from, for a card. */
export function sourceText(source: string): string {
  if (source.startsWith('profile:')) return `Profile ${source.slice('profile:'.length)}`
  return { chat: 'This chat', project: 'This project', global: 'Settings', default: 'Default' }[source] ?? source
}

export interface Shortfall { mode: string; dimension: string }

/** The dimensions a backend enforces only partly, for the "accept" toggles. */
export function partialDimensions(p: Pick<RuntimeProvider, 'enforcement'>): Shortfall[] {
  return Object.entries(p.enforcement ?? {}).flatMap(([mode, dims]) => Object.entries(dims).filter(([, level]) => level === 'partial').map(([dimension]) => ({ mode, dimension })))
}

/** The selection a scope holds for a single-provider slot (a provider id), or '' for "inherit". */
export const pickedId = (sel: RuntimeSelection | undefined): string => (typeof sel === 'string' ? sel : '')

/** A tool source is on at a scope when its `enable` list names it and its `disable` list does not. */
export function sourceOn(sel: RuntimeSelection | undefined, id: string): boolean {
  const s = typeof sel === 'object' && sel ? sel : {}
  return (s.enable ?? []).includes(id) && !(s.disable ?? []).includes(id)
}

/** What a tool source's switch shows: at everywhere, that scope's own list; at a project or chat, whether it is in force there, since a wider scope may already have it on. */
export function sourceShown(at: 'global' | 'project' | 'chat', sel: RuntimeSelection | undefined, resolution: Pick<RuntimeResolution, 'extra'>, id: string): boolean {
  return at === 'global' ? sourceOn(sel, id) : resolution.extra.includes(id)
}

/** The selection after switching source `id` on or off at a scope. */
export function toggleSource(sel: RuntimeSelection | undefined, id: string, on: boolean): RuntimeSelection {
  const s = typeof sel === 'object' && sel ? sel : {}
  const enable = (s.enable ?? []).filter((i) => i !== id)
  const disable = (s.disable ?? []).filter((i) => i !== id)
  return { enable: on ? [...enable, id] : enable, disable: on ? disable : [...disable, id] }
}

/** The lines a looser choice would loosen, when the backend asked for a confirmation (409 with a diff); null for any other error. */
export function confirmDiff(e: unknown): string[] | null {
  const d = e instanceof ApiError && e.status === 409 ? (e.body as { detail?: { needs_confirm?: boolean; diff?: string[] } } | null)?.detail : undefined
  return d?.needs_confirm && Array.isArray(d.diff) ? d.diff : null
}

/** The agent loop is chosen in the Assistant section's Loop group, not in Runtime. */
export const LOOP_SLOT = 'agent.loop'

/** The slots worth a picker: only once an extension offers something besides the built-in. With none, the Runtime block is empty. */
export const runtimeSlotsShown = (view: Pick<RuntimeView, 'slots'> | null | undefined): RuntimeSlot[] =>
  view?.slots.filter((s) => s.id !== LOOP_SLOT && s.providers.some((p) => !p.core)) ?? []
