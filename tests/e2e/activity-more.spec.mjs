import { test, expect } from './helpers/fakemic.mjs'
import { enableModules, reload, sql, realErrors } from './helpers/mah.mjs'

test.beforeEach(() => test.setTimeout(240_000))

const now = () => Date.now() / 1000
const day = () => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}` }

async function openActivity(grain, tab) {
  await enableModules(grain.api)
  // The monitor ships on; these flows start from off so "Turn on" is the first step.
  await grain.api('/activity/stop', { method: 'POST' })
  await reload(grain.page)
  await grain.page.locator('.nav-item', { hasText: 'Activity' }).first().click()
  await expect(grain.page.getByRole('heading', { name: 'Activity' }).first()).toBeVisible()
  if (tab) await grain.page.locator('.tabs button', { hasText: tab }).click()
}

test('Turn on / Pause / Resume / Turn off: the state, the sidebar indicator and the backend agree', async ({ grain }) => {
  const { page, api } = grain
  await openActivity(grain)
  await page.getByRole('button', { name: 'Turn on' }).click()
  await expect.poll(async () => (await api('/activity/status')).running, { timeout: 30_000 }).toBe(true)
  await expect(page.locator('.act-hero .act-state')).toHaveText('Recording', { timeout: 30_000 })
  await expect(page.locator('.act-indicator.live')).toBeVisible()
  await page.getByRole('button', { name: 'Pause 30m' }).click()
  await expect(page.locator('.act-hero .act-state')).toHaveText('Paused', { timeout: 30_000 })
  await expect(page.locator('.act-indicator.paused')).toBeVisible()
  expect((await api('/activity/status')).paused).toBe(true)
  await page.getByRole('button', { name: 'Resume' }).click()
  await expect(page.locator('.act-hero .act-state')).toHaveText('Recording', { timeout: 30_000 })
  // double-click Turn off is harmless
  await page.getByRole('button', { name: 'Turn off' }).dblclick()
  await expect(page.locator('.act-hero .act-state')).toHaveText('Off', { timeout: 30_000 })
  await expect(page.locator('.act-indicator')).toHaveCount(0)
  expect((await api('/activity/status')).running).toBe(false)
  expect(realErrors(grain.consoleErrors)).toEqual([])
})

test('the monitor stays on across a relaunch and the indicator comes back', async ({ grain }) => {
  const { api } = grain
  await openActivity(grain)
  await grain.page.getByRole('button', { name: 'Turn on' }).click()
  await expect.poll(async () => (await api('/activity/status')).running, { timeout: 30_000 }).toBe(true)
  await grain.relaunch()
  await expect(grain.page.locator('.act-indicator')).toBeVisible({ timeout: 30_000 })
  await grain.page.locator('.nav-item', { hasText: 'Activity' }).first().click()
  await expect(grain.page.locator('.act-hero .act-state')).toHaveText('Recording', { timeout: 30_000 })
  await grain.page.getByRole('button', { name: 'Turn off' }).click()
  await expect(grain.page.locator('.act-hero .act-state')).toHaveText('Off', { timeout: 30_000 })
})

test('category rules: add, save, an invalid regex is refused with a reason, reset to default', async ({ grain }) => {
  const { page, api, dataDir } = grain
  // some time to categorize, so the bar and the uncategorized list have something to show
  sql(dataDir, [['INSERT OR REPLACE INTO activity_day_stats(day,apps,hosts,hours,typing,cats,switches,keys,clicks,scrolls,focus_seconds,idle_seconds,first_ts,last_ts,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
    [day(), JSON.stringify({ Code: 7200, ZorbApp: 1800 }), '{}', '{"9": 3000}', '{}', JSON.stringify({ Work: 7200, 'Work/Coding': 7200 }), 10, 100, 10, 10, 9000, 0, now() - 9000, now(), now()]]])
  await openActivity(grain, 'Insights')
  await expect(page.locator('.act-catbar')).toBeVisible({ timeout: 30_000 })
  await page.getByRole('button', { name: /Edit category rules \(defaults\)/ }).click()
  const before = await api('/activity/categories')
  expect(before.default).toBe(true)
  await page.getByRole('button', { name: 'Add rule' }).click()
  await page.getByLabel('Category path').last().fill('Fun > Games')
  await page.getByLabel('Regex').last().fill('Steam|Game')
  await page.getByRole('button', { name: 'Save rules' }).click()
  await expect.poll(async () => (await api('/activity/categories')).default, { timeout: 30_000 }).toBe(false)
  expect((await api('/activity/categories')).rules.some((r) => r.name.join('/') === 'Fun/Games')).toBe(true)
  // a regex that does not compile is refused, and the previous rules stay
  await page.getByRole('button', { name: 'Add rule' }).click()
  await page.getByLabel('Category path').last().fill('Broken')
  await page.getByLabel('Regex').last().fill('(unclosed')
  await page.getByRole('button', { name: 'Save rules' }).click()
  await expect(page.locator('.act-rules .act-warn')).toBeVisible({ timeout: 30_000 })
  expect((await api('/activity/categories')).rules.some((r) => r.name.includes('Broken'))).toBe(false)
  // relaunch keeps the custom rules
  await grain.relaunch()
  expect((await api('/activity/categories')).rules.some((r) => r.name.join('/') === 'Fun/Games')).toBe(true)
  await grain.page.locator('.nav-item', { hasText: 'Activity' }).first().click()
  await grain.page.locator('.tabs button', { hasText: 'Insights' }).click()
  await grain.page.getByRole('button', { name: /Edit category rules/ }).click()
  await grain.page.getByRole('button', { name: 'Reset to default' }).click()
  await expect.poll(async () => (await api('/activity/categories')).default, { timeout: 30_000 }).toBe(true)
  expect(realErrors(grain.consoleErrors)).toEqual([])
})

test('Summarize now with nothing recorded answers without an error', async ({ grain }) => {
  const { page } = grain
  await openActivity(grain)
  await page.getByRole('button', { name: 'Summarize now' }).click()
  await expect(page.getByRole('button', { name: 'Summarize now' })).toBeEnabled({ timeout: 60_000 })
  await expect(page.getByText('No summaries yet')).toBeVisible()
  expect(realErrors(grain.consoleErrors)).toEqual([])
})

test('backend dies while Activity is open: settings changes report an error and the page survives', async ({ grain }) => {
  const { page, backend } = grain
  await openActivity(grain, 'Privacy')
  backend.child.kill('SIGKILL')
  const apps = page.locator('.act-list-editor', { hasText: 'Excluded apps' })
  await apps.getByPlaceholder('App name, e.g. Signal').fill('Signal')
  await apps.getByPlaceholder('App name, e.g. Signal').press('Enter')
  await expect(page.locator('.toast.error, [role="alert"]').first()).toBeVisible({ timeout: 30_000 })
  await page.locator('.tabs button', { hasText: 'Overview' }).click()
  await expect(page.getByRole('heading', { name: 'Activity' }).first()).toBeVisible()
  await expect(page.locator('.sidebar')).toBeVisible()
})
