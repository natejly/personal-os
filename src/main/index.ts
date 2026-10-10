import { app, BrowserWindow, dialog, Menu, powerMonitor, shell, systemPreferences } from 'electron'
import { existsSync, statSync, writeFileSync } from 'fs'
import { basename, join, resolve, sep } from 'path'
import { isOpenable } from '../shared/openable'
import { SHORTCUTS, shortcut } from '../shared/shortcuts'
import { backendInfo, backendStatus, backendToken, backendUrl, onBackendState, restartBackend, startBackend, stopBackend } from './backend'
import { registerBus } from './bus'
import { handle, on } from './ipc'
import { hookConsole, initLogs, logDir } from './logging'
import { isAppUrl } from './appUrl'
import { guardNavigation } from './navigation'
import { registerAgentBrowserIpc } from './agentBrowser'
import { registerDeskNotify } from './deskNotify'
import { registerPrintIpc, renderNotePdf } from './printDoc'
import { startPageBridge, stopPageBridge } from './pagefetch'
import { registerQuickAsk, toggleAsk } from './quickAsk'
import { gather, OPACITY_LEVELS, registerPopouts, restorePopouts, setFrontListener, toggleFront } from './popouts'
import { registerShortcuts } from './shortcuts'
import { createTray } from './tray'
import { startUpdater } from './updater'
import { registerSystemAccess } from './systemAccess'
import { background, goBackground, reveal } from './background'
import { attachContextMenu } from './attachContextMenu'
import { registerPreviewScheme, servePreviews } from './htmlPreview'

