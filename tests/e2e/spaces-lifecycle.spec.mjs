import { test, expect } from './fixtures.mjs'
import { actionsBtn, enterCanvas, menuClick, sleep, spaces } from './helpers/spaces.mjs'

const rowOf = (page, name) => page.locator('.space-row').filter({ hasText: name })

test('create / rename / switch / delete a space from the sidebar', async ({ grain }) => {
  const { page, api } = grain
  page.on('dialog', (d) => void d.accept())
  await enterCanvas(grain)

  await page.getByRole('button', { name: 'New space', exact: true }).first().click()
  await expect.poll(async () => (await spaces(grain)).length).toBe(2)
  const [s1, s2] = await spaces(grain)
  await expect(page.locator('.space-row')).toHaveCount(2)
  // the new one is active
  await expect(page.locator('.space-tab.active')).toContainText(s2.name)

  // rename through the row menu -> Rename -> type -> Enter
  await actionsBtn(page, s2.name).click()
  await page.getByRole('menuitem', { name: 'Rename' }).click()
  const input = page.locator('.space-row input')
  await input.fill('Research')
  await input.press('Enter')
  await expect.poll(async () => (await api('/canvases/' + s2.id)).name).toBe('Research')
  await expect(page.locator('.space-tab', { hasText: 'Research' })).toBeVisible()

  // Escape cancels a rename, an empty name is ignored
  await actionsBtn(page, 'Research').click()
  await page.getByRole('menuitem', { name: 'Rename' }).click()
  await page.locator('.space-row input').fill('Nope')
  await page.locator('.space-row input').press('Escape')
  await expect(rowOf(page, 'Research')).toBeVisible()
  await actionsBtn(page, 'Research').click()
  await page.getByRole('menuitem', { name: 'Rename' }).click()
  await page.locator('.space-row input').fill('   ')
  await page.locator('.space-row input').press('Enter')
  expect((await api('/canvases/' + s2.id)).name).toBe('Research')

  // tab double-click rename
  await page.locator('.space-tab', { hasText: 'Research' }).dblclick()
  await page.locator('.space-tab input').fill('Deep work')
  await page.locator('.space-tab input').press('Enter')
  await expect.poll(async () => (await api('/canvases/' + s2.id)).name).toBe('Deep work')

  // switch by clicking a row, a tab, and ⌃1
  await rowOf(page, s1.name).click()
  await expect(page.locator('.space-tab.active')).toContainText(s1.name)
  await page.locator('.space-tab', { hasText: 'Deep work' }).click()
  await expect(page.locator('.space-tab.active')).toContainText('Deep work')
  await page.keyboard.press('Control+1')
  await expect(page.locator('.space-tab.active')).toContainText(s1.name)

  // delete the non-active space
  await actionsBtn(page, 'Deep work').click()
  await page.getByRole('menuitem', { name: 'Delete space' }).click()
  await expect.poll(async () => (await spaces(grain)).length).toBe(1)
  await expect(page.locator('.space-row')).toHaveCount(1)
  expect(grain.consoleErrors).toEqual([])
})

test('deleting the active space falls back, deleting the last one makes a fresh space', async ({ grain }) => {
  const { page, api } = grain
  page.on('dialog', (d) => void d.accept())
  const a = (await spaces(grain))[0]
  const b = await api('/canvases', { method: 'POST', body: { name: 'B' } })
  const c = await api('/canvases', { method: 'POST', body: { name: 'C' } })
  await page.reload()
  await page.waitForSelector('.sidebar')
  await enterCanvas(grain)
  await page.locator('.space-tab', { hasText: 'B' }).click()
  await expect(page.locator('.space-tab.active')).toContainText('B')
  await actionsBtn(page, 'B').click()
  await page.getByRole('menuitem', { name: 'Delete space' }).click()
  await expect.poll(async () => (await spaces(grain)).length).toBe(2)
  // fell back to a neighbour, canvas still rendered, no blank state
  await expect(page.locator('.space-tab.active')).toHaveCount(1)
  await expect(page.locator('.spaces-bar')).toBeVisible()

  for (const n of [a.name, 'C']) {
    await actionsBtn(page, n).click()
    await page.getByRole('menuitem', { name: 'Delete space' }).click()
    await sleep(400)
  }
  // never zero spaces: one fresh space appears
  await expect.poll(async () => (await spaces(grain)).length, { timeout: 10000 }).toBe(1)
  await expect(page.locator('.space-tab')).toHaveCount(1)
  await expect(page.locator('.space-tab.active')).toHaveCount(1)
  void c
  expect(grain.consoleErrors).toEqual([])
})

