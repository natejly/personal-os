/**
 * The Settings → Views exceptions, read without the module catalog. A leaf on purpose: store.ts
 * needs `viewHidden`, and the catalog (modules.ts → shell/registry) imports views that import the
 * store, so reaching these through modules.ts would close an import cycle.
 */
import type { Settings } from '@shared/types'

/** A missing homeWidgets key means the card is shown. */
export const homeModuleOn = (s: Settings, key: string): boolean => s.homeWidgets?.[key] !== false
export const viewHidden = (s: Settings, view: string): boolean =>
  (s.hiddenViews ?? []).includes(view)
/** A sidebar row that is not shown: its view is turned off, or only the row is hidden (`sidebarHidden`). */
export const rowHidden = (s: Settings, key: string): boolean => viewHidden(s, key) || (s.sidebarHidden ?? []).includes(key)
