/**
 * The global gather and quick-capture shortcuts. `globalShortcut.register` throws on a malformed accelerator and returns
 * false when another app already owns it, so both paths are handled and reported to the renderer.
 */
import { app, BrowserWindow, globalShortcut } from 'electron'
import { handle } from './ipc'
import { toggleGather } from './popouts'
import { toggleAsk } from './quickAsk'
import { toggleCapture } from './quickCapture'
import type { ShortcutState } from '../shared/types'

export const DEFAULT_GATHER = 'Control+Alt+Command+Space'
export const DEFAULT_CAPTURE = 'CommandOrControl+Shift+Space'
export const DEFAULT_ASK = 'Alt+Space'

let getMain: () => BrowserWindow | null = () => null

type Which = 'gather' | 'capture' | 'ask'
type Slot = { which: Which; registered: string; state: ShortcutState }
const gatherSlot: Slot = { which: 'gather', registered: '', state: { accelerator: DEFAULT_GATHER, ok: false, message: null, which: 'gather' } }
const captureSlot: Slot = { which: 'capture', registered: '', state: { accelerator: DEFAULT_CAPTURE, ok: false, message: null, which: 'capture' } }
const askSlot: Slot = { which: 'ask', registered: '', state: { accelerator: DEFAULT_ASK, ok: false, message: null, which: 'ask' } }

/** A failure is pushed as it happens; one at startup can beat the window, so Settings also pulls the state. */
const report = (s: ShortcutState): void => {
  if (s.ok) return
  const m = getMain()
  if (m && !m.isDestroyed()) m.webContents.send('shortcuts:failed', s)
}

/**
 * Registers the new accelerator before dropping the old one, so a taken or malformed choice leaves the
 * working shortcut bound and current. The failure is still returned (and reported) so the caller can show the reason.
 */
const swap = (slot: Slot, accel: string, run: () => void): ShortcutState => {
  if (accel === slot.registered) return slot.state
  let ok = false
  let message: string | null = null
  try {
    // One of Grain's own shortcuts already holds it: refuse here rather than trust each OS to.
    if ([gatherSlot, captureSlot, askSlot].some((o) => o !== slot && o.registered === accel)) throw new Error('another Grain shortcut already uses it')
    ok = globalShortcut.register(accel, run)
    if (!ok) message = `${accel} is already in use by another app. Choose a different shortcut.`
  } catch (e) {
    message = `${accel} is not a valid shortcut: ${(e as Error).message}`
  }
  const result: ShortcutState = { accelerator: accel, ok, message, which: slot.which }
  if (ok) {
    if (slot.registered) globalShortcut.unregister(slot.registered)
    slot.registered = accel
  }
  if (ok || !slot.registered) slot.state = result
  report(result)
  return result
}

const apply = (accelerator: string): ShortcutState => swap(gatherSlot, accelerator.trim() || DEFAULT_GATHER, () => void toggleGather())

/** Same failure handling as the gather accelerator: a bad or taken one reports back and never throws. */
const applyCapture = (accelerator: string): ShortcutState => swap(captureSlot, accelerator.trim() || DEFAULT_CAPTURE, toggleCapture)

const applyAsk = (accelerator: string): ShortcutState => swap(askSlot, accelerator.trim() || DEFAULT_ASK, toggleAsk)

export const gatherShortcut = (): ShortcutState => gatherSlot.state
export const askShortcut = (): ShortcutState => askSlot.state

export const registerShortcuts = (mainWindow: () => BrowserWindow | null, accelerator = DEFAULT_GATHER, captureAccelerator = DEFAULT_CAPTURE, askAccelerator = DEFAULT_ASK): void => {
  getMain = mainWindow
  handle('shortcuts:gather', () => gatherSlot.state)
  handle('shortcuts:set-gather', (_e, accel: string) => apply(accel))
  handle('shortcuts:capture', () => captureSlot.state)
  handle('shortcuts:set-capture', (_e, accel: string) => applyCapture(accel))
  handle('shortcuts:ask', () => askSlot.state)
  handle('shortcuts:set-ask', (_e, accel: string) => applyAsk(accel))
  app.on('will-quit', () => globalShortcut.unregisterAll())
  apply(accelerator)
  applyCapture(captureAccelerator)
  applyAsk(askAccelerator)
}
