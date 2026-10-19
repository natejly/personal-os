import { Bot, Brain, Cpu, Gauge, Package, Palette, PanelLeft, Plug, ShieldCheck, SlidersHorizontal, Zap, type Icon } from '../icons'
import type { SettingsTab } from './settingsTabs'

/** The Settings rail, in order: id, label, icon and a one-sentence description of what the section holds. */
export const SETTINGS_SECTIONS: { id: SettingsTab; label: string; icon: Icon; description: string }[] = [
  { id: 'general', label: 'General', icon: Palette, description: 'Theme, fonts, notifications, shortcuts and voice.' },
  { id: 'models', label: 'Models', icon: Cpu, description: 'Providers, the models each tier uses and how the assistant replies.' },
  { id: 'agent', label: 'Agent', icon: Bot, description: 'How long the agent loop may run, plus desks, workers, coding sessions, the browser and checks.' },
  { id: 'context', label: 'Context and memory', icon: Brain, description: 'What the assistant remembers, how memory is searched and how long chats are compacted.' },
  { id: 'permissions', label: 'Permissions', icon: ShieldCheck, description: 'Approvals, per-tool access, file edits, network rules and system access.' },
  { id: 'extensions', label: 'Extensions', icon: Package, description: 'Review, enable and configure extensions.' },
  { id: 'hooks', label: 'Hooks and automations', icon: Zap, description: 'Event hooks and scheduled background jobs.' },
  { id: 'integrations', label: 'Integrations', icon: Plug, description: 'Mail and calendar accounts, connectors, web search and messaging.' },
  { id: 'layout', label: 'Sidebar and layout', icon: PanelLeft, description: 'Profiles and which rows and views the sidebar shows.' },
  { id: 'usage', label: 'Usage', icon: Gauge, description: 'Tokens, calls and what they cost.' },
  { id: 'advanced', label: 'Advanced', icon: SlidersHorizontal, description: 'Data and backups, the effective configuration and developer tools.' }
]
