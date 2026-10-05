import { test, expect } from './fixtures.mjs'
import { msgBox, assistants, users, newChat, say, sayAndWait, anyStop, benign } from './helpers/chat.mjs'
import { seedChats } from './helpers/seed.mjs'

test.describe.configure({ timeout: 240_000 })

const row = (page, title) => page.locator('.convo-list .convo-item', { hasText: title }).first()
const menuOf = (page, title) => page.getByRole('button', { name: `Chat options: ${title}` })

async function mkChat(grain, title) {
  const c = await grain.api('/conversations', { method: 'POST', body: { title } })
  return c
}

test('rename from the sidebar menu and from the header; Esc cancels; persists', async ({ grain }) => {
  const { page, api } = grain
  const a = await mkChat(grain, 'Alpha topic')
  await page.reload()
  await page.waitForSelector('.convo-list .convo-item')
  await menuOf(page, 'Alpha topic').click({ force: true })
  await page.getByRole('menuitem', { name: 'Rename' }).click()
  const input = page.locator('.convo-rename')
  await expect(input).toBeFocused()
  await input.fill('Renamed once')
  await input.press('Enter')
  await expect(row(page, 'Renamed once')).toBeVisible()
  // Esc cancels
  await menuOf(page, 'Renamed once').click({ force: true })
  await page.getByRole('menuitem', { name: 'Rename' }).click()
  await page.locator('.convo-rename').fill('nope')
  await page.locator('.convo-rename').press('Escape')
  await expect(row(page, 'Renamed once')).toBeVisible()
  await expect(row(page, 'nope')).toHaveCount(0)
  // header rename
  await row(page, 'Renamed once').click()
  await page.locator('.title-btn').click()
  const h = page.getByRole('textbox', { name: 'Chat title' })
  await h.fill('Header name')
  await h.press('Enter')
  await expect(row(page, 'Header name')).toBeVisible()
  expect((await api(`/conversations/${a.id}`)).title).toBe('Header name')
  // blank title is ignored
  await page.locator('.title-btn').click()
  await page.getByRole('textbox', { name: 'Chat title' }).fill('   ')
  await page.getByRole('textbox', { name: 'Chat title' }).press('Enter')
  await expect(row(page, 'Header name')).toBeVisible()
  const p2 = await grain.relaunch()
  await expect(p2.locator('.convo-list .convo-item', { hasText: 'Header name' })).toBeVisible()
  expect(benign(grain.consoleErrors)).toEqual([])
})

test('pin, archive, unarchive, delete with undo', async ({ grain }) => {
  const { page, api } = grain
  const ids = []
  for (const t of ['Chat one', 'Chat two', 'Chat three']) ids.push((await mkChat(grain, t)).id)
  await page.reload()
  await page.waitForSelector('.convo-list .convo-item')
  // pin
  await menuOf(page, 'Chat one').click({ force: true })
  await page.getByRole('menuitem', { name: 'Pin' }).click()
  await expect(page.locator('.pinned-head')).toBeVisible()
  await expect(page.locator('.convo-list .convo-item').first()).toContainText('Chat one')
  await menuOf(page, 'Chat one').click({ force: true })
  await page.getByRole('menuitem', { name: 'Unpin' }).click()
  await expect(page.locator('.pinned-head')).toHaveCount(0)
  // archive then unarchive
  await menuOf(page, 'Chat two').click({ force: true })
  await page.getByRole('menuitem', { name: 'Archive' }).click()
  await expect(row(page, 'Chat two')).toHaveCount(0)
  await page.getByRole('button', { name: 'Archived' }).click()
  await page.getByRole('button', { name: 'Unarchive chat: Chat two' }).click()
  await expect(row(page, 'Chat two')).toBeVisible()
  // archive + toast undo
  await menuOf(page, 'Chat three').click({ force: true })
  await page.getByRole('menuitem', { name: 'Archive' }).click()
  await page.getByRole('button', { name: 'Undo' }).first().click()
  await expect(row(page, 'Chat three')).toBeVisible()
  // delete from the menu: the row goes, an undo toast brings it back
  await menuOf(page, 'Chat one').click({ force: true })
  await page.getByRole('menuitem', { name: 'Delete' }).click()
  await expect(row(page, 'Chat one')).toHaveCount(0)
  expect((await api('/conversations')).map((c) => c.title)).not.toContain('Chat one')
  await page.getByRole('button', { name: 'Undo' }).first().click()
  await expect(row(page, 'Chat one')).toBeVisible({ timeout: 20_000 })
  // delete an archived chat from the archive list
  await menuOf(page, 'Chat two').click({ force: true })
  await page.getByRole('menuitem', { name: 'Archive' }).click()
  await page.getByRole('button', { name: 'Delete chat: Chat two' }).click()
  await expect(page.getByRole('button', { name: 'Delete chat: Chat two' })).toHaveCount(0)
  expect(benign(grain.consoleErrors)).toEqual([])
})

