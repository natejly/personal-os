/** Pure placement rules shared by new windows and Tidy Up. No DOM, no store. */
import type { Rect } from '@shared/types'

/** The one gap between neighbouring windows: snapping, new windows and Tidy Up all use it. */
export const GAP = 12

/** Space kept between a window and the edge of the visible canvas when it is fitted in. */
export const FIT_MARGIN = 24

/**
 * `r` brought wholly inside `vis` (both in canvas space, so pan and zoom are already folded into `vis`):
 * shrunk to the visible size less `margin` on every side, never below `min`, then slid back in. A rect
 * that already fits comes back unchanged.
 */
export const fitInView = (r: Rect, vis: Rect, min: { w: number; h: number }, margin = FIT_MARGIN): Rect => {
  const w = Math.min(r.w, Math.max(min.w, vis.w - 2 * margin))
  const h = Math.min(r.h, Math.max(min.h, vis.h - 2 * margin))
  const x = Math.max(vis.x + margin, Math.min(r.x, vis.x + vis.w - margin - w))
  const y = Math.max(vis.y + margin, Math.min(r.y, vis.y + vis.h - margin - h))
  return { x: Math.round(x), y: Math.round(y), w: Math.round(w), h: Math.round(h) }
}
