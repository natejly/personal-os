import test from 'node:test'
import assert from 'node:assert/strict'
import type { Rect, SnapMode } from '@shared/types'
import {
  canvasFromScreen, constrain, edgeZone, equalSpacing, guideLines, resizeRect, screenFromCanvas,
  snapMove, snapResize, snapValue, tidyLayout, visibleRect, zoneRect,
  type SnapContext, type Viewport, type WindowRect
} from './snapping'

const view = (zoom = 1, panX = 0, panY = 0, width = 1000, height = 800): Viewport => ({ zoom, panX, panY, width, height })
const ctx = (o: Partial<SnapContext> & { mode?: SnapMode } = {}): SnapContext => {
  const v = o.view ?? view()
  const others = o.others ?? []
  return { mode: o.mode ?? 'both', grid: o.grid ?? 16, view: v, others, lines: o.lines ?? guideLines(others, v), free: o.free }
}
const rect = (x: number, y: number, w = 200, h = 100): Rect => ({ x, y, w, h })
const win = (id: string, x: number, y: number, w = 200, h = 100): WindowRect => ({ id, x, y, w, h })
const min = { w: 80, h: 60 }
const right = (r: Rect): number => r.x + r.w
const bottom = (r: Rect): number => r.y + r.h

test('grid snap rounds to the pitch and is idempotent', () => {
  assert.equal(snapValue(23, 16), 16)
  assert.equal(snapValue(25, 16), 32)
  const once = snapMove(rect(23, 41), ctx({ mode: 'grid' })).rect
  assert.deepEqual(once, rect(16, 48))
  assert.deepEqual(snapMove(once, ctx({ mode: 'grid' })).rect, once)
})

test('a zero pitch leaves the value alone', () => {
  assert.equal(snapValue(23, 0), 23)
})

test('resizing the left edge does not move the right edge', () => {
  const start = rect(100, 100, 200, 100)
  const raw = resizeRect(start, 'w', { x: -37, y: 0 }, min)
  assert.equal(right(raw), right(start))
  const snapped = snapResize(raw, 'w', ctx({ mode: 'grid' }), min).rect
  assert.equal(right(snapped), right(start))
  assert.equal(snapped.x, 64)
  assert.equal(snapped.w, 236)
})

test('resizing the top edge does not move the bottom edge', () => {
  const start = rect(100, 100, 200, 100)
  const snapped = snapResize(resizeRect(start, 'n', { x: 0, y: -11 }, min), 'n', ctx({ mode: 'grid' }), min).rect
  assert.equal(bottom(snapped), bottom(start))
})

test('a resize never shrinks below the minimum size', () => {
  const r = resizeRect(rect(100, 100, 200, 100), 'w', { x: 400, y: 0 }, min)
  assert.equal(r.w, min.w)
  assert.equal(right(r), 300)
})

test('an Alt resize keeps the centre fixed', () => {
  const start = rect(100, 100, 200, 100)
  const r = resizeRect(start, 'e', { x: 40, y: 0 }, min, true)
  assert.equal(r.w, 280)
  assert.equal(r.x + r.w / 2, start.x + start.w / 2)
})

test('a guide inside the threshold snaps and one outside it does not', () => {
  const others = [win('a', 500, 400)]
  const near = snapMove(rect(496, 40), ctx({ mode: 'guides', others }))
  assert.equal(near.rect.x, 500)
  assert.equal(near.guides.length, 1)
  assert.equal(near.guides[0].id, 'a')
  assert.equal(near.guides[0].edge, 'start')
  const far = snapMove(rect(492, 40), ctx({ mode: 'guides', others }))
  assert.equal(far.rect.x, 492)
  assert.deepEqual(far.guides, [])
})

test('the guide threshold scales with zoom: the same screen distance snaps at 0.5 and at 2.0', () => {
  const others = [win('a', 500, 400)]
  // 4 screen px away at each zoom: 8 canvas pt at 0.5, 2 canvas pt at 2.0.
  const out = snapMove(rect(492, 40), ctx({ mode: 'guides', others, view: view(0.5) }))
  assert.equal(out.rect.x, 500)
  const inn = snapMove(rect(498, 40), ctx({ mode: 'guides', others, view: view(2) }))
  assert.equal(inn.rect.x, 500)
  // 8 canvas pt is 16 screen px at zoom 2.0, well outside the 6 px threshold.
  assert.equal(snapMove(rect(492, 40), ctx({ mode: 'guides', others, view: view(2) })).rect.x, 492)
})

