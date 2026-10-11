import { applyOverlayTheme, hideMenuBar, overlayChrome, solidFrameless } from './shared'
import { coloredTrayIcon } from './trayIcon'
import type { Platform } from './types'

export const win32: Platform = {
  id: 'win32',
  mainWindowChrome: overlayChrome,
  popoutChrome: solidFrameless,
  floatingChrome: solidFrameless,
  setupWindow: hideMenuBar,
  applyChromeTheme: applyOverlayTheme,
  menuBar: 'hidden',
  macMenus: false,
  systemPermissions: false,
  trayIcon: () => coloredTrayIcon(),
  // The installer build updates in place.
  updaterEnabled: true,
  quitWhenAllWindowsClosed: true,
  pythonCommand: 'python',
  enterBackgroundMode: () => undefined
}
