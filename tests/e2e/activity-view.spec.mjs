import { readFileSync, existsSync } from 'node:fs'
import { join } from 'node:path'
import { test, expect } from './helpers/fakemic.mjs'
import { enableModules, reload, small, sql, realErrors } from './helpers/mah.mjs'

test.beforeEach(() => test.setTimeout(240_000))

async function openActivity(grain, tab) {
  await enableModules(grain.api)
  await reload(grain.page)
  await grain.page.locator('.nav-item', { hasText: 'Activity' }).first().click()
  await expect(grain.page.getByRole('heading', { name: 'Activity' }).first()).toBeVisible()
  if (tab) await grain.page.locator('.tabs button', { hasText: tab }).click()
}

const permLog = (g) => {
  const f = join(g.dataDir, 'perm-calls.log')
  return existsSync(f) ? readFileSync(f, 'utf8').trim().split('\n').filter(Boolean) : []
}
const now = () => Date.now() / 1000

test('Activity ships shown and on; the first view carries the access checklist and records only app focus', async ({ grain }) => {
  const { page, api } = grain
  // Shown by default now (hiddenViews ships empty); showing the view records nothing extra.
  await expect(page.locator('.nav-item', { hasText: 'Activity' })).toHaveCount(1)
  await openActivity(grain)
  await expect(page.locator('.act-state')).not.toHaveText('Off')
  await expect(page.getByRole('button', { name: 'Turn off' })).toBeVisible()
  await expect(page.getByText('Access on this machine')).toBeVisible()
  // the checklist states what is missing and why, never silently
  await expect(page.locator('.act-caps li').first()).toBeVisible()
  await expect(page.locator('.act-caps li.bad, .act-caps li.warn').first()).toContainText(/\w/)
  expect((await api('/activity/status')).running).toBe(true)
  // Off is one click and sticks: nothing more is recorded after it.
  await page.getByRole('button', { name: 'Turn off' }).click()
  await expect.poll(async () => (await api('/activity/status')).running, { timeout: 30_000 }).toBe(false)
  expect(realErrors(grain.consoleErrors)).toEqual([])
})

test('permission Grant buttons ask once per row, never crash, and Open System Settings is a plain call', async ({ grain }) => {
  const { page, api } = grain
  await openActivity(grain)
  const before = await page.locator('.act-state').textContent() // granting a permission never flips the switch
  const status = await api('/activity/status')
  const requestable = status.capabilities.filter((c) => c.requestable && c.state !== 'granted' && c.id !== 'automation')
  expect(requestable.length).toBeGreaterThan(0)
  const first = requestable[0]
  const row = page.locator('.act-caps li', { hasText: first.label }).first()
  await row.getByRole('button', { name: /Grant/ }).click()
  await expect(page.getByText(`stubbed request for ${first.id}`).or(page.getByText(/switch it on in the pane/)).first()).toBeVisible({ timeout: 30_000 })
  await expect.poll(() => permLog(grain)).toContain(`request ${first.id}`)
  if (first.settings_url) {
    await row.getByRole('button', { name: 'Open System Settings' }).click()
    await expect.poll(() => permLog(grain)).toContain(`open ${first.id}`)
  }
  // "ask for everything missing" fans out one request per missing row, and the page survives it
  if (requestable.length > 1) {
    await page.getByRole('button', { name: 'Ask for everything missing' }).click()
    await expect.poll(() => permLog(grain).filter((l) => l.startsWith('request ')).length, { timeout: 30_000 }).toBeGreaterThanOrEqual(requestable.length)
  }
  await expect(page.locator('.act-state')).toHaveText(before)
  expect(realErrors(grain.consoleErrors)).toEqual([])
})

