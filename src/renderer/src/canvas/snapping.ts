/**
 * Grid, alignment-guide, equal-spacing and edge-zone maths. Pure: no DOM, no React, no store, so
 * `snapping.test.ts` runs under `node:test`.
 *
 * Every threshold below is in SCREEN pixels and is divided by `view.zoom` before it meets a canvas
 * coordinate, so snapping feels identical at 50 % and at 200 %. The grid pitch is the exception:
 * it is a canvas-space quantity by definition, because positions persist (contract §10).
 */
import type { Rect, SnapMode } from '@shared/types'
import { GAP } from './layout'

export const GUIDE_PX = 6
export const GAP_PX = 2
export const EDGE_PX = 8
export const EDGE_HOLD_MS = 250
export const GRID_SIZES = [8, 16, 24, 32]

export type Axis = 'x' | 'y'
export type Handle = 'n' | 's' | 'e' | 'w' | 'ne' | 'nw' | 'se' | 'sw'
export type SnapEdge = 'start' | 'center' | 'end'
export type Zone = 'left' | 'right' | 'top' | 'bottom' | 'top-left' | 'top-right' | 'bottom-left' | 'bottom-right'

export interface Point { x: number; y: number }
export interface Size { w: number; h: number }
export interface WindowRect extends Rect { id: string }

/** How the screen sees the plane: `.canvas-plane` is `translate(panX,panY) scale(zoom)`, origin `0 0`. */
export interface Viewport { zoom: number; panX: number; panY: number; width: number; height: number }

/** One alignment line, in canvas space. `span` is the producing rect's extent on the other axis. */
export interface GuideLine { axis: Axis; at: number; id: string | null; span: [number, number]; /** only this edge of the dragged rect may snap to it */ only?: SnapEdge }
export interface AppliedGuide extends GuideLine { edge: SnapEdge; delta: number }
/** One equal-gap pill: the run from `from` to `to` along `axis`, drawn at `cross` on the other axis. */
export interface GapPill { axis: Axis; from: number; to: number; cross: number }
export interface SnapResult { rect: Rect; guides: AppliedGuide[]; gaps: GapPill[] }

export interface SnapContext {
  mode: SnapMode
  grid: number
  view: Viewport
  /** the other windows in the space, snapshotted at drag start */
  others: WindowRect[]
  /** `guideLines(others, view)`, snapshotted at drag start */
  lines: GuideLine[]
  /** Meta held: every snap off */
  free?: boolean
}

const AXES: Axis[] = ['x', 'y']
const SIZE = { x: 'w', y: 'h' } as const
const EDGES: SnapEdge[] = ['start', 'center', 'end']

const edgeValue = (r: Rect, axis: Axis, e: SnapEdge): number =>
  e === 'start' ? r[axis] : e === 'end' ? r[axis] + r[SIZE[axis]] : r[axis] + r[SIZE[axis]] / 2

const overlaps = (a: Rect, b: Rect, axis: Axis): boolean =>
  a[axis] < b[axis] + b[SIZE[axis]] && b[axis] < a[axis] + a[SIZE[axis]]

const handleEdges = (h: Handle): { x?: 'start' | 'end'; y?: 'start' | 'end' } => ({
  ...(h.includes('w') ? { x: 'start' as const } : h.includes('e') ? { x: 'end' as const } : {}),
  ...(h.includes('n') ? { y: 'start' as const } : h.includes('s') ? { y: 'end' as const } : {})
})

export const screenFromCanvas = (p: Point, v: Viewport): Point => ({ x: p.x * v.zoom + v.panX, y: p.y * v.zoom + v.panY })
export const canvasFromScreen = (p: Point, v: Viewport): Point => ({ x: (p.x - v.panX) / v.zoom, y: (p.y - v.panY) / v.zoom })

/** The canvas-space rect the viewport currently shows. */
export const visibleRect = (v: Viewport): Rect => {
  const o = canvasFromScreen({ x: 0, y: 0 }, v)
  return { x: o.x, y: o.y, w: v.width / v.zoom, h: v.height / v.zoom }
}

