/**
 * Quick ask: a frameless always-on-top bar that starts a new chat. It loads the renderer with `?surface=ask`;
 * the renderer talks to the backend with its own token, so main only owns the window, the clipboard read
 * (renderers never read it themselves) and the hand-off to the main window.
 */
import { BrowserWindow, clipboard } from 'electron'
import { join } from 'path'
import { guardNavigation } from './navigation'
import { handle } from './ipc'

const WIDTH = 560
const MIN_H = 76
const MAX_H = 640

let ask: BrowserWindow | null = null

export const toggleAsk = (): void => {
  if (ask && !ask.isDestroyed()) {
    ask.close()
    return
  }
  const w = new BrowserWindow({
    width: WIDTH,
    height: MIN_H,
    show: false,
    frame: false,
    resizable: false,
    alwaysOnTop: true,
    skipTaskbar: true,
    fullscreenable: false,
    backgroundColor: '#1c1c1e',
    webPreferences: { preload: join(__dirname, '../preload/index.js'), contextIsolation: true, nodeIntegration: false, sandbox: true }
  })
  ask = w
  guardNavigation(w.webContents)
  w.once('ready-to-show', () => { w.show(); w.focus() })
  // Losing focus dismisses it. A reply in flight keeps running in the backend and is in the chat list.
  w.on('blur', () => { if (!w.isDestroyed()) w.close() })
  w.on('closed', () => { if (ask === w) ask = null })
  if (process.env.ELECTRON_RENDERER_URL) void w.loadURL(`${process.env.ELECTRON_RENDERER_URL}/?surface=ask`)
  else void w.loadFile(join(__dirname, '../renderer/index.html'), { query: { surface: 'ask' } })
}

/** Only the bar's own window may use these: any other renderer has no business with the clipboard. */
export const registerQuickAsk = (openChat: (conversationId: string) => void): void => {
  const mine = (e: Electron.IpcMainInvokeEvent): boolean => !!ask && !ask.isDestroyed() && e.sender === ask.webContents
  handle('quickask:clipboard', (e) => (mine(e) ? clipboard.readText() : ''))
  handle('quickask:resize', (e, height: number) => {
    if (!mine(e) || !ask || !Number.isFinite(height)) return
    ask.setContentSize(WIDTH, Math.round(Math.min(MAX_H, Math.max(MIN_H, height))))
  })
  handle('quickask:open', (e, conversationId: string) => {
    if (!mine(e) || typeof conversationId !== 'string' || !/^[\w-]{1,80}$/.test(conversationId)) return
    ask?.close()
    openChat(conversationId)
  })
}
