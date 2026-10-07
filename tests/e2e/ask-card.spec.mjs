import { test } from './fixtures.mjs'
import { expect, newChat, say, realErrors } from './helpers/cowork.mjs'
test.describe.configure({ timeout: 300_000 })

const Q = 'How should the folder be sorted?'
const ask = (extra) => `!!tool ask_user ${JSON.stringify({ question: Q, options: ['By type', 'By date'], ...extra })}`

test('ask_user: one clean card, a chip answers, the card collapses to one muted line', async ({ grain }) => {
  const { page } = grain
  await grain.api('/settings', { method: 'PUT', body: { toolDeferAbove: 0 } })
  await newChat(page)
  await say(page, ask())
  const card = page.locator('.desk-ask')
  await expect(card).toContainText(Q, { timeout: 60_000 })
  await expect(page.getByText('The agent has a question')).toHaveCount(0)
  await expect(page.getByText('needs approval')).toHaveCount(0)
  const send = card.getByRole('button', { name: 'Send answer' })
  await expect(send).toHaveCount(0) // nothing typed, nothing to send
  await card.getByRole('textbox', { name: 'Your answer' }).fill('x')
  await expect(send).toBeVisible()
  await card.getByRole('textbox', { name: 'Your answer' }).fill('')
  await card.getByRole('button', { name: 'By date' }).click()
  await expect(page.locator('.msg.assistant').last()).toContainText('MOCK: tool done', { timeout: 60_000 })
  await expect(card).toHaveCount(0)
  await expect(page.locator('.ask-done')).toContainText(`${Q} · By date`)
  expect(realErrors(grain)).toEqual([])
})

test('ask_user: Enter sends a typed answer', async ({ grain }) => {
  const { page } = grain
  await grain.api('/settings', { method: 'PUT', body: { toolDeferAbove: 0 } })
  await newChat(page)
  await say(page, ask({ options: undefined }))
  const box = page.getByRole('textbox', { name: 'Your answer' })
  await expect(box).toBeVisible({ timeout: 60_000 })
  await box.fill('by project')
  await box.press('Enter')
  await expect(page.locator('.ask-done')).toContainText(`${Q} · by project`, { timeout: 60_000 })
  expect(realErrors(grain)).toEqual([])
})
