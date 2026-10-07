import { test, expect } from './fixtures.mjs'
import { apiChat } from './helpers/chat.mjs'

// A run this window did not stream (here: started over the API, like a Telegram message or a worker's wake turn)
// still leaves an unread dot on a chat that is out of sight, and none on the chat in front of you.
test('a reply from a run this window did not stream: dot on the other chat until opened, none on the open one', async ({ grain }) => {
  const { page, api } = grain
  const a = await api('/conversations', { method: 'POST', body: { title: 'Notify chat A' } })
  const b = await api('/conversations', { method: 'POST', body: { title: 'Notify chat B' } })
  await page.reload()
  await page.waitForSelector('.sidebar')
  const rowA = page.locator('.sidebar .convo-item', { hasText: 'Notify chat A' })
  const rowB = page.locator('.sidebar .convo-item', { hasText: 'Notify chat B' })
  await rowA.click()
  await expect(rowA).toBeVisible()
  await page.waitForTimeout(2500)  // /events replays its ring on connect; frames in the first 2 s are history, not news

  await apiChat(api, b.id, '!!reply hello from B')
  await expect(rowB.locator('.pulse.unread')).toHaveCount(1, { timeout: 30_000 })

  // The chat on screen is read as it lands.
  await apiChat(api, a.id, '!!reply hello from A')
  await expect(page.locator('.msg.assistant').last()).toContainText('hello from A', { timeout: 30_000 })
  await expect(rowA.locator('.pulse.unread')).toHaveCount(0)

  await rowB.click()
  await expect(page.locator('.msg.assistant').last()).toContainText('hello from B')
  await expect(rowB.locator('.pulse.unread')).toHaveCount(0, { timeout: 10_000 })
  expect(grain.consoleErrors).toEqual([])
})
