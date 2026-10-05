import { test } from './fixtures.mjs'
import { scriptLLM } from './helpers/scriptllm.mjs'
import { expect, realErrors, deskStatus, waitStatus, openCowork, rail, WRITE, DELIVER, DONE, settingsFor } from './helpers/cowork.mjs'
test.describe.configure({ timeout: 300_000 })

const mk = (grain, body) => grain.api('/cowork/desks', { method: 'POST', body })
const tab = (page, name) => page.locator('.desk-tabs').getByRole('button', { name: new RegExp('^' + name) })
const REVIEWED = (llm, ...steps) => llm.push(...steps, { text: 'reviewer ok' }, { text: 'final' })
const deskFile = async (grain, id, path) => (await grain.api(`/cowork/desks/${id}/file?path=${encodeURIComponent(path)}`, { raw: true })).status

async function reviewDesk(grain, title, extra = []) {
  await grain.api('/settings', { method: 'PUT', body: settingsFor })
  const llm = await scriptLLM(grain)
  REVIEWED(llm, { calls: [WRITE] }, { calls: [DELIVER] }, ...extra, { calls: [DONE] })
  const { desk } = await mk(grain, { brief: 'make a report', title, autonomy: 'propose', start: true })
  await openCowork(grain.page)
  await waitStatus(grain, desk.id, 'review')
  await rail(grain.page).getByText(title).click()
  await tab(grain.page, 'Output').click()
  return { llm, desk }
}

test('ask-as-it-goes desk: every change shows a card; Deny leaves the file unwritten and the desk alive; Approve writes it', async ({ grain }) => {
  await grain.api('/settings', { method: 'PUT', body: settingsFor })
  const llm = await scriptLLM(grain)
  llm.push({ calls: [WRITE] })
  const { page } = grain
  const { desk } = await mk(grain, { brief: 'write carefully', title: 'Careful', autonomy: 'ask', start: true })
  await openCowork(page)
  await rail(page).getByText('Careful').click()
  await waitStatus(grain, desk.id, 'needs_approval', 90_000)
  const banners = page.locator('.desk-banners')
  await expect(banners.locator('.desk-approval').first()).toBeVisible({ timeout: 60_000 })
  expect(await deskFile(grain, desk.id, 'outputs/report.md')).not.toBe(200)
  await expect(rail(page).locator('.desk-row-status').first()).toHaveText('Approval needed')
  llm.push({ text: 'ok, not writing it' })
  await banners.getByRole('button', { name: /^(Deny|Reject)$/ }).first().click()
  await expect.poll(() => deskStatus(grain, desk.id), { timeout: 60_000 }).not.toBe('needs_approval')
  expect(await deskFile(grain, desk.id, 'outputs/report.md')).not.toBe(200)
  expect((await grain.api('/approvals?status=denied')).length).toBe(1)
  expect(realErrors(grain)).toEqual([])
})

test('ask-as-it-goes desk: Approve writes the file once', async ({ grain }) => {
  await grain.api('/settings', { method: 'PUT', body: settingsFor })
  const llm = await scriptLLM(grain)
  llm.push({ calls: [WRITE] })
  const { page } = grain
  const { desk } = await mk(grain, { brief: 'write carefully', title: 'Careful 2', autonomy: 'ask', start: true })
  await openCowork(page)
  await rail(page).getByText('Careful 2').click()
  await waitStatus(grain, desk.id, 'needs_approval', 90_000)
  llm.push({ text: 'written' })
  await page.locator('.desk-banners').getByRole('button', { name: /^(Approve|Allow once|Allow)$/ }).first().dblclick()
  await expect.poll(() => deskFile(grain, desk.id, 'outputs/report.md'), { timeout: 60_000 }).toBe(200)
  expect((await grain.api('/approvals?status=approved')).length).toBe(1)
  expect(realErrors(grain)).toEqual([])
})

test('review: accept into a new doc, verified, the desk closes out and can be archived', async ({ grain }) => {
  const { desk } = await reviewDesk(grain, 'Reviewed')
  const { page } = grain
  await expect(page.locator('.desk-review-head')).toContainText('1 output')
  await page.getByRole('button', { name: /Accept selected/ }).click()
  await expect(page.locator('.desk-output .desk-verified').first()).toBeVisible()
  await expect.poll(() => deskStatus(grain, desk.id), { timeout: 60_000 }).toBe('done')
  await page.locator('.desk-actions').getByRole('button', { name: 'Archive', exact: true }).click()
  await expect.poll(async () => (await grain.api(`/cowork/desks/${desk.id}`)).archived).toBeTruthy()
  await expect(rail(page)).toHaveCount(0)
  expect(realErrors(grain)).toEqual([])
})

