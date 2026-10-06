/** The Settings sections, and where the ids older code still passes to openSettings now live. Pure: no store import. */
export type SettingsTab = 'model' | 'usage' | 'permissions' | 'integrations' | 'texting' | 'appearance' | 'system' | 'advanced'
export type AdvancedGroup = 'assistant' | 'approvals' | 'files' | 'memory' | 'desks' | 'mail' | 'voice' | 'layout' | 'data' | 'developer'
export type LegacySettingsTab = 'workspace' | 'provider' | 'memory' | 'meetings' | 'cowork' | 'modules' | 'behavior' | 'data' | 'spending'

const LEGACY: Record<LegacySettingsTab, { tab: SettingsTab; group?: AdvancedGroup }> = {
  workspace: { tab: 'permissions' },
  provider: { tab: 'model' },
  memory: { tab: 'advanced', group: 'memory' },
  meetings: { tab: 'integrations' },
  cowork: { tab: 'advanced', group: 'desks' },
  modules: { tab: 'advanced', group: 'layout' },
  behavior: { tab: 'advanced', group: 'assistant' },
  data: { tab: 'advanced', group: 'data' },
  spending: { tab: 'usage' }
}
const CURRENT: readonly string[] = ['model', 'usage', 'permissions', 'integrations', 'texting', 'appearance', 'system', 'advanced']

/** Any id, current or old, to a section (and the Advanced group to open). An unknown id lands on Model. */
export function resolveTab(id: string): { tab: SettingsTab; group?: AdvancedGroup } {
  if (CURRENT.includes(id)) return { tab: id as SettingsTab }
  return LEGACY[id as LegacySettingsTab] ?? { tab: 'model' }
}
