/** The Settings sections, and where the ids older code still passes to openSettings now live. Pure: no store import. */
export type SettingsSectionId = 'general' | 'models' | 'assistant' | 'permissions' | 'extensions' | 'integrations' | 'advanced'
export type SettingsTab = SettingsSectionId | `ext:${string}`
/** An anchor inside a section (`sec-<group>` in the pane). */
export type SettingsGroup = string
/** Older name for a group id. */
export type AdvancedGroup = SettingsGroup
export type LegacySettingsTab = 'appearance' | 'model' | 'agents' | 'memory' | 'system' | 'sidebar' | 'workspace' | 'provider' | 'cowork' | 'texting' | 'modules' | 'behavior' | 'data' | 'spending' | 'learning' | 'search' | 'map' | 'effective' | 'developer'

/** The sections in rail order. */
export const SECTION_ORDER: readonly SettingsSectionId[] = ['general', 'models', 'assistant', 'permissions', 'extensions', 'integrations', 'advanced']

/** Section ids that no longer exist, as the section (and the group to open) that absorbed them. One map for every caller: openSettings, the palette, a panel's `group`. */
export const SECTION_ALIAS: Record<string, { tab: SettingsSectionId; group?: SettingsGroup }> = {
  agent: { tab: 'assistant' },
  context: { tab: 'assistant', group: 'memories' },
  layout: { tab: 'general', group: 'sidebar' },
  hooks: { tab: 'advanced' },
  usage: { tab: 'advanced', group: 'usage' },
  jobs: { tab: 'advanced' }
}

const LEGACY: Record<LegacySettingsTab, { tab: SettingsTab; group?: SettingsGroup }> = {
  appearance: { tab: 'general' },
  model: { tab: 'models' },
  provider: { tab: 'models', group: 'provider' },
  behavior: { tab: 'models', group: 'assistant' },
  agents: { tab: 'assistant' },
  cowork: { tab: 'assistant', group: 'desks' },
  memory: { tab: 'assistant', group: 'memories' },
  learning: { tab: 'assistant', group: 'learning' },
  search: { tab: 'assistant', group: 'search' },
  map: { tab: 'assistant', group: 'map' },
  system: { tab: 'permissions', group: 'system' },
  workspace: { tab: 'permissions' },
  sidebar: { tab: 'general', group: 'sidebar' },
  modules: { tab: 'general', group: 'sidebar' },
  texting: { tab: 'integrations', group: 'texting' },
  data: { tab: 'advanced', group: 'data' },
  effective: { tab: 'advanced', group: 'effective' },
  developer: { tab: 'advanced', group: 'developer' },
  spending: { tab: 'advanced', group: 'usage' }
}
const CURRENT: readonly string[] = SECTION_ORDER

/** Any id, current or old, to a section (and the group to scroll to). An unknown id lands on Models. */
export function resolveTab(id: string): { tab: SettingsTab; group?: SettingsGroup } {
  if (id === 'ext:core:runtime') return { tab: 'extensions', group: 'runtime' }
  if (SECTION_ALIAS[id]) return SECTION_ALIAS[id]
  if (CURRENT.includes(id) || id.startsWith('ext:')) return { tab: id as SettingsTab }
  return LEGACY[id as LegacySettingsTab] ?? { tab: 'models' }
}

/** The section a tab belongs to: itself, or for a registered panel the section it is shown in by default (the runtime panel sits in Extensions, others in Advanced; a panel's own `group` is applied by the pane). */
export function sectionOf(tab: SettingsTab): SettingsSectionId {
  if (tab === 'ext:core:runtime') return 'extensions'
  return tab.startsWith('ext:') ? 'advanced' : (tab as SettingsSectionId)
}

export const DEFAULT_TAB: SettingsTab = 'general'

/** The tab to show: `tab` while it is among `available`, otherwise General (an extension's tab can vanish while open). */
export function shownTab(tab: SettingsTab, available: readonly string[]): SettingsTab {
  return available.includes(tab) ? tab : DEFAULT_TAB
}

