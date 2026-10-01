/**
 * The global gather shortcut. `globalShortcut.register` throws on a malformed accelerator and returns
 * false when another app already owns it, so both paths are handled and reported to the renderer.
 */
import { app, BrowserWindow, globalShortcut } from 'electron'
import { handle } from './ipc'
import { toggleGather } from './popouts'
import type { ShortcutState } from '../shared/types'

export const DEFAULT_GATHER = 'Control+Alt+Command+Space'

let current: ShortcutState = { accelerator: DEFAULT_GATHER, ok: false, message: null }
let registered = ''
let getMain: () => BrowserWindow | null = () => null

const apply = (accelerator: string): ShortcutState => {
  const accel = accelerator.trim() || DEFAULT_GATHER
  if (registered) {
    globalShortcut.unregister(registered)
    registered = ''
  }
  let ok = false
  let message: string | null = null
  try {
    ok = globalShortcut.register(accel, () => void toggleGather())
    if (!ok) message = `${accel} is already in use by another app. Choose a different shortcut.`
  } catch (e) {
    message = `${accel} is not a valid shortcut: ${(e as Error).message}`
  }
  if (ok) registered = accel
  current = { accelerator: accel, ok, message }
  if (!ok) {
    const m = getMain()
    if (m && !m.isDestroyed()) m.webContents.send('shortcuts:failed', current)
  }
  return current
}

export const gatherShortcut = (): ShortcutState => current

export const registerShortcuts = (mainWindow: () => BrowserWindow | null, accelerator = DEFAULT_GATHER): void => {
  getMain = mainWindow
  handle('shortcuts:gather', () => current)
  handle('shortcuts:set-gather', (_e, accel: string) => apply(accel))
  app.on('will-quit', () => globalShortcut.unregisterAll())
  apply(accelerator)
}
