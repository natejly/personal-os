/**
 * What a subframe may navigate to, and which loopback targets are the app's own services.
 * Kept free of Electron so the decisions can be tested on their own.
 */
import { isLoopbackHost } from './pageGuard'

function portOf(url: URL): string {
  if (url.port) return url.port
  if (url.protocol === 'http:' || url.protocol === 'ws:') return '80'
  if (url.protocol === 'https:' || url.protocol === 'wss:') return '443'
  return ''
}

/**
 * Subframes are the renderer's own documents, srcdoc and grain-preview: previews and the renderer's own blob: URLs (a PDF the
 * side panel fetched with the app token, handed to the built-in viewer); nothing on the sidecar, and not the
 * disk: a file dropped on a preview frame has no business loading in it. A blob made inside a sandboxed
 * preview belongs to an opaque origin (`blob:null/…`) and is refused with everything else.
 */
export function frameNavigationAllowed(url: string, rendererUrl?: string): boolean {
  if (url === 'about:blank') return true
  if (url.startsWith('grain-preview://')) return true // HTML fence previews, served with their own CSP
  if (url.startsWith('blob:file:///')) return true // the packaged renderer is a file:// document
  if (!rendererUrl) return false
  if (url === rendererUrl || url.startsWith(rendererUrl.endsWith('/') ? rendererUrl : `${rendererUrl}/`)) return true
  try {
    return url.startsWith(`blob:${new URL(rendererUrl).origin}/`)
  } catch {
    return false
  }
}

/** A loopback URL aimed at one local service (the sidecar, or the page loader), whatever spelling it uses. */
export function targetsLoopbackService(url: string, serviceUrl: string): boolean {
  if (!serviceUrl) return false
  let target: URL
  let service: URL
  try {
    target = new URL(url)
    service = new URL(serviceUrl)
  } catch {
    return false
  }
  if (!isLoopbackHost(target.hostname) || !isLoopbackHost(service.hostname)) return false
  if (portOf(target) !== portOf(service)) return false
  return target.protocol === 'http:' || target.protocol === 'https:' || target.protocol === 'ws:' || target.protocol === 'wss:'
}
