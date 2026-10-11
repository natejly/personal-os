import type { Settings } from '@shared/types'

export type DeskTab = 'context' | 'files' | 'changes' | 'checklist' | 'workers' | 'review' | 'terminal'

/** What the chat's side panel has to show; the tab bar and the selected tab both follow it. */
export interface DeskTabFlags {
  hasConversation: boolean
  hasChecklist: boolean
  hasWorkers: boolean
  hasDesk: boolean
  hasTerminal: boolean
}

/** The Checklist and Workers tabs are hidden unless the setting is switched off. Missing means hidden. Pure: no store import. */
export const panelTabsHidden = (settings?: Pick<Settings, 'hideChatPanelTabs'> | null): boolean => settings?.hideChatPanelTabs !== false

/** Whether a tab belongs in the tab bar. Checklist and Workers never do while they are hidden. */
export function tabShown(tab: DeskTab, f: DeskTabFlags, hidden: boolean): boolean {
  if (hidden && (tab === 'checklist' || tab === 'workers')) return false
  return tab === 'context' || (f.hasConversation && (tab === 'files' || tab === 'changes')) || (tab === 'checklist' && f.hasChecklist) || (tab === 'workers' && f.hasWorkers) || (tab === 'review' && f.hasDesk) || (tab === 'terminal' && f.hasTerminal)
}

/** The tab a freshly opened panel starts on: a terminal request, then review, then a live worker (unless Workers is hidden), then Files. */
export function initialDeskTab(o: { terminalRequest: boolean; reviewing: boolean; liveWorker: boolean; hidden: boolean }): DeskTab {
  return o.terminalRequest ? 'terminal' : o.reviewing ? 'review' : o.liveWorker && !o.hidden ? 'workers' : 'files'
}

/** The tab to show: Context while requested, the picked tab while it is shown, else Files (or Context on a draft chat). Never a hidden tab. */
export function resolveDeskTab(picked: DeskTab, contextOpen: boolean, f: DeskTabFlags, hidden: boolean): DeskTab {
  return contextOpen ? 'context' : tabShown(picked, f, hidden) ? picked : f.hasConversation ? 'files' : 'context'
}
