import { test } from './fixtures.mjs'
import { expect, realErrors, resize, settingsFor } from './helpers/cowork.mjs'
test.describe.configure({ timeout: 300_000 })

const wf = (name, steps, extra = {}) => JSON.stringify({ name, description: `${name} workflow`, params: { who: { type: 'string', default: 'World' } }, steps, ...extra })
const docStep = (id, title, extra = {}) => ({ id, tool: 'doc_create', args: { title, content: 'made by a workflow' }, ...extra })

async function openWorkflows(page) {
  await page.locator('.sidebar').getByText('Library', { exact: true }).click()
  await page.getByRole('tab', { name: /^Automations/ }).click()
  await page.getByRole('tab', { name: /^Workflows/ }).click()
}
const docTitles = async (grain) => (await grain.api('/docs')).map((d) => d.title)

test('workflows panel: empty state, create through the editor with live validation, list', async ({ grain }) => {
  await grain.api('/settings', { method: 'PUT', body: settingsFor })
  const { page } = grain
  await openWorkflows(page)
  await expect(page.getByText('No workflows yet')).toBeVisible()
  await page.getByRole('button', { name: 'New workflow' }).first().click()
  const ta = page.getByLabel(/Definition \(JSON\)/)
  await ta.fill('{ not json')
  await expect(page.getByRole('button', { name: 'Save', exact: true })).toBeDisabled()
  await ta.fill(wf('hello-wf', [docStep('one', 'Hello {{who}}')]))
  await expect(page.getByText(/^Valid\./)).toBeVisible()
  await page.getByRole('button', { name: 'Save', exact: true }).click()
  await expect(page.locator('.skill-name', { hasText: 'hello-wf' })).toBeVisible()
  expect(await grain.api('/workflows')).toHaveLength(1)
  expect(realErrors(grain)).toEqual([])
})

test('run a workflow: propose, review the plan, approve, steps tick off in order and the work happens once', async ({ grain }) => {
  await grain.api('/settings', { method: 'PUT', body: settingsFor })
  const { page } = grain
  await grain.api('/workflows', { method: 'POST', body: { text: wf('two-step', [docStep('first', 'First {{who}}'), docStep('second', 'Second {{who}}', { needs: ['first'] })]) } })
  await openWorkflows(page)
  await page.getByRole('button', { name: /Run/ }).first().click()
  await page.getByPlaceholder('string').or(page.getByPlaceholder('World')).first().fill('Ada')
  await page.getByRole('button', { name: 'Review the plan' }).click()
  const run = page.locator('.wf-run').first()
  await expect(run).toContainText('waiting for your approval')
  expect(await docTitles(grain)).toHaveLength(0) // nothing starts before approval
  await expect(run.locator('.wf-step')).toHaveCount(2)
  await run.locator('.skill-actions').getByRole('button', { name: /Approve and run/ }).dblclick()
  await expect(run).toContainText('done', { timeout: 90_000 })
  await expect(run.locator('.wf-step.done')).toHaveCount(2)
  const titles = await docTitles(grain)
  expect(titles.filter((t) => t === 'First Ada')).toHaveLength(1)
  expect(titles.filter((t) => t === 'Second Ada')).toHaveLength(1)
  expect(realErrors(grain)).toEqual([])
})

test('a step marked approval:required waits on a card; Deny fails the step and what needs it, Approve continues', async ({ grain }) => {
  await grain.api('/settings', { method: 'PUT', body: settingsFor })
  const { page } = grain
  const w = await grain.api('/workflows', { method: 'POST', body: { text: wf('gated', [docStep('gate', 'Gated doc', { approval: 'required' }), docStep('after', 'After doc', { needs: ['gate'] })]) } })
  const r = await grain.api(`/workflows/${w.id}/runs`, { method: 'POST', body: { params: {} } })
  await grain.api(`/workflow-runs/${r.id}/approve`, { method: 'POST', body: { plan_digest: r.plan_digest } })
  await openWorkflows(page)
  const run = page.locator('.wf-run').first()
  await expect(run).toContainText('Step gate is waiting for you', { timeout: 60_000 })
  expect(await docTitles(grain)).toHaveLength(0)
  await run.getByRole('button', { name: 'Approve', exact: true }).click()
  await expect(run).toContainText('done', { timeout: 90_000 })
  expect(await docTitles(grain)).toEqual(expect.arrayContaining(['Gated doc', 'After doc']))

  // and the Deny path on a second run
  const r2 = await grain.api(`/workflows/${w.id}/runs`, { method: 'POST', body: { params: {} } })
  await grain.api(`/workflow-runs/${r2.id}/approve`, { method: 'POST', body: { plan_digest: r2.plan_digest } })
  await page.reload()
  await openWorkflows(page)
  const run2 = page.locator('.wf-run').filter({ hasText: 'waiting for you' }).first()
  await expect(run2).toContainText('Step gate is waiting for you', { timeout: 60_000 })
  await run2.getByRole('button', { name: 'Deny', exact: true }).click()
  await expect.poll(async () => (await grain.api(`/workflow-runs/${r2.id}`)).status, { timeout: 60_000 }).toMatch(/failed|cancelled/)
  expect((await docTitles(grain)).filter((t) => t === 'Gated doc')).toHaveLength(1)
  expect(realErrors(grain)).toEqual([])
})