test('only the nearest guide per axis is applied', () => {
  const others = [win('a', 500, 400), win('b', 503, 600)]
  const out = snapMove(rect(504, 40), ctx({ mode: 'guides', others }))
  assert.equal(out.guides.length, 1)
  assert.equal(out.guides[0].id, 'b')
  assert.equal(out.rect.x, 503)
})

test('one guide lands on each axis at most', () => {
  const others = [win('a', 500, 400)]
  const out = snapMove(rect(497, 403), ctx({ mode: 'guides', others }))
  assert.deepEqual(out.guides.map((g) => g.axis), ['x', 'y'])
  assert.equal(out.rect.x, 500)
  assert.equal(out.rect.y, 400)
})

test('the viewport centre lines are guide candidates', () => {
  const v = view(1, 0, 0, 1000, 800)
  const out = snapMove(rect(398, 40), ctx({ mode: 'guides', view: v }))
  assert.equal(out.guides[0].id, null)
  assert.equal(out.rect.x + out.rect.w / 2, 500)
})

test('with snap off nothing moves', () => {
  const others = [win('a', 500, 400)]
  const out = snapMove(rect(496, 41), ctx({ mode: 'off', others }))
  assert.deepEqual(out.rect, rect(496, 41))
  const free = snapMove(rect(496, 41), ctx({ mode: 'both', others, free: true }))
  assert.deepEqual(free.rect, rect(496, 41))
})

test('a guide beats the grid on the same axis, and the grid still runs on the other', () => {
  const others = [win('a', 703, 400)]
  const out = snapMove(rect(700, 530), ctx({ mode: 'both', others }))
  assert.equal(out.rect.x, 703)
  assert.equal(out.rect.y, 528)
})

test('equal spacing fires at nearly equal gaps and centres the rect', () => {
  const others = [win('a', 0, 0, 100, 100), win('c', 400, 0, 100, 100)]
  const eq = equalSpacing(rect(199, 0, 100, 100), others, 'x', 2)
  assert.ok(eq)
  assert.equal(eq.delta, 1)
  assert.equal(eq.pills.length, 2)
  assert.deepEqual(eq.pills.map((p) => p.to - p.from), [100, 100])
})

test('equal spacing does not fire when the gaps are far apart', () => {
  const others = [win('a', 0, 0, 100, 100), win('c', 400, 0, 100, 100)]
  assert.equal(equalSpacing(rect(150, 0, 100, 100), others, 'x', 2), null)
})

test('equal spacing ignores windows in another row', () => {
  const others = [win('a', 0, 400, 100, 100), win('c', 400, 400, 100, 100)]
  assert.equal(equalSpacing(rect(199, 0, 100, 100), others, 'x', 2), null)
})

test('equal spacing matches a gap that already exists on one side', () => {
  const others = [win('a', 0, 0, 100, 100), win('b', 150, 0, 100, 100)]
  const eq = equalSpacing(rect(299, 0, 100, 100), others, 'x', 2)
  assert.ok(eq)
  assert.equal(eq.delta, 1)
  assert.deepEqual(eq.pills.map((p) => p.to - p.from), [50, 50])
})

test('snapMove applies equal spacing when no guide is in range', () => {
  const others = [win('a', 0, 0, 100, 100), win('c', 400, 0, 100, 100)]
  const out = snapMove(rect(199, 0, 100, 100), ctx({ mode: 'guides', others }))
  assert.equal(out.rect.x, 200)
  assert.equal(out.gaps.length, 2)
})

test('each edge zone maps to the right rect for a known viewport', () => {
  const v = view(1, 0, 0, 1000, 800)
  const natural = { w: 400, h: 300 }
  assert.deepEqual(zoneRect('left', v, natural), { x: 0, y: 0, w: 500, h: 800 })
  assert.deepEqual(zoneRect('right', v, natural), { x: 500, y: 0, w: 500, h: 800 })
  assert.deepEqual(zoneRect('top', v, natural), { x: 0, y: 0, w: 1000, h: 800 })
  assert.deepEqual(zoneRect('bottom', v, natural), { x: 300, y: 250, w: 400, h: 300 })
  assert.deepEqual(zoneRect('top-left', v, natural), { x: 0, y: 0, w: 500, h: 400 })
  assert.deepEqual(zoneRect('top-right', v, natural), { x: 500, y: 0, w: 500, h: 400 })
  assert.deepEqual(zoneRect('bottom-left', v, natural), { x: 0, y: 400, w: 500, h: 400 })
  assert.deepEqual(zoneRect('bottom-right', v, natural), { x: 500, y: 400, w: 500, h: 400 })
})

