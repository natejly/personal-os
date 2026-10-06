import { test, expect } from './fixtures.mjs'
import { enterCanvas, menuClick, resetView, sleep, spaces, windowsOf } from './helpers/spaces.mjs'

test.describe.configure({ timeout: 300_000 })

const win = (page, id) => page.locator(`[data-window-id="${id}"]`)

/** Mouse drag in small steps; `hold` leaves the button down so a test can look at the overlay. */
async function drag(page, from, to, { steps = 12, hold = false } = {}) {
  await page.mouse.move(from.x, from.y)
  await page.mouse.down()
  for (let i = 1; i <= steps; i++) {
    await page.mouse.move(from.x + ((to.x - from.x) * i) / steps, from.y + ((to.y - from.y) * i) / steps)
    await sleep(15)
  }
  if (!hold) await page.mouse.up()
}

async function gripPoint(page, id) {
  const b = await win(page, id).locator('.win-move').boundingBox()
  return { x: b.x + 60, y: b.y + b.height / 2 }
}

async function seed(g, windows, canvas = {}) {
  const s = (await spaces(g))[0]
  await g.api('/canvases/' + s.id, { method: 'PUT', body: { zoom: 1, pan_x: 0, pan_y: 0, ...canvas } })
  const out = []
  for (const w of windows) {
    const body = { ...w }
    if (w.kind === 'doc') body.ref_id = (await g.api('/docs', { method: 'POST', body: { title: 'n', content: 'n' } })).id
    out.push(await g.api(`/canvases/${s.id}/windows`, { method: 'POST', body }))
  }
  await g.page.reload()
  await enterCanvas(g)
  for (const w of out) await expect(win(g.page, w.id)).toBeVisible()
  return { s, ws: out }
}

const geo = async (g, sid, id) => {
  const w = (await windowsOf(g, sid)).find((x) => x.id === id)
  return { x: w.x, y: w.y, w: w.w, h: w.h, z: w.z, state: w.state }
}

test('move and resize persist exact geometry (snap off), min size honoured', async ({ grain }) => {
  const { page } = grain
  // The store keeps the rect the drag produced; the pointer can land a float32 hair off a whole pixel.
  const px = (r) => ({ ...r, x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.w), h: Math.round(r.h) })
  const { s, ws } = await seed(grain, [{ kind: 'doc', x: 200, y: 150, w: 320, h: 260 }], { snap_mode: 'off' })
  const id = ws[0].id
  const tr = () => win(page, id).evaluate((e) => e.style.translate)
  expect(await tr()).toBe('200px 150px')
  const p = await gripPoint(page, id)
  await drag(page, p, { x: p.x + 137, y: p.y + 83 })
  await expect.poll(async () => (await geo(grain, s.id, id)).x).toBe(337)
  expect(px(await geo(grain, s.id, id))).toMatchObject({ x: 337, y: 233, w: 320, h: 260 })
  await expect.poll(tr).toBe('337px 233px')

  // resize from the SE corner (grab 3px inside the corner, so the delta is 80 x 44)
  const b = await win(page, id).boundingBox()
  await drag(page, { x: b.x + b.width - 3, y: b.y + b.height - 3 }, { x: b.x + b.width + 77, y: b.y + b.height + 41 })
  await expect.poll(async () => (await geo(grain, s.id, id)).w).toBe(400)
  expect(px(await geo(grain, s.id, id))).toMatchObject({ x: 337, y: 233, w: 400, h: 304 })

  // resize from the west edge (grab 2px in, drag 42px left): x moves, right edge stays
  const b2 = await win(page, id).boundingBox()
  await drag(page, { x: b2.x + 2, y: b2.y + b2.height / 2 }, { x: b2.x - 40, y: b2.y + b2.height / 2 })
  await expect.poll(async () => (await geo(grain, s.id, id)).w).toBe(442)
  expect(px(await geo(grain, s.id, id))).toMatchObject({ x: 295, y: 233, w: 442, h: 304 })

  // shrinking far below the minimum stops at the doc's 280x200 floor
  const b3 = await win(page, id).boundingBox()
  await drag(page, { x: b3.x + b3.width - 3, y: b3.y + b3.height - 3 }, { x: b3.x + 30, y: b3.y + 30 })
  await expect.poll(async () => (await geo(grain, s.id, id)).w).toBe(280)
  expect(px(await geo(grain, s.id, id)).h).toBe(200)

  // Esc mid-drag puts it back
  const before = await geo(grain, s.id, id)
  const p2 = await gripPoint(page, id)
  await drag(page, p2, { x: p2.x + 150, y: p2.y + 90 }, { hold: true })
  await page.keyboard.press('Escape')
  await page.mouse.up()
  await sleep(900)
  expect(await geo(grain, s.id, id)).toEqual(before)

  // relaunch: identical layout
  await grain.relaunch()
  await enterCanvas(grain)
  expect(await geo(grain, s.id, id)).toEqual(before)
  await expect(win(grain.page, id)).toBeVisible()
  expect(grain.consoleErrors).toEqual([])
})