test('record everything: typed confirmation, a visible warning on every tab, and turning it off restores the lists', async ({ grain }) => {
  const { page, api } = grain
  await openActivity(grain)
  const before = (await api('/activity/status')).config
  expect(before.excludeApps.length).toBeGreaterThan(0)
  await page.getByRole('button', { name: 'Turn record everything on' }).click()
  await expect(page.getByText('Record everything, with the filters down?')).toBeVisible()
  // Cancel leaves everything as it was
  await page.getByRole('button', { name: 'Cancel' }).click()
  expect((await api('/activity/status')).recordEverything).toBe(false)
  await page.getByRole('button', { name: 'Turn record everything on' }).click()
  await page.getByRole('button', { name: 'Yes, record everything' }).click()
  await expect(page.locator('.act-hero .act-pill.warn', { hasText: 'Recording everything' })).toBeVisible({ timeout: 30_000 })
  const on = await api('/activity/status')
  expect(on.recordEverything).toBe(true)
  expect(on.config.excludeApps).toEqual([])
  expect(on.config.redact).toBe(false)
  // the privacy tab says the filters are down
  await page.locator('.tabs button', { hasText: 'Privacy' }).click()
  await expect(page.getByText(/Record everything is on: redaction is off/)).toBeVisible()
  await page.locator('.tabs button', { hasText: 'Overview' }).click()
  await page.getByRole('button', { name: 'Turn record everything off' }).click()
  await expect(page.locator('.act-hero .act-pill.warn', { hasText: 'Recording everything' })).toHaveCount(0, { timeout: 30_000 })
  const off = (await api('/activity/status')).config
  expect(off.excludeApps).toEqual(before.excludeApps)
  expect(off.redact).toBe(before.redact)
  expect(off.signals).toEqual(before.signals)
  expect(realErrors(grain.consoleErrors)).toEqual([])
})

test('signals and retention persist across a relaunch; out-of-range numbers are refused', async ({ grain }) => {
  const { page, api } = grain
  await openActivity(grain, 'Signals')
  // toggle a signal
  await page.locator('.act-signal', { hasText: 'Apps and windows' }).locator('.switch').click()
  await expect.poll(async () => (await api('/activity/status')).config.signals.apps).toBe(false)
  // a number inside its range is saved, outside it snaps back
  const interval = page.locator('label.act-field', { hasText: 'Sample interval' }).locator('input')
  await interval.fill('999')
  await interval.press('Enter')
  await expect(interval).toHaveValue('5')
  await interval.fill('12')
  await interval.press('Enter')
  await expect.poll(async () => (await api('/activity/status')).config.sampleSeconds).toBe(12)
  await page.locator('.tabs button', { hasText: 'Privacy' }).click()
  const keep = page.locator('label.act-field', { hasText: 'Keep raw samples' }).locator('input')
  await keep.fill('0')
  await keep.press('Enter')
  await expect(keep).toHaveValue('48')
  await keep.fill('96')
  await keep.press('Enter')
  await expect.poll(async () => (await api('/activity/status')).config.retentionHours).toBe(96)

  await grain.relaunch()
  await grain.page.locator('.nav-item', { hasText: 'Activity' }).first().click()
  await grain.page.locator('.tabs button', { hasText: 'Privacy' }).click()
  await expect(grain.page.locator('label.act-field', { hasText: 'Keep raw samples' }).locator('input')).toHaveValue('96')
  await grain.page.locator('.tabs button', { hasText: 'Signals' }).click()
  await expect(grain.page.locator('label.act-field', { hasText: 'Sample interval' }).locator('input')).toHaveValue('12')
  const cfg = (await api('/activity/status')).config
  expect(cfg.signals.apps).toBe(false)
})

test('exclusions editor: add, dedupe case-insensitively, Enter adds, remove, and conditional rules', async ({ grain }) => {
  const { page, api } = grain
  await openActivity(grain, 'Privacy')
  const apps = page.locator('.act-list-editor', { hasText: 'Excluded apps' })
  const box = apps.getByPlaceholder('App name, e.g. Signal')
  await box.fill('Signal')
  await box.press('Enter')
  await expect.poll(async () => (await api('/activity/status')).config.excludeApps).toContain('Signal')
  await box.fill('signal')
  await apps.getByRole('button', { name: 'Add', exact: true }).click()
  const list = (await api('/activity/status')).config.excludeApps
  expect(list.filter((a) => a.toLowerCase() === 'signal')).toHaveLength(1)
  await apps.getByTitle('Stop excluding Signal').click()
  await expect.poll(async () => (await api('/activity/status')).config.excludeApps).not.toContain('Signal')
  // blank input cannot be added
  await expect(apps.getByRole('button', { name: 'Add', exact: true })).toBeDisabled()
  // a conditional rule
  const rules = page.locator('.act-list-editor', { hasText: 'Conditional exclusions' })
  await rules.getByPlaceholder('app').fill('Safari')
  await rules.getByPlaceholder('title').fill('/bank|login/')
  await rules.getByRole('button', { name: 'Add', exact: true }).click()
  await expect.poll(async () => (await api('/activity/status')).config.excludeRules).toEqual([{ app: 'Safari', title: '/bank|login/' }])
  await rules.getByTitle('Remove rule').click()
  await expect.poll(async () => (await api('/activity/status')).config.excludeRules).toEqual([])
  // the default title patterns are present and removable one at a time
  const titles = page.locator('.act-list-editor', { hasText: 'Excluded window titles and URLs' })
  await titles.getByTitle('Stop excluding password').click()
  await expect.poll(async () => (await api('/activity/status')).config.excludeTitlePatterns).not.toContain('password')
  expect(realErrors(grain.consoleErrors)).toEqual([])
})

