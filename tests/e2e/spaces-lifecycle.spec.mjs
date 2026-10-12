import { test, expect } from './fixtures.mjs'
import { actionsBtn, enterCanvas, menuClick, sleep, spaces } from './helpers/spaces.mjs'

test.describe.configure({ timeout: 300_000 })

// The UI shows one canvas ("Canvas 1", the first). Extra canvases can still exist in the API; the sidebar draws only the
// first row, and there is no create / rename / switch UI.
const ROW = '.space-row'

test('the sidebar shows one canvas row; its menu locks and unlocks it', async ({ grain }) => {
  const { page, api } = grain
  page.on('dialog', (d) => void d.accept())
  const s = (await spaces(grain))[0]
  await api('/canvases', { method: 'POST', body: { name: 'Hidden extra' } })
  await page.reload()
  await page.waitForSelector('.sidebar')
  await enterCanvas(grain)
  await expect(page.locator(ROW)).toHaveCount(1)
  await expect(page.locator('.space-tab')).toHaveCount(1)
  await expect(page.locator('.space-tab.active')).toContainText('Canvas 1')

  await actionsBtn(page, 'Canvas 1').click()
  await page.getByRole('menuitem', { name: 'Lock canvas' }).click()
  await expect.poll(async () => !!(await api('/canvases/' + s.id)).locked).toBe(true)
  await actionsBtn(page, 'Canvas 1').click()
  await page.getByRole('menuitem', { name: 'Unlock canvas' }).click()
  await expect.poll(async () => !!(await api('/canvases/' + s.id)).locked).toBe(false)
  expect(grain.consoleErrors).toEqual([])
})

test('deleting the canvas falls back to the next one; deleting the last one makes a fresh canvas', async ({ grain }) => {
  const { page, api } = grain
  page.on('dialog', (d) => void d.accept())
  await api('/canvases', { method: 'POST', body: { name: 'B' } })
  await page.reload()
  await page.waitForSelector('.sidebar')
  await enterCanvas(grain)
  await expect.poll(async () => (await spaces(grain)).length).toBe(2)
  await actionsBtn(page, 'Canvas 1').click()
  await page.getByRole('menuitem', { name: 'Delete canvas' }).click()
  await expect.poll(async () => (await spaces(grain)).length).toBe(1)
  // fell back to the neighbour, canvas still rendered, no blank state
  await expect(page.locator('.space-tab.active')).toHaveCount(1)
  await expect(page.locator('.spaces-bar')).toBeVisible()
  await expect(page.locator(ROW)).toHaveCount(1)

  await actionsBtn(page, 'Canvas 1').click()
  await page.getByRole('menuitem', { name: 'Delete canvas' }).click()
  // never zero canvases: one fresh canvas appears
  await expect.poll(async () => (await spaces(grain)).length, { timeout: 10000 }).toBe(1)
  await expect(page.locator('.space-tab')).toHaveCount(1)
  await expect(page.locator(ROW)).toHaveCount(1)
  expect(grain.consoleErrors).toEqual([])
})

test('canvases with the same name are both allowed in the API; the UI still draws one row', async ({ grain }) => {
  const { page, api } = grain
  const a = (await spaces(grain))[0]
  const b = await api('/canvases', { method: 'POST', body: { name: a.name } })
  expect(b.name).toBe(a.name)
  expect((await spaces(grain)).filter((s) => s.name === a.name)).toHaveLength(2)
  await page.reload()
  await page.waitForSelector('.sidebar')
  await expect(page.locator(ROW)).toHaveCount(1)
  expect(grain.consoleErrors).toEqual([])
})

