/** What the extensions need from the running app: their keybindings, and the view-opened event. Started once, by App. */
import { acceleratorOf, canonicalAccelerator } from '@shared/shortcuts'
import { useStore } from '../store'
import { emitViewOpened } from './events'
import { keybindings, ownerOn } from './host'

export function startExtensionRuntime(): () => void {
  const mac = window.os.platform === 'darwin'
  const onKey = (e: KeyboardEvent): void => {
    if (e.defaultPrevented) return
    const chord = acceleratorOf(e, mac)
    const state = useStore.getState()
    const hit = keybindings.find((k) => canonicalAccelerator(k.keys, mac) === chord && ownerOn(state.settings, k.extension) && (!k.when || k.when(state)))
    if (!hit) return
    e.preventDefault()
    hit.run()
  }
  window.addEventListener('keydown', onKey)
  const unsubscribe = useStore.subscribe((s, prev) => { if (s.view !== prev.view) emitViewOpened(s.view) })
  return () => {
    window.removeEventListener('keydown', onKey)
    unsubscribe()
  }
}
