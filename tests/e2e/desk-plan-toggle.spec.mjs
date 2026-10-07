// Regression: ISSUE-011 — the Plan toggle is offered on a draft that starts as a desk, which ignores it
// Found by /qa on 2026-10-06
// Report: .gstack/qa-reports/run-20261006T212932Z/
import { test, expect } from './fixtures.mjs'
import { newChat } from './helpers/chat.mjs'

test('the draft hides Plan while it will start autonomous, and shows it again when autonomy is turned off', async ({ grain }) => {
  const { page, api } = grain
  await api('/settings', { method: 'PUT', body: { autonomousByDefault: true } })
  await page.reload()
  await page.waitForSelector('.sidebar')
  await newChat(page)
  const plan = page.getByRole('button', { name: /^Plan/ })
  await expect(page.getByRole('button', { name: /^Mode/ })).toBeVisible()
  await expect(plan).toHaveCount(0)
  await page.getByRole('button', { name: /^Mode/ }).click()
  await page.getByRole('button', { name: 'Turn off' }).click()
  await expect(plan).toBeVisible()
})

test('a draft in a classic chat default still has the Plan toggle', async ({ grain }) => {
  const { page } = grain
  await newChat(page)
  await expect(page.getByRole('button', { name: /^Plan/ })).toBeVisible()
})