/** One findable block of settings: the section it lives in, its group anchor id (`sec-<sec>` in the pane) and the words people type for it. `ext` is the extension that renders the block: with it off there is nothing to scroll to. */
/** `open` is the collapsed group that holds the block when that is not the block's own id. */
export interface SettingsEntry { tab: SettingsTab; sec: string; title: string; keys: string; ext?: string; open?: string }
/** Stands in for the `ext` of the Runtime block: it renders nothing until some slot has a non-core provider, so a search hit needs that to be off the `off` set. */
export const RUNTIME_EXT = 'core:runtime'
export const SETTINGS_INDEX: readonly SettingsEntry[] = [
  { tab: 'general', sec: 'look', title: 'Theme, accent, fonts and zoom', keys: 'preset presets dark light colour color custom accent font typeface code monospace size scale recap blobs' },
  { tab: 'general', sec: 'notifications', title: 'Notifications', keys: 'notify alerts unread dot jobs finish' },
  { tab: 'general', sec: 'shortcuts', title: 'Shortcuts and voice', keys: 'keyboard hotkey dictation microphone speech quick note quick ask gather widgets' },
  { tab: 'models', sec: 'provider', title: 'Providers', keys: 'provider add key api key verify replace remove address base url openai anthropic fireworks openrouter ollama litellm custom setup' },
  { tab: 'models', sec: 'models', title: 'Models', keys: 'chat model default tier high medium low worker embedding search rerank vision image fast model' },
  { tab: 'models', sec: 'assistant', title: 'Assistant behaviour', keys: 'standing instructions system prompt reply style tone titles follow-up suggestions selection toolbar compact summarize' },
  { tab: 'models', sec: 'picker', title: 'Models in the picker', keys: 'composer model menu choose show hide checkbox reset suggested' },
  { tab: 'assistant', sec: 'loop', title: 'Agent loop limits', keys: 'loop loop picker rounds max rounds model calls repeat repeat limit tool errors doom stuck sensitivity stuck detection final round tool result size inline plan steps verify passes parallel reads retries idle timeout stop runaway cap' },
  { tab: 'assistant', sec: 'desks', title: 'Desks', keys: 'cowork desk folder agents workers autonomous' },
  { tab: 'assistant', sec: 'workers', title: 'Background workers', keys: 'delegate delegation concurrent queue tool rounds' },
  { tab: 'assistant', sec: 'coding', title: 'Coding sessions', keys: 'opencode claude code repo git coding agent', ext: 'coding' },
  { tab: 'assistant', sec: 'browser', title: 'Browser', keys: 'web pages visible session sites access' },
  { tab: 'assistant', sec: 'checks', title: 'Checks and commands', keys: 'gates test lint build verify commands' },
  { tab: 'extensions', sec: 'runtime', title: 'Runtime', keys: 'runtime sandbox router client tool source runtime slot where tools run', ext: RUNTIME_EXT },
  { tab: 'assistant', sec: 'learning', title: 'Learning', keys: 'learn from chats writing style remember profile' },
  { tab: 'assistant', sec: 'search', title: 'Memory search and index', keys: 'embedding rebuild index retrieval vectors passages hybrid reranker chunks' },
  { tab: 'assistant', sec: 'map', title: 'Map', keys: 'map graph connections knowledge related notes' },
  { tab: 'assistant', sec: 'compaction', title: 'Compaction', keys: 'compaction auto compact compact summarize context window micro micro compact keep recent tool defer defer tools tokens window long chats' },
  { tab: 'assistant', sec: 'inspector', title: 'Context inspector', keys: 'context inspector what the model sees tokens blocks prompt breakdown size' },
  { tab: 'assistant', sec: 'memories', title: 'Memories', keys: 'memories memory saved facts remember list edit forget' },
  { tab: 'permissions', sec: 'mode', title: 'Permission mode', keys: 'auto manual allow everything approvals ask risky reviewer' },
  { tab: 'permissions', sec: 'presets', title: 'Permission presets', keys: 'preset presets bundle apply duplicate export import read only manual auto allow everything project chat scope diff' },
  { tab: 'permissions', sec: 'safety', title: 'Run safety', keys: 'snapshot undo checkpoint safety' },
  { tab: 'permissions', sec: 'tools', title: 'Per-tool access', keys: 'tools on off ask hide disable' },
  { tab: 'permissions', sec: 'always-ask', title: 'Always ask first', keys: 'confirm cards rules deny allow grants approvals revoke' },
  { tab: 'permissions', sec: 'file-edits', title: 'File edits', keys: 'diff review agentic editing apply' },
  { tab: 'permissions', sec: 'network', title: 'Network for commands', keys: 'shell hosts allow list sandbox internet domains' },
  { tab: 'permissions', sec: 'system', title: 'System access', keys: 'macos accessibility screen recording automation folders privacy permission' },
  { tab: 'extensions', sec: 'extensions', title: 'Extensions', keys: 'review proposed assistant drafted install enable disable permissions diff accept reject' },
  { tab: 'integrations', sec: 'accounts', title: 'Mail and Calendar account', keys: 'google gmail calendar sign in oauth client outbox undo send' },
  { tab: 'integrations', sec: 'connectors', title: 'Connectors', keys: 'mcp library tools planner mail' },
  { tab: 'integrations', sec: 'web-search', title: 'Web search', keys: 'firecrawl brave tavily exa searxng github token reader api key' },
  { tab: 'integrations', sec: 'texting', title: 'Telegram bot', keys: 'texting text telegram bot pairing messages phone sms chat token botfather', ext: 'telegram' },
  { tab: 'general', sec: 'profiles', title: 'Profiles', keys: 'lean focus defaults preset bundle theme effort quiet' },
  { tab: 'general', sec: 'sidebar', title: 'Sidebar rows', keys: 'hide show lists calendar mail health library files spaces projects modules views' },
  { tab: 'advanced', sec: 'usage', title: 'Usage and cost', keys: 'tokens spend price billing calls' },
  { tab: 'advanced', sec: 'data', title: 'Data, backups and trash', keys: 'export import backup restore presets archive trash delete diagnostics support logs' },
  { tab: 'advanced', sec: 'effective', title: 'Effective configuration', keys: 'effective config dump sources where settings come from default profile preset user project chat differs json' },
  { tab: 'advanced', sec: 'developer', title: 'Developer tools', keys: 'traces otel telemetry export context preview system prompt debug extensions hooks reload latency' }
]

/** Entries whose title or keywords hold every typed word; `extra` are entries that exist only at run time (an extension's section), `off` the extensions turned off. Empty query: none. */
export function searchSettings(q: string, extra: readonly SettingsEntry[] = [], off: ReadonlySet<string> = new Set()): SettingsEntry[] {
  const words = q.toLowerCase().split(/\s+/).filter(Boolean)
  if (!words.length) return []
  return [...SETTINGS_INDEX, ...extra].filter((e) => !(e.ext && off.has(e.ext))).filter((e) => { const hay = `${e.title} ${e.keys}`.toLowerCase(); return words.every((w) => hay.includes(w)) })
}
