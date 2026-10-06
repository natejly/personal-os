import type { Doc } from '@shared/types'
import { titleKey } from '../features/notes/wikilinks'

export type PanelTab = 'outline' | 'recordings' | 'links' | 'history'
export const PANEL_TABS: PanelTab[] = ['outline', 'recordings', 'links', 'history']
export interface PanelState { open: boolean; tab: PanelTab }

const DEFAULT: PanelState = { open: false, tab: 'outline' }

/** Read the stored panel state. Anything unexpected falls back to closed rather than throwing. */
export function parsePanelState(raw: string | null): PanelState {
  if (!raw) return { ...DEFAULT }
  try {
    const v = JSON.parse(raw) as Partial<PanelState> | null
    const tab = PANEL_TABS.find((t) => t === v?.tab) ?? DEFAULT.tab
    return { open: v?.open === true, tab }
  } catch {
    return { ...DEFAULT }
  }
}

/**
 * The doc a `[[Title]]` points at: a live title match (case and spacing ignored), preferring the
 * same scope as the doc the link was written in, then the most recently updated. The doc being
 * read is never its own target unless nothing else matches.
 */
export function resolveWikiDoc(docs: Doc[], title: string, scope: string, selfId?: string): Doc | null {
  const key = titleKey(title)
  if (!key) return null
  const hits = docs.filter((d) => titleKey(d.title) === key)
  if (hits.length === 0) return null
  const rank = (d: Doc): number => (d.id === selfId ? 2 : 0) + ((d.project_id ?? '') === scope ? 0 : 1)
  return [...hits].sort((a, b) => rank(a) - rank(b) || b.updated_at - a.updated_at)[0]
}
