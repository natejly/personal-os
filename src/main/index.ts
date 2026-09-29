import { app, BrowserWindow, ipcMain, Menu, shell } from 'electron'
import { join } from 'path'
import { backendStatus, backendUrl, startBackend, stopBackend } from './backend'

let win: BrowserWindow | null = null
const isMac = process.platform === 'darwin'

function createWindow(): void {
  win = new BrowserWindow({
    width: 1280,
    height: 820,
    minWidth: 820,
    minHeight: 520,
    show: false,
    title: 'Personal OS',
    titleBarStyle: isMac ? 'hiddenInset' : 'default',
    trafficLightPosition: { x: 16, y: 16 },
    vibrancy: isMac ? 'sidebar' : undefined,
    visualEffectState: 'active',
    backgroundColor: '#00000000',
    webPreferences: {
      preload: join(__dirname, '../preload/index.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false
    }
  })

  win.once('ready-to-show', () => win?.show())
  win.webContents.setWindowOpenHandler(({ url }) => {
    void shell.openExternal(url)
    return { action: 'deny' }
  })

  if (process.env.ELECTRON_RENDERER_URL) {
    void win.loadURL(process.env.ELECTRON_RENDERER_URL)
  } else {
    void win.loadFile(join(__dirname, '../renderer/index.html'))
  }
}

const sendMenu = (action: string): void => win?.webContents.send('menu', action)

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
        { type: 'separator' },
        ...(isMac ? [] : [{ label: 'Settings…', accelerator: 'CmdOrCtrl+,', click: () => sendMenu('settings') }]),
        isMac ? { role: 'close' } : { role: 'quit' }
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
        { type: 'separator' },
        { label: 'Toggle Sidebar', accelerator: 'CmdOrCtrl+B', click: () => sendMenu('toggle-sidebar') },
        { label: 'Toggle Context Panel', accelerator: 'CmdOrCtrl+I', click: () => sendMenu('toggle-context') },
        { type: 'separator' },
        { role: 'reload' },
        { role: 'toggleDevTools' },
        { type: 'separator' },
        { role: 'resetZoom' },
        { role: 'zoomIn' },
        { role: 'zoomOut' },
        { type: 'separator' },
        { role: 'togglefullscreen' }
      ]
    },
    { role: 'windowMenu' }
  ]
  Menu.setApplicationMenu(Menu.buildFromTemplate(template))
}

app.whenReady().then(async () => {
  ipcMain.handle('backend:url', () => backendUrl())
  ipcMain.handle('backend:status', () => backendStatus())
  buildMenu()
  try {
    await startBackend()
  } catch (e) {
    console.error('[main] backend failed to start:', (e as Error).message)
  }
  createWindow()
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow()
  })
})

app.on('window-all-closed', () => {
  if (!isMac) app.quit()
})
app.on('before-quit', stopBackend)