export const snapValue = (v: number, pitch: number): number => (pitch > 0 ? Math.round(v / pitch) * pitch : v)
export const snapRectToGrid = (r: Rect, pitch: number): Rect => ({ ...r, x: snapValue(r.x, pitch), y: snapValue(r.y, pitch) })

/** Shift held: keep the larger component, drop the other. */
export const constrain = (d: Point): Point => (Math.abs(d.x) >= Math.abs(d.y) ? { x: d.x, y: 0 } : { x: 0, y: d.y })

export const clampSize = (r: Rect, min: Size): Rect => ({ ...r, w: Math.max(min.w, r.w), h: Math.max(min.h, r.h) })

export const guideLines = (others: WindowRect[], view: Viewport): GuideLine[] => {
  const lines: GuideLine[] = []
  for (const r of others) {
    const vSpan: [number, number] = [r.y, r.y + r.h]
    const hSpan: [number, number] = [r.x, r.x + r.w]
    lines.push(
      { axis: 'x', at: r.x, id: r.id, span: vSpan },
      { axis: 'x', at: r.x + r.w / 2, id: r.id, span: vSpan },
      { axis: 'x', at: r.x + r.w, id: r.id, span: vSpan },
      { axis: 'y', at: r.y, id: r.id, span: hSpan },
      { axis: 'y', at: r.y + r.h / 2, id: r.id, span: hSpan },
      { axis: 'y', at: r.y + r.h, id: r.id, span: hSpan },
      // exactly GAP from a neighbour: my start edge past its end, my end edge before its start
      { axis: 'x', at: r.x + r.w + GAP, id: r.id, span: vSpan, only: 'start' },
      { axis: 'x', at: r.x - GAP, id: r.id, span: vSpan, only: 'end' },
      { axis: 'y', at: r.y + r.h + GAP, id: r.id, span: hSpan, only: 'start' },
      { axis: 'y', at: r.y - GAP, id: r.id, span: hSpan, only: 'end' }
    )
  }
  const v = visibleRect(view)
  lines.push({ axis: 'x', at: v.x + v.w / 2, id: null, span: [v.y, v.y + v.h] })
  lines.push({ axis: 'y', at: v.y + v.h / 2, id: null, span: [v.x, v.x + v.w] })
  return lines
}

/** The nearest line on `axis` within `tol`, over the dragged rect's `edges`. At most one per axis. */
export const nearestGuide = (r: Rect, lines: GuideLine[], axis: Axis, tol: number, edges: SnapEdge[] = EDGES): AppliedGuide | null => {
  let best: AppliedGuide | null = null
  for (const l of lines) {
    if (l.axis !== axis) continue
    for (const e of edges) {
      if (l.only && l.only !== e) continue
      const delta = l.at - edgeValue(r, axis, e)
      if (Math.abs(delta) > tol) continue
      if (!best || Math.abs(delta) < Math.abs(best.delta)) best = { ...l, edge: e, delta }
    }
  }
  return best
}

const pill = (axis: Axis, from: number, to: number, a: Rect, b: Rect): GapPill => {
  const cross: Axis = axis === 'x' ? 'y' : 'x'
  const lo = Math.max(a[cross], b[cross])
  const hi = Math.min(a[cross] + a[SIZE[cross]], b[cross] + b[SIZE[cross]])
  return { axis, from, to, cross: lo < hi ? (lo + hi) / 2 : (a[cross] + b[cross]) / 2 }
}

/**
 * Equal spacing on one axis: either centre the rect between its two neighbours, or match a gap that
 * already exists in the same row. Returns the shift to apply and the pills to draw.
 */