test('edge zones follow pan and zoom', () => {
  const v = view(2, -200, -100, 1000, 800)
  assert.deepEqual(zoneRect('left', v, { w: 1, h: 1 }), { x: 100, y: 50, w: 250, h: 400 })
})

test('a pointer near an edge picks that zone, and the middle picks none', () => {
  const v = view(1, 0, 0, 1000, 800)
  assert.equal(edgeZone({ x: 3, y: 400 }, v), 'left')
  assert.equal(edgeZone({ x: 997, y: 400 }, v), 'right')
  assert.equal(edgeZone({ x: 500, y: 2 }, v), 'top')
  assert.equal(edgeZone({ x: 500, y: 798 }, v), 'bottom')
  assert.equal(edgeZone({ x: 2, y: 2 }, v), 'top-left')
  assert.equal(edgeZone({ x: 998, y: 795 }, v), 'bottom-right')
  assert.equal(edgeZone({ x: 500, y: 400 }, v), null)
  assert.equal(edgeZone({ x: 500, y: 900 }, v), null)
})

test('canvasFromScreen and screenFromCanvas round-trip', () => {
  const views = [view(1, 0, 0), view(0.5, 120, -80), view(2, -340, 610), view(1.37, 12.5, 0.25)]
  for (const v of views) {
    for (const p of [{ x: 0, y: 0 }, { x: 640, y: 480 }, { x: -220.5, y: 90.25 }]) {
      const back = canvasFromScreen(screenFromCanvas(p, v), v)
      assert.ok(Math.abs(back.x - p.x) < 1e-9 && Math.abs(back.y - p.y) < 1e-9)
      const fwd = screenFromCanvas(canvasFromScreen(p, v), v)
      assert.ok(Math.abs(fwd.x - p.x) < 1e-9 && Math.abs(fwd.y - p.y) < 1e-9)
    }
  }
})

test('the visible rect is the viewport in canvas space', () => {
  assert.deepEqual(visibleRect(view(0.5, -100, -50, 1000, 800)), { x: 200, y: 100, w: 2000, h: 1600 })
})

test('shift constrains a drag to its dominant axis', () => {
  assert.deepEqual(constrain({ x: 40, y: 9 }), { x: 40, y: 0 })
  assert.deepEqual(constrain({ x: -3, y: 22 }), { x: 0, y: 22 })
})

test('guideLines emits three lines per axis per window, a GAP line each side, plus the two viewport centres', () => {
  const lines = guideLines([win('a', 0, 0, 100, 100)], view())
  assert.equal(lines.length, 12)
  assert.deepEqual(lines.filter((l) => l.axis === 'x').map((l) => l.at), [0, 50, 100, 112, -12, 500])
})

test('tidy up packs GAP apart in reading order and wraps', () => {
  const out = tidyLayout([win('a', 9, 9, 300, 200), win('b', 500, 40, 300, 200), win('c', 80, 600, 300, 200)], view(1, 0, 0, 700, 800), 16)
  assert.deepEqual(out.map((r) => [r.x, r.y]), [[16, 16], [328, 16], [16, 228]])
  assert.deepEqual(out.map((r) => r.w), [300, 300, 300])
})

test('a dragged rect snaps its edge to exactly GAP from a neighbour', () => {
  const others = [win('a', 0, 0, 100, 100)]
  const ctx = { mode: 'guides' as const, grid: 16, view: view(), others, lines: guideLines(others, view()) }
  // start edge near a's end + GAP (112)
  assert.equal(snapMove({ x: 115, y: 300, w: 80, h: 80 }, ctx).rect.x, 112)
  // end edge near a's start - GAP (-12)
  assert.equal(snapMove({ x: -95, y: 300, w: 80, h: 80 }, ctx).rect.x, -92)
})
