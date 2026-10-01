import { app, BrowserWindow, ipcMain, Menu, shell } from 'electron'
import { existsSync } from 'fs'
import { join } from 'path'
import { backendInfo, backendStatus, backendToken, backendUrl, onBackendState, restartBackend, startBackend, stopBackend } from './backend'
import { registerBus } from './bus'
import { hookConsole, initLogs, logDir } from './logging'
import { guardNavigation } from './navigation'
import { startPageBridge, stopPageBridge } from './pagefetch'
import { gather, OPACITY_LEVELS, registerPopouts, restorePopouts, setFrontListener, toggleFront } from './popouts'
import { registerShortcuts } from './shortcuts'
import { createTray } from './tray'

let win: BrowserWindow | null = null
const isMac = process.platform === 'darwin'

// The app was renamed from "Personal OS" to "Grain", which moves the userData directory Electron
// derives from the app name. Existing installs keep their data: if the new location has none but a
// legacy one does, keep using the legacy directory. Must run before anything touches userData.
for (const legacy of ['personal-os', 'Personal OS']) {
  const legacyDir = join(app.getPath('appData'), legacy)
  if (!existsSync(join(app.getPath('userData'), 'data')) && existsSync(join(legacyDir, 'data'))) {
    app.setPath('userData', legacyDir)
    break
  }
}

// Rotating logs for this process and the backend's raw output: ~/Library/Logs/Grain when packaged,
// <userData>/logs in dev. The backend writes its own backend.log into the same folder (PERSONAL_OS_LOG_DIR).
initLogs(app.isPackaged ? app.getPath('logs') : join(app.getPath('userData'), 'logs'))
hookConsole()

