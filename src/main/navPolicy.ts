/**
 * What a frame may navigate to, and what a web widget may dial on this machine.
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

/** Subresources: only the local services are off limits. Ordinary https, data and blob keep working. */
export function webviewRequestBlocked(url: string, services: string[]): boolean {
  return services.some((service) => targetsLoopbackService(url, service))
}

/** Top-level guest navigations: http(s) only, and not onto the sidecar or the page loader. */
export function webviewNavigationBlocked(url: string, services: string[]): boolean {
  if (url === 'about:blank') return false
  try {
    const target = new URL(url)
    if (target.protocol !== 'http:' && target.protocol !== 'https:') return true
  } catch {
    return true
  }
  return webviewRequestBlocked(url, services)
}