test('deleting the open chat returns to an empty new-chat view', async ({ grain }) => {
  const { page } = grain
  await newChat(page)
  await sayAndWait(page, '!!reply bye', 'bye')
  await expect(page.locator('.convo-list .convo-item.active')).toHaveCount(1)
  await page.locator('.convo-list .convo-item.active').click({ button: 'right' })
  await page.getByRole('menuitem', { name: 'Delete' }).click()
  await expect(page.locator('.convo-list .convo-item')).toHaveCount(0)
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible()
  await expect(users(page)).toHaveCount(0)
  await sayAndWait(page, '!!reply still works', 'still works')
})

test('sidebar order (newest first), active state follows selection, auto-title after first reply', async ({ grain }) => {
  const { page, api } = grain
  await newChat(page)
  await sayAndWait(page, 'Plan a trip to Lisbon', 'MOCK')
  await newChat(page)
  await sayAndWait(page, 'Debug my parser please', 'MOCK')
  const items = page.locator('.convo-list .convo-item')
  await expect(items).toHaveCount(2)
  await expect(items.first()).toContainText('parser')
  await expect(items.first()).toHaveClass(/active/)
  await items.nth(1).click()
  await expect(items.nth(1)).toHaveClass(/active/)
  await expect(items.first()).not.toHaveClass(/active/)
  await expect(users(page).first()).toContainText('Lisbon')
  // a new reply bumps the chat to the top
  await sayAndWait(page, 'one more thing', 'MOCK')
  await expect(items.first()).toContainText(/Lisbon|Plan a trip|one more/)
  // titles are generated off the first user message, never left "New chat"
  await expect.poll(async () => (await api('/conversations')).map((c) => c.title).join('|'), { timeout: 60_000 }).not.toMatch(/New chat|Untitled/)
})

test('search 300 chats: unique word finds the one chat; no matches; Esc clears', async ({ grain }) => {
  const { page } = grain
  const ids = seedChats(grain, 300, 4, 217, 'zyxwvutsr')
  await page.reload()
  await expect(page.locator('.convo-list .convo-item').first()).toBeVisible({ timeout: 30_000 })
  await page.getByRole('button', { name: 'Search chats' }).click()
  const q = page.getByRole('textbox', { name: 'Search chats' })
  await q.fill('zyxwvutsr')
  await expect(page.getByText('Seed chat 217').first()).toBeVisible({ timeout: 30_000 })
  await expect(page.locator('.convo-list .convo-item', { hasText: 'Seed chat' })).toHaveCount(1)
  await q.press('Enter')
  await expect(page.locator('.convo-list .convo-item.active')).toContainText('Seed chat 217')
  await expect(users(page).first()).toContainText('zyxwvutsr')
  await page.getByRole('button', { name: 'Search chats' }).click()
  await q.fill('qqqqnothingqqqq')
  await expect(page.getByText('No matches.')).toBeVisible()
  await q.press('Escape')
  await expect(q).toHaveCount(0)
  expect(ids.length).toBe(300)
  expect(benign(grain.consoleErrors)).toEqual([])
})

test('300 seeded chats render in the sidebar and the app stays responsive', async ({ grain }) => {
  const { page } = grain
  seedChats(grain, 300, 2)
  await page.reload()
  await expect(page.locator('.convo-list .convo-item').first()).toBeVisible({ timeout: 30_000 })
  const n = await page.locator('.convo-list .convo-item').count()
  expect(n).toBeGreaterThanOrEqual(50)
  const t0 = Date.now()
  await page.locator('.convo-list .convo-item').nth(5).click()
  await expect(users(page).first()).toBeVisible()
  expect(Date.now() - t0).toBeLessThan(5000)
})
