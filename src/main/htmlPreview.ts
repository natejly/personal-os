/** Serves the HTML fence previews from `grain-preview://doc/<id>` with their own CSP header (see shared/htmlPreview). */
import { protocol } from 'electron'
import { randomUUID } from 'crypto'
import { PREVIEW_FRAME_CSP, PREVIEW_SCHEME, wrapPreviewDoc } from '../shared/htmlPreview'
import { handle } from './ipc'

const MAX_DOCS = 200
const MAX_SOURCE = 4 * 1024 * 1024
const docs = new Map<string, string>()

/** Before app ready. */
export function registerPreviewScheme(): void {
  protocol.registerSchemesAsPrivileged([
    { scheme: PREVIEW_SCHEME, privileges: { standard: true, secure: true, supportFetchAPI: false, corsEnabled: false, stream: false } }
  ])
}

/** After app ready. */
export function servePreviews(): void {
  handle('preview:put', (_e, source: unknown) => {
    if (typeof source !== 'string' || source.length > MAX_SOURCE) throw new Error('preview:put: bad source')
    const id = randomUUID()
    docs.set(id, wrapPreviewDoc(source))
    if (docs.size > MAX_DOCS) docs.delete(docs.keys().next().value as string)
    return id
  })
  protocol.handle(PREVIEW_SCHEME, (req) => {
    const url = new URL(req.url)
    const doc = url.host === 'doc' ? docs.get(url.pathname.slice(1)) : undefined
    if (doc === undefined) return new Response('not found', { status: 404 })
    return new Response(doc, {
      headers: {
        'Content-Type': 'text/html; charset=utf-8',
        'Content-Security-Policy': PREVIEW_FRAME_CSP,
        'X-Content-Type-Options': 'nosniff',
        'Cache-Control': 'no-store'
      }
    })
  })
}
