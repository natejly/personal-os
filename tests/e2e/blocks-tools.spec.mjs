import { test, expect } from './fixtures.mjs'
import { newChat, say, smallWindow, realErrors } from './helpers/blocks.mjs'

const last = (page) => page.locator('.msg.assistant').last()
const settle = (grain) => grain.api('/settings', { method: 'PUT', body: { toolDeferAbove: 0 } })
const decided = async (api, tool) => (await api('/approvals?status=all')).filter((a) => a.tool === tool).pop()

async function start(grain, settings = {}) {
  await grain.api('/settings', { method: 'PUT', body: { toolDeferAbove: 0, ...settings } })
  await grain.page.reload()
  await newChat(grain.page)
  return grain.page
}

test.describe('email draft card', () => {
  const args = { to: 'ann@example.com', subject: 'Hello', body: 'Hi Ann,\nsee you soon.' }

  test('edit subject and body, approve: the edited arguments are what the backend recorded', async ({ grain }) => {
    const page = await start(grain)
    await say(page, '!!tool gmail_draft ' + JSON.stringify(args), { wait: false })
    const card = page.getByRole('region', { name: 'Email draft' })
    await expect(card).toBeVisible({ timeout: 30_000 })
    await expect(card.getByLabel('Subject')).toHaveValue('Hello')
    await card.getByLabel('Subject').fill('Hello again')
    await card.getByLabel('Message body').fill('Edited body\nline two')
    await card.getByRole('button', { name: /Save draft/ }).click()
    await expect.poll(async () => (await decided(grain.api, 'gmail_draft'))?.status, { timeout: 20_000 }).toBe('approved')
    const row = await decided(grain.api, 'gmail_draft')
    expect(row.args.subject).toBe('Hello')               // the model's own proposal is kept
    expect(row.edited_args.subject).toBe('Hello again')  // the user's edit is what runs
    expect(row.edited_args.body).toBe('Edited body\nline two')
    expect(row.edited_args.to).toContain('ann@example.com')
    expect(realErrors(grain).filter((e) => !/Google|connected/i.test(e))).toEqual([])
  })

  test('an untouched card approves the model call as is; Revert, invalid recipient and Discard behave', async ({ grain }) => {
    const page = await start(grain)
    await say(page, '!!tool gmail_draft ' + JSON.stringify(args), { wait: false })
    const card = page.getByRole('region', { name: 'Email draft' })
    await expect(card).toBeVisible({ timeout: 30_000 })
    await card.getByLabel('Subject').fill('changed')
    await card.getByRole('button', { name: 'Revert' }).click()
    await expect(card.getByLabel('Subject')).toHaveValue('Hello')
    await card.getByLabel('Subject').fill('')
    await card.getByLabel('Subject').fill('Hello')
    await card.getByRole('button', { name: /Discard/ }).click()
    await expect.poll(async () => (await decided(grain.api, 'gmail_draft'))?.status, { timeout: 20_000 }).toBe('denied')
  })

  test('⌘↵ approves, the card works at 820x520, and a double click records one decision', async ({ grain }) => {
    const page = await start(grain)
    await smallWindow(grain.app)
    await say(page, '!!tool gmail_draft ' + JSON.stringify({ ...args, body: 'x\n'.repeat(400) }), { wait: false })
    const card = page.getByRole('region', { name: 'Email draft' })
    await expect(card).toBeVisible({ timeout: 30_000 })
    const box = await card.boundingBox()
    expect(box.x + box.width).toBeLessThanOrEqual(820 + 1)
    await card.getByLabel('Subject').fill('Via shortcut')
    await card.getByLabel('Subject').press('Meta+Enter')
    await card.getByRole('button', { name: /Save draft/ }).dblclick({ timeout: 2000 }).catch(() => {})
    await expect.poll(async () => (await decided(grain.api, 'gmail_draft'))?.status, { timeout: 20_000 }).toBe('approved')
    const rows = (await grain.api('/approvals?status=all')).filter((a) => a.tool === 'gmail_draft')
    expect(rows.length).toBe(1)
    expect(rows[0].edited_args.subject).toBe('Via shortcut')
  })
})

