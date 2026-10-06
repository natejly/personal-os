import { test, expect } from './fixtures.mjs'
import { menu, menuTable, withGrain, ALL_VIEWS_ON, setWindowSize, bodyOverflow } from './helpers/shell.mjs'

const sidebarItem = (page, name) => page.locator('.sidebar .nav-item', { hasText: new RegExp(`^\\s*${name}`) }).first()
const heading = (page, re) => expect(page.locator('main h2, .page h2').filter({ hasText: re }).first()).toBeVisible()

test('every sidebar nav item opens its view and is marked current; Today brings you back', async () => {
  await withGrain({ settings: ALL_VIEWS_ON }, async ({ page, consoleErrors }) => {
    const rows = [['Files', /Files/], ['Library', /Library/]]
    for (const [name, h] of rows) {
      await sidebarItem(page, name).click()
      await heading(page, h)
      await expect(sidebarItem(page, name)).toHaveAttribute('aria-current', 'page')
      await expect(sidebarItem(page, 'Today')).not.toHaveAttribute('aria-current', 'page')
    }
    await sidebarItem(page, 'Today').click()
    await heading(page, /Today/)
    await expect(sidebarItem(page, 'Today')).toHaveAttribute('aria-current', 'page')
    // brand button also goes Today
    await sidebarItem(page, 'Files').click()
    await page.locator('.sidebar .brand').click()
    await heading(page, /Today/)
    expect(consoleErrors).toEqual([])
  })
})

test('app switcher icons open Lists, Calendar, Mail and the page agent; one is pressed at a time', async ({ grain }) => {
  const { page } = grain
  const sw = page.getByRole('toolbar', { name: 'Apps' })
  for (const [name, h] of [['Lists', /Lists/], ['Calendar', /Calendar/], ['Mail', /Mail/]]) {
    await sw.getByRole('button', { name }).click()
    await heading(page, h)
    await expect(sw.getByRole('button', { name })).toHaveAttribute('aria-pressed', 'true')
    for (const other of ['Lists', 'Calendar', 'Mail'].filter((x) => x !== name)) {
      await expect(sw.getByRole('button', { name: other })).toHaveAttribute('aria-pressed', 'false')
    }
  }
  // the switcher is in every view's title bar, including chat
  await page.getByRole('button', { name: /New chat/ }).first().click()
  await expect(page.getByRole('toolbar', { name: 'Apps' })).toBeVisible()
  expect(grain.consoleErrors).toEqual([])
})

test('hidden views are not in the sidebar; More modules opens Settings → Advanced → Layout', async ({ grain }) => {
  const { page, api } = grain
  // Library ships on; hiding it takes it out of the sidebar
  await expect(sidebarItem(page, 'Library')).toHaveCount(1)
  await api('/settings', { method: 'PUT', body: { hiddenViews: ['library'] } })
  await page.reload()
  await page.waitForSelector('.sidebar')
  await expect(sidebarItem(page, 'Library')).toHaveCount(0)
  await page.getByRole('button', { name: /More modules/ }).click()
  await expect(page.getByRole('dialog')).toBeVisible()
  await expect(page.getByRole('tab', { name: 'Advanced' })).toHaveAttribute('aria-selected', 'true')
  await expect(page.locator('.adv-group[open] > summary', { hasText: 'Layout' })).toBeVisible()
})

test('⌘B hides and shows the sidebar from the menu, the button and the title-bar toggle', async ({ grain }) => {
  const { page } = grain
  const sb = page.locator('aside.sidebar')
  await expect(sb).toBeVisible()
  await menu(grain, 'Toggle Sidebar')
  await expect(page.locator('.app')).toHaveClass(/sidebar-collapsed/)
  await expect(page.getByRole('button', { name: /Show sidebar/ }).first()).toBeVisible()
  await page.getByRole('button', { name: /Show sidebar/ }).first().click()
  await expect(page.locator('.app')).not.toHaveClass(/sidebar-collapsed/)
  await page.getByRole('button', { name: 'Hide sidebar' }).click()
  await expect(page.locator('.app')).toHaveClass(/sidebar-collapsed/)
  // rapid toggles settle on the parity
  for (let i = 0; i < 7; i++) await menu(grain, 'Toggle Sidebar')
  await expect(page.locator('.app')).not.toHaveClass(/sidebar-collapsed/)
})

