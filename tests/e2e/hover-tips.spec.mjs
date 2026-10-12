import { test, expect } from './fixtures.mjs'

test('buttons carry a title, and hovering shows the app tooltip with the shortcut', async ({ grain }) => {
  const { page } = grain
  const newChat = page.locator('button.new-chat')
  await expect(newChat).toHaveAttribute('title', /^New chat \(.+N\)$/)
  await expect(page.locator('button.settings-btn')).toHaveAttribute('title', /^Settings \(.+,\)$/)
  await newChat.hover()
  const tip = page.locator('.hover-tip')
  await expect(tip).toHaveText(/^New chat \(.+N\)$/)
  await page.mouse.move(600, 400)
  await expect(tip).toHaveCount(0)
})
