/**
 * The Settings → Modules exceptions, read without the module catalog. A leaf on purpose: store.ts
 * needs `viewHidden`, and the catalog (modules.ts → shell/registry) imports views that import the
 * store, so reaching these through modules.ts would close an import cycle.
 */
import type { Settings } from '@shared/types'

export const homeModuleOn = (s: Settings, key: string): boolean => s.homeWidgets?.[key] !== false
export const viewHidden = (s: Settings, view: string): boolean => (s.hiddenViews ?? []).includes(view)
