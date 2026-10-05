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
 * Subframes are the renderer's own documents and srcdoc previews; nothing on the sidecar, and not the
 * disk: a file dropped on a preview frame has no business loading in it.
 */
export function frameNavigationAllowed(url: string, rendererUrl?: string): boolean {
  if (url === 'about:blank') return true
  return !!rendererUrl && (url === rendererUrl || url.startsWith(rendererUrl.endsWith('/') ? rendererUrl : `${rendererUrl}/`))
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
