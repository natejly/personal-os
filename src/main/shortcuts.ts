/**
 * The global gather shortcut. `globalShortcut.register` throws on a malformed accelerator and returns
 * false when another app already owns it, so both paths are handled and reported to the renderer.
 */
import { app, BrowserWindow, globalShortcut } from 'electron'
import { handle } from './ipc'
import { toggleGather } from './popouts'
import { toggleCapture } from './quickCapture'
import type { ShortcutState } from '../shared/types'

export const DEFAULT_GATHER = 'Control+Alt+Command+Space'
export const DEFAULT_CAPTURE = 'CommandOrControl+Shift+Space'

let getMain: () => BrowserWindow | null = () => null

type Slot = { registered: string; state: ShortcutState }
const gatherSlot: Slot = { registered: '', state: { accelerator: DEFAULT_GATHER, ok: false, message: null } }
const captureSlot: Slot = { registered: '', state: { accelerator: DEFAULT_CAPTURE, ok: false, message: null } }

/**
 * Registers the new accelerator before dropping the old one, so a taken or malformed choice leaves the
 * working shortcut bound and current. The failure is still returned so the caller can show the reason.
 */
const swap = (slot: Slot, accel: string, run: () => void): ShortcutState => {
  if (accel === slot.registered) return slot.state
  let ok = false
  let message: string | null = null
  try {
    ok = globalShortcut.register(accel, run)
    if (!ok) message = `${accel} is already in use by another app. Choose a different shortcut.`
  } catch (e) {
    message = `${accel} is not a valid shortcut: ${(e as Error).message}`
  }
  const result = { accelerator: accel, ok, message }
  if (ok) {
    if (slot.registered) globalShortcut.unregister(slot.registered)
    slot.registered = accel
  }
  if (ok || !slot.registered) slot.state = result
  return result
}

const apply = (accelerator: string): ShortcutState => {
  const result = swap(gatherSlot, accelerator.trim() || DEFAULT_GATHER, () => void toggleGather())
  if (!result.ok) {
    const m = getMain()
    if (m && !m.isDestroyed()) m.webContents.send('shortcuts:failed', result)
  }
  return result
}

/** Same failure handling as the gather accelerator: a bad or taken one reports back and never throws. */
const applyCapture = (accelerator: string): ShortcutState => {
  return swap(captureSlot, accelerator.trim() || DEFAULT_CAPTURE, toggleCapture)
}

export const gatherShortcut = (): ShortcutState => gatherSlot.state

export const registerShortcuts = (mainWindow: () => BrowserWindow | null, accelerator = DEFAULT_GATHER, captureAccelerator = DEFAULT_CAPTURE): void => {
  getMain = mainWindow
  handle('shortcuts:gather', () => gatherSlot.state)
  handle('shortcuts:set-gather', (_e, accel: string) => apply(accel))
  handle('shortcuts:capture', () => captureSlot.state)
  handle('shortcuts:set-capture', (_e, accel: string) => applyCapture(accel))
  app.on('will-quit', () => globalShortcut.unregisterAll())
  apply(accelerator)
  applyCapture(captureAccelerator)
}