registerPreviewScheme() // before ready
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

  win.once('ready-to-show', () => { if (win) reveal(win) })

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

  // Right-click: spelling fixes on a misspelled word, the edit items in a field, and on selected text
  // the same four verbs as the floating toolbar.
  attachContextMenu(
    win,
    ['Explain', 'Summarize', 'Verify', 'Ask…'].map((label) => ({
      label,
      click: () => sendMenu(`selection:${label.replace('…', '').toLowerCase()}`)
    }))
  )

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
  reveal(win)
  if (!background) win.focus()
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
async function storedShortcuts(): Promise<{ gather?: string; capture?: string; ask?: string }> {
  const base = backendUrl()
  if (!base) return {}
  try {
    const token = backendToken()
    const r = await fetch(`${base}/settings`, { headers: token ? { 'X-Personal-OS-Token': token } : {} })
    if (!r.ok) return {}
    const s = (await r.json()) as { gatherShortcut?: string; quickCaptureShortcut?: string; quickAskShortcut?: string }
    return { gather: s.gatherShortcut?.trim() || undefined, capture: s.quickCaptureShortcut?.trim() || undefined, ask: s.quickAskShortcut?.trim() || undefined }
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

/**
 * A menu item whose label, accelerator and action come from the shortcut registry (src/shared/shortcuts.ts),
 * the same table the shortcut overlay lists. An entry with no action (the composer's ⇧⌘P) is shown for
 * discovery only: registering it would let the menu swallow the key the component binds itself.
 */
const item = (id: string): Electron.MenuItemConstructorOptions => {
  const { label, keys, action, scope } = shortcut(id)
  if (!action) return { label, accelerator: keys, registerAccelerator: false }
  return { label, accelerator: keys, click: () => (scope === 'window' ? sendWindowMenu : sendMenu)(action) }
}

const SPACES = SHORTCUTS.filter((s) => s.id.startsWith('space-')).map((s) => item(s.id))

function buildMenu(): void {
  const template: Electron.MenuItemConstructorOptions[] = [
    ...(isMac
      ? [
          {
            label: app.name,
            submenu: [
              { role: 'about' as const },
              { type: 'separator' as const },
              item('settings'),
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
        item('new-chat'),
        // Not an OS-global shortcut: nothing outside the app acts. Files gets a doc in the default place.
        item('new-note'),
        item('daily-note'),
        item('upload'),
        // ⌘W lives in the Window menu now: `role: 'close'` here could not be intercepted by the canvas.
        ...(isMac
          ? []
          : [
              { type: 'separator' as const },
              item('settings'),
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
        item('find'),
        item('find-next'),
        item('find-prev'),
        ...(isMac
          ? [{ type: 'separator' }, { label: 'Speech', submenu: [{ role: 'startSpeaking' }, { role: 'stopSpeaking' }] }] as Electron.MenuItemConstructorOptions[]
          : [])
      ]
    },
    {
      label: 'View',
      submenu: [
        item('view-chat'),
        item('view-todos'),
        item('view-calendar'),
        item('view-docs'),
        item('view-mail'),
        item('view-memory'),
        // No digit for these: the graph is a mode of Memory (⌘6) and Uploads is a Files section (⌘4, ⌘U).
        { label: 'Knowledge Graph…', click: () => sendMenu('view:graph') },
        { label: 'Uploads', click: () => sendMenu('view:documents') },
        { label: 'Library', click: () => sendMenu('view:library') },
        { type: 'separator' },
        // Inside the Markdown editor ⌘K is still the link chord: the renderer hands it back.
        item('palette'),
        { type: 'separator' },
        // ⌘⇧[ / ⌘⇧] step through chats (⌃⌘[ / ⌃⌘] are pop-out transparency and ⌥⌘arrows are spaces).
        item('chat-prev'),
        item('chat-next'),
        item('chat-search'),
        { type: 'separator' },
        item('toggle-sidebar'),
        // ⌘I asks about what is on screen. The chat's context inspector, which used to own it, moves one
        // modifier over.
        item('page-agent'),
        item('toggle-context'),
        { type: 'separator' },
        { role: 'reload' },
        { role: 'toggleDevTools' },
        { type: 'separator' },
        // Explicit, so zoom-reset carries its own chord rather than resetZoom's default.
        // These change the uiZoom setting (the renderer writes it and every window follows), not the page directly.
        item('zoom-reset'),
        item('zoom-in'),
        item('zoom-out'),
        { type: 'separator' },
        { role: 'togglefullscreen' }
      ]
    },
    {
      label: 'Spaces',
      submenu: [
        item('canvas-toggle'),
        item('canvas-new'),
        { type: 'separator' },
        // ⌥⌘arrows, not ⌃arrows: macOS owns ⌃←/⌃→/⌃↑ and an app accelerator loses to a system one.
        item('canvas-prev'),
        item('canvas-next'),
        item('canvas-overview'),
        { type: 'separator' },
        ...SPACES,
        { type: 'separator' },
        item('canvas-tidy'),
        // One item, not a checkbox: the menu is built once and the lock belongs to whichever space
        // is active, so the renderer's padlock is the state, and this is only the shortcut.
        item('canvas-lock')
      ]
    },
    {
      label: 'Window',
      submenu: [
        // Plain items, never roles: the canvas gets first refusal on ⌘W/⌘M and the renderer falls
        // through to closeSelf()/minimizeSelf() when no canvas window has focus.
        item('close-window'),
        item('minimize-window'),
        ...(isMac ? [{ role: 'zoom' as const }, { type: 'separator' as const }, { role: 'front' as const }] : []),
        { type: 'separator' },
        item('popout'),
        item('unpopout'),
        item('pin'),
        // Transparency is a pop-out's own property, so these ride sendWindowMenu like the pin above:
        // whoever has focus answers, and a widget still on the canvas only stores the level.
        item('opacity-down'),
        item('opacity-up'),
        {
          label: 'Transparency',
          submenu: OPACITY_LEVELS.map((o) => ({
            label: o === 1 ? 'Opaque' : `${Math.round(o * 100)}%`,
            click: () => sendWindowMenu(`canvas:opacity:${Math.round(o * 100)}`)
          }))
        },
        { type: 'separator' },
        { ...item('gather-widgets'), click: () => void gather() },
        // No accelerator: the global one is the user's to choose (Settings), and a menu key would shadow it in-app.
        { label: 'Quick Ask', click: toggleAsk },
        {
          ...item('popouts-front'),
          id: 'popouts-front',
          type: 'checkbox',
          click: (mi) => { mi.checked = toggleFront() }
        }
      ]
    },
    {
      // role 'help' gives the macOS menu search field.
      label: 'Help',
      role: 'help',
      submenu: [
        item('help'),
        { label: 'Using Grain', click: () => sendMenu('help:guide') },
        { type: 'separator' },
        { label: 'Open Logs', click: () => void (logDir() ? shell.openPath(logDir()) : undefined) }
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
  goBackground()
  servePreviews()
  registerAgentBrowserIpc()
  registerDeskNotify(() => win, showMain, sendMenu)
  handle('ui:zoom', (e, percent: number) => {
    if (typeof percent === 'number' && percent >= 80 && percent <= 160) e.sender.setZoomFactor(percent / 100)
  })
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
  registerPrintIpc()
  handle('print:export-pdf', async (e, title: string, content: string, filename: string, mode: 'save' | 'bytes') => {
    if (mode !== 'save') return new Uint8Array(await renderNotePdf(String(title), String(content)))
    // The save sheet comes first so a cancel renders nothing; the sheet itself confirms an overwrite.
    const name = basename(String(filename)).replace(/[/:]/g, ' ')
    const opts = { title: 'Download PDF', defaultPath: join(app.getPath('downloads'), name), filters: [{ name: 'PDF', extensions: ['pdf'] }] }
    const parent = BrowserWindow.fromWebContents(e.sender)
    const r = parent ? await dialog.showSaveDialog(parent, opts) : await dialog.showSaveDialog(opts)
    if (r.canceled || !r.filePath) return null
    writeFileSync(r.filePath, await renderNotePdf(String(title), String(content)))
    return r.filePath
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
  // A file the side panel shows. Inside the home folder only; opening is limited to types that cannot run.
  handle('data:file-action', async (_e, path: string, action: string) => {
    const p = resolve(String(path))
    const home = app.getPath('home')
    if (!p.startsWith(home + sep) || !existsSync(p) || !statSync(p).isFile()) return false
    if (action === 'reveal') { shell.showItemInFolder(p); return true }
    return action === 'open' && isOpenable(p) && !(await shell.openPath(p))
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
  registerSystemAccess()
  on('window:close-self', (e) => BrowserWindow.fromWebContents(e.sender)?.close())
  on('window:minimize-self', (e) => BrowserWindow.fromWebContents(e.sender)?.minimize())
  on('app:show', () => showMain())  // a chat notification was clicked: bring the main window forward
  registerPopouts(() => win)
  registerQuickAsk((id) => { showMain(); sendMenu(`open-chat:${id}`) })
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
  registerShortcuts(() => win, stored.gather, stored.capture, stored.ask)
  createWindow()
  void restorePopouts()
  startUpdater()
  powerMonitor.on('resume', nudgeScheduler)
  powerMonitor.on('unlock-screen', nudgeScheduler)
  app.on('activate', () => { if (!background) showMain() })
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
