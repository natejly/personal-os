/**
 * The canvas store against a recording `fetch`: which windows go live, the live viewport, restore bounds,
 * the layout debounce and its unload flush. A renderer-ish global is enough — nothing here mounts.
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import type { Canvas, CanvasWindow } from '@shared/types'
import { liveWindows } from './Canvas'
import { WIDGETS } from './registry'
import { flushLayoutOnUnload, setLiveViewport, useCanvas, viewport } from './store'
import { toUrl } from './widgets/web'
import type { Viewport } from './snapping'

interface Call { url: string; method: string; keepalive: boolean; body: { windows?: { id: string; x: number }[] } | null }
const calls: Call[] = []
let failNext = new Set<string>()

const g = globalThis as unknown as { window?: unknown; fetch: unknown }
g.window = { innerWidth: 1440, innerHeight: 900, addEventListener: () => undefined, os: {} }
g.fetch = async (url: string, init?: { method?: string; body?: string; keepalive?: boolean }): Promise<unknown> => {
  const method = init?.method ?? 'GET'
  const body = init?.body ? JSON.parse(init.body) : null
  calls.push({ url, method, keepalive: !!init?.keepalive, body })
  const key = `${method} ${url}`
  if (failNext.has(key)) {
    failNext.delete(key)
    return { ok: false, status: 500, statusText: 'boom', json: async () => ({ detail: 'boom' }) }
  }
  return { ok: true, status: 200, statusText: 'OK', json: async () => ({ ok: true, updated: (body?.windows ?? []).length }) }
}

const win = (over: Partial<CanvasWindow> = {}): CanvasWindow => ({
  id: 'w1', canvas_id: 'c1', kind: 'chat', ref_id: null, project_id: null, title: '',
  x: 100, y: 120, w: 400, h: 300, z: 0, state: 'normal', restore_bounds: null, popout_bounds: null,
  pinned: 0, config: {}, created_at: 0, updated_at: 0, ...over
})

const canvas = (id: string, windows: CanvasWindow[], over: Partial<Canvas> = {}): Canvas => ({
  id, name: id, project_id: null, position: 0, snap_mode: 'both', grid_size: 16,
  zoom: 1, pan_x: 0, pan_y: 0, wallpaper: '', locked: 0, created_at: 0, updated_at: 0, windows, ...over
})

const seed = (...cs: Canvas[]): void => {
  calls.length = 0
  failNext = new Set()
  const canvases: Record<string, Canvas> = {}
  for (const c of cs) canvases[c.id] = c
  useCanvas.setState({ canvases, order: cs.map((c) => c.id), activeCanvasId: cs[0].id, focusedWindowId: null, loaded: true })
}

const rowOf = (cid: string, id: string): CanvasWindow => {
  const w = useCanvas.getState().canvases[cid].windows.find((x) => x.id === id)
  assert.ok(w)
  return w
}

const layoutPuts = (): Call[] => calls.filter((c) => c.method === 'PUT' && c.url.includes('/layout'))
const idsOf = (c: Call): string[] => (c.body?.windows ?? []).map((w) => w.id).sort()

// ---- what the registry and liveWindows() agree is live -------------------------------
const view: Viewport = { zoom: 1, panX: 0, panY: 0, width: 4000, height: 3000 }

test('the web widget address bar takes a URL, a bare domain, or words for search', () => {
  assert.equal(toUrl('https://news.ycombinator.com'), 'https://news.ycombinator.com')
  assert.equal(toUrl('HTTP://example.com/a?b=1'), 'HTTP://example.com/a?b=1')
  assert.equal(toUrl('github.com/natejly'), 'https://github.com/natejly')
  assert.equal(toUrl('electron webview docs'), 'https://www.google.com/search?q=electron%20webview%20docs')
  assert.equal(toUrl('recipes'), 'https://www.google.com/search?q=recipes')
  assert.equal(toUrl('   '), '')
})

test('the registry still marks exactly five kinds heavy, which is what the cap counts', () => {
  const heavy = Object.values(WIDGETS).filter((d) => d.heavy).map((d) => d.kind)
  assert.deepEqual([...heavy].sort(), ['calendar', 'dashboard-widget', 'graph', 'usage', 'web'])
})

/** Canvas.tsx renders every window up front (`EAGER`), so the heavy cap is held in reserve, not applied. */
test('every window on the space is live, heavy kinds included', () => {
  const heavy = Array.from({ length: 8 }, (_, i) => win({ id: `h${i}`, kind: 'graph', x: i * 10, y: 0, z: i }))
  assert.equal(liveWindows(heavy, view, false).size, 8)
  const light = Array.from({ length: 8 }, (_, i) => win({ id: `l${i}`, kind: 'note', x: i * 10, y: 0, z: i }))
  assert.equal(liveWindows(light, view, false).size, 8)
})