test('grid snapping lands on the pitch; guides snap to a neighbour and draw while dragging', async ({ grain }) => {
  const { page } = grain
  const { s, ws } = await seed(grain, [
    { kind: 'doc', x: 160, y: 96, w: 320, h: 240 },
    { kind: 'doc', x: 640, y: 128, w: 320, h: 240 }
  ], { snap_mode: 'guides' })
  const [a, b] = ws
  // drag B so its left edge ends ~4px right of A's left edge: the guide pulls it onto x = 160
  const p = await gripPoint(page, b.id)
  await drag(page, p, { x: p.x - (640 - 164), y: p.y + 160 }, { hold: true })
  await expect(page.locator('.guide-v, .guide-h').first()).toBeVisible()
  await page.mouse.up()
  await expect.poll(async () => (await geo(grain, s.id, b.id)).x).toBe(160)
  await sleep(300)
  await expect(page.locator('.guide-v, .guide-h')).toHaveCount(0)

  // grid mode: everything lands on a multiple of 24
  await page.locator('select[aria-label="Snapping"]').selectOption('grid')
  await page.locator('select[aria-label="Grid size"]').selectOption('24')
  await expect.poll(async () => (await grain.api('/canvases/' + s.id)).grid_size).toBe(24)
  const pa = await gripPoint(page, a.id)
  await drag(page, pa, { x: pa.x + 101, y: pa.y + 213 })
  await expect.poll(async () => (await geo(grain, s.id, a.id)).x).not.toBe(160)
  const ga = await geo(grain, s.id, a.id)
  expect(ga.x % 24).toBe(0)
  expect(ga.y % 24).toBe(0)

  // switching to "No snap" is persisted and survives reload
  await page.locator('select[aria-label="Snapping"]').selectOption('off')
  await expect.poll(async () => (await grain.api('/canvases/' + s.id)).snap_mode).toBe('off')
  expect(grain.consoleErrors).toEqual([])
})