test('editing a workflow withdraws a pending approval; a stale digest cannot be approved; cancel works', async ({ grain }) => {
  await grain.api('/settings', { method: 'PUT', body: settingsFor })
  const w = await grain.api('/workflows', { method: 'POST', body: { text: wf('editme', [docStep('a', 'A doc')]) } })
  const r = await grain.api(`/workflows/${w.id}/runs`, { method: 'POST', body: { params: {} } })
  await grain.api(`/workflows/${w.id}`, { method: 'PUT', body: { text: wf('editme', [docStep('a', 'A different doc')]) } })
  const bad = await grain.api(`/workflow-runs/${r.id}/approve`, { method: 'POST', body: { plan_digest: r.plan_digest }, raw: true })
  expect(bad.status).toBe(409)
  const wrongDigest = await grain.api(`/workflow-runs/${r.id}/approve`, { method: 'POST', body: { plan_digest: 'deadbeef' }, raw: true })
  expect(wrongDigest.status).toBe(409)
  expect(await docTitles(grain)).toHaveLength(0)
  // cancel a proposed run
  const r2 = await grain.api(`/workflows/${w.id}/runs`, { method: 'POST', body: { params: {} } })
  const { page } = grain
  await openWorkflows(page)
  const row = page.locator('.wf-run').first()
  await row.locator('.skill-actions').getByRole('button', { name: /Cancel/ }).click()
  await expect.poll(async () => (await grain.api(`/workflow-runs/${r2.id}`)).status).toBe('cancelled')
  // double approve of a done run changes nothing
  const r3 = await grain.api(`/workflows/${w.id}/runs`, { method: 'POST', body: { params: {} } })
  await grain.api(`/workflow-runs/${r3.id}/approve`, { method: 'POST', body: { plan_digest: r3.plan_digest } })
  await expect.poll(async () => (await grain.api(`/workflow-runs/${r3.id}`)).status, { timeout: 90_000 }).toBe('done')
  await grain.api(`/workflow-runs/${r3.id}/approve`, { method: 'POST', body: { plan_digest: r3.plan_digest }, raw: true })
  expect((await docTitles(grain)).filter((t) => t === 'A different doc')).toHaveLength(1)
  expect(realErrors(grain)).toEqual([])
})

test('a 40-step chain runs in order at 820x520 and the run card scrolls', async ({ grain }) => {
  await grain.api('/settings', { method: 'PUT', body: settingsFor })
  await resize(grain)
  const steps = Array.from({ length: 40 }, (_, i) => docStep(`s${i}`, `Chain ${i}`, i ? { needs: [`s${i - 1}`] } : {}))
  const w = await grain.api('/workflows', { method: 'POST', body: { text: wf('chain', steps) } })
  const r = await grain.api(`/workflows/${w.id}/runs`, { method: 'POST', body: { params: {} } })
  await grain.api(`/workflow-runs/${r.id}/approve`, { method: 'POST', body: { plan_digest: r.plan_digest } })
  await openWorkflows(grain.page)
  await expect.poll(async () => (await grain.api(`/workflow-runs/${r.id}`)).status, { timeout: 200_000 }).toBe('done')
  expect(await docTitles(grain)).toHaveLength(40)
  await expect(grain.page.locator('.wf-run').first()).toContainText('done')
  expect(await grain.page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1)).toBe(false)
  expect(realErrors(grain)).toEqual([])
})

test('independent steps run side by side (waves), and a step that needs a failed step is blocked', async ({ grain }) => {
  await grain.api('/settings', { method: 'PUT', body: settingsFor })
  const steps = [
    { id: 'bad', tool: 'doc_read', args: { id: 'does-not-exist' } },
    docStep('indep', 'Independent'),
    docStep('child', 'Child of bad', { needs: ['bad'] })
  ]
  const w = await grain.api('/workflows', { method: 'POST', body: { text: wf('waves', steps) } })
  const r = await grain.api(`/workflows/${w.id}/runs`, { method: 'POST', body: { params: {} } })
  await grain.api(`/workflow-runs/${r.id}/approve`, { method: 'POST', body: { plan_digest: r.plan_digest } })
  await expect.poll(async () => (await grain.api(`/workflow-runs/${r.id}`)).status, { timeout: 90_000 }).toBe('failed')
  const titles = await docTitles(grain)
  expect(titles).toContain('Independent')
  expect(titles).not.toContain('Child of bad')
  const run = await grain.api(`/workflow-runs/${r.id}`)
  expect(run.steps.find((s) => s.step_id === 'child').status).toMatch(/blocked|skipped|pending/)
  // resume only re-runs what is not done
  const res = await grain.api(`/workflow-runs/${r.id}/resume`, { method: 'POST', raw: true })
  expect([200, 409]).toContain(res.status)
  await grain.page.waitForTimeout(3000)
  expect((await docTitles(grain)).filter((t) => t === 'Independent')).toHaveLength(1)
})
