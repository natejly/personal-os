/**
 * The Settings → Views exceptions, read without the module catalog. A leaf on purpose: store.ts
 * needs `viewHidden`, and the catalog (modules.ts → shell/registry) imports views that import the
 * store, so reaching these through modules.ts would close an import cycle.
 */
import type { Settings } from '@shared/types'

/** A card is shown only when its homeWidgets key is true: the daily recap makes a model call, so it starts off. */
export const homeModuleOn = (s: Settings, key: string): boolean => s.homeWidgets?.[key] === true
export const viewHidden = (s: Settings, view: string): boolean =>
  (s.hiddenViews ?? []).includes(view)
/** A sidebar row that is not shown: its view is turned off, or only the row is hidden (`sidebarHidden`). */
export const rowHidden = (s: Settings, key: string): boolean => viewHidden(s, key) || (s.sidebarHidden ?? []).includes(key)
