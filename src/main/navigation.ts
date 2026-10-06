import { shell } from 'electron'
import { mainFrameNavigationAllowed } from './appUrl'
import { frameNavigationAllowed } from './navPolicy'

const openExternal = (url: string): void => {
  if (/^https?:\/\//i.test(url)) void shell.openExternal(url)
}

/**
 * The app and every pop-out must never navigate away from the renderer bundle. A click on a markdown
 * link in a widget would otherwise replace the whole app with that page, and a page that fails to load
 * or paints no background leaves the transparent vibrancy window looking like an empty grey rectangle.
 * It would also hand the preload (window.os.backendToken()) to a page the model supplied.
 * Top-level navigation is not covered by CSP, so it is blocked here and handed to the system browser.
 * Only the renderer's own URL passes (appUrl.ts): a file dropped beside the composer used to count
 * as "local" and replace the app with the file. openExternal ignores it, so such a drop does nothing.
 */
export function guardNavigation(contents: Electron.WebContents): void {
  contents.on('will-navigate', (e, url) => {
    if (mainFrameNavigationAllowed(url)) return
    e.preventDefault()
    openExternal(url)
  })
  contents.on('will-frame-navigate', (details) => {
    if (details.isMainFrame) return // the main frame is handled by will-navigate
    if (frameNavigationAllowed(details.url, process.env.ELECTRON_RENDERER_URL)) return
    details.preventDefault()
  })
  contents.setWindowOpenHandler(({ url }) => {
    openExternal(url)
    return { action: 'deny' }
  })
  // No window enables webviewTag, so a <webview> can never attach; this is belt and braces.
  contents.on('will-attach-webview', (e) => e.preventDefault())
}
