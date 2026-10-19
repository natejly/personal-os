import { Bot, Brain, Cpu, Gauge, Package, Palette, PanelLeft, Plug, ShieldCheck, SlidersHorizontal, Zap, type Icon } from '../icons'
import type { SettingsTab } from './settingsTabs'

/** The Settings rail, in order: id, label and icon. */
export const SETTINGS_SECTIONS: { id: SettingsTab; label: string; icon: Icon }[] = [
  { id: 'general', label: 'General', icon: Palette },
  { id: 'models', label: 'Models', icon: Cpu },
  { id: 'agent', label: 'Agent', icon: Bot },
  { id: 'context', label: 'Context and memory', icon: Brain },
  { id: 'permissions', label: 'Permissions', icon: ShieldCheck },
  { id: 'extensions', label: 'Extensions', icon: Package },
  { id: 'hooks', label: 'Hooks and automations', icon: Zap },
  { id: 'integrations', label: 'Integrations', icon: Plug },
  { id: 'layout', label: 'Sidebar and layout', icon: PanelLeft },
  { id: 'usage', label: 'Usage', icon: Gauge },
  { id: 'advanced', label: 'Advanced', icon: SlidersHorizontal }
]
