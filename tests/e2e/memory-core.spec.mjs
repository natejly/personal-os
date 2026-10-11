import { test, expect } from './fixtures.mjs'
import { callWith, send, shrink, sleep, sqlite, systemOf } from './helpers/kb.mjs'
import { openMemory as openMemoryTab } from './helpers/home.mjs'

const pane = (page) => page.locator('.knowledge-body')
const clean = (g) => expect(g.consoleErrors.filter((e) => !/favicon|ResizeObserver/.test(e))).toEqual([])

async function openMemory(page) {
  await openMemoryTab(page, 'List')
  await expect(page.getByPlaceholder(/Remember something/)).toBeVisible()
}
const addBox = (page) => page.getByPlaceholder(/Remember something/)

test('add, edit, pin, forget a memory from Settings; counts follow', async ({ grain }) => {
  const { page, api } = grain
  await openMemory(page)
  await expect(page.getByText('No memories yet.')).toBeVisible()
  await expect(page.getByText(/^0 memories · 0 entities, 0 relations/)).toBeVisible()
  await addBox(page).fill('I live in Lisbon')
  await page.getByLabel('Kind for the new memory').selectOption('preference')
  await page.getByRole('button', { name: 'Add', exact: true }).click()
  await expect(pane(page).getByText('I live in Lisbon')).toBeVisible()
  await expect(page.getByText(/^1 memory · /)).toBeVisible()
  await expect(addBox(page)).toHaveValue('')
  let rows = await api('/memories')
  expect(rows).toHaveLength(1)
  expect(rows[0]).toMatchObject({ content: 'I live in Lisbon', kind: 'preference', source: 'user', pinned: 0 })

  // edit: click text, Enter commits; the old wording stays in history
  await pane(page).getByText('I live in Lisbon').click()
  const ta = page.locator('.mem-row textarea')
  await ta.fill('I live in Porto')
  await ta.press('Enter')
  await expect(pane(page).getByText('I live in Porto')).toBeVisible()
  rows = await api('/memories')
  expect(rows).toHaveLength(1)
  expect(rows[0].content).toBe('I live in Porto')
  expect(rows[0].kind).toBe('preference')

  // Esc cancels an edit
  await pane(page).getByText('I live in Porto').click()
  await page.locator('.mem-row textarea').fill('discarded')
  await page.locator('.mem-row textarea').press('Escape')
  await expect(pane(page).getByText('I live in Porto')).toBeVisible()

  // pin and unpin
  await page.getByRole('button', { name: /^Pin memory/ }).click()
  await expect(page.getByRole('button', { name: /^Unpin memory/ })).toBeVisible()
  expect((await api('/memories'))[0].pinned).toBeTruthy()
  await page.getByRole('button', { name: /^Unpin memory/ }).click()
  await expect(page.getByRole('button', { name: /^Pin memory/ })).toBeVisible()

  // kind change
  await page.getByLabel('Memory kind').selectOption('goal')
  await expect.poll(async () => (await api('/memories'))[0].kind).toBe('goal')

  // history shows the earlier wording
  await page.getByRole('button', { name: /Show past versions/ }).click()
  await expect(pane(page).getByText(/I live in Lisbon/)).toBeVisible()

  // the superseded wording is listed under History with what replaced it
  await page.locator('label.chip-check').click()
  await expect(pane(page).getByText(/replaced by “I live in Porto”/)).toBeVisible()
  await page.locator('label.chip-check').click()

  // forget moves it to the trash; restoring it from there brings it back
  await page.getByRole('button', { name: /^Forget memory/ }).click()
  await expect(page.getByText('No memories yet.')).toBeVisible()
  await expect(page.getByText(/^0 memories · /)).toBeVisible()
  expect(await api('/memories')).toHaveLength(0)
  const trash = await api('/trash')
  expect(trash.groups.memories).toHaveLength(1)
  await api(`/trash/memory/${trash.groups.memories[0].id}/restore`, { method: 'POST' })
  const p2 = await grain.relaunch()
  await openMemoryTab(p2, 'List')
  await expect(p2.locator('.knowledge-body').getByText('I live in Porto')).toBeVisible()
  clean(grain)
})

test('blank memory is refused, Enter adds, whitespace-only add is disabled, markup stays text', async ({ grain }) => {
  const { page, api } = grain
  await openMemory(page)
  await addBox(page).fill('   ')
  await expect(page.getByRole('button', { name: 'Add', exact: true })).toBeDisabled()
  await addBox(page).fill('<img src=x onerror=alert(1)> **not bold**')
  await addBox(page).press('Enter')
  await expect(pane(page).getByText('<img src=x onerror=alert(1)> **not bold**')).toBeVisible()
  await expect(page.locator('.mem-row img')).toHaveCount(0)
  await expect(api('/memories', { method: 'POST', body: { content: '  ' } })).rejects.toThrow(/400/)
  clean(grain)
})

