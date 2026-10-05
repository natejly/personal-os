import { app, BrowserWindow, dialog, Menu, powerMonitor, shell, systemPreferences } from 'electron'
import { existsSync, statSync } from 'fs'
import { join } from 'path'
import { backendInfo, backendStatus, backendToken, backendUrl, onBackendState, restartBackend, startBackend, stopBackend } from './backend'
import { registerBus } from './bus'
import { handle, on } from './ipc'
import { hookConsole, initLogs, logDir } from './logging'
import { isAppUrl } from './appUrl'
import { guardNavigation } from './navigation'
import { registerAgentBrowserIpc } from './agentBrowser'
import { registerDeskNotify } from './deskNotify'
import { startPageBridge, stopPageBridge } from './pagefetch'
import { gather, OPACITY_LEVELS, registerPopouts, restorePopouts, setFrontListener, toggleFront } from './popouts'
import { registerShortcuts } from './shortcuts'
import { createTray } from './tray'
import { startUpdater } from './updater'

let win: BrowserWindow | null = null
const isMac = process.platform === 'darwin'

// The app was renamed from "Personal OS" to "Grain", which moves the userData directory Electron
// derives from the app name. Existing installs keep their data: if the new location has none but a
// legacy one does, keep using the legacy directory. Must run before anything touches userData.
// GRAIN_USER_DATA points the whole app at another directory (testing a packaged build without touching real data).
const userDataOverride = process.env.GRAIN_USER_DATA
if (userDataOverride) app.setPath('userData', userDataOverride)
for (const legacy of userDataOverride ? [] : ['personal-os', 'Personal OS']) {
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
    trafficLightPosition: { x: 16, y: 14 },
    vibrancy: isMac ? 'sidebar' : undefined,
    visualEffectState: 'active',
    backgroundColor: '#00000000',
    webPreferences: {
      preload: join(__dirname, '../preload/index.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true
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

/** The Mac woke or unlocked: have the job scheduler run its pass now, so a slot missed asleep fires at once. */
function nudgeScheduler(): void {
  const base = backendUrl()
  if (!base) return
  const token = backendToken()
  fetch(`${base}/jobs/wake`, { method: 'POST', headers: token ? { 'X-Personal-OS-Token': token } : {} }).catch(() => {
    // The backend is down or restarting; its own loop catches up within a minute anyway.
  })
}

/** The stored accelerators, so a gather or capture shortcut the user chose is still registered after a relaunch. */
async function storedShortcuts(): Promise<{ gather?: string; capture?: string }> {
  const base = backendUrl()
  if (!base) return {}
  try {
    const token = backendToken()
    const r = await fetch(`${base}/settings`, { headers: token ? { 'X-Personal-OS-Token': token } : {} })
    if (!r.ok) return {}
    const s = (await r.json()) as { gatherShortcut?: string; quickCaptureShortcut?: string }
    return { gather: s.gatherShortcut?.trim() || undefined, capture: s.quickCaptureShortcut?.trim() || undefined }
  } catch {
    return {}
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
  const target = BrowserWindow.getFocusedWindow() ?? win
  // A focused window that is not the renderer (the shown agent browser) has no preload to hear 'menu'.
  if (target && !target.isDestroyed() && !isAppUrl(target.webContents.getURL())) {
    if (action === 'close-window') target.close()
    else if (action === 'minimize-window') target.minimize()
    return
  }
  deliver(target, action)
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
        // Not an OS-global shortcut: nothing outside the app acts. Files gets a doc in the default place.
        { label: 'New File', accelerator: 'CmdOrCtrl+Shift+N', click: () => sendMenu('new-note') },
        { label: "Today's File", accelerator: 'CmdOrCtrl+Shift+D', click: () => sendMenu('daily-note') },
        { label: 'Upload File…', accelerator: 'CmdOrCtrl+U', click: () => sendMenu('upload') },
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
    {
      // The stock edit roles, spelled out so Find can sit beside them. The label stays 'Edit' so macOS
      // still appends its own dictation and emoji items.
      label: 'Edit',
      submenu: [
        { role: 'undo' }, { role: 'redo' }, { type: 'separator' },
        { role: 'cut' }, { role: 'copy' }, { role: 'paste' }, { role: 'pasteAndMatchStyle' }, { role: 'delete' }, { role: 'selectAll' },
        { type: 'separator' },
        { label: 'Find…', accelerator: 'CmdOrCtrl+F', click: () => sendWindowMenu('chat:find') },
        { label: 'Find Next', accelerator: 'CmdOrCtrl+G', click: () => sendWindowMenu('chat:find-next') },
        { label: 'Find Previous', accelerator: 'Shift+CmdOrCtrl+G', click: () => sendWindowMenu('chat:find-prev') },
        ...(isMac
          ? [{ type: 'separator' }, { label: 'Speech', submenu: [{ role: 'startSpeaking' }, { role: 'stopSpeaking' }] }] as Electron.MenuItemConstructorOptions[]
          : [])
      ]
    },
    {
      label: 'View',
      submenu: [
        { label: 'Today', accelerator: 'CmdOrCtrl+0', click: () => sendMenu('view:home') },
        { label: 'Chats', accelerator: 'CmdOrCtrl+1', click: () => sendMenu('view:chat') },
        { label: 'Lists', accelerator: 'CmdOrCtrl+2', click: () => sendMenu('view:todos') },
        { label: 'Calendar', accelerator: 'CmdOrCtrl+3', click: () => sendMenu('view:calendar') },
        { label: 'Files', accelerator: 'CmdOrCtrl+4', click: () => sendMenu('view:docs') },
        { label: 'Mail', accelerator: 'CmdOrCtrl+5', click: () => sendMenu('view:mail') },
        { label: 'Memory…', accelerator: 'CmdOrCtrl+6', click: () => sendMenu('view:memory') },
        { label: 'Activity', accelerator: 'CmdOrCtrl+7', click: () => sendMenu('view:activity') },
        // No digit for these: the graph is a mode of Memory (⌘6) and Uploads is a Files section (⌘4, ⌘U).
        { label: 'Knowledge Graph…', click: () => sendMenu('view:graph') },
        { label: 'Uploads', click: () => sendMenu('view:documents') },
        // ⌘M is Minimize in the Window menu, so Meetings takes ⌘⇧M.
        { label: 'Meetings', accelerator: 'CmdOrCtrl+Shift+M', click: () => sendMenu('view:meetings') },
        { label: 'Library', click: () => sendMenu('view:library') },
        { type: 'separator' },
        // Inside the Markdown editor ⌘K is still the link chord: the renderer hands it back.
        { label: 'Command Palette…', accelerator: 'CmdOrCtrl+K', click: () => sendMenu('palette') },
        { type: 'separator' },
        // ⌘⇧[ / ⌘⇧] step through chats (⌃⌘[ / ⌃⌘] are pop-out transparency and ⌥⌘arrows are spaces).
        { label: 'Previous Chat', accelerator: 'CmdOrCtrl+Shift+[', click: () => sendMenu('chat:prev') },
        { label: 'Next Chat', accelerator: 'CmdOrCtrl+Shift+]', click: () => sendMenu('chat:next') },
        { label: 'Search Chats', accelerator: 'CmdOrCtrl+Shift+F', click: () => sendMenu('chat:search') },
        { type: 'separator' },
        { label: 'Toggle Sidebar', accelerator: 'CmdOrCtrl+B', click: () => sendMenu('toggle-sidebar') },
        // ⌘I asks about what is on screen. The chat's context inspector, which used to own it, moves one
        // modifier over.
        { label: 'Page Agent', accelerator: 'CmdOrCtrl+I', click: () => sendMenu('page-agent') },
        { label: 'Toggle Context Panel', accelerator: 'Control+Command+I', click: () => sendMenu('toggle-context') },
        // Shown for discovery only: the composer binds ⇧⌘P itself, so the menu must not swallow it.
        { label: 'Cycle Plan Mode', accelerator: 'CmdOrCtrl+Shift+P', registerAccelerator: false },
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
        { label: 'Toggle Spaces', accelerator: 'CmdOrCtrl+Shift+C', click: () => sendMenu('canvas:toggle') },
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
        { label: 'Return to Space', accelerator: 'Control+Command+Shift+O', click: () => sendWindowMenu('canvas:unpopout') },
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

// A second full instance would start a second backend on the same SQLite directory (two schedulers,
// and its startup recovery would mark the first one's live runs interrupted). Hand focus to the first.
// An instance pointed at an external backend (PERSONAL_OS_BACKEND_URL: the dev and test setup) spawns
// none of its own, so it may run beside the main app and does not take the lock.
const gotLock = !!process.env.PERSONAL_OS_BACKEND_URL || app.requestSingleInstanceLock()
if (!gotLock) app.quit()
else app.on('second-instance', () => { if (app.isReady()) showMain() })

if (gotLock) app.whenReady().then(async () => {
  registerAgentBrowserIpc()
  registerDeskNotify(() => win, showMain, sendMenu)
  handle('backend:url', () => backendUrl())
  handle('backend:status', () => backendStatus())
  handle('backend:token', () => backendToken())
  handle('backend:info', () => backendInfo())
  handle('backend:restart', () => restartBackend())
  handle('backend:open-logs', () => (logDir() ? shell.openPath(logDir()) : 'No log folder'))
  // Every window hears the supervisor: the main window re-fetches, a pop-out re-points at a new port.
  onBackendState((info) => {
    for (const w of BrowserWindow.getAllWindows()) if (!w.isDestroyed() && !w.webContents.isDestroyed()) w.webContents.send('backend:state', info)
  })
  handle('data:choose-export-path', async () => {
    const stamp = new Date().toISOString().slice(0, 10)
    const r = await dialog.showSaveDialog({ title: 'Export all data', defaultPath: join(app.getPath('documents'), `grain-export-${stamp}.zip`), filters: [{ name: 'Zip archive', extensions: ['zip'] }] })
    return r.canceled || !r.filePath ? null : r.filePath
  })
  handle('data:choose-input-files', async () => {
    const r = await dialog.showOpenDialog({ title: 'Add inputs to the desk', defaultPath: app.getPath('home'), properties: ['openFile', 'multiSelections'] })
    return r.canceled ? [] : r.filePaths
  })
  handle('data:choose-folder', async () => {
    const r = await dialog.showOpenDialog({ title: 'Work in a folder', defaultPath: app.getPath('home'), properties: ['openDirectory', 'createDirectory'] })
    return r.canceled || !r.filePaths[0] ? null : r.filePaths[0]
  })
  // Folders only: openPath on a file or .app would run it.
  handle('data:reveal', async (_e, path: string) => {
    const p = String(path)
    if (!existsSync(p) || !statSync(p).isDirectory()) return false
    return !(await shell.openPath(p))
  })
  // A staged restore is applied by the backend at its next start, so relaunching the whole app does it.
  handle('data:relaunch', () => { app.relaunch(); app.quit() })
  // The composer's mic: macOS shows its prompt once, from here; after a denial only System Settings can change it.
  handle('media:mic-access', async () => {
    if (!isMac) return 'granted'
    const st = systemPreferences.getMediaAccessStatus('microphone')
    if (st !== 'not-determined') return st
    return (await systemPreferences.askForMediaAccess('microphone')) ? 'granted' : 'denied'
  })
  on('window:close-self', (e) => BrowserWindow.fromWebContents(e.sender)?.close())
  on('window:minimize-self', (e) => BrowserWindow.fromWebContents(e.sender)?.minimize())
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
  const stored = await storedShortcuts()
  registerShortcuts(() => win, stored.gather, stored.capture)
  createWindow()
  void restorePopouts()
  startUpdater()
  powerMonitor.on('resume', nudgeScheduler)
  powerMonitor.on('unlock-screen', nudgeScheduler)
  app.on('activate', showMain)
})

app.on('window-all-closed', () => {
  if (!isMac) app.quit()
})
app.on('before-quit', () => {
  stopPageBridge()
})
// will-quit fires after every before-quit handler, so pop-outs have persisted their last bounds
// (which needs the backend's token) before the backend goes away.
app.on('will-quit', () => stopBackend())
// A crash or Ctrl-C of the dev run skips before-quit; the backend must not outlive us.
process.on('exit', () => stopBackend(true))
for (const sig of ['SIGINT', 'SIGTERM'] as const) process.on(sig, () => { stopBackend(true); app.exit(0) })
