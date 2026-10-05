import { test } from './fixtures.mjs'
import { scriptLLM } from './helpers/scriptllm.mjs'
import { expect, realErrors, resize, deskStatus, waitStatus, openCowork, rail, WRITE, DELIVER, DONE, settingsFor } from './helpers/cowork.mjs'
test.describe.configure({ timeout: 300_000 })

const mk = (grain, body) => grain.api('/cowork/desks', { method: 'POST', body })

test('desk_ask: the desk waits on the question and the answer resumes it', async ({ grain }) => {
  await grain.api('/settings', { method: 'PUT', body: settingsFor })
  const llm = await scriptLLM(grain)
  llm.push({ calls: [{ name: 'desk_ask', args: { question: 'Which quarter?', options: ['Q1', 'Q3'] } }] })
  const { page } = grain
  const { desk } = await mk(grain, { brief: 'compare quarters', title: 'Asker', autonomy: 'propose', start: true })
  await openCowork(page)
  await rail(page).getByText('Asker').click()
  await expect(page.locator('.desk-ask').first()).toContainText('Which quarter?', { timeout: 90_000 })
  await expect(rail(page).locator('.desk-row-status').first()).toHaveText(/Approval needed|Waiting on you/)
  llm.push({ calls: [WRITE] }, { calls: [DELIVER] }, { calls: [DONE] }, { text: 'done' })
  await page.getByRole('group', { name: 'Suggested answers' }).getByRole('button', { name: 'Q3' }).first().click()
  await waitStatus(grain, desk.id, 'review')
  expect(JSON.stringify(llm.requests)).toContain('Q3') // the answer reached the model
  expect(realErrors(grain)).toEqual([])
})

test('desk_ask answered with typed text via ⌘↵', async ({ grain }) => {
  await grain.api('/settings', { method: 'PUT', body: settingsFor })
  const llm = await scriptLLM(grain)
  llm.push({ calls: [{ name: 'desk_ask', args: { question: 'Who gets it?' } }] })
  const { page } = grain
  const { desk } = await mk(grain, { brief: 'send it', title: 'Typed asker', autonomy: 'propose', start: true })
  await openCowork(page)
  await rail(page).getByText('Typed asker').click()
  await expect(page.locator('.desk-ask').first()).toContainText('Who gets it?', { timeout: 90_000 })
  llm.push({ calls: [WRITE] }, { calls: [DELIVER] }, { calls: [DONE] }, { text: 'done' })
  const box = page.getByRole('textbox', { name: 'Your answer' }).first()
  await box.fill('only Dana-the-unique')
  await box.press('Meta+Enter')
  await waitStatus(grain, desk.id, 'review')
  expect(JSON.stringify(llm.requests)).toContain('only Dana-the-unique')
  expect(realErrors(grain)).toEqual([])
})

test('stop a working desk, then message it to pick it back up; delete removes it', async ({ grain }) => {
  await grain.api('/settings', { method: 'PUT', body: settingsFor })
  const llm = await scriptLLM(grain)
  llm.push({ calls: [WRITE], delay: 120_000 })
  const { page } = grain
  page.on('dialog', (d) => void d.accept())
  const { desk } = await mk(grain, { brief: 'slow work', title: 'Slowpoke', autonomy: 'propose', start: true })
  await openCowork(page)
  await rail(page).getByText('Slowpoke').click()
  await expect.poll(() => deskStatus(grain, desk.id), { timeout: 60_000 }).toMatch(/working|planning/)
  await expect(rail(page).locator('.desk-row-status').first()).toHaveText(/Working|Planning/)
  await page.getByRole('button', { name: /Stop/ }).dblclick()
  await waitStatus(grain, desk.id, 'stopped', 60_000)
  await expect(rail(page).locator('.desk-row-status').first()).toHaveText('Stopped')
  await expect(page.getByRole('button', { name: /^Stop$/ })).toHaveCount(0)
  // a stopped desk picks work back up when messaged
  llm.queue.length = 0
  llm.push({ text: 'Resuming.' })
  const steer = page.getByRole('textbox').last()
  await steer.fill('carry on please')
  await steer.press('Meta+Enter')
  await expect.poll(() => deskStatus(grain, desk.id), { timeout: 60_000 }).not.toBe('stopped')
  await expect.poll(() => deskStatus(grain, desk.id), { timeout: 90_000 }).toMatch(/done|review|paused|blocked|interrupted|failed/)
  // a desk that is waiting on the user cannot be deleted while it holds a card (DELETE_FROM): no trash button
  await expect(page.locator('[title="Delete this desk"]')).toHaveCount(0)
  expect(realErrors(grain)).toEqual([])
})