test('two spaces with the same name are both allowed (consistent in UI and API)', async ({ grain }) => {
  const { page, api } = grain
  const a = (await spaces(grain))[0]
  const b = await api('/canvases', { method: 'POST', body: { name: a.name } })
  expect(b.name).toBe(a.name)
  await page.reload()
  await page.waitForSelector('.sidebar')
  await expect(page.locator('.space-row')).toHaveCount(2)
  // rename one to the other's name through the UI
  const c = await api('/canvases', { method: 'POST', body: { name: 'Third' } })
  await page.reload()
  await page.waitForSelector('.sidebar')
  await enterCanvas(grain)
  await actionsBtn(page, 'Third').click()
  await page.getByRole('menuitem', { name: 'Rename' }).click()
  await page.locator('.space-row input').fill(a.name)
  await page.locator('.space-row input').press('Enter')
  await expect.poll(async () => (await api('/canvases/' + c.id)).name).toBe(a.name)
  expect((await spaces(grain)).filter((s) => s.name === a.name)).toHaveLength(3)
  expect(grain.consoleErrors).toEqual([])
})

test('long and odd space names survive the round trip', async ({ grain }) => {
  const { page, api } = grain
  const odd = ('<b>"x"</b> 😀 ' + 'long '.repeat(60)).trim()
  const c = await api('/canvases', { method: 'POST', body: { name: odd } })
  expect((await api('/canvases/' + c.id)).name).toBe(odd)
  await page.reload()
  await page.waitForSelector('.sidebar')
  await enterCanvas(grain)
  await expect(page.locator('.space-row').nth(1)).toBeVisible()
  // layout still fits: the sidebar row never grows wider than the sidebar
  const sb = await page.locator('.sidebar').boundingBox()
  const rb = await page.locator('.space-row').nth(1).boundingBox()
  expect(rb.x + rb.width).toBeLessThanOrEqual(sb.x + sb.width + 1)
  expect(await page.locator('.spaces-bar').evaluate((e) => e.scrollWidth <= e.clientWidth + 1)).toBe(true)
})

test('relaunch restores spaces, active layout and lock', async ({ grain }) => {
  const { api } = grain
  const s = (await spaces(grain))[0]
  await api('/canvases', { method: 'POST', body: { name: 'Second' } })
  const w = await api(`/canvases/${s.id}/windows`, { method: 'POST', body: { kind: 'note', ref_id: (await api('/notes', { method: 'POST', body: { body: 'hi' } })).id, x: 123, y: 77, w: 301, h: 222 } })
  await api(`/canvases/${s.id}`, { method: 'PUT', body: { zoom: 1.25, pan_x: -40, pan_y: 22, locked: true } })
  await grain.relaunch()
  await enterCanvas(grain)
  const after = await api('/canvases/' + s.id)
  expect(after.locked).toBeTruthy()
  expect(after.zoom).toBe(1.25)
  const win = after.windows.find((x) => x.id === w.id)
  expect([win.x, win.y, win.w, win.h]).toEqual([123, 77, 301, 222])
  await expect(grain.page.locator(`[data-window-id="${w.id}"]`)).toBeVisible()
  await expect(grain.page.locator('button[title^="Unlock space"]')).toBeVisible()
  // plane transform restored
  const tf = await grain.page.locator('.plane, [class*=plane]').first().evaluate((e) => e.style.transform).catch(() => '')
  if (tf) expect(tf).toContain('scale(1.25)')
  expect(grain.consoleErrors).toEqual([])
})

test('lock blocks add / close / move; unlock restores them', async ({ grain }) => {
  const { api, page, app } = grain
  const s = (await spaces(grain))[0]
  const w = await api(`/canvases/${s.id}/windows`, { method: 'POST', body: { kind: 'note', x: 100, y: 100, w: 300, h: 220 } })
  await page.reload()
  await page.waitForSelector('.sidebar')
  await enterCanvas(grain)
  const win = page.locator(`[data-window-id="${w.id}"]`)
  await expect(win).toBeVisible()
  await menuClick(app, 'Lock / Unlock Space')
  await expect.poll(async () => !!(await api('/canvases/' + s.id)).locked).toBe(true)
  await expect(page.locator('button[title="Add widget"], button[title^="Space locked"]').first()).toBeDisabled()
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
  await menuClick(app, 'Lock / Unlock Space')
  await expect.poll(async () => !!(await api('/canvases/' + s.id)).locked).toBe(false)
  await expect(win.locator('.win-close')).toHaveCount(1)
  expect(grain.consoleErrors).toEqual([])
})
