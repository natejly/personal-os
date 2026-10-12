/** The Settings sections, and where the ids older code still passes to openSettings now live. Pure: no store import. */
export type SettingsTab = 'model' | 'models' | 'usage' | 'permissions' | 'integrations' | 'agents' | 'memory' | 'appearance' | 'sidebar' | 'system' | 'advanced'
/** Collapsed groups left on the Advanced tab. */
export type AdvancedGroup = 'data' | 'developer'
export type LegacySettingsTab = 'workspace' | 'provider' | 'memory' | 'cowork' | 'modules' | 'behavior' | 'data' | 'spending' | 'texting'

const LEGACY: Record<LegacySettingsTab, { tab: SettingsTab; group?: AdvancedGroup }> = {
  workspace: { tab: 'permissions' },
  provider: { tab: 'model' },
  memory: { tab: 'memory' },
  cowork: { tab: 'agents' },
  texting: { tab: 'integrations' },
  modules: { tab: 'sidebar' },
  behavior: { tab: 'model' },
  data: { tab: 'advanced', group: 'data' },
  spending: { tab: 'usage' }
}
const CURRENT: readonly string[] = ['model', 'models', 'usage', 'permissions', 'integrations', 'agents', 'memory', 'appearance', 'sidebar', 'system', 'advanced']

/** Any id, current or old, to a section (and the Advanced group to open). An unknown id lands on Model. */
export function resolveTab(id: string): { tab: SettingsTab; group?: AdvancedGroup } {
  if (CURRENT.includes(id)) return { tab: id as SettingsTab }
  return LEGACY[id as LegacySettingsTab] ?? { tab: 'model' }
}

/** One findable block of settings: the tab it lives on, its anchor id (`sec-<sec>` in the pane) and the words people type for it. */
export interface SettingsEntry { tab: SettingsTab; sec: string; title: string; keys: string }
export const SETTINGS_INDEX: readonly SettingsEntry[] = [
  { tab: 'model', sec: 'provider', title: 'Provider and model', keys: 'api key base url openai fireworks ollama litellm default model fast model setup' },
  { tab: 'model', sec: 'assistant', title: 'Assistant behaviour', keys: 'standing instructions system prompt reply style tone titles follow-up suggestions selection toolbar compact summarize' },
  { tab: 'models', sec: 'keys', title: 'API keys', keys: 'provider key openai anthropic fireworks openrouter remove saved' },
  { tab: 'models', sec: 'picker', title: 'Models in the picker', keys: 'composer model menu choose show hide checkbox reset suggested' },
  { tab: 'usage', sec: 'usage', title: 'Usage and cost', keys: 'tokens spend price billing calls' },
  { tab: 'permissions', sec: 'mode', title: 'Permission mode', keys: 'auto manual allow everything approvals ask risky reviewer' },
  { tab: 'permissions', sec: 'safety', title: 'Run safety', keys: 'snapshot undo checkpoint safety' },
  { tab: 'permissions', sec: 'tools', title: 'Per-tool access', keys: 'tools on off ask hide disable' },
  { tab: 'permissions', sec: 'always-ask', title: 'Always ask first', keys: 'confirm cards rules deny allow grants approvals revoke' },
  { tab: 'permissions', sec: 'file-edits', title: 'File edits', keys: 'diff review agentic editing apply' },
  { tab: 'permissions', sec: 'network', title: 'Network for commands', keys: 'shell hosts allow list sandbox internet domains' },
  { tab: 'integrations', sec: 'accounts', title: 'Mail and Calendar account', keys: 'google gmail calendar sign in oauth client outbox undo send' },
  { tab: 'integrations', sec: 'connectors', title: 'Connectors', keys: 'mcp library tools planner mail' },
  { tab: 'integrations', sec: 'web-search', title: 'Web search', keys: 'firecrawl brave tavily exa searxng github token reader api key' },
  { tab: 'integrations', sec: 'texting', title: 'Telegram bot', keys: 'texting text telegram bot pairing messages phone sms chat token botfather' },
  { tab: 'agents', sec: 'desks', title: 'Desks', keys: 'cowork desk folder agents workers autonomous' },
  { tab: 'agents', sec: 'workers', title: 'Background workers', keys: 'delegate delegation concurrent queue tool rounds' },
  { tab: 'agents', sec: 'coding', title: 'Coding sessions', keys: 'opencode claude code repo git coding agent' },
  { tab: 'agents', sec: 'browser', title: 'Browser', keys: 'web pages visible session sites access' },
  { tab: 'agents', sec: 'checks', title: 'Checks and commands', keys: 'gates test lint build verify commands' },
  { tab: 'memory', sec: 'learning', title: 'Learning', keys: 'learn from chats writing style remember profile' },
  { tab: 'memory', sec: 'search', title: 'Memory search and index', keys: 'embedding rebuild index retrieval vectors passages hybrid reranker chunks' },
  { tab: 'appearance', sec: 'look', title: 'Theme, accent, fonts and zoom', keys: 'preset presets dark light colour color custom accent font typeface code monospace size scale recap blobs' },
  { tab: 'appearance', sec: 'notifications', title: 'Notifications', keys: 'notify alerts unread dot jobs finish' },
  { tab: 'appearance', sec: 'shortcuts', title: 'Shortcuts and voice', keys: 'keyboard hotkey dictation microphone speech quick note quick ask gather widgets' },
  { tab: 'sidebar', sec: 'sidebar', title: 'Sidebar rows', keys: 'hide show lists calendar mail health library files spaces projects jobs modules views' },
  { tab: 'system', sec: 'system', title: 'System access', keys: 'macos accessibility screen recording automation folders privacy permission' },
  { tab: 'advanced', sec: 'data', title: 'Data, backups and trash', keys: 'export import backup restore presets archive trash delete diagnostics support logs' },
  { tab: 'advanced', sec: 'developer', title: 'Developer tools', keys: 'traces otel telemetry export context preview system prompt debug' }
]

/** Entries whose title or keywords hold every typed word. Empty query: none. */
export function searchSettings(q: string): SettingsEntry[] {
  const words = q.toLowerCase().split(/\s+/).filter(Boolean)
  if (!words.length) return []
  return SETTINGS_INDEX.filter((e) => { const hay = `${e.title} ${e.keys}`.toLowerCase(); return words.every((w) => hay.includes(w)) })
}
