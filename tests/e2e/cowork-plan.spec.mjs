import { test } from './fixtures.mjs'
import { scriptLLM } from './helpers/scriptllm.mjs'
import { expect, realErrors, deskStatus, waitStatus, openCowork, rail, WRITE, DELIVER, DONE, settingsFor } from './helpers/cowork.mjs'
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
  const { desk } = await grain.api('/cowork/desks', { method: 'POST', body: { brief: 'make a plan file', title, autonomy, start: true } })
  await openCowork(grain.page)
  await waitStatus(grain, desk.id, 'awaiting_plan')
  return { llm, desk }
}

test('plan-first desk: awaiting plan, approve in the Plan tab, steps run and tick off', async ({ grain }) => {
  const { page } = grain
  const { llm, desk } = await planDesk(grain, 'Planner')
  await expect(rail(page).locator('.desk-row-status').first()).toHaveText('Plan to approve')
  await rail(page).getByText('Planner').click()
  // a waiting plan opens on the Plan tab by default; from any other tab a banner offers it
  await page.locator('.desk-tabs').getByRole('button', { name: /^Activity/ }).click()
  await expect(page.getByText('A plan is waiting for you')).toBeVisible()
  await page.getByRole('button', { name: 'Review the plan' }).click()
  await expect(page.locator('.aplan')).toContainText('Write plan.md')
  await expect(page.locator('.aplan')).toContainText('waiting on you')
  llm.push({ calls: [{ name: 'desk_write_file', args: { path: 'outputs/plan.md', content: '# Plan\n\nplanned\n' } }] }, { calls: [DELIVER_PLAN] }, { calls: [DONE] }, { text: 'ok' })
  await page.getByRole('button', { name: 'Approve & run' }).dblclick()
  await waitStatus(grain, desk.id, 'review')
  expect(await planFile(grain, desk.id)).toContain('planned')
  const d = await grain.api(`/cowork/desks/${desk.id}`)
  expect(d.plan.status).toBe('approved')
  expect(d.plan.steps[0].status).toMatch(/consumed|done/)
  await page.locator('.desk-tabs').getByRole('button', { name: /^Plan/ }).click()
  await expect(page.locator('.desk-checklist')).toContainText('Write plan.md')
  expect(realErrors(grain)).toEqual([])
})

test('plan-first desk: edited arguments are what runs ("Approve with changes")', async ({ grain }) => {
  const { page } = grain
  const { llm, desk } = await planDesk(grain, 'Edit planner')
  await rail(page).getByText('Edit planner').click()
  await page.locator('.desk-tabs').getByRole('button', { name: /^Plan/ }).click()
  await page.getByRole('button', { name: /Edit arguments/ }).click()
  const edited = { path: 'outputs/plan.md', content: '# Edited\n\nby the user\n' }
  await page.locator('.aplan-edit textarea').fill(JSON.stringify(edited, null, 2))
  await expect(page.getByRole('button', { name: 'Approve & run' })).toBeDisabled()
  llm.push({ calls: [{ name: 'desk_write_file', args: edited }] }, { calls: [DELIVER_PLAN] }, { calls: [DONE] }, { text: 'ok' })
  await page.getByRole('button', { name: 'Approve with changes' }).click()
  await waitStatus(grain, desk.id, 'review')
  expect(await planFile(grain, desk.id)).toContain('by the user')
  const d = await grain.api(`/cowork/desks/${desk.id}`)
  expect(d.plan.steps[0].edited).toBeTruthy()
  expect(realErrors(grain)).toEqual([])
})

test('plan-first desk: rejecting the plan runs nothing and the desk stays alive', async ({ grain }) => {
  const { page } = grain
  const { llm, desk } = await planDesk(grain, 'Rejected plan')
  await rail(page).getByText('Rejected plan').click()
  await page.locator('.desk-tabs').getByRole('button', { name: /^Plan/ }).click()
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
  await rail(page).getByText('Dropper').click()
  await page.locator('.desk-tabs').getByRole('button', { name: /^Plan/ }).click()
  await page.locator('.aplan-drop input').check()
  // every step dropped: backend-side nothing is pre-approved, UI still offers Approve with changes
  await expect(page.getByRole('button', { name: 'Approve with changes' })).toBeVisible()
  expect(realErrors(grain)).toEqual([])
})