test('review: append to an existing doc keeps the old text', async ({ grain }) => {
  await grain.api('/docs', { method: 'POST', body: { title: 'Running notes', content: 'EXISTING LINE\n' } })
  await reviewDesk(grain, 'Appender')
  const { page } = grain
  await page.locator('.desk-output .seg').getByRole('button', { name: /append/i }).click()
  await expect(page.getByRole('button', { name: /Accept selected/ })).toBeDisabled() // needs a target
  await page.locator('.desk-output select').selectOption({ label: 'Running notes' })
  await page.getByRole('button', { name: /Accept selected/ }).click()
  await expect(page.locator('.desk-output .desk-verified').first()).toBeVisible()
  const docs = await grain.api('/docs')
  const d = docs.find((x) => x.title === 'Running notes')
  const full = await grain.api(`/docs/${d.id}`)
  expect(full.content).toContain('EXISTING LINE')
  // docEditMode defaults to review: the append lands as a pending revision the user confirms in the editor
  expect(JSON.stringify(full.pending)).toContain('Hello from the desk')
  expect(realErrors(grain)).toEqual([])
})

test('review: reject with a note, and "Send back" wakes the desk with the feedback', async ({ grain }) => {
  const { llm, desk } = await reviewDesk(grain, 'Rejector')
  const { page } = grain
  llm.push({ text: 'I will revise it.' })
  await page.locator('.desk-review-foot').getByRole('button', { name: 'Send back' }).click()
  await page.getByPlaceholder(/What needs changing/).fill('add a conclusion please-xyz')
  await page.getByPlaceholder(/What needs changing/).press('Enter')
  await expect.poll(() => JSON.stringify(llm.requests).includes('add a conclusion please-xyz'), { timeout: 60_000 }).toBe(true)
  await expect.poll(() => deskStatus(grain, desk.id), { timeout: 60_000 }).not.toBe('review').catch(() => {})
  expect(realErrors(grain)).toEqual([])
})

test('review: rejecting all outputs marks them rejected and finishes the desk', async ({ grain }) => {
  const { desk } = await reviewDesk(grain, 'Rejector 2')
  const { page } = grain
  await page.getByRole('button', { name: /Reject (all|selected)/ }).click()
  await page.getByPlaceholder(/Why reject/).fill('not wanted')
  await page.getByRole('button', { name: 'Reject', exact: true }).last().click()
  await expect.poll(async () => (await grain.api(`/cowork/desks/${desk.id}`)).outputs[0].status, { timeout: 60_000 }).toBe('rejected')
  await expect.poll(() => deskStatus(grain, desk.id), { timeout: 60_000 }).toBe('done')
  expect(realErrors(grain)).toEqual([])
})

test('a file rewritten after it was delivered is stale: plain accept is refused, Accept current file ships the new bytes', async ({ grain }) => {
  const rewrite = { name: 'desk_write_file', args: { path: 'outputs/report.md', content: '# Report v2\n\nrewritten later\n', mode: 'overwrite' } }
  const { desk } = await reviewDesk(grain, 'Stale one', [{ calls: [rewrite] }])
  const { page } = grain
  await expect(page.locator('.desk-output').getByText('stale')).toBeVisible({ timeout: 30_000 })
  await page.getByRole('button', { name: /Accept selected/ }).click()
  await expect(page.locator('.desk-output .desk-unverified, .desk-output .aplan-invalid').first()).toBeVisible({ timeout: 30_000 })
  await page.getByRole('button', { name: 'Accept current file' }).click()
  await expect(page.locator('.desk-output .desk-verified').first()).toBeVisible({ timeout: 30_000 })
  const docs = await grain.api('/docs')
  const fulls = await Promise.all(docs.map((d) => grain.api('/docs/' + d.id)))
  expect(fulls.some((d) => /rewritten later/.test(d.content || '') || /rewritten later/.test(JSON.stringify(d.pending)))).toBe(true)
  expect(realErrors(grain)).toEqual([])
})