test('redaction test panel shows what would be stored and never stores it', async ({ grain }) => {
  const { page, api } = grain
  await openActivity(grain, 'Privacy')
  await page.getByPlaceholder('Paste a string to see what would be stored').fill('mail me at jane.doe@example.com password is hunter2')
  await expect(page.locator('.act-test-out')).toBeVisible({ timeout: 30_000 })
  const out = await page.locator('.act-test-out').innerText()
  expect(out).not.toContain('jane.doe@example.com')
  expect(out).not.toContain('hunter2')
  expect((await api('/activity/events')).length).toBe(0)
})

test('summaries and raw log: seeded rows render, single rows delete, purge asks first', async ({ grain }) => {
  const { page, api, dataDir } = grain
  const t = now()
  const day = new Date().toISOString().slice(0, 10)
  const stmts = []
  for (let i = 0; i < 300; i++) {
    stmts.push(["INSERT INTO activity_events(id,ts,kind,app,bundle,title,url,text,meta,duration_ms,rolled_up,expires_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
      [`ev${i}`, t - i * 30, 'focus', i % 2 ? 'Code' : 'Slack', '', `Window ${i}`, '', '', '{}', 30000, 0, t + 86400]])
  }
  for (let i = 0; i < 3; i++) {
    stmts.push(["INSERT INTO activity_summaries(id,day,period_start,period_end,headline,body,apps,event_count,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
      [`sum${i}`, day, t - 3600 * (i + 1), t - 3600 * i, `Headline ${i}`, `Worked on **thing ${i}**`, '["Code","Slack"]', 12, t]])
  }
  sql(dataDir, stmts)
  await small(grain.app)
  await openActivity(grain)
  await expect(page.locator('.act-period')).toHaveCount(3)
  await expect(page.locator('.act-period').first()).toContainText('Headline')
  await page.locator('.act-period').first().getByTitle('Delete this summary').click()
  await expect(page.locator('.act-period')).toHaveCount(2)
  expect((await api('/activity/summaries')).length).toBe(2)

  await page.locator('.tabs button', { hasText: 'Signals' }).click()
  await expect(page.locator('.act-log tbody tr').first()).toBeVisible()
  expect(await page.locator('.act-log tbody tr').count()).toBeGreaterThanOrEqual(200)
  await page.locator('.act-log tbody tr').first().getByTitle('Delete this row').click()
  await expect.poll(async () => (await api('/activity/events?limit=2000')).length).toBe(299)

  await page.locator('.tabs button', { hasText: 'Privacy' }).click()
  await page.getByRole('button', { name: 'Delete raw samples' }).click()
  await expect(page.getByRole('heading', { name: /Delete all raw samples/ })).toBeVisible()
  await page.getByRole('button', { name: 'Cancel' }).click()
  expect((await api('/activity/events?limit=2000')).length).toBe(299)
  await page.getByRole('button', { name: 'Delete raw samples' }).click()
  await page.getByRole('button', { name: 'Delete', exact: true }).click()
  await expect.poll(async () => (await api('/activity/events?limit=2000')).length).toBe(0)
  // summaries stay when only raw samples are purged
  expect((await api('/activity/summaries')).length).toBe(2)
  expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(0)
  expect(realErrors(grain.consoleErrors)).toEqual([])
})
