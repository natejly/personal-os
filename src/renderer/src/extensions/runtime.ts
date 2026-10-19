/** What the extensions need from the running app: their keybindings and menu items, and the view-opened event. Started once, by App. */
import { extensionMenuAction, type ExtensionMenuItem } from '@shared/extensionMenu'
import { acceleratorOf, canonicalAccelerator } from '@shared/shortcuts'
import { api } from '../lib/api'
import { useStore } from '../store'
import { emitViewOpened } from './events'
import { keybindings, menuItems, ownerOn } from './host'

/** The menu items of the extensions that are on, as the main process lists them. */
const visibleMenuItems = (): ExtensionMenuItem[] =>
  menuItems.filter((m) => ownerOn(useStore.getState().settings, m.extension)).map(({ id, label, group }) => ({ id, label, group }))

export function startExtensionRuntime(): () => void {
  const mac = window.os.platform === 'darwin'
  const onKey = (e: KeyboardEvent): void => {
    if (e.defaultPrevented) return
    // A plain or Shift key in a text field is typing, not a shortcut.
    if (!(e.ctrlKey || e.metaKey || e.altKey) && (e.target as HTMLElement | null)?.closest?.('input, textarea, select, [contenteditable]')) return
    const chord = acceleratorOf(e, mac)
    const state = useStore.getState()
    const hit = keybindings.find((k) => canonicalAccelerator(k.keys, mac) === chord && ownerOn(state.settings, k.extension) && (!k.when || k.when(state)))
    if (!hit) return
    e.preventDefault()
    hit.run()
  }
  window.addEventListener('keydown', onKey)
  const unsubscribe = useStore.subscribe((s, prev) => {
    if (s.view === prev.view) return
    emitViewOpened(s.view)
    void api.extensions.activate(`onView:${s.view}`).catch(() => undefined)  // an extension whose code waits for this view starts now
  })
  const stopMenu = window.os.onMenu((action) => {
    menuItems.find((m) => extensionMenuAction(m.id) === action && ownerOn(useStore.getState().settings, m.extension))?.run()
  })
  let listed = ''
  const publishMenu = (): void => {
    const items = visibleMenuItems()
    const key = JSON.stringify(items)
    if (key === listed) return
    listed = key
    void window.os.setMenuItems(items)
  }
  publishMenu()
  const unsubscribeMenu = useStore.subscribe(publishMenu)
  return () => {
    window.removeEventListener('keydown', onKey)
    unsubscribe()
    stopMenu()
    unsubscribeMenu()
  }
}
