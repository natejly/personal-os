import { test, expect } from './fixtures.mjs'
import { resize, noOverflow } from './helpers/library.mjs'

async function openScheduled(page) {
  await page.getByRole('button', { name: /New chat/ }).first().click()
  await page.locator('.inbox-link').click()
  const btn = page.getByRole('button', { name: /^Scheduled \(/ })
  await expect(btn).toBeVisible({ timeout: 20_000 })
  if ((await btn.getAttribute('aria-expanded')) !== 'true') await btn.click()
  await expect(page.getByText('Scheduled tasks')).toBeVisible()
}

async function addTask(page, { name, prompt, mode = 'Repeat' }) {
  await page.getByRole('button', { name: 'Schedule a task' }).click()
  await page.getByPlaceholder(/^Name, e\.g\./).fill(name)
  await page.getByPlaceholder(/^What should it do/).fill(prompt)
  await page.getByLabel('When it runs').selectOption({ label: mode })
}

const job = (name, over = {}) => ({ name, prompt: `say hi from ${name}`, kind: 'cron', cron: '0 9 * * *', enabled: false, ...over })

test('fresh install: no jobs enabled by default; scheduled list shows empty state', async ({ grain }) => {
  const { page, api } = grain
  const jobs = await api('/jobs')
  expect(jobs.filter((j) => j.enabled)).toEqual([])
  await openScheduled(page)
  if (!jobs.length) await expect(page.getByText('None yet.')).toBeVisible()
  expect(grain.consoleErrors).toEqual([])
})

test('create a repeating job for every preset the form offers, and read each back', async ({ grain }) => {
  const { page, api } = grain
  await openScheduled(page)
  const cases = [
    ['Hourly', null, null, '0 * * * *'],
    ['Daily', '07:30', null, '30 7 * * *'],
    ['Weekdays', '18:05', null, '5 18 * * 1-5'],
    ['Weekly', '09:00', 'Friday', '0 9 * * 5']
  ]
  for (const [preset, time, day, cron] of cases) {
    const name = `job ${preset}`
    await addTask(page, { name, prompt: `do ${preset}` })
    await page.getByLabel('How often').selectOption({ label: preset })
    if (day) await page.getByLabel('Day of the week').selectOption({ label: day })
    if (time) await page.getByLabel('At', { exact: true }).fill(time)
    await page.getByRole('button', { name: 'Schedule', exact: true }).click()
    await expect(page.locator('.job-name', { hasText: name })).toBeVisible()
    const j = (await api('/jobs')).find((x) => x.name === name)
    expect(j.cron).toBe(cron)
    expect(j.enabled).toBe(true)
  }
  // custom cron
  await addTask(page, { name: 'job custom', prompt: 'custom' })
  await page.getByLabel('How often').selectOption({ label: 'Custom cron' })
  await page.getByLabel('Cron expression').fill('*/15 8-17 * * 1-5')
  await page.getByRole('button', { name: 'Schedule', exact: true }).click()
  await expect(page.locator('.job-name', { hasText: 'job custom' })).toBeVisible()
  expect((await api('/jobs')).find((x) => x.name === 'job custom').cron).toBe('*/15 8-17 * * 1-5')

  // invalid custom crons are refused and nothing is saved
  const before = (await api('/jobs')).length
  for (const bad of ['nonsense', '* * * *', '* * * * * *', '0 0 30 2 *', '61 * * * *']) {
    await addTask(page, { name: 'bad cron', prompt: 'x' })
    await page.getByLabel('How often').selectOption({ label: 'Custom cron' })
    await page.getByLabel('Cron expression').fill(bad)
    await page.getByRole('button', { name: 'Schedule', exact: true }).click()
    await expect(page.getByText(/Jobs: /).first()).toBeVisible({ timeout: 10_000 })
    expect((await api('/jobs')).length).toBe(before)
    await page.getByRole('button', { name: 'Schedule a task' }).click() // close the form
  }
  expect(grain.consoleErrors.filter((e) => !/status of 400/.test(e))).toEqual([])
})

test('enable / disable toggle, edit, delete', async ({ grain }) => {
  const { page, api } = grain
  const j = await api('/jobs', { method: 'POST', body: job('Toggle me') })
  await openScheduled(page)
  const sw = page.locator('label.switch-wrap', { has: page.getByLabel('Toggle me enabled') }).locator('input')
  await expect(sw).not.toBeChecked()
  await sw.locator('xpath=..').click()
  await expect.poll(async () => (await api('/jobs')).find((x) => x.id === j.id).enabled).toBe(true)
  await expect.poll(async () => (await api('/jobs')).find((x) => x.id === j.id).next_due_at).toBeTruthy()
  await sw.locator('xpath=..').click()
  await expect.poll(async () => (await api('/jobs')).find((x) => x.id === j.id).enabled).toBe(false)

  await page.getByRole('button', { name: 'Edit Toggle me' }).click()
  await page.getByPlaceholder(/^Name, e\.g\./).fill('Renamed job')
  await page.getByRole('button', { name: 'Save', exact: true }).click()
  await expect(page.locator('.job-name', { hasText: 'Renamed job' })).toBeVisible()

  // delete asks with a native confirm: dismissing keeps the job, accepting removes it
  page.once('dialog', (dlg) => dlg.dismiss())
  await page.getByRole('button', { name: 'Delete Renamed job' }).click()
  await page.waitForTimeout(500)
  expect((await api('/jobs')).some((x) => x.id === j.id)).toBe(true)
  page.once('dialog', (dlg) => dlg.accept())
  await page.getByRole('button', { name: 'Delete Renamed job' }).click()
  await expect.poll(async () => (await api('/jobs')).some((x) => x.id === j.id)).toBe(false)
  expect(grain.consoleErrors).toEqual([])
})

test('run now: mock model answers, run shows in the journal / inbox / history', async ({ grain }) => {
  const { page, api } = grain
  const j = await api('/jobs', { method: 'POST', body: job('Journal job', { prompt: '!!reply the weekly digest is ready' }) })
  await openScheduled(page)
  await page.getByRole('button', { name: 'Run Journal job now' }).click()
  await expect.poll(async () => (await api(`/jobs/${j.id}/runs`)).length, { timeout: 30_000 }).toBe(1)
  await expect.poll(async () => (await api(`/jobs/${j.id}/runs`))[0].status, { timeout: 30_000 }).toMatch(/done|ok|complete/i)
  const inbox = await api('/inbox')
  expect(JSON.stringify(inbox)).toContain('Journal job')
  await expect(page.getByText(/Journal job/).first()).toBeVisible()
  await expect(page.getByText(/the weekly digest is ready/).first()).toBeVisible({ timeout: 20_000 })
  // history panel
  await page.getByRole('button', { name: 'History of Journal job' }).click()
  await expect(page.locator('.job-history-run').first()).toBeVisible()
  // double-click on Run launches at most two but must not crash; both are recorded
  expect(grain.consoleErrors).toEqual([])
})

test('one-off task fires ~5s ahead then retires; spent job cannot be re-enabled without a new time', async ({ grain }) => {
  const { api } = grain
  const runAt = Date.now() / 1000 + 5
  const j = await api('/jobs', { method: 'POST', body: job('One shot', { kind: 'once', cron: '', run_at: runAt, enabled: true, prompt: '!!reply fired once' }) })
  expect(j.kind).toBe('once')
  await expect.poll(async () => (await api(`/jobs/${j.id}/runs`)).length, { timeout: 45_000 }).toBe(1)
  const after = (await api('/jobs')).find((x) => x.id === j.id)
  expect(after.enabled).toBe(false)
  expect(after.last_fired_at).toBeTruthy()
  await expect.poll(async () => (await api(`/jobs/${j.id}/runs`))[0].status, { timeout: 30_000 }).toMatch(/done|ok|complete/i)
  const r = await api(`/jobs/${j.id}`, { method: 'PATCH', body: { enabled: true }, raw: true })
  expect(r.status).toBe(400)
  // a past instant is refused on create
  const bad = await api('/jobs', { method: 'POST', body: job('Past', { kind: 'once', cron: '', run_at: Date.now() / 1000 - 3600 }), raw: true })
  expect(bad.status).toBe(400)
  // and no second run appears
  await new Promise((r) => setTimeout(r, 4000))
  expect((await api(`/jobs/${j.id}/runs`)).length).toBe(1)
})

test('one-off task created from the UI (datetime-local) schedules and fires', async ({ grain }) => {
  const { page, api } = grain
  await openScheduled(page)
  await addTask(page, { name: 'UI once', prompt: '!!reply ui fired', mode: 'Once' })
  const d = new Date(Date.now() + 60_000)
  const p = (n) => String(n).padStart(2, '0')
  await page.getByLabel('When it should run').fill(`${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`)
  await page.getByRole('button', { name: 'Schedule', exact: true }).click()
  await expect(page.locator('.job-name', { hasText: 'UI once' })).toBeVisible()
  const j = (await api('/jobs')).find((x) => x.name === 'UI once')
  expect(j.kind).toBe('once')
  expect(Math.abs(j.run_at - d.getTime() / 1000)).toBeLessThan(61)
  // a past time through the form is refused
  await addTask(page, { name: 'UI past', prompt: 'x', mode: 'Once' })
  await page.getByLabel('When it should run').fill('2020-01-01T10:00')
  await page.getByRole('button', { name: 'Schedule', exact: true }).click()
  await expect(page.getByText(/already passed/)).toBeVisible({ timeout: 10_000 })
  expect((await api('/jobs')).find((x) => x.name === 'UI past')).toBeUndefined()
})

test('missed one-off slot catches up once, late, with a late note', async ({ grain }) => {
  const { api } = grain
  const j = await api('/jobs', { method: 'POST', body: job('Missed', { kind: 'once', cron: '', run_at: Date.now() / 1000 + 3, enabled: false, prompt: '!!reply caught up' }) })
  await new Promise((r) => setTimeout(r, 5000)) // the instant passes while the job is off
  expect((await api(`/jobs/${j.id}/runs`)).length).toBe(0)
  await api(`/jobs/${j.id}`, { method: 'PATCH', body: { enabled: true } })
  await expect.poll(async () => (await api(`/jobs/${j.id}/runs`)).length, { timeout: 45_000 }).toBe(1)
  const run = (await api(`/jobs/${j.id}/runs`))[0]
  expect(JSON.stringify(run)).toMatch(/late/)
  const inbox = await api('/inbox')
  expect(JSON.stringify(inbox)).toMatch(/Missed/)
})

test('100 jobs listed at 820x520 without overflow', async ({ grain }) => {
  const { page, api } = grain
  const seeded = (await api('/jobs')).length
  for (let i = 0; i < 100; i++) await api('/jobs', { method: 'POST', body: job(`Bulk ${String(i).padStart(3, '0')}`, { cron: `${i % 60} ${i % 24} * * *` }) })
  await resize(grain)
  await openScheduled(page)
  await expect(page.getByRole('button', { name: /^Scheduled \(\d{3,}\)/ })).toBeVisible({ timeout: 20_000 })
  await expect(page.locator('.inbox-jobs li')).toHaveCount(seeded + 100)
  await noOverflow(page)
  expect(grain.consoleErrors).toEqual([])
})

test('jobs persist across relaunch; backend killed mid-action reports instead of crashing', async ({ grain }) => {
  const { api } = grain
  await api('/jobs', { method: 'POST', body: job('Persist me', { enabled: true }) })
  const page = await grain.relaunch()
  await openScheduled(page)
  await expect(page.locator('.job-name', { hasText: 'Persist me' })).toBeVisible()
  await expect(page.getByLabel('Persist me enabled')).toBeChecked()
  grain.backend.child.kill('SIGKILL')
  await page.locator('label.switch-wrap', { has: page.getByLabel('Persist me enabled') }).click()
  await expect(page.getByText(/Jobs: /).first()).toBeVisible({ timeout: 15_000 })
  await expect(page.locator('.job-name', { hasText: 'Persist me' })).toBeVisible()
})

test('double-clicking Schedule creates one job; double Run-now does not wedge the list', async ({ grain }) => {
  const { page, api } = grain
  await openScheduled(page)
  await addTask(page, { name: 'Dbl', prompt: '!!reply dbl' })
  await page.getByRole('button', { name: 'Schedule', exact: true }).dblclick()
  await expect(page.locator('.job-name', { hasText: 'Dbl' })).toHaveCount(1)
  await page.waitForTimeout(800)
  expect((await api('/jobs')).filter((j) => j.name === 'Dbl').length).toBe(1)
  await page.getByRole('button', { name: 'Run Dbl now' }).dblclick()
  await expect.poll(async () => (await api('/jobs')).find((j) => j.name === 'Dbl').last_run_id, { timeout: 30_000 }).toBeTruthy()
  await expect(page.locator('.job-name', { hasText: 'Dbl' })).toHaveCount(1)
  // the overlap guard refuses the second click with a 409
  expect(grain.consoleErrors.filter((e) => !/409/.test(e))).toEqual([])
})

test('a 100 KB prompt is refused cleanly, never a crash; 8000 chars is the edge that works', async ({ grain }) => {
  const { api } = grain
  const r = await api('/jobs', { method: 'POST', body: job('Huge', { prompt: 'x'.repeat(100_000) }), raw: true })
  expect(r.status).toBeGreaterThanOrEqual(400)
  expect(r.status).toBeLessThan(500)
  const ok = await api('/jobs', { method: 'POST', body: job('Edge', { prompt: 'x'.repeat(8000) }), raw: true })
  expect(ok.status).toBe(200)
})
