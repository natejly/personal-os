/**
 * Fenced ```html / ```svg blocks in assistant markdown: which fences are ours, and the document the
 * svg preview iframe gets. Pure (no DOM, no React) so node:test covers the trust boundary.
 *
 * Both previews run in an iframe with `sandbox="allow-scripts"` and NEVER allow-same-origin, so the
 * content lives in an opaque origin: no app cookies, no app storage, no access to the app's DOM, no
 * preload bridge. An html preview is served by the `grain-preview:` scheme with its own CSP header
 * (shared/htmlPreview.ts): a srcdoc frame would inherit the app's CSP, which blocks inline script. An svg
 * preview stays a srcdoc frame whose CSP meta tag, injected first, forbids script outright.
 */

export const PREVIEW_SANDBOX = 'allow-scripts'

/** SVG never needs a script, and nothing it loads may leave the document. */
export const SVG_CSP = [
  "default-src 'none'",
  "script-src 'none'",
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

export type FenceKind = 'html' | 'svg' | null

/** Which of our preview renderers a fence language belongs to. */
export function fenceKind(lang: string): FenceKind {
  const l = (lang || '').trim().toLowerCase()
  if (l === 'html' || l === 'htm') return 'html'
  if (l === 'svg') return 'svg'
  return null
}

const metaFor = (csp: string): string => `<meta http-equiv="Content-Security-Policy" content="${csp.replace(/"/g, '&quot;')}">`

/** The full svg document for the iframe's `srcdoc`, the CSP meta first, ahead of anything the model wrote. */
export function buildPreviewDoc(code: string): string {
  return `<!doctype html>${metaFor(SVG_CSP)}<meta charset="utf-8"><body style="margin:0;display:flex;justify-content:center;background:transparent">${(code || '').trim()}</body>`
}
