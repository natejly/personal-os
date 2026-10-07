// Regression: ISSUE-009 — edit and resend is offered in a desk chat but the backend refuses it
// Found by /qa on 2026-10-06
// Report: .gstack/qa-reports/run-20261006T212932Z/
import { test, expect } from './fixtures.mjs'
import { newChat, say } from './helpers/chat.mjs'

test('a default (autonomous) chat offers no Edit message on a sent message', async ({ grain }) => {
  const { page, api } = grain
  await api('/settings', { method: 'PUT', body: { autonomousByDefault: true } })
  await page.reload()
  await page.waitForSelector('.sidebar')
  await newChat(page)
  await say(page, '!!reply hello there')
  const mine = page.locator('.msg.user').first()
  await expect(page.locator('.msg.assistant').last()).toContainText('hello there', { timeout: 30_000 })
  await mine.hover()
  // The row's other actions are there once the reply has settled, so a missing Edit is not a missing row.
  await expect(mine.getByRole('button', { name: 'Delete message' })).toBeVisible({ timeout: 60_000 })
  await expect(mine.getByRole('button', { name: 'Edit message' })).toHaveCount(0)
})