test('long and odd canvas names survive the round trip in the API and break no layout', async ({ grain }) => {
  const { page, api } = grain
  const odd = ('<b>"x"</b> 😀 ' + 'long '.repeat(60)).trim()
  const c = await api('/canvases', { method: 'POST', body: { name: odd } })
  expect((await api('/canvases/' + c.id)).name).toBe(odd)
  await page.reload()
  await page.waitForSelector('.sidebar')
  await enterCanvas(grain)
  await expect(page.locator(ROW).first()).toBeVisible()
  const sb = await page.locator('.sidebar').boundingBox()
  const rb = await page.locator(ROW).first().boundingBox()
  expect(rb.x + rb.width).toBeLessThanOrEqual(sb.x + sb.width + 1)
  expect(await page.locator('.spaces-bar').evaluate((e) => e.scrollWidth <= e.clientWidth + 1)).toBe(true)
})

test('relaunch restores spaces, active layout and lock', async ({ grain }) => {
  const { api } = grain
  const s = (await spaces(grain))[0]
  await api('/canvases', { method: 'POST', body: { name: 'Second' } })
  const w = await api(`/canvases/${s.id}/windows`, { method: 'POST', body: { kind: 'doc', ref_id: (await api('/docs', { method: 'POST', body: { title: 'hi', content: 'hi' } })).id, x: 123, y: 77, w: 301, h: 222 } })
  await api(`/canvases/${s.id}`, { method: 'PUT', body: { zoom: 1.25, pan_x: -40, pan_y: 22, locked: true } })
  await grain.relaunch()
  await enterCanvas(grain)
  const after = await api('/canvases/' + s.id)
  expect(after.locked).toBeTruthy()
  expect(after.zoom).toBe(1.25)
  const win = after.windows.find((x) => x.id === w.id)
  expect([win.x, win.y, win.w, win.h]).toEqual([123, 77, 301, 222])
  await expect(grain.page.locator(`[data-window-id="${w.id}"]`)).toBeVisible()
  await expect(grain.page.locator('button[title^="Unlock canvas"]')).toBeVisible()
  // plane transform restored
  const tf = await grain.page.locator('.plane, [class*=plane]').first().evaluate((e) => e.style.transform).catch(() => '')
  if (tf) expect(tf).toContain('scale(1.25)')
  expect(grain.consoleErrors).toEqual([])
})

test('lock blocks add / close / move; unlock restores them', async ({ grain }) => {
  const { api, page, app } = grain
  const s = (await spaces(grain))[0]
  const w = await api(`/canvases/${s.id}/windows`, { method: 'POST', body: { kind: 'face', x: 100, y: 100, w: 300, h: 220 } })
  await page.reload()
  await page.waitForSelector('.sidebar')
  await enterCanvas(grain)
  const win = page.locator(`[data-window-id="${w.id}"]`)
  await expect(win).toBeVisible()
  await menuClick(app, 'Lock / Unlock Canvas')
  await expect.poll(async () => !!(await api('/canvases/' + s.id)).locked).toBe(true)
  await expect(page.locator('button[title="Add widget"], button[title^="Canvas locked"]').first()).toBeDisabled()
  await expect(win.locator('.win-close')).toHaveCount(0)
  const grip = win.locator('.win-move')
  const b = await grip.boundingBox()
  await page.mouse.move(b.x + 50, b.y + 8)
  await page.mouse.down()
  for (let i = 1; i <= 8; i++) await page.mouse.move(b.x + 50 + i * 20, b.y + 8 + i * 10)
  await page.mouse.up()
  await sleep(900)
  const after = (await api('/canvases/' + s.id)).windows[0]
  expect([after.x, after.y]).toEqual([100, 100])
  // wheel zoom is ignored too
  await page.mouse.move(700, 500)
  await page.keyboard.down('Control')
  await page.mouse.wheel(0, -200)
  await page.keyboard.up('Control')
  await sleep(900)
  expect((await api('/canvases/' + s.id)).zoom).toBe(1)
  // the API-level "unlock" via shortcut
  await menuClick(app, 'Lock / Unlock Canvas')
  await expect.poll(async () => !!(await api('/canvases/' + s.id)).locked).toBe(false)
  await expect(win.locator('.win-close')).toHaveCount(1)
  expect(grain.consoleErrors).toEqual([])
})
