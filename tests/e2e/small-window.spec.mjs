import { test, expect } from './fixtures.mjs'
import { enableModules, reload, small, sql, seedSegments, realErrors } from './helpers/mah.mjs'

test.beforeEach(() => test.setTimeout(240_000))

const overflow = (page) => page.evaluate(() => {
  const wide = []
  for (const el of document.querySelectorAll('main *')) {
    const r = el.getBoundingClientRect()
    if (r.width > 0 && r.right > window.innerWidth + 1) {
      // inside its own scroller is fine
      let p = el.parentElement, scrolled = false
      while (p && p !== document.body) { const o = getComputedStyle(p).overflowX; if (o === 'auto' || o === 'scroll' || o === 'hidden') { scrolled = true; break } p = p.parentElement }
      if (!scrolled) wide.push(`${el.tagName}.${String(el.className).slice(0, 40)} right=${Math.round(r.right)}`)
    }
  }
  return { page: document.documentElement.scrollWidth - document.documentElement.clientWidth, wide: wide.slice(0, 5) }
})

test('Meetings with a transcript and the enhanced column open fits 820x520', async ({ grain }) => {
  const { page, api, dataDir, app } = grain
  const m = await api('/meetings', { method: 'POST', body: { title: 'A long meeting title that goes on and on and on for a while to test wrapping in the toolbar' } })
  await api(`/meetings/${m.id}`, { method: 'PUT', body: { notes: '# Notes\n- one\n- two', enhanced: '## Decisions\n- ship it', summary: 'headline' } })
  seedSegments(dataDir, m.id, Array.from({ length: 20 }, (_, i) => ({ text: `A reasonably long spoken sentence number ${i} that wraps in the narrow transcript column`, channel: i % 2 ? 'output' : 'mic', t: i * 10 })))
  sql(dataDir, [["UPDATE meetings SET status='ready', started_at=?, duration_ms=200000 WHERE id=?", [Date.now() / 1000 - 600, m.id]]])
  await small(app)
  await enableModules(api)
  await reload(page)
  await page.locator('.nav-item', { hasText: 'Meetings' }).first().click()
  await page.locator('.mtg-row').first().click()
  // narrow window: the review column starts closed, so the toolbar is reachable
  await expect(page.locator('.mtg-enhanced')).toBeHidden()
  await page.getByRole('button', { name: 'Transcript', exact: true }).click()
  await expect(page.locator('.mtg-line')).toHaveCount(20)
  await page.getByRole('button', { name: 'Enhanced notes and action items' }).click()
  await expect(page.locator('.mtg-enhanced')).toBeVisible()
  const o = await overflow(page)
  expect(o.page).toBeLessThanOrEqual(0)
  expect(o.wide).toEqual([])
  // the notepad is still usable: it has real width
  const ed = await page.locator('textarea.md-input').boundingBox()
  expect(ed.width).toBeGreaterThan(150)
  expect(realErrors(grain.consoleErrors)).toEqual([])
})

test('every Activity tab fits 820x520', async ({ grain }) => {
  const { page, api, app, dataDir } = grain
  const t = Date.now() / 1000
  sql(dataDir, Array.from({ length: 40 }, (_, i) => ["INSERT INTO activity_events(id,ts,kind,app,bundle,title,url,text,meta,duration_ms,rolled_up,expires_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
    [`e${i}`, t - i * 20, 'focus', 'Code', '', `A fairly long window title for row ${i} in the raw log table`, 'https://example.com/a/very/long/path/that/keeps/going', 'typed text captured here', '{}', 20000, 0, t + 86400]]))
  await small(app)
  await enableModules(api)
  await reload(page)
  await page.locator('.nav-item', { hasText: 'Activity' }).first().click()
  for (const tab of ['Overview', 'Insights', 'Signals', 'Privacy']) {
    await page.locator('.tabs button', { hasText: tab }).click()
    await expect(page.locator('.page-body')).toBeVisible()
    const o = await overflow(page)
    expect(o.page, tab).toBeLessThanOrEqual(0)
    expect(o.wide, tab).toEqual([])
  }
  expect(realErrors(grain.consoleErrors)).toEqual([])
})

test('Health page and its two panels fit 820x520', async ({ grain }) => {
  const { page, api, app } = grain
  await small(app)
  await enableModules(api)
  await reload(page)
  await page.getByRole('button', { name: 'Health', exact: true }).click()
  await expect(page.locator('.hl-tile').first()).toBeVisible()
  let o = await overflow(page)
  expect(o.page).toBeLessThanOrEqual(0)
  await page.getByRole('button', { name: 'Choose and edit metrics' }).click()
  await expect(page.getByRole('region', { name: 'Metrics' })).toBeVisible()
  o = await overflow(page)
  expect(o.page).toBeLessThanOrEqual(0)
  expect(o.wide).toEqual([])
  await page.getByRole('button', { name: 'Connected services' }).click()
  o = await overflow(page)
  expect(o.page).toBeLessThanOrEqual(0)
  expect(realErrors(grain.consoleErrors)).toEqual([])
})

test('at 820x520 a pending enhance proposal can still be accepted from the floating review column', async ({ grain }) => {
  const { page, api, dataDir, app } = grain
  const m = await api('/meetings', { method: 'POST', body: { title: 'Review me' } })
  await api(`/meetings/${m.id}`, { method: 'PUT', body: { notes: 'typed notes' } })
  const t = Date.now() / 1000
  sql(dataDir, [
    ["UPDATE meetings SET status='ready', started_at=? WHERE id=?", [t - 100, m.id]],
    ['INSERT INTO meeting_revisions(id,meeting_id,before,after,summary,author,tool,status,created_at) VALUES(?,?,?,?,?,?,?,?,?)', ['rev1', m.id, '', '## Proposed\n- accepted line', 'proposal', 'assistant', 'meeting_enhance', 'pending', t]]
  ])
  await small(app)
  await enableModules(api)
  await reload(page)
  await page.locator('.nav-item', { hasText: 'Meetings' }).first().click()
  await page.locator('.mtg-row', { hasText: 'Review me' }).click()
  await page.getByRole('button', { name: 'Enhanced notes and action items' }).click()
  await expect(page.locator('.mtg-enhanced')).toBeVisible()
  await page.getByTitle('What accepting would change').click()
  await page.getByRole('button', { name: 'Accept', exact: true }).click()
  await expect.poll(async () => (await api(`/meetings/${m.id}`)).enhanced).toContain('accepted line')
  expect((await api(`/meetings/${m.id}`)).notes).toBe('typed notes')
  // the column can be closed to get the notepad back
  await page.getByTitle('Close').click()
  await expect(page.locator('.mtg-enhanced')).toBeHidden()
})
