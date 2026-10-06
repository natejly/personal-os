import { test } from './fixtures.mjs'
import { scriptLLM } from './helpers/scriptllm.mjs'
import { expect, realErrors, deskStatus, waitStatus, deskChat, openChat, strip, DONE, settingsFor } from './helpers/cowork.mjs'
test.describe.configure({ timeout: 300_000 })

const PLAN = (content = '# Plan\n\nplanned\n') => ({
  name: 'propose_plan',
  args: { title: 'Write the plan file', intent: 'one file', steps: [
    { tool: 'desk_write_file', title: 'Write plan.md', why: 'the deliverable', arguments: { path: 'outputs/plan.md', content } }] }
})
const DELIVER_PLAN = { name: 'desk_deliver', args: { path: 'outputs/plan.md', title: 'The plan file' } }
const planFile = async (grain, id) => (await grain.api(`/cowork/desks/${id}/file?path=${encodeURIComponent('outputs/plan.md')}`)).text

async function planDesk(grain, title, autonomy = 'plan') {
  await grain.api('/settings', { method: 'PUT', body: settingsFor })
  const llm = await scriptLLM(grain)
  llm.push({ calls: [PLAN()] })
  const { desk } = await deskChat(grain, { brief: 'make a plan file', title, autonomy })
  await waitStatus(grain, desk.id, 'awaiting_plan')
  await openChat(grain.page, title)
  return { llm, desk }
}

test('plan-first chat: awaiting plan, approve the plan card in the chat, steps run and tick off', async ({ grain }) => {
  const { page } = grain
  const { llm, desk } = await planDesk(grain, 'Planner')
  await expect(strip(page)).toContainText('Plan to approve')
  // one plan card in the chat, not the transcript's and the desk's both
  await expect(page.getByRole('button', { name: /^Approve/ })).toHaveCount(1)
  await expect(page.locator('.aplan')).toContainText('Write plan.md')
  await expect(page.locator('.aplan')).toContainText('waiting on you')
  llm.push({ calls: [{ name: 'desk_write_file', args: { path: 'outputs/plan.md', content: '# Plan\n\nplanned\n' } }] }, { calls: [DELIVER_PLAN] }, { calls: [DONE] }, { text: 'ok' })
  await page.getByRole('button', { name: 'Approve & run' }).dblclick()
  await waitStatus(grain, desk.id, 'review')
  expect(await planFile(grain, desk.id)).toContain('planned')
  const d = await grain.api(`/cowork/desks/${desk.id}`)
  expect(d.plan.status).toBe('approved')
  expect(d.plan.steps[0].status).toMatch(/consumed|done/)
  await expect(page.locator('.desk-checklist')).toContainText('Write plan.md')
  expect(realErrors(grain)).toEqual([])
})

test('plan-first chat: edited arguments are what runs ("Approve with changes")', async ({ grain }) => {
  const { page } = grain
  const { llm, desk } = await planDesk(grain, 'Edit planner')
  await page.getByRole('button', { name: /Edit arguments/ }).click()
  const edited = { path: 'outputs/plan.md', content: '# Edited\n\nby the user\n' }
  await page.locator('.aplan-edit textarea').fill(JSON.stringify(edited, null, 2))
  // the edit relabels the button and keeps it enabled
  await expect(page.getByRole('button', { name: 'Approve & run' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Approve with changes' })).toBeEnabled()
  llm.push({ calls: [{ name: 'desk_write_file', args: edited }] }, { calls: [DELIVER_PLAN] }, { calls: [DONE] }, { text: 'ok' })
  await page.getByRole('button', { name: 'Approve with changes' }).click()
  await waitStatus(grain, desk.id, 'review')
  expect(await planFile(grain, desk.id)).toContain('by the user')
  const d = await grain.api(`/cowork/desks/${desk.id}`)
  expect(d.plan.steps[0].edited).toBeTruthy()
  expect(realErrors(grain)).toEqual([])
})

test('plan-first chat: rejecting the plan runs nothing and the desk stays alive', async ({ grain }) => {
  const { page } = grain
  const { llm, desk } = await planDesk(grain, 'Rejected plan')
  llm.push({ text: 'Understood, tell me what you want instead.' })
  await page.getByRole('button', { name: 'Reject', exact: true }).click()
  await expect.poll(async () => (await grain.api(`/cowork/desks/${desk.id}`)).plan?.status, { timeout: 60_000 }).toBe('rejected')
  expect(JSON.stringify(await grain.api(`/cowork/desks/${desk.id}/files`))).not.toContain('plan.md')
  expect(await deskStatus(grain, desk.id)).not.toBe('failed')
  expect(realErrors(grain)).toEqual([])
})

test('dropping a step from a plan leaves it unauthorised', async ({ grain }) => {
  const { page } = grain
  const { desk } = await planDesk(grain, 'Dropper')
  await page.locator('.aplan-drop input').check()
  // every step dropped: nothing is left to approve, so the button reads "Approve 0 of 1" and is disabled
  await expect(page.getByRole('button', { name: /^Approve 0 of \d+$/ })).toBeDisabled()
  await expect(page.getByText('Nothing left to approve')).toBeVisible()
  expect(realErrors(grain)).toEqual([])
})
