/**
 * What the renderer may hand the app token, and which loopback targets are the app's own services.
 * Kept free of Electron so the decisions can be tested on their own.
 */
import { isLoopbackHost } from './pageGuard'

const WIDGET_RENDER = /^\/widgets\/[^/]+\/render$/
const SOURCE_FETCH = /^\/sources\/[^/]+\/fetch$/
// Signed per artifact (rt/re in the query) and sandboxed, so the frame needs no app token.
const ARTIFACT_RENDER = /^\/artifacts\/[^/]+\/render$/

function originOf(url: string): string | null {
  try {
    return new URL(url).origin
  } catch {
    return null
  }
}

function portOf(url: URL): string {
  if (url.port) return url.port
  if (url.protocol === 'http:' || url.protocol === 'ws:') return '80'
  if (url.protocol === 'https:' || url.protocol === 'wss:') return '443'
  return ''
}

function widgetId(url: string): string | null {
  try {
    const match = new URL(url).pathname.match(/^\/widgets\/([^/]+)\/render$/)
    return match ? match[1] : null
  } catch {
    return null
  }
}

/**
 * The app token is attached to the widget iframe's first load, which the renderer starts and
 * which cannot set a header itself. Electron's header hook has no initiator field, so the
 * referrer and the frame's current URL stand in for it: a request whose referrer or committed
 * document is already the sidecar does not get the token. Otherwise one widget can open every
 * other widget's HTML.
 */
export function shouldAttachWidgetToken(args: {
  url: string
  referrer?: string
  frameUrl?: string
  resourceType?: string
  backendUrl: string
  rendererUrl?: string
}): boolean {
  if (args.resourceType && args.resourceType !== 'subFrame') return false
  const backend = originOf(args.backendUrl)
  if (!backend) return false
  let request: URL
  try {
    request = new URL(args.url)
  } catch {
    return false
  }
  if (request.origin !== backend || !WIDGET_RENDER.test(request.pathname)) return false
  if (args.referrer) {
    const from = originOf(args.referrer)
    if (from === backend) return false
    if (!args.referrer.startsWith('file:')) {
      const renderer = args.rendererUrl ? originOf(args.rendererUrl) : null
      if (from && from.startsWith('http') && from !== renderer) return false
    }
  }
  if (args.frameUrl && originOf(args.frameUrl) === backend && widgetId(args.frameUrl) !== widgetId(args.url)) return false
  return true
}

/**
 * Subframes may load the widget document, its data sources and saved artifacts. The rest of the API stays in the app,
 * and so does the disk: every frame the renderer makes is a sidecar URL or srcdoc, so a file dropped
 * on a preview frame has no business loading in it.
 */
export function frameNavigationAllowed(url: string, backendUrl: string, rendererUrl?: string): boolean {
  if (url === 'about:blank') return true
  if (rendererUrl && (url === rendererUrl || url.startsWith(rendererUrl.endsWith('/') ? rendererUrl : `${rendererUrl}/`))) return true
  const backend = originOf(backendUrl)
  if (!backend) return false
  let request: URL
  try {
    request = new URL(url)
  } catch {
    return false
  }
  if (request.origin !== backend) return false
  return WIDGET_RENDER.test(request.pathname) || SOURCE_FETCH.test(request.pathname) || ARTIFACT_RENDER.test(request.pathname)
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
