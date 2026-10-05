import { test } from './fixtures.mjs'
import { scriptLLM } from './helpers/scriptllm.mjs'
import { expect, realErrors, deskStatus, waitStatus, openCowork, rail, WRITE, DELIVER, DONE, settingsFor } from './helpers/cowork.mjs'
test.describe.configure({ timeout: 300_000 })


test('a propose-autonomy desk works, delivers, reaches review, and accept puts the file into a doc', async ({ grain }) => {
  await grain.api('/settings', { method: 'PUT', body: settingsFor })
  const llm = await scriptLLM(grain)
  llm.push({ calls: [WRITE] }, { calls: [DELIVER] }, { calls: [DONE] }, { text: 'Finished.' })
  const { page } = grain
  const created = await grain.api('/cowork/desks', { method: 'POST', body: { brief: 'Write a short report', title: 'Report desk', autonomy: 'propose', start: true } })
  const id = created.desk.id
  await openCowork(page)
  await expect(rail(page)).toContainText('Report desk')
  await waitStatus(grain, id, 'review')
  await expect(rail(page).locator('.desk-row-status').first()).toHaveText('Ready to review')
  await rail(page).getByText('Report desk').click()
  await page.locator('.desk-tabs').getByRole('button', { name: /^Output/ }).click()
  await expect(page.locator('.desk-output')).toContainText('The report')
  await page.getByRole('button', { name: 'Preview' }).click()
  await expect(page.locator('.desk-output-excerpt')).toContainText('Hello from the desk')
  await page.getByRole('button', { name: /Accept selected/ }).click()
  await expect(page.locator('.desk-output .desk-verified').first()).toBeVisible({ timeout: 30_000 })
  const d = await grain.api(`/cowork/desks/${id}`)
  expect(d.outputs[0].status).toMatch(/accepted|promoted/)
  expect(['done', 'review']).toContain(d.status)
  expect((await grain.api('/docs')).some((x) => /report/i.test(x.title) || /Hello from the desk/.test(x.content || ''))).toBe(true)
  expect(realErrors(grain)).toEqual([])
})
