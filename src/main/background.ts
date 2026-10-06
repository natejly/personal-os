import { app, type BrowserWindow } from 'electron'

/** GRAIN_E2E_BACKGROUND=1: windows are real but never take focus or bring the app forward (automated test runs). */
export const background =
  process.env.E2E_FOREGROUND !== '1' && (process.env.PLAYWRIGHT_TEST === '1' || process.env.GRAIN_E2E_BACKGROUND === '1')

/** Call before any window exists: no Dock icon, and the app is never the frontmost one. */
export const goBackground = (): void => {
  if (!background || process.platform !== 'darwin') return
  app.setActivationPolicy('accessory')
  app.dock?.hide()
}

/** show(), or showInactive() in background mode. */
export const reveal = (w: BrowserWindow): void => (background ? w.showInactive() : w.show())

/** Raise the app itself over other apps; a no-op in background mode. */
export const stealFocus = (): void => { if (!background) app.focus({ steal: true }) }
