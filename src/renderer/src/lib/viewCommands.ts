import { useEffect, useRef } from 'react'
import { CORE, registerCommand } from '../extensions/host'
import { useStore } from '../store'
import { FILES_KINDS } from './chatFiles'

/** Palette commands that set the theme at once. Imported for its side effect by App. */
for (const [id, label, theme] of [['light', 'Theme: Light', 'light'], ['dark', 'Theme: Dark', 'dark'], ['system', 'Theme: System', 'system']] as const) {
  registerCommand(CORE, { id: `theme:${id}`, label, section: 'Settings', run: () => void useStore.getState().saveSettings({ theme }) })
}

/**
 * A palette command aimed at a view that may not be mounted yet. The request is held until the view
 * takes it, on mount or when it is already on screen.
 */
const pending = new Set<string>()
const EVENT = 'grain:view-command'

export function requestViewCommand(name: string): void {
  pending.add(name)
  window.dispatchEvent(new CustomEvent(EVENT, { detail: name }))
}

/** Takes a held request for `name`, true when there was one. For a view that folds it into its own mount load. */
export const takeViewCommand = (name: string): boolean => pending.delete(name)

/**
 * Runs `fn` once per request for `name`, including one made before this view mounted. While `ready` is
 * false (not connected, not hydrated) the request stays held and runs when it turns true.
 */
export function useViewCommand(name: string, fn: () => void, ready = true): void {
  const ref = useRef(fn)
  ref.current = fn
  useEffect(() => {
    if (!ready) return
    const take = (): void => { if (pending.delete(name)) ref.current() }
    take()
    const on = (e: Event): void => { if ((e as CustomEvent<string>).detail === name) take() }
    window.addEventListener(EVENT, on)
    return (): void => window.removeEventListener(EVENT, on)
  }, [name, ready])
}

// ---- Files commands in the palette ----
// Registered here, at import, so the palette lists them before the Files view has ever mounted.
const files = (): ReturnType<typeof useStore.getState> => useStore.getState()
registerCommand(CORE, { id: 'files:upload', label: 'Upload file', section: 'Files', run: () => { files().showFiles({ kind: 'uploads' }); requestViewCommand('files:upload') } })
registerCommand(CORE, { id: 'files:today', label: "Today's note", section: 'Files', run: () => { files().showFiles({ kind: 'notes' }); void files().openDailyNote() } })
registerCommand(CORE, { id: 'files:trash', label: 'Trash', section: 'Files', run: () => files().showFiles({ kind: 'trash' }) })
registerCommand(CORE, {
  id: 'files:this-chat', label: 'Files in this chat', section: 'Files',
  when: (s) => s.view === 'chat' && !!s.focusedConversationId,
  run: () => useStore.setState((st) => ({ contextOpen: false, deskPanel: true, deskTabRequest: { tab: 'files', n: (st.deskTabRequest?.n ?? 0) + 1, at: Date.now() } }))
})
for (const k of FILES_KINDS) registerCommand(CORE, { id: `files:kind:${k.id}`, label: `Files: ${k.label}`, section: 'Files', run: () => files().showFiles({ kind: k.id }) })
