/** Pure placement rules shared by blob toggles, new windows and Tidy Up. No DOM, no store. */
import type { Rect } from '@shared/types'

/** The one gap between neighbouring windows: snapping, free-spot search, new windows and Tidy Up all use it. */
export const GAP = 12

const clear = (r: Rect, o: Rect): boolean =>
  r.x + r.w + GAP <= o.x || o.x + o.w + GAP <= r.x || r.y + r.h + GAP <= o.y || o.y + o.h + GAP <= r.y

const inside = (r: Rect, b: Rect): boolean => r.x >= b.x && r.y >= b.y && r.x + r.w <= b.x + b.w && r.y + r.h <= b.y + b.h

/** `r` as-is when it keeps GAP from every other window, else the nearest free spot to its right or below a neighbour (right wins ties); `r` again when nothing is free. */
export const freeSpot = (r: Rect, others: Rect[], bounds: Rect): Rect => {
  if (others.every((o) => clear(r, o))) return r
  const cands: Rect[] = []
  for (const o of others) {
    cands.push({ ...r, x: o.x + o.w + GAP }, { ...r, y: o.y + o.h + GAP })
  }
  const free = cands.filter((c) => inside(c, bounds) && others.every((o) => clear(c, o)))
  const cost = (c: Rect): number => Math.hypot(c.x - r.x, c.y - r.y) + (c.y !== r.y ? 0.5 : 0)
  free.sort((a, b) => cost(a) - cost(b))
  return free[0] ?? r
}

export interface BlobConfig { restore?: Rect; blobAt?: { x: number; y: number } }

/** Where a blob grows back to: its exact old rect if it was not moved, else the old size at the blob's new top-left, kept on screen. */
export const unfoldRect = (blob: Rect, cfg: BlobConfig, fallback: { w: number; h: number }, bounds: Rect): Rect => {
  const size = cfg.restore ?? fallback
  const moved = !cfg.restore || !cfg.blobAt || cfg.blobAt.x !== blob.x || cfg.blobAt.y !== blob.y
  const x = moved ? blob.x : cfg.restore!.x
  const y = moved ? blob.y : cfg.restore!.y
  return {
    w: size.w, h: size.h,
    x: Math.round(Math.max(bounds.x, Math.min(x, bounds.x + bounds.w - size.w))),
    y: Math.round(Math.max(bounds.y, Math.min(y, bounds.y + bounds.h - size.h)))
  }
}