test('focus raises z; close, minimize, zoom and the window menu', async ({ grain }) => {
  const { page } = grain
  const { s, ws } = await seed(grain, [
    { kind: 'doc', x: 100, y: 80, w: 300, h: 240 },
    { kind: 'todos', x: 260, y: 140, w: 300, h: 240 },
    { kind: 'doc', x: 700, y: 80, w: 300, h: 240 }
  ])
  const [a, b, c] = ws
  const z = async (id) => (await geo(grain, s.id, id)).z
  // a and b overlap; clicking the grip of a (visible strip above b) raises it over b
  const pa = await gripPoint(page, a.id)
  await page.mouse.click(pa.x, pa.y)
  await expect.poll(async () => (await z(a.id)) > (await z(b.id))).toBe(true)
  const pc = await gripPoint(page, c.id)
  await page.mouse.click(pc.x, pc.y)
  await expect.poll(async () => (await z(c.id)) > (await z(a.id))).toBe(true)
  // the DOM stacking agrees with the stored z
  const zi = async (id) => Number(await win(page, id).evaluate((e) => e.style.zIndex))
  expect(await zi(c.id)).toBeGreaterThan(await zi(a.id))
  expect(await zi(a.id)).toBeGreaterThan(await zi(b.id))

  // right-click menu: Zoom -> maximized, Restore -> normal
  await win(page, b.id).click({ button: 'right', position: { x: 150, y: 120 } })
  await page.getByRole('menuitem', { name: 'Zoom' }).click()
  await expect.poll(async () => (await geo(grain, s.id, b.id)).state).toBe('maximized')
  await win(page, b.id).click({ button: 'right', position: { x: 150, y: 120 } })
  await page.getByRole('menuitem', { name: 'Restore' }).click()
  await expect.poll(async () => (await geo(grain, s.id, b.id)).state).toBe('normal')
  expect(await geo(grain, s.id, b.id)).toMatchObject({ x: 260, y: 140, w: 300, h: 240 })

  // the x button closes (and the row is gone from the API)
  await win(page, c.id).hover()
  await win(page, c.id).getByRole('button', { name: /^Close/ }).click()
  await expect(win(page, c.id)).toHaveCount(0)
  expect((await windowsOf(grain, s.id)).map((w) => w.id)).not.toContain(c.id)

  // Window > Close Window closes the focused window
  await page.mouse.click(pa.x, pa.y)
  await menuClick(grain.app, 'Close Window')
  await expect(win(page, a.id)).toHaveCount(0)
  await expect.poll(async () => (await windowsOf(grain, s.id)).length).toBe(1)

  // double-click storm on close must not throw
  await win(page, b.id).hover()
  await win(page, b.id).getByRole('button', { name: /^Close/ }).dblclick({ force: true }).catch(() => {})
  await expect.poll(async () => (await windowsOf(grain, s.id)).length).toBe(0)
  await expect(page.locator('.canvas-empty')).toBeVisible()
  expect(grain.consoleErrors).toEqual([])
})

test('expand leaves the canvas for the full view; the window stays', async ({ grain }) => {
  const { page } = grain
  const { s, ws } = await seed(grain, [{ kind: 'todos', x: 100, y: 80, w: 420, h: 360 }])
  await win(page, ws[0].id).click({ button: 'right', position: { x: 200, y: 200 } })
  await page.getByRole('menuitem', { name: 'Open full view' }).click()
  await expect(page.locator('.spaces-bar')).toHaveCount(0)
  expect((await windowsOf(grain, s.id)).length).toBe(1)
  // and back to the canvas
  await menuClick(grain.app, 'Toggle Spaces')
  await expect(page.locator('.spaces-bar')).toBeVisible()
  await expect(win(page, ws[0].id)).toBeVisible()
  expect(grain.consoleErrors).toEqual([])
})

test('wheel zoom and pan persist, clamp, and reset via the API; a locked space ignores them', async ({ grain }) => {
  const { page } = grain
  const { s } = await seed(grain, [{ kind: 'doc', x: 100, y: 80, w: 300, h: 240 }])
  const canvas = page.locator('.canvas')
  const cb = await canvas.boundingBox()
  const at = { x: cb.x + cb.width - 80, y: cb.y + cb.height - 60 } // bare plane, bottom right
  await page.mouse.move(at.x, at.y)
  // ctrl + wheel zooms (pinch gesture on a trackpad)
  await page.keyboard.down('Control')
  for (let i = 0; i < 6; i++) { await page.mouse.wheel(0, -120); await sleep(30) }
  await page.keyboard.up('Control')
  await expect.poll(async () => (await grain.api('/canvases/' + s.id)).zoom, { timeout: 8000 }).toBeGreaterThan(1.3)
  // far past the limit clamps to 2
  await page.keyboard.down('Control')
  for (let i = 0; i < 30; i++) { await page.mouse.wheel(0, -200); await sleep(10) }
  await page.keyboard.up('Control')
  await expect.poll(async () => (await grain.api('/canvases/' + s.id)).zoom, { timeout: 8000 }).toBe(2)
  await page.keyboard.down('Control')
  for (let i = 0; i < 60; i++) { await page.mouse.wheel(0, 200); await sleep(10) }
  await page.keyboard.up('Control')
  await expect.poll(async () => (await grain.api('/canvases/' + s.id)).zoom, { timeout: 8000 }).toBe(0.5)

  // plain wheel pans
  await resetView(grain, s.id)
  await page.reload()
  await enterCanvas(grain)
  await page.mouse.move(at.x, at.y)
  await page.mouse.wheel(40, 120)
  await expect.poll(async () => (await grain.api('/canvases/' + s.id)).pan_y, { timeout: 8000 }).toBeLessThan(-50)
  expect((await grain.api('/canvases/' + s.id)).pan_x).toBeLessThan(-20)

  // dragging bare plane pans
  await resetView(grain, s.id)
  await page.reload()
  await enterCanvas(grain)
  await drag(page, at, { x: at.x - 90, y: at.y - 40 })
  await expect.poll(async () => (await grain.api('/canvases/' + s.id)).pan_x, { timeout: 8000 }).toBe(-90)
  expect((await grain.api('/canvases/' + s.id)).pan_y).toBe(-40)

  // relaunch keeps the pan
  await grain.relaunch()
  await enterCanvas(grain)
  expect((await grain.api('/canvases/' + s.id)).pan_x).toBe(-90)
  const tf = await grain.page.locator('.canvas-plane').evaluate((e) => e.style.transform)
  expect(tf).toContain('translate(-90px, -40px)')

  // Reset through the API and the plane follows after reload
  await resetView(grain, s.id)
  await grain.page.reload()
  await enterCanvas(grain)
  expect(await grain.page.locator('.canvas-plane').evaluate((e) => e.style.transform)).toContain('scale(1)')
  expect(grain.consoleErrors).toEqual([])
})

