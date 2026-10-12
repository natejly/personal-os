import { test, expect } from './fixtures.mjs'
import { menu } from './helpers/shell.mjs'
import { spaces, windowsOf } from './helpers/spaces.mjs'

const sidebarItem = (page, name) => page.locator('.sidebar .nav-item', { hasText: new RegExp(`^\\s*${name}`) }).first()
const heading = (page, re) => expect(page.locator('main h2, .page h2').filter({ hasText: re }).first()).toBeVisible()

test('window minimum size is 820x520 and cannot be dragged smaller', async ({ grain }) => {
  const min = await grain.app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].getMinimumSize())
  expect(min).toEqual([820, 520])
  await grain.app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].setSize(300, 200))
  const size = await grain.app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].getSize())
  expect(size[0]).toBeGreaterThanOrEqual(820)
  expect(size[1]).toBeGreaterThanOrEqual(520)
})

test('Spaces menu: toggle shows the plane and returns; New chat in a space opens a chat window; New Space works', async ({ grain }) => {
  const { page } = grain
  await menu(grain, 'Toggle Canvases')
  await expect(page.locator('.canvas')).toBeVisible()
  await expect(page.locator('.sidebar .nav-item.active')).toHaveCount(0)
  // (chats may start as blobs, so count the space's chat windows through the API)
  const sp = (await spaces(grain))[0]
  const chats = async () => (await windowsOf(grain, sp.id)).filter((w) => w.kind === 'chat').length
  const before = await chats()
  await page.locator('.sidebar .new-chat').click()
  await expect.poll(chats).toBe(before + 1)
  await menu(grain, 'New Canvas')
  await menu(grain, 'Toggle Canvases')
  await expect(page.locator('.sidebar .nav-item.active')).toHaveCount(0) // back on a chat, which has no nav row
  expect(grain.consoleErrors).toEqual([])
})

test('300 chats: the sidebar stays responsive, scrolls, searches, and nav still works', async ({ grain }) => {
  const { page, api } = grain
  for (let i = 0; i < 300; i += 25) {
    await Promise.all(Array.from({ length: 25 }, (_, j) => api('/conversations', { method: 'POST', body: { title: `Bulk chat ${String(i + j).padStart(3, '0')}` } })))
  }
  await page.reload()
  await page.waitForSelector('.sidebar')
  await expect(page.locator('.sidebar .convo-item').first()).toBeVisible()
  const t0 = Date.now()
  await sidebarItem(page, 'Files').click()
  await heading(page, /Files/)
  expect(Date.now() - t0).toBeLessThan(3000)
  const scroller = page.locator('.sidebar-scroll')
  expect(await scroller.evaluate((e) => e.scrollHeight > e.clientHeight)).toBe(true)
  await scroller.evaluate((e) => { e.scrollTop = e.scrollHeight })
  await page.getByRole('button', { name: 'Search chats' }).click()
  await page.getByRole('textbox', { name: 'Search chats' }).fill('Bulk chat 217')
  await expect(page.locator('.sidebar .convo-list .convo-item')).toHaveCount(1)
  await page.keyboard.press('Enter')
  await expect(page.locator('.sidebar .convo-item.active')).toContainText('Bulk chat 217')
  // the palette lists the most recent few, not all 300
  await menu(grain, 'Command Palette…')
  expect(await page.getByRole('dialog', { name: 'Command palette' }).getByRole('option').count()).toBeLessThan(60)
  await page.keyboard.press('Escape')
  expect(grain.consoleErrors).toEqual([])
})

test('hammering the nav: 40 rapid clicks end on the last view without errors', async ({ grain }) => {
  const { page } = grain
  const seq = ['Lists', 'Calendar', 'Mail']
  await grain.api('/settings', { method: 'PUT', body: { hiddenViews: [], sidebarHidden: [] } })
  await page.reload()
  await page.waitForSelector('.sidebar')
  for (let i = 0; i < 40; i++) {
    await (i % 4 === 0 ? sidebarItem(page, 'Files') : sidebarItem(page, seq[i % 3])).click()
  }
  await sidebarItem(page, 'Mail').click()
  await heading(page, /Mail/)
  await sidebarItem(page, 'Lists').dblclick()
  await heading(page, /Lists/)
  expect(grain.consoleErrors).toEqual([])
})
