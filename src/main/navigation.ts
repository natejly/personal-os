import { session, shell } from 'electron'
import { mainFrameNavigationAllowed } from './appUrl'
import { backendToken, backendUrl } from './backend'
import { frameNavigationAllowed, shouldAttachWidgetToken, webviewNavigationBlocked, webviewRequestBlocked } from './navPolicy'
import { pageBridgeUrl } from './pagefetch'

const openExternal = (url: string): void => {
  if (/^https?:\/\//i.test(url)) void shell.openExternal(url)
}

/**
 * Widget iframes load `/widgets/{id}/render` and cannot send the app token themselves.
 * Attach it on that one path, and only when the renderer opened the frame. A request the
 * widget document starts — including a navigation to another widget — does not get the token.
 */
function localServices(): string[] {
  return [backendUrl(), pageBridgeUrl()].filter((url): url is string => Boolean(url))
}

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

/** The web widget's session is not the app's. It still must not dial the sidecar or the page loader. */
export function guardWebWidgetSession(): void {
  const ses = session.fromPartition('persist:web-widget')
  ses.webRequest.onBeforeRequest((details, callback) => {
    callback({ cancel: webviewRequestBlocked(details.url, localServices()) })
  })
  // With no handler Electron grants every request (geolocation, notifications, clipboard read, media),
  // so any page opened in a web widget would get them silently. Allow only the harmless few.
  const harmless = new Set(['fullscreen', 'clipboard-sanitized-write'])
  ses.setPermissionRequestHandler((_wc, perm, cb) => cb(harmless.has(perm)))
  ses.setPermissionCheckHandler((_wc, perm) => harmless.has(perm))
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
    const src = params.src ?? ''
    if (!/^https?:\/\//i.test(src) || webviewNavigationBlocked(src, localServices())) e.preventDefault()
  })
  contents.on('did-attach-webview', (_e, guest) => {
    const blocked = (url: string): boolean => webviewNavigationBlocked(url, localServices())
    guest.on('will-navigate', (e, url) => { if (blocked(url)) e.preventDefault() })
    guest.on('will-redirect', (e, url) => { if (blocked(url)) e.preventDefault() })
    guest.setWindowOpenHandler(({ url }) => {
      if (/^https?:\/\//i.test(url) && !blocked(url)) void guest.loadURL(url)
      return { action: 'deny' }
    })
  })
}
