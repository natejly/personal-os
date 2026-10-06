import { test, expect } from './fixtures.mjs'
import { msgBox, assistants, users, newChat, say, sayAndWait, stopBtn, anyStop, benign } from './helpers/chat.mjs'

test.describe.configure({ timeout: 240_000 })

test('new chat button and ⌘N both reach an empty composer; empty send is disabled', async ({ grain }) => {
  const { page } = grain
  await newChat(page)
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Send', exact: true })).toBeDisabled()
  await msgBox(page).fill('   ')
  await expect(page.getByRole('button', { name: 'Send', exact: true })).toBeDisabled()
  await msgBox(page).press('Enter')
  await expect(users(page)).toHaveCount(0)
  await sayAndWait(page, '!!reply first', 'first')
  await page.keyboard.press('Meta+n') // a native menu accelerator: Playwright's keyboard does not reach it
  await grain.app.evaluate(({ Menu }) => {
    const find = (items) => { for (const i of items) { if (i.label === 'New Chat') return i; const r = i.submenu && find(i.submenu.items); if (r) return r } }
    find(Menu.getApplicationMenu().items).click()
  })
  await expect(users(page)).toHaveCount(0)
  await expect(msgBox(page)).toBeFocused()
  expect(benign(grain.consoleErrors)).toEqual([])
})

test('Enter sends, Shift+Enter makes a newline, ⌘Enter sends when idle', async ({ grain }) => {
  const { page } = grain
  await newChat(page)
  const box = msgBox(page)
  await box.click()
  await page.keyboard.type('line one')
  await page.keyboard.press('Shift+Enter')
  await page.keyboard.type('line two')
  await expect(box).toHaveValue('line one\nline two')
  await expect(users(page)).toHaveCount(0)
  await page.keyboard.press('Enter')
  await expect(users(page)).toHaveCount(1)
  await expect(users(page).first()).toContainText('line one')
  await expect(users(page).first()).toContainText('line two')
  await expect(assistants(page).last()).toContainText('MOCK', { timeout: 30_000 })
  await expect(stopBtn(page)).toHaveCount(0)
  await box.fill('!!reply via cmd')
  await box.press('Meta+Enter')
  await expect(assistants(page).last()).toContainText('via cmd', { timeout: 30_000 })
  await expect(box).toHaveValue('')
})

test('double Enter sends only once', async ({ grain }) => {
  const { page, api } = grain
  await newChat(page)
  const box = msgBox(page)
  await box.fill('!!slow 1500')
  await page.keyboard.press('Enter')
  await page.keyboard.press('Enter')
  await expect(users(page)).toHaveCount(1)
  await expect(assistants(page).last()).toContainText('MOCK', { timeout: 30_000 })
  const convs = await api('/conversations')
  expect(convs.length).toBe(1)
  const c = await api(`/conversations/${convs[0].id}`)
  expect(c.messages.filter((m) => m.role === 'user').length).toBe(1)
})

test('streaming render, Stop button and Esc both end a slow reply', async ({ grain }) => {
  const { page, api } = grain
  await newChat(page)
  await say(page, '!!slow 8000 !!reply never shown fully')
  const stop = stopBtn(page)
  await expect(stop).toBeVisible()
  const t0 = Date.now()
  await stop.click()
  await expect(anyStop(page)).toHaveCount(0, { timeout: 20_000 })
  await sayAndWait(page, '!!reply after stop', 'after stop')
  await say(page, '!!slow 8000')
  await expect(stop).toBeVisible()
  await msgBox(page).focus()
  await page.keyboard.press('Escape')
  await expect(anyStop(page)).toHaveCount(0, { timeout: 20_000 })
  await sayAndWait(page, '!!reply after esc', 'after esc')
  const [c0] = await api('/conversations')
  const c = await api(`/conversations/${c0.id}`)
  expect(c.messages.length).toBeGreaterThanOrEqual(6)
  expect(benign(grain.consoleErrors)).toEqual([])
})

test('Enter while streaming queues a follow-up that sends after the reply', async ({ grain }) => {
  const { page, llm } = grain
  await newChat(page)
  await say(page, '!!slow 3000 !!reply first answer')
  await expect(stopBtn(page)).toBeVisible()
  await msgBox(page).fill('!!reply queued answer')
  await msgBox(page).press('Enter')
  const tray = page.getByLabel('Queued follow-ups')
  await expect(tray).toContainText('queued answer')
  await expect(assistants(page).last()).toContainText('queued answer', { timeout: 30_000 })
  await expect(tray).toHaveCount(0)
  expect(llm.calls.length).toBeGreaterThanOrEqual(2)
})

test('queue: remove, edit and steer-now', async ({ grain }) => {
  const { page } = grain
  await newChat(page)
  await say(page, '!!slow 6000')
  await expect(stopBtn(page)).toBeVisible()
  await msgBox(page).fill('drop me')
  await msgBox(page).press('Enter')
  const tray = page.getByLabel('Queued follow-ups')
  await expect(tray).toContainText('drop me')
  await tray.getByRole('button', { name: 'Remove' }).click()
  await expect(tray).toHaveCount(0)
  await msgBox(page).fill('edit me')
  await msgBox(page).press('Enter')
  await tray.getByRole('button', { name: 'Edit' }).click()
  await expect(msgBox(page)).toHaveValue(/edit me/)
  await expect(tray).toHaveCount(0)
  await msgBox(page).press('Meta+Enter') // steer now
  await expect(users(page).filter({ hasText: 'edit me' })).toHaveCount(1, { timeout: 15_000 })
  await expect(anyStop(page)).toHaveCount(0, { timeout: 30_000 })
})

test('draft persists across chat switches and relaunch', async ({ grain }) => {
  const { page } = grain
  await newChat(page)
  await sayAndWait(page, '!!reply alpha chat', 'alpha chat')
  await msgBox(page).fill('half typed A')
  await newChat(page)
  await sayAndWait(page, '!!reply beta chat', 'beta chat')
  await msgBox(page).fill('half typed B')
  const items = page.locator('.convo-list .convo-item')
  await expect(items).toHaveCount(2)
  await items.nth(1).click()
  const first = await msgBox(page).inputValue()
  await items.nth(0).click()
  const second = await msgBox(page).inputValue()
  expect([first, second].sort()).toEqual(['half typed A', 'half typed B'])
  await page.waitForTimeout(1200)
  const p2 = await grain.relaunch()
  const it2 = p2.locator('.convo-list .convo-item')
  await expect(it2).toHaveCount(2)
  await it2.nth(0).click()
  const r1 = await msgBox(p2).inputValue()
  await it2.nth(1).click()
  const r2 = await msgBox(p2).inputValue()
  expect([r1, r2].sort()).toEqual(['half typed A', 'half typed B'])
})

test('a message typed right after Stop is a new turn, not a paused queue entry', async ({ grain }) => {
  const { page } = grain
  await newChat(page)
  await say(page, '!!slow 6000')
  await expect(stopBtn(page)).toBeVisible()
  await stopBtn(page).click()
  // No wait for the run to settle: this lands while the button still reads "Stopping".
  await say(page, '!!reply fresh turn after stop')
  await expect(assistants(page).last()).toContainText('fresh turn after stop', { timeout: 60_000 })
  await expect(page.getByLabel('Queued follow-ups')).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Resume' })).toHaveCount(0)
})