test('search filters the memory list and the empty result says so', async ({ grain }) => {
  const { page, api } = grain
  for (const c of ['alpha rocket fuel', 'beta tomato soup', 'gamma rocket engine']) await api('/memories', { method: 'POST', body: { content: c } })
  await openMemory(page)
  await expect(page.locator('.mem-row')).toHaveCount(3)
  await page.getByPlaceholder('Search memory').fill('rocket')
  await expect(page.locator('.mem-row')).toHaveCount(2)
  await expect(pane(page).getByText('beta tomato soup')).toHaveCount(0)
  await page.getByPlaceholder('Search memory').fill('zzzzqq')
  await expect(page.getByText('No memories match.')).toBeVisible()
  await page.getByPlaceholder('Search memory').fill('')
  await expect(page.locator('.mem-row')).toHaveCount(3)
  clean(grain)
})

test('a pinned memory reaches the system prompt of an unrelated chat; a forgotten one does not', async ({ grain }) => {
  const { page, api, llm } = grain
  const pin = await api('/memories', { method: 'POST', body: { content: 'ZEBRAPIN the user is allergic to kiwi', pinned: true } })
  const gone = await api('/memories', { method: 'POST', body: { content: 'QUOKKAGONE favourite colour is teal' } })
  await api('/memories/' + gone.id, { method: 'DELETE' })
  await page.getByRole('button', { name: /New chat/ }).first().click()
  await send(page, '!!reply sure thing')
  await expect(page.locator('.msg.assistant').last()).toContainText('sure thing', { timeout: 30_000 })
  const sys = systemOf(callWith(llm, 'sure thing')[0])
  expect(sys).toContain('ZEBRAPIN')
  expect(sys).not.toContain('QUOKKAGONE')
  expect(pin.id).toBeTruthy()
  // the context panel shows the memory that was sent
  await page.getByRole('button', { name: 'Toggle context panel' }).click()
  await expect(page.locator('.ctx, [class*=context]').getByText(/ZEBRAPIN/).first()).toBeVisible()
  clean(grain)
})

test('memory scoping: project memory only in that project, isolated project hides personal memory', async ({ grain }) => {
  const { page, api, llm } = grain
  const shared = await api('/projects', { method: 'POST', body: { name: 'Shared' } })
  const iso = await api('/projects', { method: 'POST', body: { name: 'Iso', memory_mode: 'isolated' } })
  await api('/memories', { method: 'POST', body: { content: 'PERSONALM wears glasses', pinned: true } })
  await api('/memories', { method: 'POST', body: { content: 'SHAREDM project fact', pinned: true, project_id: shared.id } })
  await api('/memories', { method: 'POST', body: { content: 'ISOM isolated fact', pinned: true, project_id: iso.id } })
  const ask = async (pid, tag) => {
    const c = await api('/conversations', { method: 'POST', body: { project_id: pid, title: tag } })
    await page.reload()
    await page.locator('.sidebar').getByText(tag, { exact: true }).first().click()
    await send(page, `!!reply ${tag}done`)
    await expect(page.locator('.msg.assistant').last()).toContainText(`${tag}done`, { timeout: 30_000 })
    return systemOf(callWith(llm, `${tag}done`)[0])
  }
  const s1 = await ask(shared.id, 'inshared')
  expect(s1).toContain('PERSONALM')
  expect(s1).toContain('SHAREDM')
  expect(s1).not.toContain('ISOM')
  const s2 = await ask(iso.id, 'inisolated')
  expect(s2).toContain('ISOM')
  expect(s2).not.toContain('PERSONALM')
  expect(s2).not.toContain('SHAREDM')
  const s3 = await ask(null, 'inpersonal')
  expect(s3).toContain('PERSONALM')
  expect(s3).not.toContain('SHAREDM')
  expect(s3).not.toContain('ISOM')
})

test('auto-learn after a reply with a garbage extraction answer: no crash, nothing stored, UI clean', async ({ grain }) => {
  const { page, api, llm, backend } = grain
  await api('/settings', { method: 'PUT', body: { autoLearn: true } })
  await page.getByRole('button', { name: /New chat/ }).first().click()
  await send(page, 'Remember that my sister is called Marta. !!reply Noted, your sister is Marta.')
  await expect(page.locator('.msg.assistant').last()).toContainText('Noted', { timeout: 30_000 })
  await expect.poll(() => llm.calls.filter((c) => !c.stream && !c.tools?.length).length, { timeout: 20_000 }).toBeGreaterThan(0)
  await sleep(1500)
  expect(await api('/memories')).toHaveLength(0)
  expect(backend.log()).not.toMatch(/Traceback/)
  await expect(page.locator('.toast, [role=alert]')).toHaveCount(0)
  // the chat still works afterwards
  await send(page, '!!reply still alive')
  await expect(page.locator('.msg.assistant').last()).toContainText('still alive', { timeout: 30_000 })
  clean(grain)
})

