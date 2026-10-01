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