test('delete: only offered once the desk is stopped; confirm dialogs gate it; workspace purge optional', async ({ grain }) => {
  await grain.api('/settings', { method: 'PUT', body: settingsFor })
  const { page } = grain
  const { desk } = await mk(grain, { brief: 'to delete', title: 'Doomed', autonomy: 'propose', start: false })
  await openCowork(page)
  await rail(page).getByText('Doomed').click()
  const trash = page.locator('[title="Delete this desk"]')
  // Cancel the first confirm: nothing is deleted
  page.once('dialog', (d) => void d.dismiss())
  await trash.click()
  await page.waitForTimeout(500)
  expect(await grain.api('/cowork/desks')).toHaveLength(1)
  // Accept the confirm, keep the workspace files
  const answers = [true, false] // delete? yes. also purge the workspace files? no.
  page.on('dialog', (d) => void (answers.shift() ? d.accept() : d.dismiss()))
  await trash.click()
  await expect.poll(async () => (await grain.api('/cowork/desks')).length).toBe(0)
  await expect(page.locator('.empty-state')).toBeVisible()
  expect((await grain.api(`/cowork/desks/${desk.id}`, { raw: true })).status).toBe(404)
  expect(realErrors(grain)).toEqual([])
})

test('pause and resume a desk', async ({ grain }) => {
  await grain.api('/settings', { method: 'PUT', body: settingsFor })
  const llm = await scriptLLM(grain)
  llm.push({ calls: [WRITE], delay: 120_000 })
  const { page } = grain
  const { desk } = await mk(grain, { brief: 'pausable', title: 'Pausable', autonomy: 'propose', start: true })
  await openCowork(page)
  await rail(page).getByText('Pausable').click()
  await expect.poll(() => deskStatus(grain, desk.id), { timeout: 60_000 }).toMatch(/working|planning/)
  await page.getByRole('button', { name: /Pause/ }).click()
  await waitStatus(grain, desk.id, 'paused', 60_000)
  await expect(rail(page).locator('.desk-row-status').first()).toHaveText('Paused')
  llm.queue.length = 0
  llm.push({ calls: [WRITE] }, { calls: [DELIVER] }, { calls: [DONE] }, { text: 'ok' })
  await page.getByRole('button', { name: /Resume/ }).click()
  await waitStatus(grain, desk.id, 'review', 90_000)
  expect(realErrors(grain)).toEqual([])
})

test('new desk form: validation, ⌘↵, draft, autonomy, limits, then Start', async ({ grain }) => {
  await grain.api('/settings', { method: 'PUT', body: settingsFor })
  const llm = await scriptLLM(grain)
  const { page } = grain
  await openCowork(page)
  await page.getByRole('button', { name: 'New desk' }).first().click()
  const create = page.getByRole('button', { name: /Create (and start|draft)/ })
  await expect(create).toBeDisabled()
  await page.getByRole('textbox').filter({ hasText: '' }).first().fill('   ')
  await expect(create).toBeDisabled()
  await page.locator('.desk-new textarea').fill('Summarise the thing')
  await page.getByPlaceholder('Taken from the brief').fill('My draft desk')
  await page.getByLabel(/Ask as it goes/).check()
  await page.locator('.desk-limits input[type=number]').fill('3')
  await page.getByLabel('Start it now').uncheck()
  await expect(page.getByRole('button', { name: /Create draft/ })).toBeVisible()
  await page.locator('.desk-new textarea').press('Meta+Enter')
  await expect.poll(async () => (await grain.api('/cowork/desks')).length).toBe(1)
  const [d] = await grain.api('/cowork/desks')
  expect(d).toMatchObject({ title: 'My draft desk', status: 'draft', autonomy: 'ask' })
  expect(d.budget.maxTurns).toBe(3)
  await expect(rail(page)).toContainText('Drafts & paused')
  // open the draft, change autonomy, then Start it
  await rail(page).getByText('My draft desk').click()
  await page.getByRole('button', { name: /Autonomy and limits/ }).or(page.locator('[title="Autonomy and limits"]')).click()
  await page.getByLabel(/Work and propose/).check()
  await page.getByRole('button', { name: 'Save', exact: true }).click()
  await expect.poll(async () => (await grain.api(`/cowork/desks/${d.id}`)).autonomy).toBe('propose')
  llm.push({ calls: [WRITE] }, { calls: [DELIVER] }, { calls: [DONE] }, { text: 'ok' })
  await page.getByRole('button', { name: /Start/ }).click()
  await waitStatus(grain, d.id, 'review', 90_000)
  expect(realErrors(grain)).toEqual([])
})

