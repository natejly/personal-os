import { test, expect } from './fixtures.mjs'

test('app boots, a chat round-trips through the provider', async ({ grain }) => {
  const { page, llm } = grain
  await page.getByRole('button', { name: /New chat/ }).first().click()
  const box = page.getByRole('textbox', { name: 'Message' })
  await box.fill('!!reply Hello from the mock')
  await box.press('Enter')
  await expect(page.locator('.msg.assistant').last()).toContainText('Hello from the mock', { timeout: 30_000 })
  expect(llm.calls.length).toBeGreaterThan(0)
})