test('a minimized or popped window has no body, at any zoom and in overview', () => {
  const ws = [win({ id: 'a' }), win({ id: 'b', state: 'minimized' }), win({ id: 'c', state: 'popped' })]
  for (const [zoom, overview] of [[1, false], [0.3, false], [1, true]] as const) {
    assert.deepEqual([...liveWindows(ws, { ...view, zoom }, overview)], ['a'], `zoom=${zoom} overview=${overview}`)
  }
})

// ---- a gesture's values are what viewport() reports, uncommitted --------------------

test('setLiveViewport overrides the stored row for the length of a gesture', () => {
  seed(canvas('c1', [win()]))
  assert.equal(viewport().zoom, 1)
  setLiveViewport({ zoom: 1.75, pan_x: -220, pan_y: -40 })
  const v = viewport()
  assert.deepEqual([v.zoom, v.panX, v.panY], [1.75, -220, -40])
  // Nothing was written: no re-render of the plane, no debounced PUT.
  assert.deepEqual(calls, [])
  assert.equal(useCanvas.getState().canvases['c1'].zoom, 1)
  setLiveViewport(null)
  assert.equal(viewport().zoom, 1)
})

test('store sets per gesture: one commit instead of one per tick', () => {
  seed(canvas('c1', [win()]))
  const TICKS = 60
  let sets = 0
  const stop = useCanvas.subscribe(() => sets++)
  for (let i = 0; i < TICKS; i++) useCanvas.getState().setViewport('c1', { pan_x: -i, pan_y: 0 })
  const before = sets
  sets = 0
  for (let i = 0; i < TICKS; i++) setLiveViewport({ zoom: 1, pan_x: -i, pan_y: 0 })
  setLiveViewport(null)
  useCanvas.getState().setViewport('c1', { zoom: 1, pan_x: -(TICKS - 1), pan_y: 0 })
  const after = sets
  stop()
  console.log(`  ${TICKS} wheel ticks: canvas-store sets before=${before} after=${after}`)
  assert.equal(before, TICKS)
  assert.equal(after, 1)
})

// ---- un-maximize pays the bounds back from any state --------------------------------

test('minimizing a maximized window no longer strands it', async () => {
  seed(canvas('c1', [win({ x: 100, y: 120, w: 400, h: 300 })]))
  const st = useCanvas.getState()
  await st.setWindowState('w1', 'maximized')
  assert.deepEqual(rowOf('c1', 'w1').restore_bounds, { x: 100, y: 120, w: 400, h: 300 })
  const big = rowOf('c1', 'w1')
  assert.ok(big.w > 400 && big.h > 300)

  await st.setWindowState('w1', 'minimized')
  assert.deepEqual(rowOf('c1', 'w1').restore_bounds, { x: 100, y: 120, w: 400, h: 300 })

  await st.setWindowState('w1', 'normal')
  const back = rowOf('c1', 'w1')
  assert.deepEqual([back.x, back.y, back.w, back.h, back.state, back.restore_bounds], [100, 120, 400, 300, 'normal', null])
})

test('re-maximizing a stranded window does not overwrite the bounds it still owes', async () => {
  seed(canvas('c1', [win({ x: 100, y: 120, w: 400, h: 300 })]))
  const st = useCanvas.getState()
  await st.setWindowState('w1', 'maximized')
  await st.setWindowState('w1', 'minimized')
  await st.setWindowState('w1', 'maximized')
  assert.deepEqual(rowOf('c1', 'w1').restore_bounds, { x: 100, y: 120, w: 400, h: 300 })
  await st.setWindowState('w1', 'normal')
  const back = rowOf('c1', 'w1')
  assert.deepEqual([back.x, back.y, back.w, back.h], [100, 120, 400, 300])
})

// ---- flushLayout keeps failed ids dirty, and a space switch flushes -----------------

test('a failed layout PUT leaves its ids dirty for the next flush', async () => {
  seed(canvas('c1', [win({ id: 'w1' }), win({ id: 'w2', x: 600 })]))
  const st = useCanvas.getState()
  st.patchWindow('w1', { x: 222 })
  st.markLayoutDirty(['w1', 'w2'])
  failNext.add('PUT /canvases/c1/layout')
  await st.flushLayout()
  assert.equal(layoutPuts().length, 1)
  // Nothing new marked dirty: the retry only happens because the failed ids were kept.
  await st.flushLayout()
  assert.deepEqual(idsOf(layoutPuts()[1]), ['w1', 'w2'])
  // And a success really does clear them.
  await st.flushLayout()
  assert.equal(layoutPuts().length, 2)
})

test('a space switch flushes the layout before the active canvas moves', async () => {
  seed(canvas('c1', [win({ id: 'w1' })]), canvas('c2', [win({ id: 'w9', canvas_id: 'c2' })]))
  const st = useCanvas.getState()
  st.patchWindow('w1', { x: 333 })
  st.markLayoutDirty(['w1'])
  assert.equal(layoutPuts().length, 0)
  st.setActiveCanvas('c2')
  const puts = layoutPuts()
  assert.equal(puts.length, 1)
  assert.equal(puts[0].url, '/canvases/c1/layout')
  assert.equal(puts[0].body?.windows?.[0].x, 333)
  await new Promise((r) => setTimeout(r, 0))
})