export const equalSpacing = (r: Rect, others: WindowRect[], axis: Axis, tol: number): { delta: number; pills: GapPill[] } | null => {
  const cross: Axis = axis === 'x' ? 'y' : 'x'
  const key = SIZE[axis]
  const row = others.filter((o) => overlaps(r, o, cross)).sort((a, b) => a[axis] - b[axis])
  if (row.length < 2) return null
  const end = (o: Rect): number => o[axis] + o[key]
  const before = row.filter((o) => end(o) <= r[axis])
  const after = row.filter((o) => o[axis] >= end(r))
  const left = before.length ? before[before.length - 1] : null
  const right = after.length ? after[0] : null

  if (left && right) {
    const gapL = r[axis] - end(left)
    const gapR = right[axis] - end(r)
    const delta = (gapR - gapL) / 2
    if (Math.abs(delta) > tol) return null
    const x = r[axis] + delta
    const moved: Rect = { ...r, [axis]: x } as Rect
    return { delta, pills: [pill(axis, end(left), x, left, moved), pill(axis, end(moved), right[axis], moved, right)] }
  }

  const refs: { size: number; p: GapPill }[] = []
  for (let i = 0; i < row.length - 1; i++) {
    const a = row[i]
    const b = row[i + 1]
    const gap = b[axis] - end(a)
    if (gap > 0) refs.push({ size: gap, p: pill(axis, end(a), b[axis], a, b) })
  }
  const anchor = left ?? right
  if (!anchor || !refs.length) return null
  let best: { delta: number; pills: GapPill[] } | null = null
  for (const ref of refs) {
    const x = left ? end(left) + ref.size : anchor[axis] - ref.size - r[key]
    const delta = x - r[axis]
    if (Math.abs(delta) > tol) continue
    if (best && Math.abs(best.delta) <= Math.abs(delta)) continue
    const moved: Rect = { ...r, [axis]: x } as Rect
    const own = left ? pill(axis, end(anchor), x, anchor, moved) : pill(axis, end(moved), anchor[axis], moved, anchor)
    best = { delta, pills: [ref.p, own] }
  }
  return best
}

/** Per axis: a guide inside `GUIDE_PX/zoom` wins outright, then equal spacing, then the grid (§10). */
export const snapMove = (rect: Rect, ctx: SnapContext): SnapResult => {
  const out: Rect = { ...rect }
  const guides: AppliedGuide[] = []
  const gaps: GapPill[] = []
  if (ctx.free || ctx.mode === 'off') return { rect: out, guides, gaps }
  const useGuides = ctx.mode === 'guides' || ctx.mode === 'both'
  const useGrid = ctx.mode === 'grid' || ctx.mode === 'both'
  for (const axis of AXES) {
    if (useGuides) {
      const g = nearestGuide(rect, ctx.lines, axis, GUIDE_PX / ctx.view.zoom)
      if (g) {
        out[axis] = rect[axis] + g.delta
        guides.push(g)
        continue
      }
      const eq = equalSpacing(rect, ctx.others, axis, GAP_PX / ctx.view.zoom)
      if (eq) {
        out[axis] = rect[axis] + eq.delta
        gaps.push(...eq.pills)
        continue
      }
    }
    if (useGrid) out[axis] = snapValue(rect[axis], ctx.grid)
  }
  return { rect: out, guides, gaps }
}

/** Raw 8-way resize geometry. The opposite edge is fixed unless `fromCenter` (Alt). */
export const resizeRect = (start: Rect, handle: Handle, d: Point, min: Size, fromCenter = false): Rect => {
  const out: Rect = { ...start }
  const e = handleEdges(handle)
  for (const axis of AXES) {
    const side = e[axis]
    if (!side) continue
    const key = SIZE[axis]
    const delta = d[axis]
    if (fromCenter) {
      const c = start[axis] + start[key] / 2
      const size = Math.max(min[key], start[key] + 2 * (side === 'end' ? delta : -delta))
      out[axis] = c - size / 2
      out[key] = size
    } else if (side === 'start') {
      const far = start[axis] + start[key]
      const near = Math.min(start[axis] + delta, far - min[key])
      out[axis] = near
      out[key] = far - near
    } else {
      out[key] = Math.max(min[key], start[key] + delta)
    }
  }
  return out
}

