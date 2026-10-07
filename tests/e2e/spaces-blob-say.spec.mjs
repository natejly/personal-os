import { test, expect } from './fixtures.mjs'
import { enterCanvas, spaces } from './helpers/spaces.mjs'

test.describe.configure({ timeout: 300_000 })

// Raw thinking never reaches the screen: a short thought produces no summary line, so the chat shows only the
// live "Thinking" line until the answer streams, and the shrunk blob says nothing from the chain of thought.
test('raw thinking stays off the activity line and the blob bubble', async ({ grain }) => {
  const { page, api } = grain
  const s = (await spaces(grain))[0]
  const c = await api('/conversations', { method: 'POST', body: { title: 'Bubble chat' } })
  const w = await api(`/canvases/${s.id}/windows`, { method: 'POST', body: { kind: 'chat', ref_id: c.id, x: 64, y: 48, w: 520, h: 560 } })
  await page.reload()
  await enterCanvas(grain)
  const win = page.locator(`[data-window-id="${w.id}"]`)
  await expect(win).toBeVisible()
  const box = win.getByRole('textbox', { name: 'Message' })
  await box.fill('!!think The user wants a brief. I should look at the calendar first. Then I !!slow 9000 !!reply All done')
  await box.press('Enter')

  await expect(win.locator('.thinking')).toBeVisible({ timeout: 20_000 })
  await expect(win).not.toContainText('I should look at the calendar first.')

  await win.getByTitle('Shrink to a face').click()
  await expect(win.locator('.blob-say')).not.toContainText('calendar')

  await win.locator('.chat-blob').click({ timeout: 40_000 })
  await expect(win.locator('.msg.assistant').last()).toContainText('All done', { timeout: 40_000 })
  await expect(win).not.toContainText('I should look at the calendar first.')
  expect(grain.consoleErrors).toEqual([])
})
