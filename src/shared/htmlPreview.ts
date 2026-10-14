/**
 * The HTML fence preview document and the policy it is served under. Pure (no Electron, no DOM) so node:test
 * covers it. The main process serves the wrapped document from the `grain-preview:` scheme with this CSP as a
 * response header, so the frame has its own policy instead of inheriting the app's: scripts run, CDN scripts
 * (https only) load, and nothing can connect anywhere.
 */

export const PREVIEW_SCHEME = 'grain-preview'

export const previewUrl = (id: string, dashboard = false): string => `${PREVIEW_SCHEME}://${dashboard ? 'dash' : 'doc'}/${id}`

/** Saved dashboards: the same frame with no network of any kind (no https scripts, styles, images or fonts). */
export const DASHBOARD_FRAME_CSP = [
  "default-src 'none'",
  "script-src 'unsafe-inline' 'unsafe-eval'",
  "style-src 'unsafe-inline'",
  'img-src data: blob:',
  'font-src data:',
  'media-src data: blob:',
  "connect-src 'none'",
  "worker-src 'none'",
  "frame-src 'none'",
  "child-src 'none'",
  "form-action 'none'",
  "base-uri 'none'",
  "object-src 'none'",
  "manifest-src 'none'"
].join('; ')

export const PREVIEW_FRAME_CSP = [
  "default-src 'none'",
  "script-src 'unsafe-inline' 'unsafe-eval' https:",
  "style-src 'unsafe-inline' https:",
  'img-src data: blob: https:',
  'font-src data: https:',
  'media-src data: blob: https:',
  "connect-src 'none'",
  "worker-src blob:",
  "form-action 'none'",
  "base-uri 'none'",
  "object-src 'none'",
  "frame-src 'none'"
].join('; ')

export const PREVIEW_HEIGHT_MESSAGE = 'grain-preview-height'
export const PREVIEW_MIN_HEIGHT = 120
export const PREVIEW_MAX_HEIGHT = 1200

/** The height a preview frame reports, clamped to a sane range; null when it is not a finite number. */
export function clampPreviewHeight(h: unknown): number | null {
  if (typeof h !== 'number' || !Number.isFinite(h)) return null
  return Math.min(PREVIEW_MAX_HEIGHT, Math.max(PREVIEW_MIN_HEIGHT, Math.ceil(h)))
}

// offsetHeight is the document's own box; scrollHeight would never drop below the frame's viewport.
const REPORTER = `<script>(function(){var d=document.documentElement;function s(){parent.postMessage({type:${JSON.stringify(PREVIEW_HEIGHT_MESSAGE)},height:d.offsetHeight},'*')}if(window.ResizeObserver)new ResizeObserver(s).observe(d);else{addEventListener('load',s);addEventListener('resize',s)}})()</script>`

/**
 * The full document to serve. Our charset and height reporter go first, ahead of anything the model wrote;
 * any leading doctype is re-emitted in front of them to stay out of quirks mode. A fragment gets a body.
 */
export function wrapPreviewDoc(source: string, head = ''): string {
  let body = (source || '').trim().replace(/^<!doctype[^>]*>/i, '').trim()
  if (!/<html[\s>]/i.test(body)) {
    body = `<body style="margin:0;padding:12px;font-family:system-ui,sans-serif">${body}</body>`
  }
  return `<!doctype html><meta charset="utf-8">${REPORTER}${head}${body}`
}

// WebRTC ignores connect-src, so this removes the constructors from the dashboard's own window.
// ponytail: a nested srcdoc/javascript: iframe still has them (no in-page fix), a process-level WebRTC switch would close it
const NO_RTC = `<script>['RTCPeerConnection','webkitRTCPeerConnection','RTCDataChannel','RTCSessionDescription','RTCIceCandidate'].forEach(function(k){try{Object.defineProperty(window,k,{value:undefined,configurable:false,writable:false})}catch(e){}})</script>`

/** JSON for a script element: `<` and the line separators can never close the tag or break the text. */
const safeJson = (v: unknown): string => JSON.stringify(v).replace(/</g, '\\u003c').replace(/\u2028/g, '\\u2028').replace(/\u2029/g, '\\u2029')

/** A dashboard document: the preview wrapper plus the RTC kill and the bound rows as `#grain-data`, ahead of the page. */
export function wrapDashboardDoc(source: string, data?: Record<string, unknown[]>): string {
  const extra = NO_RTC + (data ? `<script type="application/json" id="grain-data">${safeJson(data)}</script>` : '')
  return wrapPreviewDoc(source, extra)
}
