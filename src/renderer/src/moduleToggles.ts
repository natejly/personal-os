/**
 * The Settings → Views exceptions, read without the module catalog. A leaf on purpose: store.ts
 * needs `viewHidden`, and the catalog (modules.ts → shell/registry) imports views that import the
 * store, so reaching these through modules.ts would close an import cycle.
 */
import type { Settings } from '@shared/types'

/** Matches backend llm.DEFAULT_SETTINGS. Used when settings have not loaded yet. */
export const DEFAULT_HIDDEN_VIEWS: readonly string[] = []

/** A missing homeWidgets key means the card is shown. */
export const homeModuleOn = (s: Settings, key: string): boolean => s.homeWidgets?.[key] !== false
export const viewHidden = (s: Settings, view: string): boolean =>
  (s.hiddenViews ?? [...DEFAULT_HIDDEN_VIEWS]).includes(view)