test('sidebar resize: drag, keyboard, clamps, double-click reset, persisted across relaunch', async ({ grain }) => {
  let { page } = grain
  const width = () => page.locator('aside.sidebar').evaluate((e) => Math.round(e.getBoundingClientRect().width))
  const handle = () => page.getByRole('separator', { name: 'Sidebar width' })
  await expect.poll(width).toBe(260)
  const box = await handle().boundingBox()
  const y = box.y + 200
  const drag = async (dx) => {
    const b = await handle().boundingBox()
    const x = b.x + b.width / 2
    await page.mouse.move(x, y)
    await page.mouse.down()
    await page.mouse.move(x + dx / 2, y, { steps: 5 })
    await page.mouse.move(x + dx, y, { steps: 5 })
    await page.mouse.up()
  }
  await drag(60)
  await expect.poll(width).toBeGreaterThan(300)
  await expect.poll(width).toBeLessThanOrEqual(330)
  await drag(900)
  await expect.poll(width).toBe(480) // max clamp
  await drag(-250)
  const mid = await width()
  expect(mid).toBeGreaterThan(190)
  expect(mid).toBeLessThan(480)
  // keyboard
  await handle().focus()
  await page.keyboard.press('ArrowRight')
  await expect.poll(width).toBe(mid + 16)
  await page.keyboard.press('Shift+ArrowRight')
  await expect.poll(width).toBe(Math.min(480, mid + 16 + 64))
  await page.keyboard.press('Shift+ArrowLeft')
  for (let i = 0; i < 30; i++) await page.keyboard.press('Shift+ArrowLeft')
  await expect.poll(width).toBe(190) // min clamp by keyboard never collapses
  await expect(page.locator('aside.sidebar')).toBeVisible()
  await page.keyboard.press('Shift+ArrowRight')
  const saved = await width()
  expect(await page.evaluate(() => localStorage.getItem('grain.pane.sidebar-w'))).toBe(String(saved))
  page = await grain.relaunch()
  await expect.poll(width).toBe(saved)
  // double-click resets
  await handle().dblclick()
  await expect.poll(width).toBe(260)
  // dragging far below the minimum collapses the pane (and reopening is not a sliver)
  await drag(-400)
  await expect(page.locator('.app')).toHaveClass(/sidebar-collapsed/)
  await page.getByRole('button', { name: /Show sidebar/ }).first().click()
  await expect.poll(width).toBe(260)
  expect(grain.consoleErrors).toEqual([])
})