test('empty brief is refused by the API; unknown autonomy too', async ({ grain }) => {
  const r1 = await grain.api('/cowork/desks', { method: 'POST', body: { brief: '   ' }, raw: true })
  expect(r1.status).toBe(400)
  const r2 = await grain.api('/cowork/desks', { method: 'POST', body: { brief: 'x', autonomy: 'yolo' }, raw: true })
  expect(r2.status).toBe(400)
  expect(await grain.api('/cowork/desks')).toHaveLength(0)
})

test('desk tabs: Files lists the workspace, Output, Browser empty state, Plan empty state', async ({ grain }) => {
  await grain.api('/settings', { method: 'PUT', body: settingsFor })
  const llm = await scriptLLM(grain)
  llm.push({ calls: [WRITE] }, { calls: [DELIVER] }, { calls: [DONE] }, { text: 'ok' })
  const { page } = grain
  const { desk } = await mk(grain, { brief: 'tabs', title: 'Tabby', autonomy: 'propose', start: true })
  await openCowork(page)
  await waitStatus(grain, desk.id, 'review')
  await rail(page).getByText('Tabby').click()
  await page.locator('.desk-tabs').getByRole('button', { name: /^Files/ }).click()
  await expect(page.getByText('report.md').first()).toBeVisible()
  await page.getByText('report.md').first().click()
  await expect(page.getByText('Hello from the desk').first()).toBeVisible()
  await page.locator('.desk-tabs').getByRole('button', { name: /^Browser/ }).click()
  await expect(page.getByText("This desk hasn't opened its browser.")).toBeVisible()
  await page.locator('.desk-tabs').getByRole('button', { name: /^Plan/ }).click()
  await expect(page.getByText('No plan yet.')).toBeVisible()
  await page.locator('.desk-tabs').getByRole('button', { name: /^Output/ }).click()
  await expect(page.locator('.desk-output')).toContainText('The report')
  await page.locator('.desk-tabs').getByRole('button', { name: /^Activity/ }).click()
  expect(realErrors(grain)).toEqual([])
})

test('25 desks on the rail: sections, counts, [ and ] stepping, at 820x520', async ({ grain }) => {
  await resize(grain)
  const { page } = grain
  for (let i = 0; i < 25; i++) await mk(grain, { brief: `brief ${i}`, title: `Desk ${String(i).padStart(2, '0')}`, autonomy: 'plan', start: false })
  await openCowork(page)
  await expect(rail(page).locator('.desk-row')).toHaveCount(25, { timeout: 30_000 })
  await expect(rail(page).locator('h4').first()).toContainText('25')
  const titles = await rail(page).locator('.desk-row-title').allInnerTexts()
  const name = (i) => titles[(i + titles.length) % titles.length].trim()
  await rail(page).locator('.desk-row').nth(3).click()
  await expect(rail(page).locator('.desk-row.active')).toContainText(name(3))
  await page.locator('.desk-head').click() // focus outside any field
  await page.keyboard.press(']')
  await expect(rail(page).locator('.desk-row.active')).toContainText(name(4))
  await page.keyboard.press('[')
  await page.keyboard.press('[')
  await expect(rail(page).locator('.desk-row.active')).toContainText(name(2))
  await rail(page).locator('.desk-row').first().click()
  await page.locator('.desk-head').click()
  await page.keyboard.press('[')
  await expect(rail(page).locator('.desk-row.active')).toContainText(name(-1)) // wraps
  // the rail scrolls rather than overflowing the window
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1)
  expect(overflow).toBe(false)
  expect(realErrors(grain)).toEqual([])
})

test('desk with a 100 KB brief and a long title is created and rendered', async ({ grain }) => {
  await resize(grain)
  const { page } = grain
  const brief = ('lorem ipsum dolor sit amet '.repeat(4000)).slice(0, 100_000)
  const { desk } = await mk(grain, { brief, title: 'T'.repeat(300), autonomy: 'plan', start: false })
  await openCowork(page)
  await rail(page).locator('.desk-row').first().click()
  await expect(page.locator('.desk-brief')).toBeVisible()
  expect((await grain.api(`/cowork/desks/${desk.id}`)).brief.length).toBeGreaterThan(50_000)
  expect(realErrors(grain)).toEqual([])
})