test('the unload flush writes with keepalive, so the teardown cannot outrun it', async () => {
  seed(canvas('c1', [win({ id: 'w1' })]), canvas('c2', [win({ id: 'w9', canvas_id: 'c2' })]))
  const st = useCanvas.getState()
  st.patchWindow('w1', { x: 444 })
  st.patchWindow('w9', { y: 555 })
  st.markLayoutDirty(['w1', 'w9'])
  flushLayoutOnUnload(useCanvas.getState())
  const puts = layoutPuts()
  // One PUT per canvas, every one of them keepalive: an ordinary fetch dies with the document.
  assert.deepEqual(puts.map((c) => c.url).sort(), ['/canvases/c1/layout', '/canvases/c2/layout'])
  assert.deepEqual(puts.map((c) => c.keepalive), [true, true])
  assert.equal(puts.find((c) => c.url.includes('c1'))?.body?.windows?.[0].x, 444)
  // Nothing is carried forward: there is no page left to retry on.
  await st.flushLayout()
  assert.equal(layoutPuts().length, 2)
})

test('the unload flush writes nothing when no geometry is dirty', () => {
  seed(canvas('c1', [win({ id: 'w1' })]))
  flushLayoutOnUnload(useCanvas.getState())
  assert.deepEqual(calls, [])
})

// ---- a locked space is frozen: geometry in, and nothing out ------------------------

test('a locked space refuses every geometry change', async () => {
  seed(canvas('c1', [win({ id: 'w1', x: 100, y: 120 })], { locked: 1 }))
  const st = useCanvas.getState()

  st.setViewport('c1', { zoom: 2, pan_x: -300 })
  assert.equal(useCanvas.getState().canvases['c1'].zoom, 1)

  // A rect that somehow moved is not written back either: there is nothing to persist.
  st.patchWindow('w1', { x: 999 })
  st.markLayoutDirty(['w1'])
  await st.flushLayout()
  assert.deepEqual(layoutPuts(), [])

  await st.setWindowState('w1', 'minimized')
  assert.equal(rowOf('c1', 'w1').state, 'normal')

  assert.equal(await st.openWindow('note'), null)
  await st.closeWindow('w1')
  assert.equal(useCanvas.getState().canvases['c1'].windows.length, 1)

  st.tidyUp()
  await st.deleteSpace('c1')
  assert.ok(useCanvas.getState().canvases['c1'], 'a locked space cannot be deleted either')
  // Every refusal was local: nothing above reached the backend at all.
  assert.deepEqual(calls, [])
})

test('unlocking gives the space back', async () => {
  seed(canvas('c1', [win({ id: 'w1' })], { locked: 1 }))
  const st = useCanvas.getState()
  st.toggleLock()
  assert.equal(useCanvas.getState().canvases['c1'].locked, 0)
  assert.equal(calls.at(-1)?.url, '/canvases/c1')

  await useCanvas.getState().setWindowState('w1', 'minimized')
  assert.equal(rowOf('c1', 'w1').state, 'minimized')
})

test('a locked space still takes focus, titles and config: only geometry is frozen', async () => {
  seed(canvas('c1', [win({ id: 'w1' })], { locked: 1 }))
  const st = useCanvas.getState()
  st.focusWindow('w1')
  assert.equal(useCanvas.getState().focusedWindowId, 'w1')
  await st.setWindowConfig('w1', { url: 'https://example.com' })
  assert.deepEqual(rowOf('c1', 'w1').config, { url: 'https://example.com' })
})

// ---- why the marquee selection has to be filtered to the active space --------------

test('an id from another space really does resolve and delete, which is the hazard', async () => {
  seed(canvas('c1', [win({ id: 'w1' })]), canvas('c2', [win({ id: 'w9', canvas_id: 'c2' })]))
  const st = useCanvas.getState()
  st.setActiveCanvas('c2')
  await useCanvas.getState().closeWindow('w1')
  assert.equal(useCanvas.getState().canvases['c1'].windows.length, 0)
})

test('filtering to the active canvas is what stops it', () => {
  seed(canvas('c1', [win({ id: 'w1' }), win({ id: 'w2' }), win({ id: 'w3' })]), canvas('c2', [win({ id: 'w9', canvas_id: 'c2' })]))
  const selected = ['w1', 'w2', 'w3']
  useCanvas.getState().setActiveCanvas('c2')
  const s = useCanvas.getState()
  // The guard Canvas.tsx applies before closing anything.
  const here = new Set((s.activeCanvasId ? s.canvases[s.activeCanvasId]?.windows ?? [] : []).map((w) => w.id))
  assert.deepEqual(selected.filter((id) => here.has(id)), [])
})
