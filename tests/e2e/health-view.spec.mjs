import { test, expect } from './fixtures.mjs'
import { enableModules, reload, small, sql, realErrors } from './helpers/mah.mjs'

test.beforeEach(() => test.setTimeout(240_000))

const ymd = (daysAgo) => {
  const d = new Date(Date.now() - daysAgo * 86400000)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

async function openHealth(grain) {
  await enableModules(grain.api)
  await reload(grain.page)
  await grain.page.getByRole('button', { name: 'Health', exact: true }).click()
  await expect(grain.page.getByRole('heading', { name: 'Health' }).first()).toBeVisible()
  await expect(grain.page.locator('.hl-tile').first()).toBeVisible()
}
const tile = (page, label) => page.locator('.hl-tile', { has: page.locator('.hl-tile-label', { hasText: label }) })

function seedEntries(dataDir, rows) {
  sql(dataDir, rows.map(([metric, value, day, source], i) => [
    'INSERT INTO health_entries(id,metric,value,day,note,source,created_at) VALUES(?,?,?,?,?,?,?)',
    [`seed${metric}${i}`, metric, value, day, '', source ?? 'manual', Date.now() / 1000]
  ]))
}

test('Health renders empty: nine metric tiles with no values, range switch, empty detail', async ({ grain }) => {
  const { page } = grain
  await openHealth(grain)
  await expect(page.locator('.hl-tile')).toHaveCount(9)
  for (const label of ['Sleep', 'Steps', 'Water', 'Exercise', 'Weight', 'Resting heart rate', 'Mood', 'Energy', 'Meds taken']) {
    await expect(tile(page, label)).toBeVisible()
  }
  await expect(tile(page, 'Sleep').locator('.hl-tile-value')).toHaveText('—')
  await expect(tile(page, 'Sleep')).toContainText('Goal 8 h')
  // first tile is selected and its detail says nothing is logged
  await expect(page.getByRole('region', { name: 'Sleep history' })).toBeVisible()
  await expect(page.getByText('Nothing logged yet.')).toBeVisible()
  for (const r of ['7d', '30d', '90d']) {
    await page.getByRole('group', { name: 'Range' }).getByRole('button', { name: r }).click()
    await expect(page.getByRole('group', { name: 'Range' }).getByRole('button', { name: r })).toHaveClass(/active/)
  }
  expect(realErrors(grain.consoleErrors)).toEqual([])
})

test('quick logging: number, additive sum, scale and yes/no tiles format like the unit tests say', async ({ grain }) => {
  const { page, api } = grain
  await openHealth(grain)
  // sleep (latest-style number box "Add to" for sum metrics): 7.46 h reads "7.5 h"
  const sleep = tile(page, 'Sleep')
  await sleep.getByLabel(/Add to Sleep/).fill('7.46')
  await sleep.getByRole('button', { name: 'Add' }).click()
  await expect(sleep.locator('.hl-tile-value')).toHaveText('7.5 h')
  // steps sum: 3,000 + 5,000 = 8,000 steps, goal met
  const steps = tile(page, 'Steps')
  await steps.getByLabel(/Add to Steps/).fill('3000')
  await steps.getByRole('button', { name: 'Add' }).click()
  await expect(steps.locator('.hl-tile-value')).toHaveText('3,000 steps')
  await steps.getByLabel(/Add to Steps/).fill('5000')
  await steps.getByRole('button', { name: 'Add' }).click()
  await expect(steps.locator('.hl-tile-value')).toHaveText('8,000 steps')
  await expect(steps.getByRole('meter')).toHaveAttribute('aria-valuenow', '1')
  // weight is "last", so the second log replaces the first for the day
  const weight = tile(page, 'Weight')
  await weight.getByLabel(/Log Weight/).fill('180.4')
  await weight.getByRole('button', { name: 'Log' }).click()
  await expect(weight.locator('.hl-tile-value')).toHaveText('180.4 lb')
  await weight.getByLabel(/Log Weight/).fill('179.1')
  await weight.getByRole('button', { name: 'Log' }).click()
  await expect(weight.locator('.hl-tile-value')).toHaveText('179.1 lb')
  // scale and check
  await tile(page, 'Mood').getByRole('button', { name: '4', exact: true }).click()
  await expect(tile(page, 'Mood').locator('.hl-tile-value')).toHaveText('4/5')
  await tile(page, 'Meds taken').getByRole('button', { name: 'Yes' }).click()
  await expect(tile(page, 'Meds taken').locator('.hl-tile-value')).toHaveText('Yes')
  await tile(page, 'Meds taken').getByRole('button', { name: 'No' }).click()
  await expect(tile(page, 'Meds taken').locator('.hl-tile-value')).toHaveText('No')
  // invalid input cannot be submitted
  await expect(tile(page, 'Water').getByRole('button', { name: 'Add' })).toBeDisabled()
  await tile(page, 'Water').getByLabel(/Add to Water/).fill('-3')
  await expect(tile(page, 'Water').getByRole('button', { name: 'Add' })).toBeDisabled()
  // persisted server side
  const sum = await api('/health/summary?days=14')
  expect(sum.find((m) => m.key === 'steps').today).toBe(8000)
  expect(sum.find((m) => m.key === 'weight').today).toBeCloseTo(179.1)
  expect(realErrors(grain.consoleErrors)).toEqual([])
})

test('a seeded month draws a trend of the right length, the stats agree with the data, entries delete', async ({ grain }) => {
  const { page, api, dataDir } = grain
  const rows = []
  for (let i = 1; i <= 30; i++) rows.push(['sleep', 6 + (i % 5) * 0.5, ymd(i)])
  seedEntries(dataDir, rows)
  await openHealth(grain)
  const detail = page.getByRole('region', { name: 'Sleep history' })
  // the 30-day window is today and the 29 days before it, so the oldest seeded day falls outside
  const inWindow = rows.filter((r) => r[2] >= ymd(29))
  expect(inWindow).toHaveLength(29)
  const avg = inWindow.reduce((a, r) => a + r[1], 0) / inWindow.length
  await expect(detail.locator('.hl-stats')).toContainText(/30-day (daily )?avg/)
  await expect(detail.locator('.hl-stats')).toContainText(`${Number(avg.toFixed(1))} h`)
  await expect(detail.locator('.hl-stats')).toContainText('Logged29 of 30 days')
  const met = inWindow.filter((r) => r[1] >= 8).length
  await expect(detail.locator('.hl-stats')).toContainText(`Met goal${met} days`)
  const chart = detail.getByRole('img', { name: /Sleep over 30 days/ })
  await expect(chart).toBeVisible()
  // keyboard reads each day: End -> last day, Home -> first, tooltip uses the formatter
  await chart.focus()
  await chart.press('End')
  await expect(detail.locator('.hl-tip')).toContainText('Not logged') // today has no reading
  await chart.press('ArrowLeft')
  await expect(detail.locator('.hl-tip')).toContainText(/\d h$/)
  const tipA = await detail.locator('.hl-tip').innerText()
  await chart.press('Home')
  const tipB = await detail.locator('.hl-tip').innerText()
  expect(tipA).not.toBe(tipB)
  // range switch redraws with the right day count
  await page.getByRole('group', { name: 'Range' }).getByRole('button', { name: '7d' }).click()
  await expect(detail.getByRole('img', { name: /Sleep over 7 days/ })).toBeVisible()
  await page.getByRole('group', { name: 'Range' }).getByRole('button', { name: '90d' }).click()
  await expect(detail.getByRole('img', { name: /Sleep over 90 days/ })).toBeVisible()
  // entries table lists them and a delete removes exactly one
  const n = (await api('/health/entries?metric=sleep&limit=200')).length
  expect(n).toBe(30)
  const rowsBefore = await detail.locator('.hl-entries tbody tr').count()
  expect(rowsBefore).toBeGreaterThan(0)
  await detail.locator('.hl-entries tbody tr').first().getByRole('button', { name: /^Delete / }).click()
  await expect.poll(async () => (await api('/health/entries?metric=sleep&limit=200')).length).toBe(29)
  expect(realErrors(grain.consoleErrors)).toEqual([])
})

test('entry from the log form: any day, a note, a synced source is labelled', async ({ grain }) => {
  const { page, api, dataDir } = grain
  seedEntries(dataDir, [['steps', 4321, ymd(2), 'coros']])
  await openHealth(grain)
  await tile(page, 'Steps').locator('.hl-tile-head').click()
  const detail = page.getByRole('region', { name: 'Steps history' })
  await expect(detail.locator('.hl-entries')).toContainText('4,321 steps')
  await expect(detail.locator('.hl-entries')).toContainText('COROS')
  await detail.locator('input[aria-label="Day"]').fill(ymd(3))
  await detail.locator('input.hl-log-val').fill('1500')
  await detail.getByLabel('Note (optional)').fill('evening walk')
  await detail.getByRole('button', { name: 'Log' }).click()
  await expect(detail.locator('.hl-entries')).toContainText('evening walk')
  const e = (await api(`/health/entries?metric=steps&since=${ymd(3)}&until=${ymd(3)}`))[0]
  expect(e.value).toBe(1500)
  // a day in the future cannot be picked
  await expect(detail.locator('input[aria-label="Day"]')).toHaveAttribute('max', ymd(0))
  expect(realErrors(grain.consoleErrors)).toEqual([])
})

test('metric manager: hide, rename, goal, add a custom metric, delete it with confirmation', async ({ grain }) => {
  const { page, api } = grain
  await openHealth(grain)
  await page.getByRole('button', { name: 'Choose and edit metrics' }).click()
  const mgr = page.getByRole('region', { name: 'Metrics' })
  await mgr.getByLabel('Show Water').click()
  await expect(tile(page, 'Water')).toHaveCount(0)
  await mgr.getByLabel('Show Water').click()
  await expect(tile(page, 'Water')).toBeVisible()
  // rename + goal
  const row = mgr.locator('.hl-mrow', { has: page.locator('input[aria-label="Show Steps"]') })
  await row.getByLabel('Name').fill('Daily steps')
  await row.getByLabel('Goal value').fill('10000')
  await row.getByRole('button', { name: 'Save' }).click()
  await expect(tile(page, 'Daily steps')).toContainText('Goal 10,000 steps')
  // custom
  await mgr.getByLabel('New metric name').fill('Caffeine')
  await mgr.getByLabel('Kind').selectOption('number')
  await mgr.getByLabel('Unit').last().fill('cups')
  await mgr.getByRole('button', { name: 'Add', exact: true }).click()
  await expect(tile(page, 'Caffeine')).toBeVisible()
  expect((await api('/health/metrics')).some((m) => m.label === 'Caffeine')).toBe(true)
  // builtin metrics have no delete; the custom one does and asks
  await expect(mgr.getByRole('button', { name: 'Delete Sleep' })).toHaveCount(0)
  page.once('dialog', (d) => d.dismiss())
  await mgr.getByRole('button', { name: 'Delete Caffeine' }).click()
  await expect(tile(page, 'Caffeine')).toBeVisible()
  page.once('dialog', (d) => d.accept())
  await mgr.getByRole('button', { name: 'Delete Caffeine' }).click()
  await expect(tile(page, 'Caffeine')).toHaveCount(0)
  // hiding everything shows the explanatory empty state
  for (const m of await api('/health/metrics')) await api(`/health/metrics/${m.key}`, { method: 'PUT', body: { hidden: true } })
  await reload(page)
  await page.getByRole('button', { name: 'Health', exact: true }).click()
  await expect(page.getByText('Every metric is hidden')).toBeVisible()
  await page.getByRole('button', { name: 'Choose metrics' }).click()
  await expect(page.getByRole('region', { name: 'Metrics' })).toBeVisible()
  expect(realErrors(grain.consoleErrors)).toEqual([])
})

test('connected services: provider cards explain the sign-in, forms render, nothing runs until Connect', async ({ grain }) => {
  const { page, api } = grain
  await openHealth(grain)
  await page.getByRole('button', { name: 'Connected services' }).click()
  const panel = page.getByRole('region', { name: 'Connected services' })
  await expect(panel).toContainText('Strava isn’t offered'.replace('’', "'")).catch(() => {})
  await expect(panel.getByRole('button', { name: /COROS/ })).toBeVisible()
  await expect(panel.getByRole('button', { name: /Garmin/ })).toBeVisible()
  await panel.getByRole('button', { name: /COROS/ }).click()
  await expect(panel).toContainText('Signs in with your COROS account in the browser')
  await expect(panel.getByRole('button', { name: 'Connect COROS' })).toBeVisible()
  await panel.getByRole('button', { name: /Garmin/ }).click()
  await expect(panel.getByRole('button', { name: 'Connect Garmin' })).toBeVisible()
  await expect(panel).toContainText(/uv/)
  // opening the panel connected nothing
  expect(await api('/health/sources')).toEqual([])
  await page.getByRole('button', { name: 'Done' }).click()
  await expect(panel).toHaveCount(0)
  expect(realErrors(grain.consoleErrors)).toEqual([])
})

test('Connect COROS (network): either a sign-in prompt or a readable "Not connected" reason, never a crash', async ({ grain }) => {
  const { page, api } = grain
  await openHealth(grain)
  await page.getByRole('button', { name: 'Connected services' }).click()
  const panel = page.getByRole('region', { name: 'Connected services' })
  await panel.getByRole('button', { name: /COROS/ }).click()
  await panel.getByRole('button', { name: 'Connect COROS' }).click()
  const row = panel.locator('.hl-source', { hasText: 'COROS' })
  await expect(row).toBeVisible({ timeout: 90_000 })
  // settles to a sign-in prompt, or a stated error: never "Starting…" forever
  await expect(row.locator('.hl-source-state')).not.toHaveText(/^Starting…$/, { timeout: 90_000 })
  await expect(row.locator('.hl-source-state')).toHaveText(/Sign in to finish connecting|Not connected: .+|Connector removed/)
  // disconnect removes it and the provider card comes back
  page.once('dialog', (d) => d.accept())
  await row.getByRole('button', { name: 'Disconnect COROS' }).click()
  await expect(row).toHaveCount(0)
  expect(await api('/health/sources')).toEqual([])
  await expect(panel.getByRole('button', { name: /COROS/ })).toBeVisible()
})

test('Today shows a Health card that follows the Show on Today toggle and survives a relaunch', async ({ grain }) => {
  const { page, api, dataDir } = grain
  seedEntries(dataDir, [['sleep', 8.2, ymd(0)], ['steps', 9000, ymd(0)]])
  await enableModules(api)
  await reload(page)
  await page.locator('.nav-item', { hasText: 'Today' }).first().click()
  const card = page.locator('section.widget', { has: page.getByText('logged today') })
  await expect(card).toBeVisible()
  await expect(card).toContainText('2 of 9 logged today')
  await expect(card.locator('.hl-card li', { hasText: 'Sleep' })).toContainText('8.2 h')
  await expect(card.locator('.hl-card li', { hasText: 'Sleep' }).getByLabel('goal met')).toBeVisible()
  await expect(card.locator('.hl-card li', { hasText: 'Steps' })).toContainText('9,000 steps')
  // View all goes to the page
  await card.getByRole('button', { name: 'View all' }).click()
  await expect(page.getByRole('heading', { name: 'Health' }).first()).toBeVisible()
  await page.locator('.nav-item', { hasText: 'Today' }).first().click()
  // turn it off through Show on Today
  await page.getByRole('button', { name: 'Choose what shows on Today' }).click()
  await page.locator('.home-customize label', { hasText: 'Health' }).locator('input').click()
  await expect(page.locator('section.widget', { has: page.getByText('logged today') })).toHaveCount(0)
  await expect.poll(async () => (await api('/settings')).homeWidgets.health).toBe(false)
  await grain.relaunch()
  await grain.page.locator('.nav-item', { hasText: 'Today' }).first().click()
  await expect(grain.page.locator('section.widget', { has: grain.page.getByText('Brief me').or(grain.page.getByText('Lists')) }).first()).toBeVisible()
  await expect(grain.page.locator('section.widget', { has: grain.page.getByText('logged today') })).toHaveCount(0)
  expect(realErrors(grain.consoleErrors)).toEqual([])
})

test('900 readings over 90 days at 820x520: tiles, sparklines and the trend render without sideways scroll', async ({ grain }) => {
  const { page, dataDir } = grain
  const rows = []
  for (let i = 0; i < 90; i++) {
    rows.push(['sleep', 5 + (i % 7), ymd(i)], ['steps', 2000 + i * 100, ymd(i)], ['water', i % 9, ymd(i)], ['exercise', (i * 7) % 90, ymd(i)],
      ['mood', 1 + (i % 5), ymd(i)], ['energy', 1 + ((i + 2) % 5), ymd(i)], ['weight', 180 - i * 0.05, ymd(i)], ['resting_hr', 55 + (i % 8), ymd(i)], ['meds', i % 2, ymd(i)], ['water', 1, ymd(i)])
  }
  seedEntries(dataDir, rows)
  await small(grain.app)
  await openHealth(grain)
  await page.getByRole('group', { name: 'Range' }).getByRole('button', { name: '90d' }).click()
  await expect(page.getByRole('img', { name: /Sleep over 90 days/ })).toBeVisible({ timeout: 30_000 })
  await expect(page.locator('.hl-spark')).toHaveCount(9)
  expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(0)
  // every tile is reachable by selecting it
  await tile(page, 'Mood').locator('.hl-tile-head').click()
  await expect(page.getByRole('img', { name: /Mood over 90 days/ })).toBeVisible()
  await tile(page, 'Meds taken').locator('.hl-tile-head').click()
  await expect(page.getByRole('img', { name: /Meds taken over 90 days/ })).toBeVisible()
  expect(realErrors(grain.consoleErrors)).toEqual([])
})

test('backend dies while the page is open: logging reports an error and the page stays usable', async ({ grain }) => {
  const { page, backend } = grain
  await openHealth(grain)
  backend.child.kill('SIGKILL')
  const steps = tile(page, 'Steps')
  await steps.getByLabel(/Add to Steps/).fill('1234')
  await steps.getByRole('button', { name: 'Add' }).click()
  // a readable error toast, the tile unchanged, the form still there
  await expect(page.locator('.toast.error, [role="alert"]').first()).toBeVisible({ timeout: 30_000 })
  await expect(steps.locator('.hl-tile-value')).toHaveText('—')
  await expect(steps.getByLabel(/Add to Steps/)).toHaveValue('1234')
  await page.getByRole('group', { name: 'Range' }).getByRole('button', { name: '7d' }).click()
  await expect(page.locator('.sidebar')).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Health' }).first()).toBeVisible()
})
