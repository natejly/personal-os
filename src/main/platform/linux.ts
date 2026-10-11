import { applyOverlayTheme, hideMenuBar, overlayChrome, solidFrameless } from './shared'
import { coloredTrayIcon } from './trayIcon'
import type { Platform } from './types'

export const linux: Platform = {
  id: 'linux',
  mainWindowChrome: overlayChrome,
  popoutChrome: solidFrameless,
  floatingChrome: solidFrameless,
  setupWindow: hideMenuBar,
  applyChromeTheme: applyOverlayTheme,
  menuBar: 'hidden',
  macMenus: false,
  systemPermissions: false,
  trayIcon: () => coloredTrayIcon(),
  // An AppImage can replace itself; a distro package or a bare tree cannot.
  updaterEnabled: !!process.env.APPIMAGE,
  quitWhenAllWindowsClosed: true,
  pythonCommand: 'python3',
  enterBackgroundMode: () => undefined
}
