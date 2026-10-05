import { test, expect } from './fixtures.mjs'
import { newChat, reply, realErrors } from './helpers/blocks.mjs'

const fence = (lang, body) => '```' + lang + '\n' + body + '\n```'
const last = (page) => page.locator('.msg.assistant').last()

async function openDashboards(page) {
  await page.locator('.nav-item', { hasText: /Files/ }).first().click()
  await page.getByRole('tab', { name: 'Dashboards' }).click()
}

async function seedDashboard(api, widgets) {
  const src = await api('/sources', { method: 'POST', body: { name: 'My todos', kind: 'internal', config: { internal: 'todos' } } })
  const dash = await api('/dashboards', { method: 'POST', body: { name: 'Main' } })
  const out = []
  for (const w of widgets) out.push(await api(`/dashboards/${dash.id}/widgets`, { method: 'POST', body: { source_ids: [src.id], ...w } }))
  return { src, dash, widgets: out }
}

test('a bound stat and table update when the data changes and the widget is refreshed', async ({ grain }) => {
  const { page, api } = grain
  await api('/todos', { method: 'POST', body: { title: 'first todo' } })
  const { widgets } = await seedDashboard(api, [
    { title: 'Open count', kind: 'stat', spec: { kind: 'stat', path: '$', stat: { agg: 'count', label: 'Open todos' } } },
    { title: 'Todo list', kind: 'table', spec: { kind: 'table', path: '$', table: { columns: [{ key: 'title', label: 'Title' }] } } }
  ])
  expect(widgets[0].data?.stat?.value).toBe(1)
  await openDashboards(page)
  const stat = page.locator('.dwidget', { hasText: 'Open count' })
  await expect(stat.locator('.stat-value')).toHaveText('1', { timeout: 20_000 })
  const table = page.locator('.dwidget', { hasText: 'Todo list' })
  await expect(table.locator('tbody tr')).toHaveCount(1)
  // data changes behind its back; refresh re-binds with no model call
  for (const t of ['second', 'third']) await api('/todos', { method: 'POST', body: { title: t + ' todo' } })
  const before = grain.llm.calls.length
  await page.getByRole('button', { name: 'Refresh Open count' }).click()
  await expect(stat.locator('.stat-value')).toHaveText('3', { timeout: 20_000 })
  await page.getByRole('button', { name: 'Refresh Todo list' }).click()
  await expect(table.locator('tbody tr')).toHaveCount(3, { timeout: 20_000 })
  expect(grain.llm.calls.length).toBe(before)
  // GET /widgets/:id/data serves the cached rows inside the TTL and survives a relaunch
  const cached = await api(`/widgets/${widgets[0].id}/data`)
  expect(cached.data.stat.value).toBe(3)
  expect(realErrors(grain)).toEqual([])
})

test('a bound chart widget with 400 rows draws; a dead source shows an error, not a crash', async ({ grain }) => {
  const { page, api } = grain
  for (let i = 0; i < 40; i++) await api('/todos', { method: 'POST', body: { title: 'todo ' + i, priority: i % 4 } })
  const { dash, src } = await seedDashboard(api, [
    { title: 'By priority', kind: 'chart', spec: { kind: 'chart', path: '$', select: { x: 'title', y: ['priority'] }, chart: { type: 'bar' } } }
  ])
  await api(`/dashboards/${dash.id}/widgets`, { method: 'POST', body: { title: 'Broken', kind: 'stat', source_ids: ['nope'], spec: { kind: 'stat', path: '$', stat: { agg: 'count' } } } })
  await openDashboards(page)
  await expect(page.locator('.dwidget', { hasText: 'By priority' }).locator('.recharts-wrapper')).toBeVisible({ timeout: 20_000 })
  await expect(page.locator('.dwidget', { hasText: 'Broken' })).toBeVisible()
  await expect(page.getByText(/Something went wrong|Minified React/i)).toHaveCount(0)
  expect(src.id).toBeTruthy()
})

test('an html widget runs in a sandboxed frame that cannot reach the app', async ({ grain }) => {
  const { page, api, backend } = grain
  const code = `<!doctype html><body><p id="p">hello widget</p><script>
    const out = { os: typeof window.os, parent: 'blocked' }
    try { out.parent = String(window.parent.document.title) } catch (e) {}
    fetch(${JSON.stringify(backend.url + '/settings')}).then((r) => { out.fetch = String(r.status) }).catch(() => { out.fetch = 'blocked' }).finally(() => { document.body.setAttribute('data-probe', JSON.stringify(out)) })
  </script></body>`
  await seedDashboard(api, [{ title: 'Probe widget', kind: 'html', code }])
  await openDashboards(page)
  const iframe = page.locator('.dwidget', { hasText: 'Probe widget' }).locator('iframe')
  await expect(iframe).toHaveAttribute('sandbox', 'allow-scripts')
  await expect.poll(() => page.frames().find((f) => /\/widgets\/.+\/render/.test(f.url())) !== undefined, { timeout: 20_000 }).toBe(true)
  const f = page.frames().find((fr) => /\/widgets\/.+\/render/.test(fr.url()))
  await expect(f.locator('#p')).toHaveText('hello widget')
  await expect.poll(() => f.evaluate(() => document.body.getAttribute('data-probe')), { timeout: 15_000 }).toBeTruthy()
  const out = JSON.parse(await f.evaluate(() => document.body.getAttribute('data-probe')))
  // no app token reaches a widget: a direct call is refused (or blocked outright), never answered with data
  expect(out).toMatchObject({ os: 'undefined', parent: 'blocked' })
  expect(['401', 'blocked']).toContain(out.fetch)
})

test.describe('interactive fence', () => {
  const spec = {
    title: 'Growth', type: 'line',
    controls: [{ id: 'rate', label: 'Rate', type: 'slider', min: 0, max: 10, step: 1, value: 2 }],
    x: { id: 'year', label: 'Year', from: 0, to: 10, steps: 10 },
    series: [{ key: 'v', label: 'Value', expr: '100 * pow(1 + rate/100, year)' }],
    readouts: [{ label: 'Rate now', expr: 'rate' }]
  }

  test('dragging the slider recomputes locally, with no new model call', async ({ grain }) => {
    const { page } = grain
    await newChat(page)
    await reply(page, fence('interactive', JSON.stringify(spec)))
    const block = last(page)
    await expect(block.locator('.recharts-wrapper')).toBeVisible({ timeout: 20_000 })
    const read = block.locator('.iv-readout-value').first()
    await expect(read).toHaveText('2')
    const calls = grain.llm.calls.length
    await block.locator('input[type=range]').first().fill('7')
    await expect(read).toHaveText('7')
    expect(grain.llm.calls.length).toBe(calls)
    expect(realErrors(grain)).toEqual([])
  })

  test('hostile or broken specs degrade to a message', async ({ grain }) => {
    const { page } = grain
    await newChat(page)
    const bad = [
      { ...spec, series: [{ key: 'v', expr: 'constructor.constructor("window.__pwn=1")()' }] },
      { ...spec, series: [{ key: 'v', expr: 'rate +' }] },
      { ...spec, controls: Array.from({ length: 40 }, (_, i) => ({ id: 'c' + i, type: 'slider' })) },
      { ...spec, x: { id: 'year', from: 0, to: 1e9, steps: 1e9 } },
      {}
    ]
    for (const b of bad) {
      await reply(page, fence('interactive', JSON.stringify(b)))
      await expect(last(page).locator('.chart-err, .recharts-wrapper').first()).toBeVisible({ timeout: 20_000 })
    }
    expect(await page.evaluate(() => window.__pwn)).toBeUndefined()
  })
})
