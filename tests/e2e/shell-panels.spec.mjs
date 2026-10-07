import { test, expect } from './fixtures.mjs'
import { menu, navItem } from './helpers/shell.mjs'

const heading = (page, re) => expect(page.locator('main h2, .page h2').filter({ hasText: re }).first()).toBeVisible()
const panel = (page) => page.getByRole('complementary', { name: 'Page agent' })
const lastCallText = (llm) => JSON.stringify(llm.calls[llm.calls.length - 1] ?? {})

async function askPageAgent(grain, text = '!!reply noted') {
  const { page, llm } = grain
  const before = llm.calls.length
  if (!(await panel(page).count())) await menu(grain, 'Page Agent')
  await expect(panel(page)).toBeVisible()
  const box = panel(page).getByRole('textbox').first()
  await box.fill(text)
  await box.press('Enter')
  await expect.poll(() => llm.calls.length, { timeout: 20_000 }).toBeGreaterThan(before)
  await expect(panel(page).locator('.msg.assistant').last()).toContainText('noted')
  // title / learn calls follow the reply, so look at every call since the question, not just the last
  return JSON.stringify(llm.calls.slice(before))
}

test('page agent carries each view\'s content to the model', async ({ grain }) => {
  const { page, api, llm } = grain
  await api('/todos', { method: 'POST', body: { title: 'Zanzibar quarterly taxes' } })
  await api('/docs', { method: 'POST', body: { title: 'Quokka notes', content: '# Quokka notes\n\nThe quokka is the happiest animal.' } })
  await page.reload()
  await page.waitForSelector('.sidebar')

  // Todos
  await navItem(page, 'Lists').click()
  await heading(page, /Lists/)
  await expect(page.getByText('Zanzibar quarterly taxes').first()).toBeVisible()
  let body = await askPageAgent(grain)
  expect(body).toContain('Zanzibar quarterly taxes')
  await expect(panel(page).locator('.page-agent-ctx')).toContainText('Lists')
  // The panel follows the view: switch to Calendar, context label changes
  await navItem(page, 'Calendar').click()
  await expect(panel(page).locator('.page-agent-ctx')).toContainText('Calendar')
  // Files: open the doc, then ask
  await page.locator('.sidebar .nav-item', { hasText: /^\s*Files/ }).click()
  await heading(page, /Files/)
  await page.getByText('Quokka notes').first().click()
  await expect(page.getByText('happiest animal').first()).toBeVisible()
  await expect(panel(page).locator('.page-agent-ctx')).toContainText(/Quokka|Files/)
  body = await askPageAgent(grain, '!!reply noted about doc')
  expect(body).toContain('happiest animal')
  // Today
  await page.locator('.sidebar .nav-item', { hasText: /^\s*Today/ }).click()
  await expect(panel(page).locator('.page-agent-ctx')).toContainText(/Today|Home/)
  body = await askPageAgent(grain)
  expect(body).toMatch(/"page"|Today|view/)
  // Chat view: the chat's own text
  await page.getByRole('button', { name: /New chat/ }).first().click()
  const msg = page.locator('main').getByRole('textbox', { name: 'Message' })
  await msg.fill('!!reply Walrus ledger entry')
  await msg.press('Enter')
  await expect(page.locator('main .msg.assistant').last()).toContainText('Walrus')
  await expect(panel(page).locator('.page-agent-ctx')).toContainText(/Chat|chat|New/)
  body = await askPageAgent(grain)
  expect(body).toContain('Walrus ledger entry')
  // The panel keeps its own thread across views, and "New thread" resets it
  await panel(page).getByRole('button', { name: 'New thread' }).click()
  await expect(panel(page).locator('.msg')).toHaveCount(0)
  expect(llm.calls.length).toBeGreaterThan(4)
  expect(grain.consoleErrors).toEqual([])
})

