/**
 * Fenced ```html / ```svg blocks in assistant markdown: which fences are ours, and the document the
 * sandboxed preview iframe gets. Pure (no DOM, no React) so node:test covers the trust boundary.
 *
 * The preview is a `srcdoc` iframe with `sandbox="allow-scripts"` and NEVER allow-same-origin, so the
 * code runs in an opaque origin: no app cookies, no app storage, no access to `window.parent`'s DOM, no
 * preload bridge. A CSP meta tag (the same posture the backend's artifact render route sets in headers,
 * minus the directives a meta tag cannot carry: frame-ancestors and sandbox) is injected first, so the
 * document cannot reach the network either. Note a srcdoc frame also inherits the renderer's own CSP,
 * which has no 'unsafe-inline' for scripts: inline <script> in a fence preview is blocked there, and
 * "Save as artifact" is the way to run it (the render route serves it under its own headers).
 */

export const PREVIEW_SANDBOX = 'allow-scripts'

/** Mirrors backend artifacts.ARTIFACT_CSP for what a <meta> can express. */
export const PREVIEW_CSP = [
  "default-src 'none'",
  "script-src 'unsafe-inline'",
  "style-src 'unsafe-inline'",
  'img-src data: blob:',
  'font-src data:',
  'media-src data: blob:',
  "connect-src 'none'",
  "form-action 'none'",
  "base-uri 'none'",
  "object-src 'none'",
  "frame-src 'none'",
  "worker-src 'none'"
].join('; ')

/** SVG never needs a script, so its preview refuses them outright. */
export const SVG_CSP = PREVIEW_CSP.replace("script-src 'unsafe-inline'", "script-src 'none'")

export type FenceKind = 'html' | 'svg' | null

/** Which of our preview renderers a fence language belongs to. */
export function fenceKind(lang: string): FenceKind {
  const l = (lang || '').trim().toLowerCase()
  if (l === 'html' || l === 'htm') return 'html'
  if (l === 'svg') return 'svg'
  return null
}

const metaFor = (csp: string): string => `<meta http-equiv="Content-Security-Policy" content="${csp.replace(/"/g, '&quot;')}">`

/**
 * The full document for the iframe's `srcdoc`. The CSP meta goes first, ahead of anything the model wrote, so
 * no script can run before it takes effect; any leading doctype is re-emitted in front of it to stay out of
 * quirks mode. A fragment is wrapped so it has a body and sensible defaults.
 */
export function buildPreviewDoc(code: string, kind: 'html' | 'svg' = 'html'): string {
  const csp = kind === 'svg' ? SVG_CSP : PREVIEW_CSP
  let body = (code || '').trim()
  if (kind === 'svg') {
    return `<!doctype html>${metaFor(csp)}<meta charset="utf-8"><body style="margin:0;display:flex;justify-content:center;background:transparent">${body}</body>`
  }
  body = body.replace(/^<!doctype[^>]*>/i, '').trim()
  if (!/<html[\s>]/i.test(body)) {
    body = `<meta charset="utf-8"><body style="margin:0;padding:12px;font-family:system-ui,sans-serif">${body}</body>`
  }
  return `<!doctype html>${metaFor(csp)}${body}`
}

/** True if the document has inline script, i.e. will be blocked in the chat preview and needs the artifact route. */
export function hasScript(code: string): boolean {
  return /<script[\s>]/i.test(code || '')
}

/** The attribute set the preview iframe must use; exported so a test pins it. */
export function isSafeSandbox(attr: string): boolean {
  const tokens = attr.split(/\s+/).filter(Boolean)
  return tokens.length > 0 && tokens.every((t) => t === 'allow-scripts')
}

/** A title for "Save as artifact": the document's <title>, else a default. */
export function titleOf(code: string): string {
  const m = /<title[^>]*>([\s\S]*?)<\/title>/i.exec(code || '')
  const t = m ? m[1].replace(/\s+/g, ' ').trim() : ''
  return t.slice(0, 80) || 'HTML preview'
}
