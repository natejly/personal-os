// Regression: ISSUE-004 — workers panel missing in the main chat view
// Found by /qa on 2026-10-06
// Report: .gstack/qa-reports/run-20261006T212932Z/
import { test, expect } from './fixtures.mjs'
import { newChat, say } from './helpers/chat.mjs'

test.describe.configure({ timeout: 180_000 })

test('a delegated worker shows in the Workers panel of the main chat view', async ({ grain }) => {
  const { page, api } = grain
  await api('/settings', { method: 'PUT', body: { autonomousByDefault: false, telegramPushWorkerResults: false } })
  await page.reload()
  await page.waitForSelector('.sidebar')
  await newChat(page)
  await say(page, '!!tool delegate {"goal":"look something up","title":"Alpha job"}')
  const panel = page.locator('section.worker-panel')
  await expect(panel).toBeVisible({ timeout: 30_000 })
  await expect(panel).toContainText('Alpha job')
})
