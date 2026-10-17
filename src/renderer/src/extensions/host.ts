/**
 * The frontend half of the extension host (design doc section 3.5): what an extension's `activate(ui)` registers,
 * and the lists the shell reads. Every registration returns a Disposable; the lists are plain arrays because the
 * built-in extensions are activated once, at startup, and never disposed. The shell's own surfaces register through
 * the same functions under the owner `core`, so a surface reads one list whoever filled it.
 */
import type { FC, RefObject } from 'react'
import type { FullDesk, Message, Settings, WidgetKind } from '@shared/types'
import { canonicalAccelerator, shortcutForKeys } from '@shared/shortcuts'
import type { ModuleDef, ModuleNav } from '../shell/types'
import type { State, View } from '../store'
import type { WidgetDef } from '../canvas/registry'
import { viewHidden } from '../moduleToggles'
import { registerToolCard, type ToolCardDisplay, type ToolCardProps } from '../components/toolcards/registry'
import { extStore, onExtEvent, onMessageDisplay, onViewOpened, type AppEvent, type ExtStore, type HostEvents } from './events'
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

/** What a message slot item receives: the message, and whether the transcript can show context chips. */
export interface MessageSlotProps { message: Message; streaming: boolean; showContextChips: boolean }
/** `scope` is the composer's box (what a dictation or an insert is relative to); `onText` puts text in at the caret. */
export interface ComposerToolbarProps { scope: RefObject<HTMLElement>; onText: (raw: string) => void }
export interface SidePanelTabProps { conversationId?: string; desk?: FullDesk | null; fileCount: number }

/** The fixed slots and the item each takes (design doc section 6.1). */
export interface SlotItems {
  /** A row of the sidebar's view list; `order` places it among the others. */
  'sidebar.row': { view: View; label: string; description?: string; icon: JSX.Element; kind?: WidgetKind; badge?: ModuleNav['badge'] }
  /** A block of the sidebar between the view list and the project list. */
  'sidebar.section': { Component: FC }
  'composer.toolbar': { Component: FC<ComposerToolbarProps> }
  'message.header': { Component: FC<MessageSlotProps> }
  'message.footer': { Component: FC<MessageSlotProps> }
  /**
   * A card of the chat's side panel; it is listed while `shown` (default: always) and `count` is its badge. `Overlay` is drawn
   * over the chat whenever the card is listed, collapsed or not, so a window the card opens outlives the card closing.
   */
  'sidepanel.tab': { label: string; Component: FC<SidePanelTabProps>; Overlay?: FC<SidePanelTabProps>; shown?: (p: SidePanelTabProps) => boolean; count?: (p: SidePanelTabProps) => number }
}
export type SlotName = keyof SlotItems

/** A slot item: its `id` is unique within the slot, `order` sorts it (lower first), `when` hides it while false. */
export type SlotItem<K extends SlotName> = SlotItems[K] & { id: string; order: number; when?: (s: State) => boolean }
export type SlotEntry<K extends SlotName> = SlotItem<K> & { extension: string }

export interface PaletteCommandDef { id: string; label: string; hint?: string; run: () => void }
export interface KeybindingDef { id: string; keys: string; when?: (s: State) => boolean; run: () => void }
export interface SelectionActionDef { id: string; label: string; run: (text: string) => void }
export interface FenceRendererProps { code: string; streaming: boolean }

