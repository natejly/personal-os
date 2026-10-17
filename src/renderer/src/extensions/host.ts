/**
 * The frontend half of the extension host (design doc section 3.5): what an extension's `activate(ui)` registers,
 * and the lists the shell reads. Every registration returns a Disposable; the lists are plain arrays because the
 * built-in extensions are activated once, at startup, and never disposed.
 */
import type { FC } from 'react'
import type { ModuleDef, ModuleNav } from '../shell/types'
import type { View } from '../store'
import type { WidgetDef } from '../canvas/registry'
import { addTheme, type Scheme, type Tokens } from './theme'

export interface Disposable { dispose(): void }

export interface ViewDef {
  id: View
  label: string
  /** One line on what the view is for, shown on hover. */
  description?: string
  icon: JSX.Element
  Component: FC
  nav?: ModuleNav
  /** The canvas window that shows this view's content; its drag kind is `widget.kind`. */
  widget?: WidgetDef
}

export interface SettingsPanelDef {
  id: string
  label: string
  Component: FC
}

export interface UiApi {
  id: string
  /** A view the user can turn off under Settings → Sidebar. An id other than a built-in one is `ext:<id>`. */
  view(def: ViewDef): Disposable
  /** A Settings section of its own, listed while the extension is on. */
  settingsPanel(def: SettingsPanelDef): Disposable
  /** Set existing CSS custom properties for one colour scheme, or both when `scheme` is omitted. */
  theme(tokens: Tokens, opts?: { scheme?: Scheme }): Disposable
}

export interface ExtensionModule {
  activate(ui: UiApi): void
}

/** A registered settings panel; `tab` is its Settings section id and `extension` the id that turns it on and off. */
export interface SettingsPanel extends SettingsPanelDef {
  tab: `ext:${string}`
  extension: string
}

export const extensionModules: ModuleDef[] = []
export const settingsPanels: SettingsPanel[] = []

function add<T>(list: T[], item: T): Disposable {
  list.push(item)
  return { dispose: () => { const i = list.indexOf(item); if (i >= 0) list.splice(i, 1) } }
}

export function activateExtension(id: string, ext: ExtensionModule): void {
  ext.activate({
    id,
    view: (def) => add(extensionModules, {
      key: id, label: def.label, description: def.description, icon: def.icon, nav: def.nav, widget: def.widget,
      view: { id: def.id, Component: def.Component, optional: true }
    }),
    settingsPanel: (def) => add(settingsPanels, { ...def, tab: `ext:${id}:${def.id}`, extension: id }),
    theme: (tokens, opts) => addTheme(tokens, opts?.scheme)
  })
}
