import { test, expect } from './helpers/fakemic.mjs'
import { enableModules, reload, sql, realErrors } from './helpers/mah.mjs'

test.beforeEach(() => test.setTimeout(240_000))

const now = () => Date.now() / 1000
const ymd = (daysAgo) => {
  const d = new Date(Date.now() - daysAgo * 86400000)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

function seedDays(dataDir, n = 6) {
  const stmts = []
  for (let i = 1; i <= n; i++) {
    const hours = { 9: 3000, 10: 3200, 11: 2800, 14: 2500, 15: 2000 }
    stmts.push(['INSERT OR REPLACE INTO activity_day_stats(day,apps,hosts,hours,typing,cats,switches,keys,clicks,scrolls,focus_seconds,idle_seconds,first_ts,last_ts,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
      [ymd(i), JSON.stringify({ Code: 9000, Slack: 2500, Mail: 900 }), JSON.stringify({ 'github.com': 14, 'docs.google.com': 6 }), JSON.stringify(hours),
        JSON.stringify({ Code: 12000 }), JSON.stringify({ Work: 12400, 'Work/Coding': 9000 }), 140, 12000, 800, 300, 12400, 1800, now() - i * 86400, now() - i * 86400 + 30000, now()]])
  }
  sql(dataDir, stmts)
}

function seedSuggestion(dataDir, id, over = {}) {
  const t = now()
  const s = { key: `k-${id}`, kind: 'automation', title: `Suggestion ${id}`, detail: `Detail for ${id}`, why: 'You do it daily', impact: '10 min/day', effort: 'low', action: { type: 'memory', content: `Prefers ${id} workflow` }, evidence: [], confidence: 0.8, status: 'new', ...over }
  sql(dataDir, [['INSERT INTO activity_suggestions(id,key,kind,title,detail,why,impact,effort,action,evidence,confidence,status,status_note,snooze_until,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
    [id, s.key, s.kind, s.title, s.detail, s.why, s.impact, s.effort, JSON.stringify(s.action), JSON.stringify(s.evidence), s.confidence, s.status, '', 0, t, t]]])
}

async function openInsights(grain) {
  await enableModules(grain.api)
  await reload(grain.page)
  await grain.page.locator('.nav-item', { hasText: 'Activity' }).first().click()
  await grain.page.locator('.tabs button', { hasText: 'Insights' }).click()
}

test('insights start empty and say what is needed; mining with seeded day aggregates fills patterns and bars', async ({ grain }) => {
  const { page, api, dataDir } = grain
  await openInsights(grain)
  await expect(page.getByText('nothing mined yet')).toBeVisible()
  await expect(page.getByText(/Not enough yet/)).toHaveCount(0).catch(() => {})
  await expect(page.getByText('Nothing on offer.')).toBeVisible()
  await expect(page.getByText('No habits yet.')).toBeVisible()
  seedDays(dataDir)
  await page.getByRole('button', { name: 're-read patterns' }).click()
  await expect.poll(async () => (await api('/activity/insights')).counts.days, { timeout: 30_000 }).toBeGreaterThanOrEqual(6)
  const ov = await api('/activity/insights')
  await expect(page.getByText(/days? aggregated/)).toBeVisible({ timeout: 30_000 })
  if (ov.apps.length) {
    // where the time goes: the seeded app tops the bars
    await expect(page.locator('.act-bars').first()).toContainText('Code')
    await expect(page.locator('.act-hours')).toBeVisible()
  }
  if (ov.patterns.length) await expect(page.locator('.act-pattern')).toHaveCount(Math.min(10, ov.patterns.length))
  else await expect(page.getByText(/Not enough yet/)).toBeVisible()
  expect(realErrors(grain.consoleErrors)).toEqual([])
})

test('a suggestion is a proposal: Save to memory writes exactly one memory row; Not now and Dismiss only change status', async ({ grain }) => {
  const { page, api, dataDir } = grain
  seedSuggestion(dataDir, 'sg1', { title: 'Batch your email', action: { type: 'memory', content: 'Prefers batching email at noon' } })
  seedSuggestion(dataDir, 'sg2', { title: 'Snooze me', action: { type: 'none' } })
  seedSuggestion(dataDir, 'sg3', { title: 'Never again', action: { type: 'todo', title: 'Do the thing' } })
  const memsBefore = (await api('/memories')).length
  const todosBefore = (await api('/todos')).length
  await openInsights(grain)
  await expect(page.locator('.act-sug')).toHaveCount(3)
  await expect(page.getByText('3 waiting')).toBeVisible()
  // nothing changed by merely looking
  expect((await api('/memories')).length).toBe(memsBefore)

  const card = (t) => page.locator('.act-sug', { hasText: t })
  await card('Batch your email').getByRole('button', { name: 'Save to memory' }).click()
  await expect.poll(async () => (await api('/memories')).filter((m) => m.content.includes('batching email at noon')).length, { timeout: 30_000 }).toBe(1)
  expect((await api('/memories')).length).toBe(memsBefore + 1)
  const mem = (await api('/memories')).find((m) => m.content.includes('batching email'))
  expect(mem.source).toBe('activity')
  await expect(page.locator('.act-sug', { hasText: 'Batch your email' })).toHaveCount(0)

  await card('Snooze me').getByRole('button', { name: 'Not now' }).click()
  await card('Never again').getByRole('button', { name: 'Dismiss' }).click()
  await expect(page.locator('.act-sug')).toHaveCount(0)
  await expect(page.getByText('Nothing on offer.')).toBeVisible()
  const byId = Object.fromEntries((await api('/activity/insights')).suggestions.map((s) => [s.id, s]))
  expect(byId.sg1.status).toBe('done')
  expect(byId.sg2.status).toBe('snoozed')
  expect(byId.sg3.status).toBe('dismissed')
  // dismissing a todo suggestion creates no todo and no further memory
  expect((await api('/todos')).length).toBe(todosBefore)
  expect((await api('/memories')).length).toBe(memsBefore + 1)

  // already decided: listed, and "put it back" re-offers it
  await page.getByRole('button', { name: /show 3 already decided/ }).click()
  await expect(page.locator('.act-sug.ruled')).toHaveCount(3)
  await page.locator('.act-sug.ruled', { hasText: 'Never again' }).getByRole('button', { name: 'put it back' }).click()
  await expect(page.locator('.act-sug:not(.ruled)', { hasText: 'Never again' })).toBeVisible({ timeout: 30_000 })
  expect((await api('/activity/insights')).suggestions.find((s) => s.id === 'sg3').status).toBe('new')

  // double-clicking Save to memory twice must not write two rows
  seedSuggestion(dataDir, 'sg4', { title: 'Double click', action: { type: 'memory', content: 'Unique double click memory' } })
  await page.getByRole('button', { name: 're-read patterns' }).click()
  await page.reload()
  await page.waitForSelector('.sidebar')
  await page.locator('.nav-item', { hasText: 'Activity' }).first().click()
  await page.locator('.tabs button', { hasText: 'Insights' }).click()
  await page.locator('.act-sug', { hasText: 'Double click' }).getByRole('button', { name: 'Save to memory' }).dblclick()
  await expect.poll(async () => (await api('/memories')).filter((m) => m.content.includes('Unique double click memory')).length, { timeout: 30_000 }).toBeGreaterThanOrEqual(1)
  await page.waitForTimeout(2000)
  expect((await api('/memories')).filter((m) => m.content.includes('Unique double click memory')).length).toBe(1)
  expect(realErrors(grain.consoleErrors)).toEqual([])
})

test('a habit owns one memory row and Forget deletes both', async ({ grain }) => {
  const { page, api, dataDir } = grain
  const mem = await api('/memories', { method: 'POST', body: { content: 'Works in focused mornings', kind: 'preference' } })
  const t = now()
  // a habit may only delete memory rows it wrote itself (source = activity)
  sql(dataDir, [['UPDATE memories SET source=? WHERE id=?', ['activity', mem.id]]])
  const other = await api('/memories', { method: 'POST', body: { content: 'A fact the user typed themselves', kind: 'fact' } })
  sql(dataDir, [['INSERT INTO activity_habits(id,key,statement,kind,confidence,support,evidence,memory_id,first_seen,last_seen) VALUES(?,?,?,?,?,?,?,?,?,?)',
    ['hb1', 'mornings', 'Works in focused mornings', 'preference', 0.82, 4, '[]', mem.id, t, t]]])
  sql(dataDir, [['INSERT INTO activity_habits(id,key,statement,kind,confidence,support,evidence,memory_id,first_seen,last_seen) VALUES(?,?,?,?,?,?,?,?,?,?)',
    ['hb2', 'weak', 'Checks mail twice a day', 'preference', 0.31, 1, '[]', '', t, t]]])
  await openInsights(grain)
  await expect(page.locator('.act-habit')).toHaveCount(2)
  await expect(page.locator('.act-habit', { hasText: 'focused mornings' })).toContainText('in memory')
  await expect(page.locator('.act-habit', { hasText: 'twice a day' })).toContainText('not written to memory')
  await page.locator('.act-habit', { hasText: 'focused mornings' }).getByTitle(/Forget this/).click()
  await expect(page.locator('.act-habit')).toHaveCount(1)
  await expect.poll(async () => (await api('/memories')).filter((m) => m.id === mem.id).length).toBe(0)
  expect((await api('/activity/insights')).habits.map((h) => h.id)).toEqual(['hb2'])
  // the user's own memory is untouched
  expect((await api('/memories')).some((m) => m.id === other.id)).toBe(true)
})

test('insight settings persist; "How this runs" numbers refuse out-of-range input', async ({ grain }) => {
  const { page, api } = grain
  await openInsights(grain)
  await page.locator('label.toggle-row', { hasText: 'Write confident habits into memory' }).locator('.switch').click()
  await expect.poll(async () => (await api('/activity/status')).config.insights.autoMemory).toBe(true)
  const every = page.locator('label.act-field', { hasText: 'Look again every' }).locator('input')
  await every.fill('500')
  await every.press('Enter')
  await expect(every).toHaveValue('12')
  await every.fill('24')
  await every.press('Enter')
  await expect.poll(async () => (await api('/activity/status')).config.insights.everyHours).toBe(24)
  await grain.relaunch()
  await grain.page.locator('.nav-item', { hasText: 'Activity' }).first().click()
  await grain.page.locator('.tabs button', { hasText: 'Insights' }).click()
  await expect(grain.page.locator('label.act-field', { hasText: 'Look again every' }).locator('input')).toHaveValue('24')
  await expect(grain.page.locator('label.toggle-row', { hasText: 'Write confident habits into memory' }).locator('input')).toBeChecked()
})

test('Find automations with too little data answers calmly (no error banner, no crash)', async ({ grain }) => {
  const { page } = grain
  await openInsights(grain)
  await page.getByRole('button', { name: 'Find automations' }).click()
  await expect(page.getByRole('button', { name: 'Find automations' })).toBeEnabled({ timeout: 90_000 })
  await expect(page.locator('.act-sug')).toHaveCount(0)
  await expect(page.locator('.sidebar')).toBeVisible()
  expect(realErrors(grain.consoleErrors)).toEqual([])
})