export interface UiApi {
  id: string
  /** A view the user can turn off under Settings → Sidebar. An id other than a built-in one is `ext:<id>`. */
  view(def: ViewDef): Disposable
  /** A Settings section of its own, listed while the extension is on. */
  settingsPanel(def: SettingsPanelDef): Disposable
  slot<K extends SlotName>(slot: K, item: SlotItem<K>): Disposable
  /** A palette entry, listed while the extension is on. */
  command(def: PaletteCommandDef): Disposable
  /** Throws when `keys` collide with a core shortcut or another binding. */
  keybinding(def: KeybindingDef): Disposable
  toolCard(toolName: string, Component: FC<ToolCardProps>, display?: ToolCardDisplay): Disposable
  fenceRenderer(lang: string, Component: FC<FenceRendererProps>): Disposable
  selectionAction(def: SelectionActionDef): Disposable
  /** A canvas window kind, `ext:<id>:<name>` and declared in the manifest's `canvas.widget` slot; offered while the extension is on. */
  widget(def: WidgetDef): Disposable
  /** This extension's slice of the app store; `ext:<id>:<name>` events from its backend half land in it under `<name>`. */
  store: ExtStore
  /** `ext:<id>:<name>` is the `ui.event` hook for that name. */
  on(event: 'ui.view.opened', fn: (e: HostEvents['ui.view.opened']) => void): Disposable
  on(event: 'ui.message.display', fn: (e: HostEvents['ui.message.display']) => string | void): Disposable
  on(event: `ext:${string}` | AppEvent, fn: (data: unknown) => void): Disposable
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

/** The owner of the shell's own registrations; it is never turned off. */
export const CORE = 'core'

/** Whether what `owner` registered shows: the shell's own always does, an extension's while the user has it on. */
export const ownerOn = (settings: Settings, owner: string): boolean => owner === CORE || !viewHidden(settings, owner)

export const extensionModules: ModuleDef[] = []
export const settingsPanels: SettingsPanel[] = []
const slots: Record<SlotName, unknown[]> = {
  'sidebar.row': [], 'sidebar.section': [], 'composer.toolbar': [], 'message.header': [], 'message.footer': [], 'sidepanel.tab': []
}
export interface OwnedPaletteCommand extends PaletteCommandDef { extension: string }
export interface OwnedKeybinding extends KeybindingDef { extension: string }
export interface OwnedSelectionAction extends SelectionActionDef { extension: string }
export const paletteCommands: OwnedPaletteCommand[] = []
export const keybindings: OwnedKeybinding[] = []
export const selectionActions: OwnedSelectionAction[] = []
const fences = new Map<string, FC<FenceRendererProps>>()
/** The canvas catalog: every widget kind a window can host, whoever registered it. */
export const WIDGETS = {} as Record<WidgetKind, WidgetDef>
const widgetOwners = new Map<WidgetKind, string>()

function add<T>(list: T[], item: T): Disposable {
  list.push(item)
  return { dispose: () => { const i = list.indexOf(item); if (i >= 0) list.splice(i, 1) } }
}

/** Add to a list whose entries have a unique `id`; a repeat is a bug in the registering code. */
function assertUnique(list: { id: string }[], what: string, id: string): void {
  if (list.some((x) => x.id === id)) throw new Error(`${what} "${id}" is already registered`)
}

function addUnique<T extends { id: string }>(list: T[], what: string, item: T): Disposable {
  assertUnique(list, what, item.id)
  return add(list, item)
}

/** The items of a slot, sorted by `order`; on a tie the earlier registration comes first. */
export const slotItems = <K extends SlotName>(slot: K): SlotEntry<K>[] => slots[slot] as SlotEntry<K>[]

export function registerSlot<K extends SlotName>(owner: string, slot: K, item: SlotItem<K>): Disposable {
  const list = slotItems(slot)
  const entry: SlotEntry<K> = Object.assign({}, item, { extension: owner })
  assertUnique(list, `slot ${slot} item`, entry.id)
  const after = list.findIndex((x) => x.order > entry.order)
  list.splice(after < 0 ? list.length : after, 0, entry)
  return { dispose: () => { const i = list.indexOf(entry); if (i >= 0) list.splice(i, 1) } }
}

export const registerCommand = (owner: string, def: PaletteCommandDef): Disposable =>
  addUnique(paletteCommands, 'palette command', { ...def, extension: owner })

export function registerKeybinding(owner: string, def: KeybindingDef): Disposable {
  const core = shortcutForKeys(def.keys)
  if (core) throw new Error(`keybinding "${def.id}" collides with the core shortcut "${core.label}"`)
  const canonical = canonicalAccelerator(def.keys)
  const other = keybindings.find((k) => canonicalAccelerator(k.keys) === canonical)
  if (other) throw new Error(`keybinding "${def.id}" collides with the keybinding "${other.id}"`)
  return addUnique(keybindings, 'keybinding', { ...def, extension: owner })
}

export const registerSelectionAction = (owner: string, def: SelectionActionDef): Disposable =>
  addUnique(selectionActions, 'selection action', { ...def, extension: owner })

export function registerFenceRenderer(lang: string, Component: FC<FenceRendererProps>): Disposable {
  if (fences.has(lang)) throw new Error(`a fence renderer for "${lang}" is already registered`)
  fences.set(lang, Component)
  return { dispose: () => { if (fences.get(lang) === Component) fences.delete(lang) } }
}

export function registerWidget(owner: string, def: WidgetDef): Disposable {
  if (WIDGETS[def.kind]) throw new Error(`a widget of kind "${def.kind}" is already registered`)
  WIDGETS[def.kind] = def
  widgetOwners.set(def.kind, owner)
  return { dispose: () => { if (WIDGETS[def.kind] === def) { delete WIDGETS[def.kind]; widgetOwners.delete(def.kind) } } }
}

/** Whether the kind can be added to a canvas: the shell's own always, an extension's while the user has it on. */
export const widgetOn = (settings: Settings, kind: WidgetKind): boolean => ownerOn(settings, widgetOwners.get(kind) ?? CORE)

export const fenceRenderer = (lang: string): FC<FenceRendererProps> | undefined => fences.get(lang)

/**
 * Run an extension's `activate`. Each registration is also kept in the extension's scope, so the returned Disposable
 * undoes all of them, newest first, whether or not the extension disposed them itself.
 */
export function activateExtension(id: string, ext: ExtensionModule): Disposable {
  const scope: Disposable[] = []
  const keep = (d: Disposable): Disposable => { scope.push(d); return d }
  ext.activate({
    id,
    view: (def) => {
      const view = add(extensionModules, {
        key: id, label: def.label, description: def.description, icon: def.icon, widget: def.widget,
        view: { id: def.id, Component: def.Component, optional: true }
      })
      const row = def.nav && registerSlot(id, 'sidebar.row', { id: def.id, order: def.nav.order, view: def.id, label: def.label, description: def.description, icon: def.icon, kind: def.widget?.kind, badge: def.nav.badge })
      return keep({ dispose: () => { view.dispose(); row?.dispose() } })
    },
    settingsPanel: (def) => keep(add(settingsPanels, { ...def, tab: `ext:${id}:${def.id}`, extension: id })),
    slot: (slot, item) => keep(registerSlot(id, slot, item)),
    command: (def) => keep(registerCommand(id, def)),
    keybinding: (def) => keep(registerKeybinding(id, def)),
    toolCard: (name, Component, display) => keep(registerToolCard(name, Component, display)),
    fenceRenderer: (lang, Component) => keep(registerFenceRenderer(lang, Component)),
    selectionAction: (def) => keep(registerSelectionAction(id, def)),
    widget: (def) => keep(registerWidget(id, def)),
    store: extStore(id),
    on: ((event: string, fn: never) => keep(
      event === 'ui.view.opened' ? onViewOpened(fn) : event === 'ui.message.display' ? onMessageDisplay(fn) : onExtEvent(event, fn)
    )) as UiApi['on'],
    theme: (tokens, opts) => keep(addTheme(tokens, opts?.scheme))
  })
  return { dispose: () => { for (const d of scope.reverse()) d.dispose() } }
}
