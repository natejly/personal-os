import { Bot, Cpu, Package, Palette, ShieldCheck, SlidersHorizontal, type Icon } from '../icons'
import type { SettingsTab } from './settingsTabs'

/** The Settings rail, in order: id, label and icon. */
export const SETTINGS_SECTIONS: { id: SettingsTab; label: string; icon: Icon }[] = [
  { id: 'general', label: 'General', icon: Palette },
  { id: 'models', label: 'Models', icon: Cpu },
  { id: 'assistant', label: 'Assistant', icon: Bot },
  { id: 'permissions', label: 'Permissions', icon: ShieldCheck },
  { id: 'extensions', label: 'Extensions', icon: Package },
  { id: 'advanced', label: 'Advanced', icon: SlidersHorizontal }
]