test('tidy up: garbage model answer says nothing to tidy; seeded proposals show a badge, apply merges, dismiss clears', async ({ grain }) => {
  const { page, api, dataDir } = grain
  const a = await api('/memories', { method: 'POST', body: { content: 'The user drinks oat milk lattes every morning' } })
  const b = await api('/memories', { method: 'POST', body: { content: 'The user drinks oat milk lattes every morning.' } })
  const c = await api('/memories', { method: 'POST', body: { content: 'User moves to Berlin next month' } })
  await openMemory(page)
  await page.getByRole('button', { name: /Tidy up/ }).click()
  await expect(page.getByText('Nothing to tidy up.')).toBeVisible()
  expect(await api('/memories/proposals')).toHaveLength(0)

  const insert = (id, kind, payload) => sqlite(dataDir, "INSERT INTO memory_proposals(id,project_id,kind,payload,rationale,status,created_at) VALUES(?,NULL,?,?,?,'pending',strftime('%s','now'))", [id, kind, JSON.stringify(payload), 'same fact'])
  insert('p-merge', 'merge_memories', { ids: [a.id, b.id], text: 'The user drinks oat milk lattes every morning', snapshot: { [a.id]: a.content, [b.id]: b.content } })
  insert('p-rewrite', 'rewrite_memory', { ids: [c.id], text: 'User moves to Berlin in November 2026', snapshot: { [c.id]: c.content } })
  await page.reload()
  await openMemory(page)
  await expect(page.locator('.mem-row.proposal')).toHaveCount(2)
  await expect(page.getByRole('button', { name: /Tidy up/ }).locator('.count')).toHaveText('2')
  await page.getByRole('button', { name: 'Apply: Merge duplicates' }).click()
  await expect(page.locator('.mem-row.proposal')).toHaveCount(1)
  await expect(page.getByRole('button', { name: /Tidy up/ }).locator('.count')).toHaveText('1')
  expect((await api('/memories')).filter((m) => /oat milk/.test(m.content))).toHaveLength(1)
  await page.getByRole('button', { name: 'Dismiss: Make the date absolute' }).click()
  await expect(page.locator('.mem-row.proposal')).toHaveCount(0)
  await expect(page.getByRole('button', { name: /Tidy up/ }).locator('.count')).toHaveCount(0)
  expect((await api('/memories')).map((m) => m.content)).toContain('User moves to Berlin next month')
  // applying a stale proposal does not clobber a changed memory
  insert('p-stale', 'rewrite_memory', { ids: [c.id], text: 'User moves to Berlin in 2027', snapshot: { [c.id]: 'something else' } })
  const res = await api('/memories/proposals/p-stale/apply', { method: 'POST' })
  expect(res.status).toBe('stale')
  expect((await api('/memories')).map((m) => m.content)).toContain('User moves to Berlin next month')
  clean(grain)
})

test('300 memories and a 60 KB memory: list renders, search stays quick, window 820x520', async ({ grain }) => {
  const { page, api } = grain
  for (let i = 0; i < 300; i += 20) await Promise.all(Array.from({ length: 20 }, (_, j) => api('/memories', { method: 'POST', body: { content: `bulk memory number ${i + j} ${(i + j) % 2 ? 'odd' : 'even'}` } })))
  const big = 'long memory ' + 'lorem ipsum dolor sit amet '.repeat(2400)
  await api('/memories', { method: 'POST', body: { content: big } })
  await shrink(grain)
  await openMemory(page)
  await expect(page.locator('.mem-row')).toHaveCount(301, { timeout: 20_000 })
  await expect(page.getByText(/^301 memories/)).toBeVisible()
  const t0 = Date.now()
  await page.getByPlaceholder('Search memory').fill('odd')
  await expect(page.locator('.mem-row')).toHaveCount(100, { timeout: 10_000 }) // search is capped at 100 hits by the backend
  expect(Date.now() - t0).toBeLessThan(5000)
  // the layout does not overflow horizontally at the small size
  const over = await page.evaluate(() => document.querySelector('.knowledge-body')?.scrollWidth - document.querySelector('.knowledge-body')?.clientWidth)
  expect(over).toBeLessThanOrEqual(1)
  clean(grain)
})

test('memories persist across relaunch; export file round-trips through import', async ({ grain }) => {
  const { page, api } = grain
  await api('/memories', { method: 'POST', body: { content: 'persist me', pinned: true, kind: 'goal' } })
  await api('/memories', { method: 'POST', body: { content: 'and me' } })
  const file = await api('/memories/export')
  expect(file.memories).toHaveLength(2)
  const p = await grain.relaunch()
  await openMemoryTab(p, 'List')
  await expect(p.locator('.knowledge-body').getByText('persist me')).toBeVisible()
  await expect(p.locator('.knowledge-body').getByText('and me', { exact: true })).toBeVisible()
  const pr = await api('/projects', { method: 'POST', body: { name: 'Target' } })
  const r = await api('/memories/import', { method: 'POST', body: { file, project_id: pr.id } })
  expect(r).toEqual({ added: 2, skipped: 0 })
  const again = await api('/memories/import', { method: 'POST', body: { file, project_id: pr.id } })
  expect(again.added).toBe(0)
  await expect(api('/memories/import', { method: 'POST', body: { file: { nope: 1 } } })).rejects.toThrow(/400/)
  clean(grain)
})