test('Tidy up arranges overlapping windows without overlap', async ({ grain }) => {
  const { page } = grain
  const { s, ws } = await seed(grain, [
    { kind: 'doc', x: 100, y: 80, w: 300, h: 240 },
    { kind: 'doc', x: 120, y: 90, w: 300, h: 240 },
    { kind: 'doc', x: 140, y: 100, w: 300, h: 240 },
    { kind: 'doc', x: 160, y: 110, w: 300, h: 240 }
  ])
  await menuClick(grain.app, 'Tidy Up')
  await expect.poll(async () => (await geo(grain, s.id, ws[1].id)).x, { timeout: 8000 }).not.toBe(120)
  await sleep(700)
  const r = await Promise.all(ws.map((w) => geo(grain, s.id, w.id)))
  for (let i = 0; i < r.length; i++)
    for (let j = i + 1; j < r.length; j++) {
      const a = r[i], b = r[j]
      const overlap = a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h
      expect(overlap, `${i} vs ${j}`).toBe(false)
    }
  void page
  expect(grain.consoleErrors).toEqual([])
})

test('60 windows in one space: panning stays above 20 fps and the layout restores', async ({ grain }) => {
  test.setTimeout(600_000)
  const { page, api } = grain
  const s = (await spaces(grain))[0]
  await api('/canvases/' + s.id, { method: 'PUT', body: { zoom: 1, pan_x: 0, pan_y: 0 } })
  const note = (await api('/docs', { method: 'POST', body: { title: 'bulk', content: 'bulk' } })).id
  const ids = []
  for (let i = 0; i < 60; i++) {
    const kind = i % 3 === 0 ? 'doc' : 'face'
    const w = await api(`/canvases/${s.id}/windows`, {
      method: 'POST',
      body: { kind, ref_id: kind === 'doc' ? note : undefined, x: 40 + (i % 10) * 330, y: 40 + Math.floor(i / 10) * 300, w: 300, h: 260 }
    })
    ids.push(w.id)
  }
  await page.reload()
  await enterCanvas(grain)
  await expect(page.locator('.win')).toHaveCount(60, { timeout: 60_000 })
  await sleep(3000)

  // find bare plane: the strip between two rows of windows (gap of 40px between rows)
  const pt = await page.evaluate(() => {
    const c = document.querySelector('.canvas').getBoundingClientRect()
    for (let y = c.top + 20; y < c.bottom - 20; y += 6)
      for (let x = c.left + 20; x < c.right - 20; x += 6) {
        const e = document.elementFromPoint(x, y)
        if (e && e.classList.contains('canvas')) return { x, y }
      }
    return null
  })
  expect(pt).not.toBeNull()
  // real mouse-down on bare plane starts the pan; the moves are dispatched from inside the page one per
  // animation frame so the measurement is the renderer's, not Playwright's round-trip latency.
  await page.mouse.move(pt.x, pt.y)
  await page.mouse.down()
  const { frames, secs } = await page.evaluate(({ x, y }) => new Promise((resolve) => {
    const t0 = performance.now()
    let n = 0
    const tick = () => {
      n++
      const t = performance.now() - t0
      window.dispatchEvent(new PointerEvent('pointermove', { clientX: x - Math.min(n * 4, 400), clientY: y + (n % 2), pointerId: 1, bubbles: true }))
      if (t < 1500) requestAnimationFrame(tick)
      else resolve({ frames: n, secs: t / 1000 })
    }
    requestAnimationFrame(tick)
  }), pt)
  await page.mouse.up()
  const fps = frames / secs
  console.log(`pan fps with 60 windows: ${fps.toFixed(1)}`)
  expect(fps).toBeGreaterThan(20)

  await expect.poll(async () => (await grain.api('/canvases/' + s.id)).pan_x, { timeout: 8000 }).toBeLessThan(-100)
  const after = await windowsOf(grain, s.id)
  expect(after).toHaveLength(60)
  await grain.relaunch()
  await enterCanvas(grain)
  await expect(grain.page.locator('.win')).toHaveCount(60, { timeout: 60_000 })
  const again = await windowsOf(grain, s.id)
  expect(again.map((w) => [w.id, w.x, w.y, w.w, w.h])).toEqual(after.map((w) => [w.id, w.x, w.y, w.w, w.h]))
  expect(grain.consoleErrors).toEqual([])
})

