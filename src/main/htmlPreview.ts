/** Serves the HTML fence previews from `grain-preview://doc/<id>` with their own CSP header (see shared/htmlPreview). */
import { protocol } from 'electron'
import { randomUUID } from 'crypto'
import { DASHBOARD_FRAME_CSP, PREVIEW_FRAME_CSP, PREVIEW_SCHEME, wrapDashboardDoc, wrapPreviewDoc } from '../shared/htmlPreview'
import { handle } from './ipc'

const MAX_DOCS = 200
const MAX_SOURCE = 4 * 1024 * 1024
const docs = new Map<string, { html: string; dash: boolean }>()

/** Before app ready. */
export function registerPreviewScheme(): void {
  protocol.registerSchemesAsPrivileged([
    { scheme: PREVIEW_SCHEME, privileges: { standard: true, secure: true, supportFetchAPI: false, corsEnabled: false, stream: false } }
  ])
}

/** After app ready. */
export function servePreviews(): void {
  handle('preview:put', (_e, source: unknown, opts?: unknown) => {
    if (typeof source !== 'string' || source.length > MAX_SOURCE) throw new Error('preview:put: bad source')
    const o = (opts ?? {}) as { dashboard?: unknown; data?: unknown }
    const dash = o.dashboard === true
    const data = dash && o.data && typeof o.data === 'object' && !Array.isArray(o.data) ? (o.data as Record<string, unknown[]>) : undefined
    const html = dash ? wrapDashboardDoc(source, data) : wrapPreviewDoc(source)
    if (html.length > MAX_SOURCE + 1024 * 1024) throw new Error('preview:put: too large')
    const id = randomUUID()
    docs.set(id, { html, dash })
    if (docs.size > MAX_DOCS) docs.delete(docs.keys().next().value as string)
    return id
  })
  protocol.handle(PREVIEW_SCHEME, (req) => {
    const url = new URL(req.url)
    const doc = docs.get(url.pathname.slice(1))
    if (!doc || doc.dash !== (url.host === 'dash') || (url.host !== 'doc' && url.host !== 'dash')) return new Response('not found', { status: 404 })
    return new Response(doc.html, {
      headers: {
        'Content-Type': 'text/html; charset=utf-8',
        'Content-Security-Policy': doc.dash ? DASHBOARD_FRAME_CSP : PREVIEW_FRAME_CSP,
        'X-Content-Type-Options': 'nosniff',
        'Cache-Control': 'no-store'
      }
    })
  })
}
