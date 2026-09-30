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
}