function createWindow(): void {
  win = new BrowserWindow({
    width: 1280,
    height: 820,
    minWidth: 820,
    minHeight: 520,
    show: false,
    title: 'Grain',
    titleBarStyle: isMac ? 'hiddenInset' : 'default',
    trafficLightPosition: { x: 16, y: 16 },
    vibrancy: isMac ? 'sidebar' : undefined,
    visualEffectState: 'active',
    backgroundColor: '#00000000',
    webPreferences: {
      preload: join(__dirname, '../preload/index.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
      webviewTag: true // the web widget; guests are stripped in guardNavigation's will-attach-webview
    }
  })

  win.once('ready-to-show', () => win?.show())

  // When the renderer dies there is no React error and no macOS crash report -- the window simply goes
  // blank, and because it is transparent that looks like the app vanishing. These say why.
  win.webContents.on('render-process-gone', (_e, details) => {
    console.error(`[renderer] gone: reason=${details.reason} exitCode=${details.exitCode}`)
  })
  win.webContents.on('unresponsive', () => console.error('[renderer] unresponsive (main thread blocked)'))
  win.webContents.on('responsive', () => console.error('[renderer] responsive again'))
  win.webContents.on('console-message', (_e, level, message, line, source) => {
    if (level >= 2) console.error(`[renderer console] ${message}  (${source}:${line})`)
  })
  guardNavigation(win.webContents)

  if (process.env.ELECTRON_RENDERER_URL) {
    void win.loadURL(process.env.ELECTRON_RENDERER_URL)
  } else {
    void win.loadFile(join(__dirname, '../renderer/index.html'))
  }
}

/** A live pop-out keeps getAllWindows() non-empty, which used to make the main window unrecoverable. */
function showMain(): void {
  if (!win || win.isDestroyed()) return createWindow()
  if (win.isMinimized()) win.restore()
  win.show()
  win.focus()
}

/** The stored accelerator, so a gather shortcut the user chose is still registered after a relaunch. */
async function storedGather(): Promise<string | undefined> {
  const base = backendUrl()
  if (!base) return undefined
  try {
    const r = await fetch(`${base}/settings`)
    if (!r.ok) return undefined
    const s = (await r.json()) as { gatherShortcut?: string }
    return s.gatherShortcut?.trim() || undefined
  } catch {
    return undefined
  }
}

/**
 * A menu click must never throw in the main process: an unhandled throw here takes the whole app down
 * with no renderer crash report and no React error, which is indistinguishable from "it just vanished".
 * `isDestroyed()` on the BrowserWindow is not enough -- the render frame can be disposed while the
 * window object is alive ("Render frame was disposed before WebFrameMain could be accessed"), and it
 * can be disposed between the check and the send, so the try/catch is load-bearing, not belt-and-braces.
 */
const deliver = (target: BrowserWindow | null, action: string): void => {
  if (!target || target.isDestroyed()) return
  const wc = target.webContents
  if (!wc || wc.isDestroyed()) return
  try {
    wc.send('menu', action)
  } catch (e) {
    console.warn(`[menu] could not deliver "${action}":`, (e as Error).message)
  }
}

const sendMenu = (action: string): void => deliver(win, action)

/**
 * Window-scoped actions go to whoever has focus, not to the main window: a pop-out must answer ⌘W and
 * its own pin itself. App-wide actions keep using `sendMenu`, which the canvas only ever hosts.
 */
const sendWindowMenu = (action: string): void => {
  deliver(BrowserWindow.getFocusedWindow() ?? win, action)
}

const SPACES: Electron.MenuItemConstructorOptions[] = Array.from({ length: 9 }, (_, i) => ({
  label: `Space ${i + 1}`,
  accelerator: `Control+${i + 1}`,
  click: () => sendMenu(`canvas:space:${i + 1}`)
}))

function buildMenu(): void {
  const template: Electron.MenuItemConstructorOptions[] = [
    ...(isMac
      ? [
          {
            label: app.name,
            submenu: [
              { role: 'about' as const },
              { type: 'separator' as const },
              { label: 'Settings…', accelerator: 'CmdOrCtrl+,', click: () => sendMenu('settings') },
              { type: 'separator' as const },
              { role: 'hide' as const },
              { role: 'quit' as const }
            ]
          }
        ]
      : []),
    {
      label: 'File',
      submenu: [
        { label: 'New Chat', accelerator: 'CmdOrCtrl+N', click: () => sendMenu('new-chat') },
        { label: 'Upload Document…', accelerator: 'CmdOrCtrl+U', click: () => sendMenu('upload') },
        // ⌘W lives in the Window menu now: `role: 'close'` here could not be intercepted by the canvas.
        ...(isMac
          ? []
          : [
              { type: 'separator' as const },
              { label: 'Settings…', accelerator: 'CmdOrCtrl+,', click: () => sendMenu('settings') },
              { role: 'quit' as const }
            ])
      ]
    },
    { role: 'editMenu' },
    {
      label: 'View',
      submenu: [
        { label: 'Today', accelerator: 'CmdOrCtrl+0', click: () => sendMenu('view:home') },
        { label: 'Chats', accelerator: 'CmdOrCtrl+1', click: () => sendMenu('view:chat') },
        { label: 'Todos', accelerator: 'CmdOrCtrl+2', click: () => sendMenu('view:todos') },
        { label: 'Calendar', accelerator: 'CmdOrCtrl+3', click: () => sendMenu('view:calendar') },
        { label: 'Boards', accelerator: 'CmdOrCtrl+4', click: () => sendMenu('view:boards') },
        { label: 'Dashboards', accelerator: 'CmdOrCtrl+5', click: () => sendMenu('view:dashboards') },
        { label: 'Memory', accelerator: 'CmdOrCtrl+6', click: () => sendMenu('view:memory') },
        { label: 'Memory: Knowledge Graph', accelerator: 'CmdOrCtrl+7', click: () => sendMenu('view:graph') },
        { label: 'Documents', accelerator: 'CmdOrCtrl+8', click: () => sendMenu('view:documents') },
        { label: 'Activity', accelerator: 'CmdOrCtrl+9', click: () => sendMenu('view:activity') },
        // ⌘0..⌘9 are all taken above and ⌘M is Minimize in the Window menu, so Meetings takes ⌘⇧M.
        { label: 'Meetings', accelerator: 'CmdOrCtrl+Shift+M', click: () => sendMenu('view:meetings') },
        { label: 'Cowork', accelerator: 'CmdOrCtrl+Shift+K', click: () => sendMenu('view:cowork') },
        { type: 'separator' },
        { label: 'Toggle Spaces', accelerator: 'CmdOrCtrl+Shift+C', click: () => sendMenu('canvas:toggle') },
        { label: 'Toggle Sidebar', accelerator: 'CmdOrCtrl+B', click: () => sendMenu('toggle-sidebar') },
        // ⌘I is the Cursor reflex: ask about what is on screen. The chat's context inspector, which
        // used to own it, moves one modifier over.
        { label: 'Ask About This Page', accelerator: 'CmdOrCtrl+I', click: () => sendMenu('page-agent') },
        { label: 'Toggle Context Panel', accelerator: 'Control+Command+I', click: () => sendMenu('toggle-context') },
        { type: 'separator' },
        { role: 'reload' },
        { role: 'toggleDevTools' },
        { type: 'separator' },
        // Explicit, because ⌘0 is Today above and resetZoom's default would have been the dead duplicate.
        { role: 'resetZoom', accelerator: 'CmdOrCtrl+Alt+0' },
        { role: 'zoomIn' },
        { role: 'zoomOut' },
        { type: 'separator' },
        { role: 'togglefullscreen' }
      ]
    },
    {
      label: 'Spaces',
      submenu: [
        { label: 'New Space', accelerator: 'Control+Command+N', click: () => sendMenu('canvas:new-space') },
        { type: 'separator' },
        // ⌥⌘arrows, not ⌃arrows: macOS owns ⌃←/⌃→/⌃↑ and an app accelerator loses to a system one.
        { label: 'Previous Space', accelerator: 'Alt+Command+Left', click: () => sendMenu('canvas:prev-space') },
        { label: 'Next Space', accelerator: 'Alt+Command+Right', click: () => sendMenu('canvas:next-space') },
        { label: 'Overview', accelerator: 'Alt+Command+Up', click: () => sendMenu('canvas:overview') },
        { type: 'separator' },
        ...SPACES,
        { type: 'separator' },
        { label: 'Tidy Up', accelerator: 'Control+Command+T', click: () => sendMenu('canvas:tidy') },
        // One item, not a checkbox: the menu is built once and the lock belongs to whichever space
        // is active, so the renderer's padlock is the state, and this is only the shortcut.
        { label: 'Lock / Unlock Space', accelerator: 'Control+Command+L', click: () => sendMenu('canvas:lock') }
      ]
    },
    {
      label: 'Window',
      submenu: [
        // Plain items, never roles: the canvas gets first refusal on ⌘W/⌘M and the renderer falls
        // through to closeSelf()/minimizeSelf() when no canvas window has focus.
        { label: 'Close Window', accelerator: 'CmdOrCtrl+W', click: () => sendWindowMenu('close-window') },
        { label: 'Minimize', accelerator: 'CmdOrCtrl+M', click: () => sendWindowMenu('minimize-window') },
        ...(isMac ? [{ role: 'zoom' as const }, { type: 'separator' as const }, { role: 'front' as const }] : []),
        { type: 'separator' },
        { label: 'Pop Out', accelerator: 'Control+Command+O', click: () => sendWindowMenu('canvas:popout') },
        { label: 'Return to Canvas', accelerator: 'Control+Command+Shift+O', click: () => sendWindowMenu('canvas:unpopout') },
        { label: 'Pin on Top', accelerator: 'Control+Command+P', click: () => sendWindowMenu('canvas:pin') },
        // Transparency is a pop-out's own property, so these ride sendWindowMenu like the pin above:
        // whoever has focus answers, and a widget still on the canvas only stores the level.
        { label: 'More Transparent', accelerator: 'Control+Command+[', click: () => sendWindowMenu('canvas:opacity:down') },
        { label: 'Less Transparent', accelerator: 'Control+Command+]', click: () => sendWindowMenu('canvas:opacity:up') },
        {
          label: 'Transparency',
          submenu: OPACITY_LEVELS.map((o) => ({
            label: o === 1 ? 'Opaque' : `${Math.round(o * 100)}%`,
            click: () => sendWindowMenu(`canvas:opacity:${Math.round(o * 100)}`)
          }))
        },
        { type: 'separator' },
        { label: 'Gather Widgets', accelerator: 'Alt+Command+G', click: () => void gather() },
        {
          id: 'popouts-front',
          label: 'Bring Pop-outs to Front',
          type: 'checkbox',
          accelerator: 'Alt+Command+F',
          click: (item) => { item.checked = toggleFront() }
        }
      ]
    }
  ]
  Menu.setApplicationMenu(Menu.buildFromTemplate(template))
}

// A throw anywhere in the main process kills the app with no crash report and no React error. Log it
// and keep running: losing one menu action is recoverable, losing the window is not.
process.on('uncaughtException', (e) => console.error('[main] uncaught:', e))
process.on('unhandledRejection', (e) => console.error('[main] unhandled rejection:', e))

app.on('child-process-gone', (_e, d) => console.error(`[child] ${d.type} gone: ${d.reason}`))

app.whenReady().then(async () => {
  ipcMain.handle('backend:url', () => backendUrl())
  ipcMain.handle('backend:status', () => backendStatus())
  ipcMain.handle('backend:token', () => backendToken())
  ipcMain.handle('backend:info', () => backendInfo())
  ipcMain.handle('backend:restart', () => restartBackend())
  ipcMain.handle('backend:open-logs', () => (logDir() ? shell.openPath(logDir()) : 'No log folder'))
  // Every window hears the supervisor: the main window re-fetches, a pop-out re-points at a new port.
  onBackendState((info) => {
    for (const w of BrowserWindow.getAllWindows()) if (!w.isDestroyed() && !w.webContents.isDestroyed()) w.webContents.send('backend:state', info)
  })
  ipcMain.on('window:close-self', (e) => BrowserWindow.fromWebContents(e.sender)?.close())
  ipcMain.on('window:minimize-self', (e) => BrowserWindow.fromWebContents(e.sender)?.minimize())
  registerPopouts(() => win)
  registerBus()
  buildMenu()
  setFrontListener((on) => {
    const item = Menu.getApplicationMenu()?.getMenuItemById('popouts-front')
    if (item) item.checked = on
  })
  createTray(showMain)
  try {
    await startBackend()
  } catch (e) {
    console.error('[main] backend failed to start:', (e as Error).message)
  }
  await startPageBridge() // open_page's offscreen loader; registers itself with the backend
  // After the backend, so the stored accelerator wins over the default; still before any renderer exists.
  registerShortcuts(() => win, await storedGather())
  createWindow()
  void restorePopouts()
  app.on('activate', showMain)
})

app.on('window-all-closed', () => {
  if (!isMac) app.quit()
})
app.on('before-quit', () => {
  stopPageBridge()
  stopBackend()
})
