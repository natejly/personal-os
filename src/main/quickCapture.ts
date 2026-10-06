/**
 * Quick capture: a small always-on-top window that appends one bullet to today's daily note.
 * It loads the renderer with `?surface=capture`; the renderer posts to the backend with its own
 * token, so main only creates and toggles the window.
 */
import { BrowserWindow } from 'electron'
import { join } from 'path'
import { guardNavigation } from './navigation'
import { background, reveal } from './background'

let capture: BrowserWindow | null = null

export const toggleCapture = (): void => {
  if (capture && !capture.isDestroyed()) {
    capture.close()
    return
  }
  const w = new BrowserWindow({
    width: 420,
    height: 140,
    show: false,
    frame: false,
    resizable: false,
    alwaysOnTop: true,
    skipTaskbar: true,
    fullscreenable: false,
    backgroundColor: '#1c1c1e',
    webPreferences: { preload: join(__dirname, '../preload/index.js'), contextIsolation: true, nodeIntegration: false, sandbox: true }
  })
  capture = w
  guardNavigation(w.webContents)
  w.once('ready-to-show', () => { reveal(w); if (!background) w.focus() })
  // Losing focus dismisses it, so it never lingers behind other windows.
  w.on('blur', () => { if (!w.isDestroyed()) w.close() })
  w.on('closed', () => { if (capture === w) capture = null })
  if (process.env.ELECTRON_RENDERER_URL) void w.loadURL(`${process.env.ELECTRON_RENDERER_URL}/?surface=capture`)
  else void w.loadFile(join(__dirname, '../renderer/index.html'), { query: { surface: 'capture' } })
}