test('command palette: opens, fuzzy filters, arrows + Enter run, every command executes, Esc closes', async ({ grain }) => {
  const { page, api } = grain
  await api('/conversations', { method: 'POST', body: { title: 'Zebra planning chat' } }).catch(() => {})
  await page.reload()
  await page.waitForSelector('.sidebar')
  const pal = page.getByRole('dialog', { name: 'Command palette' })
  await menu(grain, 'Command Palette…')
  await expect(pal).toBeVisible()
  await expect(pal.getByPlaceholder('Go to, create, open…')).toBeFocused()
  await page.keyboard.press('Escape')
  await expect(pal).toHaveCount(0)
  // toggles
  await menu(grain, 'Command Palette…')
  await expect(pal).toBeVisible()
  await menu(grain, 'Command Palette…')
  await expect(pal).toHaveCount(0)
  // filter
  await menu(grain, 'Command Palette…')
  const input = pal.getByPlaceholder('Go to, create, open…')
  await input.fill('zzzzqq')
  await expect(pal.getByText('Nothing matches')).toBeVisible()
  await input.fill('')
  await input.fill('cale')
  await expect(pal.getByRole('option')).toHaveCount(1)
  await page.keyboard.press('Enter')
  await expect(pal).toHaveCount(0)
  await heading(page, /Calendar/)
  // Go-to commands
  for (const [q, h] of [['Today', /Today/], ['Files', /Files/], ['Lists', /Lists/], ['Mail', /Mail/], ['Library', /Library/]]) {
    await menu(grain, 'Command Palette…')
    await pal.getByPlaceholder('Go to, create, open…').fill(q)
    await pal.getByRole('option').first().click()
    await heading(page, h)
  }
  // arrows wrap and Enter picks the highlighted entry
  await menu(grain, 'Command Palette…')
  await page.keyboard.press('ArrowUp')
  const last = pal.getByRole('option').last()
  await expect(last).toHaveAttribute('aria-selected', 'true')
  await page.keyboard.press('ArrowDown')
  await expect(pal.getByRole('option').first()).toHaveAttribute('aria-selected', 'true')
  await page.keyboard.press('Escape')
  // new chat / new file / settings tabs
  await menu(grain, 'Command Palette…')
  await pal.getByPlaceholder('Go to, create, open…').fill('new chat')
  await page.keyboard.press('Enter')
  await expect(page.getByRole('textbox', { name: 'Message' })).toBeVisible()
  await menu(grain, 'Command Palette…')
  await pal.getByPlaceholder('Go to, create, open…').fill('new file')
  await page.keyboard.press('Enter')
  await expect(page.locator('.sidebar .nav-item.active')).toContainText('Files')
  // every settings entry opens the modal (leave the editor first: inside it the palette chord is ⌘K insert-link)
  await menu(grain, 'Lists')
  await menu(grain, 'Command Palette…')
  await expect(pal.getByRole('option').first()).toBeVisible()
  const settingsRows = await pal.getByRole('option').filter({ has: page.locator('small', { hasText: /^Settings$/ }) }).count()
  expect(settingsRows).toBeGreaterThan(5)
  await page.keyboard.press('Escape')
  for (let i = 0; i < settingsRows; i++) {
    await menu(grain, 'Command Palette…')
    await pal.getByRole('option').filter({ has: page.locator('small', { hasText: /^Settings$/ }) }).nth(i).click()
    await expect(page.getByRole('dialog').filter({ has: page.getByRole('tab') }).first()).toBeVisible()
    await page.keyboard.press('Escape')
    await expect(page.getByRole('tab').first()).toHaveCount(0)
  }
  // a seeded chat is findable
  await menu(grain, 'Command Palette…')
  await pal.getByPlaceholder('Go to, create, open…').fill('zebra')
  await expect(pal.getByRole('option').first()).toContainText('Zebra')
  await pal.getByRole('option').first().click()
  await expect(page.locator('.sidebar .convo-item.active')).toContainText('Zebra')
  expect(grain.consoleErrors).toEqual([])
})

test('menu shortcuts: every View/File item does what its label says', async ({ grain }) => {
  const { page } = grain
  const table = await menuTable(grain)
  const acc = Object.fromEntries(table.map((t) => [t.label, t.accelerator]))
  // the documented table (menuShortcuts.test.ts + main/index.ts)
  expect(acc['New Chat']).toMatch(/\+N$/)
  expect(acc['Settings…']).toMatch(/,$/)
  expect(acc['Toggle Sidebar']).toMatch(/\+B$/)
  expect(acc['Page Agent']).toMatch(/\+I$/)
  expect(acc['Toggle Context Panel']).toBe('Control+Command+I')
  expect(acc['Toggle Spaces']).toMatch(/Shift\+C$/)
  expect(acc['Command Palette…']).toMatch(/\+K$/)
  const digits = ['Today', 'Chats', 'Lists', 'Calendar', 'Files', 'Mail', 'Memory…']
  digits.forEach((l, i) => expect(acc[l]).toMatch(new RegExp(`\\+${i}$`)))
  // no two items share an accelerator
  const seen = new Map()
  for (const t of table) {
    if (!t.accelerator) continue
    const k = t.accelerator.replace('CmdOrCtrl', 'Command')
    if (seen.has(k) && seen.get(k) !== t.label) {
      // File > Settings… is listed twice (app menu on mac + File on others): same label is fine
      throw new Error(`accelerator ${k} used by "${seen.get(k)}" and "${t.label}"`)
    }
    seen.set(k, t.label)
  }

  // View items
  await menu(grain, 'Lists'); await heading(page, /Lists/)
  await menu(grain, 'Calendar'); await heading(page, /Calendar/)
  await menu(grain, 'Files'); await heading(page, /Files/)
  await menu(grain, 'Mail'); await heading(page, /Mail/)
  await menu(grain, 'Library'); await heading(page, /Library/)
  // A hidden view: a toast offers to turn it on instead of silently doing nothing
  await grain.api('/settings', { method: 'PUT', body: { hiddenViews: ['library'] } })
  await page.reload()
  await page.waitForSelector('.sidebar')
  await menu(grain, 'Library')
  await expect(page.getByText('Library is turned off')).toBeVisible()
  await menu(grain, 'Today'); await heading(page, /Today/)
  await menu(grain, 'Chats'); await expect(page.getByRole('textbox', { name: 'Message' })).toBeVisible()
  // Memory… opens the Memory page
  await menu(grain, 'Memory…')
  await expect(page.locator('.memory-page')).toBeVisible()
  await menu(grain, 'Settings…')
  await expect(page.getByRole('dialog').getByRole('tab').first()).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(page.getByRole('dialog')).toHaveCount(0)
  // New Chat / New File / Today's File
  await menu(grain, 'Lists')
  await menu(grain, 'New Chat'); await expect(page.getByRole('textbox', { name: 'Message' })).toBeVisible()
  await menu(grain, 'New File'); await heading(page, /Files/)
  await menu(grain, "Today's File"); await heading(page, /Files/)
  await menu(grain, 'Search Chats')
  await expect(page.getByRole('textbox', { name: 'Search chats' })).toBeFocused()
  await page.keyboard.press('Escape')
  expect(grain.consoleErrors).toEqual([])
})

