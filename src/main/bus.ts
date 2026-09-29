/** Optimistic cross-window hints: renderer -> main -> every other live renderer. */
import { BrowserWindow, ipcMain } from 'electron'
import type { BusMessage } from '../shared/types'

export const registerBus = (): void => {
  ipcMain.on('bus', (e, msg: BusMessage) => {
    for (const w of BrowserWindow.getAllWindows()) {
      if (w.isDestroyed()) continue
      const wc = w.webContents
      if (wc.isDestroyed() || wc.id === e.sender.id) continue
      wc.send('bus', msg)
    }
  })
}