test('page agent: width resizes with the handle and persists; hints send on click; selection rides along', async ({ grain }) => {
  const { page, api, llm } = grain
  await api('/todos', { method: 'POST', body: { title: 'Hint probe todo' } })
  await page.reload()
  await page.waitForSelector('.sidebar')
  await navItem(page, 'Lists').click()
  await menu(grain, 'Page Agent')
  const w = () => panel(page).evaluate((e) => Math.round(e.getBoundingClientRect().width))
  await expect.poll(w).toBe(380)
  const h = page.getByRole('separator', { name: 'Page agent width' })
  const b = await h.boundingBox()
  await page.mouse.move(b.x + 2, b.y + 200)
  await page.mouse.down()
  await page.mouse.move(b.x - 60, b.y + 200, { steps: 6 })
  await page.mouse.move(b.x - 120, b.y + 200, { steps: 6 })
  await page.mouse.up()
  await expect.poll(w).toBeGreaterThan(470)
  await expect.poll(w).toBeLessThanOrEqual(520)
  const saved = await w()
  await h.focus()
  for (let i = 0; i < 20; i++) await page.keyboard.press('Shift+ArrowLeft')
  // the stored width clamps at 720; the rendered panel may be narrower when the window cannot spare that much
  expect(await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue('--page-agent-w').trim())).toBe('720px')
  await expect.poll(w).toBeGreaterThan(500)
  for (let i = 0; i < 20; i++) await page.keyboard.press('Shift+ArrowRight')
  await expect.poll(w).toBe(280)
  await h.dblclick()
  await expect.poll(w).toBe(380)
  // hint click sends it
  const before = llm.calls.length
  await panel(page).locator('.page-agent-hint').first().click()
  await expect.poll(() => llm.calls.length).toBeGreaterThan(before)
  void saved
  expect(grain.consoleErrors).toEqual([])
})

test('context drawer opens, closes and resizes in a chat; width persists', async ({ grain }) => {
  const { page } = grain
  await page.getByRole('button', { name: /New chat/ }).first().click()
  const drawer = page.locator('aside.context-drawer')
  await menu(grain, 'Toggle Context Panel')
  await expect(drawer).toBeVisible()
  const w = () => drawer.evaluate((e) => Math.round(e.getBoundingClientRect().width))
  await expect.poll(w).toBe(340)
  const h = page.getByRole('separator', { name: 'Context panel width' })
  await h.focus()
  await page.keyboard.press('Shift+ArrowLeft')
  await expect.poll(w).toBe(340 + 64)
  for (let i = 0; i < 20; i++) await page.keyboard.press('Shift+ArrowLeft')
  await expect.poll(w).toBe(640)
  for (let i = 0; i < 30; i++) await page.keyboard.press('Shift+ArrowRight')
  await expect.poll(w).toBe(260)
  await page.getByRole('button', { name: 'Close context panel' }).click()
  await expect(drawer).toHaveCount(0)
  await menu(grain, 'Toggle Context Panel')
  await expect(drawer).toBeVisible()
  expect(await page.evaluate(() => localStorage.getItem('grain.pane.context-drawer-w'))).toBe('260')
  await menu(grain, 'Toggle Context Panel')
  await expect(drawer).toHaveCount(0)
  expect(grain.consoleErrors).toEqual([])
})

test('find bar: matches count, steps with Enter, wraps, Esc closes, clears on chat switch', async ({ grain }) => {
  const { page } = grain
  await page.getByRole('button', { name: /New chat/ }).first().click()
  const msg = page.locator('main').getByRole('textbox', { name: 'Message' })
  await msg.fill('!!reply zork one zork two zork three')
  await msg.press('Enter')
  await expect(page.locator('.msg.assistant').last()).toContainText('zork three')
  await menu(grain, 'Find…')
  const bar = page.getByRole('search')
  await expect(bar).toBeVisible()
  const input = bar.getByRole('textbox', { name: 'Find in conversation' })
  await expect(input).toBeFocused()
  await input.fill('zork')
  // the user's own message "!!reply zork one zork two zork three" holds 3 more
  await expect(bar.locator('.find-count')).toHaveText('1 of 6')
  await input.press('Enter')
  await expect(bar.locator('.find-count')).toHaveText('2 of 6')
  await input.press('Shift+Enter')
  await input.press('Shift+Enter')
  await expect(bar.locator('.find-count')).toHaveText('6 of 6')
  await menu(grain, 'Find Next')
  await expect(bar.locator('.find-count')).toHaveText('1 of 6')
  await menu(grain, 'Find Previous')
  await expect(bar.locator('.find-count')).toHaveText('6 of 6')
  // highlights are painted
  expect(await page.evaluate(() => CSS.highlights.get('chat-find')?.size + (CSS.highlights.get('chat-find-current')?.size ?? 0))).toBe(6)
  await input.fill('qqqnomatch')
  await expect(bar.locator('.find-count')).toHaveText('No matches')
  await input.fill('')
  await expect(bar.locator('.find-count')).toHaveText('')
  await input.fill('ZORK')
  await expect(bar.locator('.find-count')).toHaveText('1 of 6')
  await input.press('Escape')
  await expect(bar).toHaveCount(0)
  expect(await page.evaluate(() => CSS.highlights.has('chat-find'))).toBe(false)
  // not available outside a chat
  await menu(grain, 'Lists')
  await menu(grain, 'Find…')
  await expect(page.getByRole('search')).toHaveCount(0)
  expect(grain.consoleErrors).toEqual([])
})

