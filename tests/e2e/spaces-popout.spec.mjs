import { test, expect } from './fixtures.mjs'
import { enterCanvas, menuClick, sleep, spaces, windowsOf } from './helpers/spaces.mjs'

test.describe.configure({ timeout: 300_000 })

const win = (page, id) => page.locator(`[data-window-id="${id}"]`)
const popWindows = (g) => g.app.windows().filter((p) => p.url().includes('surface=widget'))

/** Focus a BrowserWindow whose page url contains `needle` (menu actions go to the focused window). */
const focusWhere = (g, needle) => g.app.evaluate(({ BrowserWindow }, needle) => {
  const w = BrowserWindow.getAllWindows().find((b) => b.webContents.getURL().includes(needle))
  if (w) { w.show(); w.focus() }
  return !!w
}, needle)

async function seedFace(g, extra = {}) {
  const s = (await spaces(g))[0]
  const w = await g.api(`/canvases/${s.id}/windows`, { method: 'POST', body: { kind: 'face', x: 120, y: 100, w: 240, h: 240, ...extra } })
  await g.page.reload()
  await enterCanvas(g)
  await expect(win(g.page, w.id)).toBeVisible()
  return { s, w }
}

async function popOut(g, w) {
  const grip = await win(g.page, w.id).locator('.win-move').boundingBox()
  await g.page.mouse.click(grip.x + 40, grip.y + 8)
  await focusWhere(g, 'index.html')
  const before = g.app.windows().length
  await menuClick(g.app, 'Pop Out')
  await expect.poll(() => g.app.windows().length, { timeout: 30_000 }).toBeGreaterThan(before)
  await expect.poll(async () => (await windowsOf(g, (await spaces(g))[0].id)).find((x) => x.id === w.id).state).toBe('popped')
  // Menu actions are dropped until the surface has loaded its window row; wait for the widget body.
  await expect.poll(() => popWindows(g).length).toBeGreaterThan(0)
  await popWindows(g)[0].locator('.popout-body .face').waitFor()
}

test('pop out a window: a second BrowserWindow appears, the canvas shows a ghost, Return brings it back', async ({ grain }) => {
  const { page } = grain
  const { s, w } = await seedFace(grain)
  const n0 = grain.app.windows().length
  await popOut(grain, w)
  expect(grain.app.windows().length).toBe(n0 + 1)
  await expect(win(page, w.id).getByText('Detached to its own window')).toBeVisible()
  // the pop-out renders the widget (no console errors in either renderer)
  const pop = popWindows(grain)[0]
  await pop.waitForLoadState('domcontentloaded')
  await sleep(1500)
  await expect(pop.locator('.face, .widget, .face-widget').first()).toBeVisible({ timeout: 20_000 })

  // popping out an already popped window again does not open a third window
  await focusWhere(grain, 'index.html')
  await menuClick(grain.app, 'Pop Out')
  await sleep(1000)
  expect(popWindows(grain)).toHaveLength(1)

  // Return to canvas through the ghost button
  await win(page, w.id).getByRole('button', { name: 'Return to canvas' }).click()
  await expect.poll(async () => (await windowsOf(grain, s.id))[0].state).toBe('normal')
  await expect.poll(() => grain.app.windows().length).toBe(n0)
  await expect(win(page, w.id).locator('.win-body')).not.toContainText('Detached')
  expect(grain.consoleErrors).toEqual([])
})

test('closing the pop-out OS window returns the widget to the canvas', async ({ grain }) => {
  const { page } = grain
  const { s, w } = await seedFace(grain)
  const n0 = grain.app.windows().length
  await popOut(grain, w)
  await grain.app.evaluate(({ BrowserWindow }) => {
    BrowserWindow.getAllWindows().find((b) => b.webContents.getURL().includes('surface=widget'))?.close()
  })
  await expect.poll(() => grain.app.windows().length, { timeout: 20_000 }).toBe(n0)
  await expect.poll(async () => (await windowsOf(grain, s.id))[0].state).toBe('normal')
  await expect(win(page, w.id).locator('.win-body')).not.toContainText('Detached')
  expect(grain.consoleErrors).toEqual([])
})

