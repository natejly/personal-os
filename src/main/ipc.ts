/** Sender-validated ipcMain wrappers: only the app's own renderer page may call the main process. */
import { ipcMain, type IpcMainEvent, type IpcMainInvokeEvent } from 'electron'
import { isAppUrl } from './appUrl'

/** Top frame of a window we created, loaded from our own renderer. Webviews and iframes are refused. */
export function trustedSender(e: IpcMainEvent | IpcMainInvokeEvent): boolean {
  const frame = e.senderFrame
  if (!frame || frame !== e.sender.mainFrame) return false
  if (e.sender.getType() !== 'window') return false
  return isAppUrl(frame.url)
}

export function handle(channel: string, fn: (e: IpcMainInvokeEvent, ...args: any[]) => unknown): void {
  ipcMain.handle(channel, (e, ...args) => {
    if (!trustedSender(e)) throw new Error(`ipc ${channel}: untrusted sender`)
    return fn(e, ...args)
  })
}

export function on(channel: string, fn: (e: IpcMainEvent, ...args: any[]) => void): void {
  ipcMain.on(channel, (e, ...args) => {
    if (!trustedSender(e)) return console.error(`[ipc] ${channel}: untrusted sender refused`)
    fn(e, ...args)
  })
}
