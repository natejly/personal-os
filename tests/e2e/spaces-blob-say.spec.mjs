import { test, expect } from './fixtures.mjs'
import { enterCanvas, spaces } from './helpers/spaces.mjs'

test.describe.configure({ timeout: 300_000 })

// The live "what is it doing" line: the chat's activity row shows the latest thought while no answer has
// streamed yet, and the blob's speech bubble says the same thing; both go once the answer arrives.
test('activity line and blob bubble show the latest thought until the answer streams', async ({ grain }) => {
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

  await expect(win.locator('.reasoning-label')).toContainText('I should look at the calendar first.', { timeout: 20_000 })

  await win.getByTitle('Shrink to a face').click()
  const bubble = win.locator('.blob-say')
  await expect(bubble).toHaveText('I should look at the calendar first.')

  await expect(bubble).toHaveCount(0, { timeout: 40_000 })
  await win.locator('.chat-blob').click()
  await expect(win.locator('.msg.assistant').last()).toContainText('All done')
  await expect(win.locator('.reasoning-label')).toContainText('Thought')
  expect(grain.consoleErrors).toEqual([])
})
