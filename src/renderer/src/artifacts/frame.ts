/**
 * The window half of the artifact trust boundary: which `message` events an artifact window listens to.
 * Pure (no DOM, no React) so `frame.test.ts` can drive it under node:test.
 *
 * The frame is `<iframe sandbox="allow-scripts" src=…/render>`, so its origin is opaque and `event.origin`
 * says nothing useful; the only identity check that holds is `event.source === iframe.contentWindow`.
 * Anything else posting on `window` (HMR, an extension, a sibling artifact window) is dropped here, before
 * `parseMessage` ever sees it. The narrowed result is already clamped / cleaned by bridge.ts.
 */
import { parseMessage, type ArtifactMessage } from './bridge'

/** `source`/`frameWindow` are `unknown` so the test can pass plain objects instead of a DOM Window. */
export function readFrameMessage(source: unknown, frameWindow: unknown, data: unknown): ArtifactMessage | null {
  if (frameWindow == null || source !== frameWindow) return null
  const r = parseMessage(data)
  return r.ok ? r.message : null
}

/** The iframe URL for one artifact: `path` is the signed render_path the backend hands out (the iframe cannot
 * send the app token). `v` renders that version and busts the iframe when a new one lands. */
export const renderUrl = (base: string, path: string, version: number): string => `${base}${path}${path.includes('?') ? '&' : '?'}v=${version}`

/** A download name for the .html export: the title as a slug, never empty. */
export const downloadName = (title: string): string => {
  const slug = title.normalize('NFKD').replace(/[̀-ͯ]/g, '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 60)
  return `${slug || 'artifact'}.html`
}
