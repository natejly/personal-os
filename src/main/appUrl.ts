import { join } from 'path'
import { pathToFileURL } from 'url'

/** True when `url` is the app's own renderer: the dev server origin, or the packaged index.html. */
export function isAppUrl(
  url: string,
  devUrl: string | undefined = process.env.ELECTRON_RENDERER_URL,
  indexPath: string = join(__dirname, '../renderer/index.html')
): boolean {
  try {
    const u = new URL(url)
    if (devUrl) return u.origin === new URL(devUrl).origin
    if (u.protocol !== 'file:') return false
    return u.pathname === pathToFileURL(indexPath).pathname
  } catch {
    return false
  }
}

/**
 * What the app window (or a pop-out) may navigate to at the top level: about:blank, or the app's
 * own renderer. Query and hash are ignored, so a pop-out's `?surface=widget&window=…` and the
 * error boundary's reload pass; every other file:// URL, such as a dropped PDF, does not.
 */
export function mainFrameNavigationAllowed(
  url: string,
  devUrl: string | undefined = process.env.ELECTRON_RENDERER_URL,
  indexPath: string = join(__dirname, '../renderer/index.html')
): boolean {
  if (url === 'about:blank') return true
  return isAppUrl(url, devUrl, indexPath)
}
