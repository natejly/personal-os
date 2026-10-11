import { app } from 'electron'
import { templateTrayIcon } from './trayIcon'
import type { Platform } from './types'

export const darwin: Platform = {
  id: 'darwin',
  mainWindowChrome: () => ({
    titleBarStyle: 'hiddenInset',
    trafficLightPosition: { x: 16, y: 14 },
    vibrancy: 'sidebar',
    visualEffectState: 'active',
    backgroundColor: '#00000000'
  }),
  // vibrancy with a transparent backgroundColor, never transparent:true — on macOS the two fight
  popoutChrome: () => ({
    frame: false,
    backgroundColor: '#00000000',
    vibrancy: 'under-window',
    visualEffectState: 'active'
  }),
  floatingChrome: () => ({ frame: false, backgroundColor: '#1c1c1e' }),
  setupWindow: () => undefined,
  applyChromeTheme: () => undefined,
  menuBar: 'native',
  macMenus: true,
  systemPermissions: true,
  trayIcon: templateTrayIcon,
  updaterEnabled: true,
  quitWhenAllWindowsClosed: false,
  pythonCommand: 'python3',
  enterBackgroundMode: () => {
    app.setActivationPolicy('accessory')
    app.dock?.hide()
  }
}