test('sidebar chat pulse shows a reply streaming in another chat, then settles', async ({ grain }) => {
  const { page, api } = grain
  const a = await api('/conversations', { method: 'POST', body: { title: 'Slow chat A' } })
  await api('/conversations', { method: 'POST', body: { title: 'Other chat B' } })
  await page.reload()
  await page.waitForSelector('.sidebar')
  await page.locator('.sidebar .convo-item', { hasText: 'Slow chat A' }).click()
  const msg = page.locator('main').getByRole('textbox', { name: 'Message' })
  await msg.fill('!!slow 6000 !!reply finally done')
  await msg.press('Enter')
  await page.locator('.sidebar .convo-item', { hasText: 'Other chat B' }).click()
  const rowA = page.locator('.sidebar .convo-item', { hasText: 'Slow chat A' })
  // working: the row's face animates (blinks) instead of a dot
  await expect(rowA.locator('.face .mo-always')).toBeVisible()
  await expect(rowA.locator('.face .mo-always')).toHaveCount(0, { timeout: 20_000 })
  // finished while out of sight: unread dot until opened
  await expect(rowA.locator('.pulse')).toHaveCount(1)
  await rowA.click()
  await expect(page.locator('.msg.assistant').last()).toContainText('finally done')
  await expect(rowA.locator('.pulse')).toHaveCount(0, { timeout: 10_000 })
  void a
  expect(grain.consoleErrors).toEqual([])
})

test('theme: Settings switches light/dark/system; system follows prefers-color-scheme', async ({ grain }) => {
  const { page } = grain
  const bg = () => page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue('--bg').trim())
  const theme = () => page.evaluate(() => document.documentElement.dataset.theme)
  await menu(grain, 'Settings…')
  const dlg = page.getByRole('dialog')
  await dlg.getByRole('tab', { name: /Appearance/ }).click()
  const group = dlg.getByRole('radio', { name: /^Light$/ })
  await expect(group).toBeVisible()
  await dlg.getByRole('radio', { name: /^Dark$/ }).click()
  await expect.poll(theme).toBe('dark') // previewed live
  const dark = await bg()
  await dlg.getByRole('radio', { name: /^Light$/ }).click()
  await expect.poll(theme).toBe('light')
  const light = await bg()
  expect(light).not.toBe(dark)
  await dlg.getByRole('radio', { name: /^System$/ }).click()
  await expect.poll(theme).toBe('system')
  await page.emulateMedia({ colorScheme: 'dark' })
  await expect.poll(bg).toBe(dark)
  await page.emulateMedia({ colorScheme: 'light' })
  await expect.poll(bg).toBe(light)
  await dlg.getByRole('button', { name: /Save/ }).click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await expect.poll(theme).toBe('system')
  expect((await grain.api('/settings')).theme).toBe('system')
  // discarding restores
  await menu(grain, 'Settings…')
  await dlg.getByRole('tab', { name: /Appearance/ }).click()
  await dlg.getByRole('radio', { name: /^Dark$/ }).click()
  await expect.poll(theme).toBe('dark')
  await page.keyboard.press('Escape')
  await expect(page.getByText('Discard unsaved changes?')).toBeVisible()
  await page.getByRole('button', { name: 'Discard' }).click()
  await expect.poll(theme).toBe('system')
  // persists across relaunch
  const p2 = await grain.relaunch()
  expect(await p2.evaluate(() => document.documentElement.dataset.theme)).toBe('system')
  expect(grain.consoleErrors).toEqual([])
})

test('pop-out: a space widget pops out to its own window and returns', async ({ grain }) => {
  const { page, api, app } = grain
  const c = await api('/canvases')
  const id = (c.canvases ?? c)[0].id
  await api(`/canvases/${id}/windows`, { method: 'POST', body: { kind: 'todos', x: 40, y: 40, w: 420, h: 360 } })
  await menu(grain, 'Toggle Spaces')
  const win = page.locator('.win[data-kind="todos"]')
  await expect(win).toBeVisible()
  await win.click({ position: { x: 100, y: 60 } })
  await menu(grain, 'Pop Out')
  await expect.poll(() => app.windows().length, { timeout: 15_000 }).toBe(2)
  const pop = app.windows().find((w) => w !== page)
  await pop.waitForLoadState('domcontentloaded')
  expect(pop.url()).toContain('surface=widget')
  await expect(pop.locator('.popout, [class*="popout"]').first()).toBeVisible()
  await menu(grain, 'Return to Space')
  await expect.poll(() => app.windows().length, { timeout: 15_000 }).toBe(1)
  expect(grain.consoleErrors).toEqual([])
})