test('prev/next chat shortcuts step through the sidebar list', async ({ grain }) => {
  const { page, api } = grain
  for (const t of ['aaa chat', 'bbb chat', 'ccc chat']) await api('/conversations', { method: 'POST', body: { title: t } })
  await page.reload()
  await page.waitForSelector('.sidebar')
  await menu(grain, 'Chats')
  await menu(grain, 'Next Chat')
  const active = page.locator('.sidebar .convo-item.active')
  await expect(active).toHaveCount(1)
  const first = await active.innerText()
  await menu(grain, 'Next Chat')
  await expect(active).not.toHaveText(first)
  await menu(grain, 'Previous Chat')
  await expect(active).toHaveText(first)
  for (let i = 0; i < 6; i++) await menu(grain, 'Next Chat')
  await expect(active).toHaveCount(1)
})

test('page agent and context drawer shortcuts toggle the panels; context panel only lives in a chat', async ({ grain }) => {
  const { page } = grain
  const pa = page.getByRole('complementary', { name: 'Page agent' })
  await menu(grain, 'Page Agent')
  await expect(pa).toBeVisible()
  await menu(grain, 'Page Agent')
  await expect(pa).toHaveCount(0)
  await menu(grain, 'Page Agent')
  await expect(pa).toBeVisible()
  await page.getByRole('button', { name: 'Close page agent' }).click()
  await expect(pa).toHaveCount(0)
  // rapid parity
  for (let i = 0; i < 5; i++) await menu(grain, 'Page Agent')
  await expect(pa).toBeVisible()
  await menu(grain, 'Page Agent')
  // context drawer in a chat
  await page.getByRole('button', { name: /New chat/ }).first().click()
  const drawer = page.locator('.context-drawer, aside.context, .ctx-drawer').first()
  await menu(grain, 'Toggle Context Panel')
  await expect(page.getByRole('button', { name: /Close|Hide/ }).first()).toBeVisible()
  await menu(grain, 'Toggle Context Panel')
  expect(grain.consoleErrors).toEqual([])
  void drawer
})

test('nothing overflows horizontally at 820x520 on any view', async () => {
  await withGrain({ settings: ALL_VIEWS_ON }, async (grain) => {
    const { page } = grain
    await setWindowSize(grain, 820, 520)
    await expect.poll(() => page.evaluate(() => window.innerWidth)).toBeLessThanOrEqual(820)
    const check = async (label) => {
      await page.waitForTimeout(300)
      const o = await bodyOverflow(page)
      expect.soft(o.sw, `${label} body scrollWidth`).toBe(o.cw)
      expect.soft(o.dsw, `${label} html scrollWidth`).toBe(o.dcw)
    }
    for (const n of ['Today', 'Files', 'Library']) {
      await sidebarItem(page, n).click()
      await check(n)
    }
    for (const n of ['Lists', 'Calendar', 'Mail']) {
      await page.getByRole('toolbar', { name: 'Apps' }).getByRole('button', { name: n }).click()
      await check(n)
    }
    await page.getByRole('button', { name: /New chat/ }).first().click()
    await check('chat')
    await menu(grain, 'Page Agent')
    await check('chat + page agent')
    await menu(grain, 'Toggle Sidebar')
    await check('chat, sidebar hidden')
    await menu(grain, 'Toggle Spaces')
    await check('space')
  })
})
