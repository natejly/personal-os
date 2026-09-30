import { shell } from 'electron'
import { backendUrl } from './backend'

const openExternal = (url: string): void => {
  if (/^https?:\/\//i.test(url)) void shell.openExternal(url)
}

/**
 * The app and every pop-out must never navigate away from the renderer bundle. A click on a markdown
 * link in a widget would otherwise replace the whole app with that page, and a page that fails to load
 * or paints no background leaves the transparent vibrancy window looking like an empty grey rectangle.
 * It would also hand the preload (window.os.backendToken()) to a page the model supplied.
 * Top-level navigation is not covered by CSP, so it is blocked here and handed to the system browser.
 */
export function guardNavigation(contents: Electron.WebContents): void {
  const local = (url: string, frame: boolean): boolean => {
    if (url === 'about:blank' || url.startsWith('file://')) return true
    const dev = process.env.ELECTRON_RENDERER_URL
    if (dev && url.startsWith(dev)) return true
    const base = backendUrl()
    return frame && !!base && url.startsWith(`${base}/`) // widget iframes are served by the sidecar
  }
  contents.on('will-navigate', (e, url) => {
    if (local(url, false)) return
    e.preventDefault()
    openExternal(url)
  })
  contents.on('will-frame-navigate', (details) => {
    if (details.isMainFrame || local(details.url, true)) return // the main frame is handled by will-navigate
    details.preventDefault()
  })
  contents.setWindowOpenHandler(({ url }) => {
    openExternal(url)
    return { action: 'deny' }
  })
  guardWebviews(contents)
}

/**
 * The web widget's <webview> guests are full Chromium pages the user pointed at the open web. They may
 * never gain the preload (that is the backend token) or node, whatever attributes the tag claims, and
 * they only ever host http(s). window.open from a page stays inside its own guest: a browser widget
 * that bounced every popup-based login to Safari would not be much of a browser.
 */
function guardWebviews(contents: Electron.WebContents): void {
  contents.on('will-attach-webview', (e, webPreferences, params) => {
    delete webPreferences.preload
    webPreferences.nodeIntegration = false
    webPreferences.contextIsolation = true
    if (!/^https?:\/\//i.test(params.src ?? '')) e.preventDefault()
  })
  contents.on('did-attach-webview', (_e, guest) => {
    guest.setWindowOpenHandler(({ url }) => {
      if (/^https?:\/\//i.test(url)) void guest.loadURL(url)
      return { action: 'deny' }
    })
  })
}
