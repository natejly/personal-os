import { session, shell } from 'electron'
import { mainFrameNavigationAllowed } from './appUrl'
import { backendToken, backendUrl } from './backend'
import { frameNavigationAllowed, shouldAttachWidgetToken } from './navPolicy'

const openExternal = (url: string): void => {
  if (/^https?:\/\//i.test(url)) void shell.openExternal(url)
}

/**
 * Widget iframes load `/widgets/{id}/render` and cannot send the app token themselves.
 * Attach it on that one path, and only when the renderer opened the frame. A request the
 * widget document starts — including a navigation to another widget — does not get the token.
 */
export function attachWidgetRenderAuth(): void {
  session.defaultSession.webRequest.onBeforeSendHeaders((details, callback) => {
    const headers = { ...details.requestHeaders }
    try {
      const base = backendUrl()
      const token = backendToken()
      if (token && base && shouldAttachWidgetToken({
        url: details.url,
        referrer: details.referrer,
        frameUrl: details.frame?.url,
        resourceType: details.resourceType,
        backendUrl: base,
        rendererUrl: process.env.ELECTRON_RENDERER_URL
      })) headers['X-Personal-OS-Token'] = token
    } catch {
      /* leave the request unchanged */
    }
    callback({ requestHeaders: headers })
  })
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
    if (frameNavigationAllowed(details.url, backendUrl(), process.env.ELECTRON_RENDERER_URL)) return
    details.preventDefault()
  })
  contents.setWindowOpenHandler(({ url }) => {
    openExternal(url)
    return { action: 'deny' }
  })
  // No window enables webviewTag, so a <webview> can never attach; this is belt and braces.
  contents.on('will-attach-webview', (e) => e.preventDefault())
}