/** Snaps only the edges the handle drags, so the opposite edge cannot drift. */
export const snapResize = (rect: Rect, handle: Handle, ctx: SnapContext, min: Size, fromCenter = false): SnapResult => {
  const out: Rect = { ...rect }
  const guides: AppliedGuide[] = []
  if (ctx.free || ctx.mode === 'off') return { rect: out, guides, gaps: [] }
  const useGuides = ctx.mode === 'guides' || ctx.mode === 'both'
  const useGrid = ctx.mode === 'grid' || ctx.mode === 'both'
  const e = handleEdges(handle)
  for (const axis of AXES) {
    const side = e[axis]
    if (!side) continue
    const key = SIZE[axis]
    let at: number | null = null
    if (useGuides) {
      const g = nearestGuide(rect, ctx.lines, axis, GUIDE_PX / ctx.view.zoom, [side])
      if (g) {
        at = edgeValue(rect, axis, side) + g.delta
        guides.push(g)
      }
    }
    if (at === null && useGrid) at = snapValue(edgeValue(rect, axis, side), ctx.grid)
    if (at === null) continue
    if (fromCenter) {
      const c = rect[axis] + rect[key] / 2
      const size = Math.max(min[key], 2 * Math.abs(c - at))
      out[axis] = c - size / 2
      out[key] = size
    } else if (side === 'start') {
      const far = rect[axis] + rect[key]
      const near = Math.min(at, far - min[key])
      out[axis] = near
      out[key] = far - near
    } else {
      out[key] = Math.max(min[key], at - rect[axis])
    }
  }
  return { rect: out, guides, gaps: [] }
}

/** `p` is viewport-local screen px, so the band is not divided by zoom. */
export const edgeZone = (p: Point, view: Viewport, band = EDGE_PX): Zone | null => {
  if (p.x < -band || p.y < -band || p.x > view.width + band || p.y > view.height + band) return null
  const l = p.x <= band
  const r = p.x >= view.width - band
  const t = p.y <= band
  const b = p.y >= view.height - band
  if (t && l) return 'top-left'
  if (t && r) return 'top-right'
  if (b && l) return 'bottom-left'
  if (b && r) return 'bottom-right'
  if (t) return 'top'
  if (b) return 'bottom'
  if (l) return 'left'
  if (r) return 'right'
  return null
}

/** `natural` is the registry default size, which only the bottom (centre) zone uses. */
export const zoneRect = (zone: Zone, view: Viewport, natural: Size): Rect => {
  const v = visibleRect(view)
  const hw = v.w / 2
  const hh = v.h / 2
  switch (zone) {
    case 'left':
      return { x: v.x, y: v.y, w: hw, h: v.h }
    case 'right':
      return { x: v.x + hw, y: v.y, w: hw, h: v.h }
    case 'top':
      return { x: v.x, y: v.y, w: v.w, h: v.h }
    case 'bottom':
      return { x: v.x + (v.w - natural.w) / 2, y: v.y + (v.h - natural.h) / 2, w: natural.w, h: natural.h }
    case 'top-left':
      return { x: v.x, y: v.y, w: hw, h: hh }
    case 'top-right':
      return { x: v.x + hw, y: v.y, w: hw, h: hh }
    case 'bottom-left':
      return { x: v.x, y: v.y + hh, w: hw, h: hh }
    case 'bottom-right':
      return { x: v.x + hw, y: v.y + hh, w: hw, h: hh }
  }
}

/** Tidy Up: shelf-pack in reading order, exactly `gap` apart, inside the visible rect (blobs are just small squares). */
export const tidyLayout = (rects: WindowRect[], view: Viewport, pitch: number, gap = GAP): WindowRect[] => {
  const v = visibleRect(view)
  const left = snapValue(v.x + gap, Math.max(pitch, 1))
  const limit = v.x + v.w - gap
  let x = left
  let y = snapValue(v.y + gap, Math.max(pitch, 1))
  let shelf = 0
  const out: WindowRect[] = []
  for (const r of rects) {
    if (x > left && x + r.w > limit) {
      x = left
      y += shelf + gap
      shelf = 0
    }
    out.push({ id: r.id, x, y, w: r.w, h: r.h })
    x += r.w + gap
    shelf = Math.max(shelf, r.h)
  }
  return out
}