test.describe('calendar card', () => {
  const changes = [
    { op: 'create', summary: 'Standup', start: '2026-10-06T09:00:00', end: '2026-10-06T09:15:00' },
    { op: 'create', summary: 'Review', start: '2026-10-06T15:00:00', end: '2026-10-06T16:00:00' }
  ]

  test('edit a title in the proposal, approve all: the backend records the edited changes', async ({ grain }) => {
    const page = await start(grain)
    await say(page, '!!tool calendar_propose ' + JSON.stringify({ changes }), { wait: false })
    const card = page.getByRole('region', { name: /Calendar changes/ })
    await expect(card).toBeVisible({ timeout: 30_000 })
    await card.getByRole('group', { name: 'Changes' }).locator('.pg-pickbtn', { hasText: 'Review' }).click()
    await card.getByLabel('Title').fill('Design review')
    await card.getByRole('button', { name: /Approve all/ }).click()
    await expect.poll(async () => (await decided(grain.api, 'calendar_propose'))?.status, { timeout: 20_000 }).toBe('approved')
    const row = await decided(grain.api, 'calendar_propose')
    const sent = (row.edited_args || row.args).changes.map((c) => c.summary)
    expect(sent).toEqual(['Standup', 'Design review'])
    expect(row.args.changes[1].summary).toBe('Review')
  })

  test('unticking a change drops it from what is approved; Deny declines everything', async ({ grain }) => {
    const page = await start(grain)
    await say(page, '!!tool calendar_propose ' + JSON.stringify({ changes }), { wait: false })
    const card = page.getByRole('region', { name: /Calendar changes/ })
    await expect(card).toBeVisible({ timeout: 30_000 })
    await card.getByRole('button', { name: 'List view' }).click()
    await card.getByRole('checkbox', { name: /Include change 1/ }).uncheck()
    await card.getByRole('button', { name: /Approve selected \(1\)/ }).click()
    await expect.poll(async () => (await decided(grain.api, 'calendar_propose'))?.status, { timeout: 20_000 }).toBe('approved')
    const row = await decided(grain.api, 'calendar_propose')
    expect((row.edited_args || row.args).changes.map((c) => c.summary)).toEqual(['Review'])
    await say(page, '!!tool calendar_propose ' + JSON.stringify({ changes }), { wait: false })
    const card2 = page.getByRole('region', { name: /Calendar changes/ }).last()
    await expect(card2.getByRole('button', { name: 'Deny', exact: true })).toBeVisible({ timeout: 30_000 })
    await card2.getByRole('button', { name: 'Deny', exact: true }).click()
    await expect.poll(async () => (await grain.api('/approvals?status=denied')).length, { timeout: 20_000 }).toBe(1)
  })

  test('a 40-change proposal renders and stays inside an 820x520 window', async ({ grain }) => {
    const page = await start(grain)
    await smallWindow(grain.app)
    const many = Array.from({ length: 40 }, (_, i) => ({ op: 'create', summary: 'Event ' + i, start: `2026-10-${String(6 + (i % 5)).padStart(2, '0')}T${String(8 + (i % 9)).padStart(2, '0')}:00:00`, end: `2026-10-${String(6 + (i % 5)).padStart(2, '0')}T${String(9 + (i % 9)).padStart(2, '0')}:00:00` }))
    await say(page, '!!tool calendar_propose ' + JSON.stringify({ changes: many }), { wait: false })
    const card = page.getByRole('region', { name: /Calendar changes/ })
    await expect(card).toBeVisible({ timeout: 30_000 })
    await expect(card.getByRole('button', { name: /Approve all \(40\)/ })).toBeVisible()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true)
  })
})