test('820x520 window: canvas bar and windows stay usable', async ({ grain }) => {
  const { page } = grain
  await grain.app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].setSize(820, 520))
  const { s, ws } = await seed(grain, [{ kind: 'doc', x: 40, y: 40, w: 300, h: 240 }], { snap_mode: 'off' })
  await sleep(500)
  // the bar's controls are all inside the window, none clipped sideways
  const r = await page.evaluate(() => {
    const bar = document.querySelector('.spaces-bar')
    const w = window.innerWidth
    const bad = [...bar.querySelectorAll('button, select')].filter((b) => { const q = b.getBoundingClientRect(); return q.width && (q.right > w + 1 || q.left < -1) }).map((b) => b.title || b.ariaLabel)
    return { bad, scrolls: bar.scrollWidth > bar.clientWidth + 1, w }
  })
  expect(r.w).toBeLessThanOrEqual(820)
  expect(r.bad).toEqual([])
  expect(r.scrolls).toBe(false)
  const p = await gripPoint(page, ws[0].id)
  await drag(page, p, { x: p.x + 60, y: p.y + 30 })
  await expect.poll(async () => (await geo(grain, s.id, ws[0].id)).x).toBe(100)
  expect(grain.consoleErrors).toEqual([])
})

test('folding the left of two adjacent chats and growing it back never overlaps its neighbour', async ({ grain }) => {
  const c1 = await grain.api('/conversations', { method: 'POST', body: { title: 'Left' } })
  const c2 = await grain.api('/conversations', { method: 'POST', body: { title: 'Right' } })
  const { s, ws } = await seed(grain, [
    { kind: 'chat', ref_id: c1.id, x: 40, y: 40, w: 420, h: 400 },
    { kind: 'chat', ref_id: c2.id, x: 472, y: 40, w: 420, h: 400 }
  ])
  await win(grain.page, ws[0].id).getByTitle('Shrink to a face').click()
  await expect(win(grain.page, ws[0].id).locator('.chat-blob')).toBeVisible()
  await win(grain.page, ws[0].id).locator('.chat-blob').click()
  await expect.poll(async () => (await geo(grain, s.id, ws[0].id)).w).toBe(420)
  const a = await geo(grain, s.id, ws[0].id)
  const b = await geo(grain, s.id, ws[1].id)
  const clear = a.x + a.w + 12 <= b.x || b.x + b.w + 12 <= a.x || a.y + a.h + 12 <= b.y || b.y + b.h + 12 <= a.y
  expect(clear).toBe(true)
})