test('pop-out opacity: more / less transparent shortcuts step the level and persist', async ({ grain }) => {
  const { s, w } = await seedFace(grain)
  await popOut(grain, w)
  const pop = popWindows(grain)[0]
  await pop.waitForLoadState('domcontentloaded')
  const opacityNow = () => grain.app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows().find((b) => b.webContents.getURL().includes('surface=widget'))?.getOpacity())
  expect(await opacityNow()).toBe(1)

  await focusWhere(grain, 'surface=widget')
  await menuClick(grain.app, 'More Transparent')
  await expect.poll(opacityNow, { timeout: 15_000 }).toBeLessThan(1)
  const o1 = await opacityNow()
  await expect.poll(async () => (await windowsOf(grain, s.id))[0].opacity).toBeCloseTo(o1, 1)
  await focusWhere(grain, 'surface=widget')
  await menuClick(grain.app, 'More Transparent')
  await expect.poll(opacityNow, { timeout: 15_000 }).toBeLessThan(o1)
  // hammer it past the floor: clamped to 0.2
  for (let i = 0; i < 12; i++) { await focusWhere(grain, 'surface=widget'); await menuClick(grain.app, 'More Transparent') }
  await expect.poll(opacityNow, { timeout: 15_000 }).toBeGreaterThanOrEqual(0.2)
  expect(await opacityNow()).toBeLessThanOrEqual(0.5)
  const floor = await opacityNow()
  await focusWhere(grain, 'surface=widget')
  await menuClick(grain.app, 'Less Transparent')
  await expect.poll(opacityNow, { timeout: 15_000 }).toBeGreaterThan(floor)
  // the named level menu
  await focusWhere(grain, 'surface=widget')
  await menuClick(grain.app, 'Opaque')
  await expect.poll(opacityNow, { timeout: 15_000 }).toBe(1)
  expect(grain.consoleErrors).toEqual([])
})

test('a popped-out window comes back after a relaunch, and a canvas relaunch with no pop-out opens only one window', async ({ grain }) => {
  const { s, w } = await seedFace(grain)
  await popOut(grain, w)
  await grain.relaunch()
  await expect.poll(() => grain.app.windows().filter((p) => p.url().includes('surface=widget')).length, { timeout: 40_000 }).toBe(1)
  expect((await windowsOf(grain, s.id))[0].state).toBe('popped')
  // close it, relaunch again: nothing reopens
  await grain.app.evaluate(({ BrowserWindow }) => {
    BrowserWindow.getAllWindows().find((b) => b.webContents.getURL().includes('surface=widget'))?.close()
  })
  await expect.poll(async () => (await windowsOf(grain, s.id))[0].state, { timeout: 20_000 }).toBe('normal')
  await grain.relaunch()
  await sleep(2500)
  expect(grain.app.windows().filter((p) => p.url().includes('surface=widget'))).toHaveLength(0)
})

test('pop-out the pin shortcut and Gather Widgets do not strand windows', async ({ grain }) => {
  const { s, w } = await seedFace(grain)
  await popOut(grain, w)
  await focusWhere(grain, 'surface=widget')
  await menuClick(grain.app, 'Pin on Top')
  await expect.poll(async () => !!(await windowsOf(grain, s.id))[0].pinned, { timeout: 15_000 }).toBe(true)
  await focusWhere(grain, 'surface=widget')
  await menuClick(grain.app, 'Pin on Top')
  await expect.poll(async () => !!(await windowsOf(grain, s.id))[0].pinned, { timeout: 15_000 }).toBe(false)
  await menuClick(grain.app, 'Gather Widgets')
  await sleep(800)
  await menuClick(grain.app, 'Gather Widgets')
  await sleep(800)
  expect(popWindows(grain)).toHaveLength(1)
  expect((await windowsOf(grain, s.id))[0].state).toBe('popped')
})

test('the ghost placeholder stays up while a window is popped out (4 pop / return cycles)', async ({ grain }) => {
  const { page } = grain
  const { s, w } = await seedFace(grain)
  for (let i = 0; i < 4; i++) {
    await popOut(grain, w)
    await expect(win(page, w.id).getByText('Detached to its own window')).toBeVisible()
    // nothing that settles later (the pop-out's own renderer, the state write) may put the live widget back
    await sleep(2500)
    await expect(win(page, w.id).getByText('Detached to its own window')).toBeVisible()
    expect((await windowsOf(grain, s.id))[0].state).toBe('popped')
    await win(page, w.id).getByRole('button', { name: 'Return to canvas' }).click()
    await expect.poll(async () => (await windowsOf(grain, s.id))[0].state).toBe('normal')
    await expect(win(page, w.id).getByText('Detached to its own window')).toHaveCount(0)
  }
  expect(grain.consoleErrors).toEqual([])
})