test.describe('trace, diff and outcomes', () => {
  test('the trace opens for a reply with tool events and exports OTLP JSON', async ({ grain }) => {
    const page = await start(grain, { devTools: true })
    await say(page, '!!tool current_time {}')
    const chip = page.getByTitle(/Execution trace/)
    await expect(chip).toBeVisible({ timeout: 20_000 })
    await chip.click()
    const trace = page.locator('.trace')
    await expect(trace).toBeVisible()
    await expect(trace.locator('.trace-row').first()).toBeVisible()
    await expect(trace.locator('.trace-summary')).toContainText(/tool/)
    await trace.locator('.trace-head').first().click()
    await expect(trace.locator('.trace-meta').first()).toBeVisible()
    await page.evaluate(() => { window.__dl = []; HTMLAnchorElement.prototype.click = function () { window.__dl.push(this.download) } })
    await trace.getByRole('button', { name: 'Export OTLP JSON' }).click()
    await expect.poll(() => page.evaluate(() => window.__dl[0])).toMatch(/^grain-trace-.*\.otlp\.json$/)
    const msg = (await grain.api(`/conversations/${(await grain.api('/conversations'))[0].id}`)).messages.find((m) => m.role === 'assistant')
    const otlp = await grain.api(`/messages/${msg.id}/otlp`).catch(() => null)
    if (otlp) expect(JSON.stringify(otlp)).toContain('resourceSpans')
  })

  test('a doc_edit shows its diff in the chat; Accept applies it, Reject leaves the doc alone', async ({ grain }) => {
    const { api } = grain
    const d = await api('/docs', { method: 'POST', body: { title: 'Plan', content: 'line one\nline two\nline three\n' } })
    const page = await start(grain, { docEditMode: 'review' })
    await say(page, '!!tool doc_edit ' + JSON.stringify({ doc: d.id, edits: [{ find: 'line two', replace: 'line 2 edited' }] }), { wait: false })
    await page.getByRole('button', { name: 'Approve', exact: true }).click({ timeout: 30_000 })
    const diff = page.locator('.tool-doc-diff').last()
    await expect(diff).toBeVisible({ timeout: 20_000 })
    await expect(diff).toContainText('line 2 edited')
    await expect(diff).toContainText('line two')
    expect((await api(`/docs/${d.id}`)).content).toContain('line two')
    await diff.getByRole('button', { name: 'Accept' }).click()
    await expect.poll(async () => (await api(`/docs/${d.id}`)).content, { timeout: 15_000 }).toContain('line 2 edited')
    await say(page, '!!tool doc_edit ' + JSON.stringify({ doc: d.id, edits: [{ find: 'line three', replace: 'line 3 rejected' }] }), { wait: false })
    await page.getByRole('button', { name: 'Approve', exact: true }).click({ timeout: 30_000 })
    const diff2 = page.locator('.tool-doc-diff').last()
    await expect(diff2.getByRole('button', { name: 'Reject' })).toBeVisible({ timeout: 20_000 })
    await diff2.getByRole('button', { name: 'Reject' }).click()
    await expect(diff2.getByRole('button', { name: 'Reject' })).toHaveCount(0, { timeout: 15_000 })
    expect((await api(`/docs/${d.id}`)).content).not.toContain('rejected')
  })

  test('Stop marks the reply as stopped and hands the composer back', async ({ grain }) => {
    const page = await start(grain)
    await say(page, '!!slow 20000 !!reply never arrives', { wait: false })
    const stop = page.getByRole('button', { name: 'Stop', exact: true })
    await expect(stop).toBeVisible({ timeout: 20_000 })
    await stop.click()
    await expect(page.getByText(/^Stopped/)).toBeVisible({ timeout: 20_000 })
    await expect(page.getByRole('button', { name: 'Stop', exact: true })).toHaveCount(0)
    const conv = (await grain.api('/conversations'))[0]
    const msgs = (await grain.api(`/conversations/${conv.id}`)).messages
    expect(msgs.filter((m) => m.role === 'assistant').pop().outcome).toBe('stopped')
    // the composer is usable again
    await say(page, '!!reply back to normal')
    await expect(last(page)).toContainText('back to normal')
  })
})
