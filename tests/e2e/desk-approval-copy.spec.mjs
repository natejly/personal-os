// Regression: ISSUE-012 — a desk approval card claims the chat read untrusted content
// Found by /qa on 2026-10-06
// Report: .gstack/qa-reports/run-20261006T212932Z/
import { test, expect } from './fixtures.mjs'
import { newChat, say } from './helpers/chat.mjs'
import { enableModules } from './helpers/mah.mjs'

test.describe.configure({ timeout: 180_000 })

test('an autonomy-forced approval card does not say the chat read untrusted content', async ({ grain }) => {
  const { page, api } = grain
  await enableModules(api)
  await api('/settings', { method: 'PUT', body: { autonomousByDefault: true, toolDeferAbove: 0 } })
  await page.reload()
  await page.waitForSelector('.sidebar')
  await newChat(page)
  await say(page, '!!tool health_log {"metric":"sleep","value":7.25,"note":"copy check"}')
  const card = page.locator('.tc-approval').first()
  await expect(card).toBeVisible({ timeout: 60_000 })
  await expect(card).toContainText('needs your OK')
  await expect(card).not.toContainText('untrusted')
  const [c] = await api('/conversations?include_desks=true')
  expect((await api(`/conversations/${c.id}`)).settings.tainted).toBeFalsy()
})
