import { test, expect } from './fixtures.mjs'
import { openAdvanced } from './helpers/home.mjs'
import { callWith, send, shrink, sleep, systemOf, upload } from './helpers/kb.mjs'

const clean = (g) => expect(g.consoleErrors.filter((e) => !/favicon|ResizeObserver/.test(e))).toEqual([])
const excerpts = (sys) => [...sys.matchAll(/^### \[(\d+)\] (.*)$/gm)].map((m) => ({ n: Number(m[1]), header: m[2] }))

const DOCS = {
  'tides.txt': 'Zephyrine harbour tide tables. High water at Zephyrine harbour is at dawn; the tidal range is four metres and the harbour master rings a brass bell.',
  'recipes.txt': 'Quillfeather pancakes. Mix flour, oat milk and a pinch of cardamom; the Quillfeather batter must rest for twenty minutes before frying.',
  'garden.txt': 'Marrowbone allotment notes. The Marrowbone plot grows kale and leeks; compost the Marrowbone beds every autumn with seaweed.'
}

async function newChat(page) {
  await page.getByRole('button', { name: /New chat/ }).first().click()
}

test('uploaded docs reach the prompt as numbered excerpts; each [n] chip opens the doc it came from', async ({ grain }) => {
  const { page, llm } = grain
  for (const [n, t] of Object.entries(DOCS)) await upload(grain, n, t)
  await newChat(page)
  await send(page, 'What do my files say about Zephyrine harbour tide Quillfeather Marrowbone? !!reply Per the files [1], also [2] and [3].')
  await expect(page.locator('.msg.assistant').last()).toContainText('Per the files', { timeout: 30_000 })
  const call = callWith(llm, 'Zephyrine harbour')[0]
  const sys = systemOf(call)
  expect(sys).toContain('## Relevant file excerpts')
  const ex = excerpts(sys)
  expect(ex.length).toBeGreaterThanOrEqual(3)
  const chips = page.locator('.msg.assistant .cite-chip')
  await expect(chips).toHaveCount(3)
  for (let i = 0; i < 3; i++) {
    const want = ex.find((e) => e.n === i + 1).header
    await chips.nth(i).click()
    const dlg = page.locator('.modal.wide')
    await expect(dlg).toBeVisible()
    const name = (await dlg.locator('h2').innerText()).trim()
    expect(want).toContain(name)
    expect(DOCS[name]).toBeTruthy()
    await expect(dlg.locator('mark')).toBeVisible()
    expect(DOCS[name]).toContain((await dlg.locator('mark').innerText()).trim().slice(0, 20))
    await page.getByRole('button', { name: 'Close excerpt' }).click()
    await expect(dlg).toBeHidden()
  }
  // the Sources list repeats them
  await page.locator('.msg.assistant summary', { hasText: /Sources \(/ }).click()
  await expect(page.locator('.sources li')).toHaveCount(ex.length)
  clean(grain)
})

test('a number the reply cites but no excerpt has stays plain text; code keeps its brackets', async ({ grain }) => {
  const { page } = grain
  await upload(grain, 'tides.txt', DOCS['tides.txt'])
  await newChat(page)
  await send(page, 'Zephyrine harbour? !!reply Cited [1] but year [2024] and `arr[1]` and bogus [9].')
  await expect(page.locator('.msg.assistant').last()).toContainText('Cited', { timeout: 30_000 })
  await expect(page.locator('.msg.assistant .cite-chip')).toHaveCount(1)
  await expect(page.locator('.msg.assistant').last()).toContainText('[2024]')
  await expect(page.locator('.msg.assistant').last()).toContainText('[9]')
  await expect(page.locator('.msg.assistant code', { hasText: 'arr[1]' })).toBeVisible()
  clean(grain)
})

test('context drawer lists the excerpts that were sent, and opens them', async ({ grain }) => {
  const { page } = grain
  for (const [n, t] of Object.entries(DOCS)) await upload(grain, n, t)
  await newChat(page)
  await send(page, 'Tell me about Marrowbone compost !!reply Compost every autumn [1].')
  await expect(page.locator('.msg.assistant').last()).toContainText('Compost every autumn', { timeout: 30_000 })
  await page.getByRole('button', { name: 'Toggle context panel' }).click()
  const drawer = page.locator('aside, .ctx-drawer, [class*=context]').filter({ hasText: /Uploads \(/ }).last()
  await expect(drawer.getByText(/Uploads \(\d+ excerpt/)).toBeVisible()
  await expect(drawer.getByText('garden.txt').first()).toBeVisible()
  await drawer.getByRole('button', { name: /garden\.txt/ }).first().click()
  await expect(page.locator('.modal.wide h2')).toHaveText('garden.txt')
  await page.getByRole('button', { name: 'Close excerpt' }).click()
  clean(grain)
})

test('retrieval respects project scope: project docs only in that project, isolated hides personal docs', async ({ grain }) => {
  const { page, api, llm } = grain
  const a = await api('/projects', { method: 'POST', body: { name: 'ProjA' } })
  const b = await api('/projects', { method: 'POST', body: { name: 'ProjB' } })
  const iso = await api('/projects', { method: 'POST', body: { name: 'ProjIso', memory_mode: 'isolated' } })
  await upload(grain, 'tides.txt', DOCS['tides.txt'], a.id)
  await upload(grain, 'recipes.txt', DOCS['recipes.txt'], b.id)
  await upload(grain, 'garden.txt', DOCS['garden.txt'])
  const ask = async (proj, title, tag) => {
    await api('/conversations', { method: 'POST', body: { project_id: proj?.id ?? null, title } })
    await page.reload()
    await page.locator('.sidebar').getByText(title, { exact: true }).first().click()
    await send(page, `Zephyrine Quillfeather Marrowbone ${tag} !!reply done${tag}`)
    await expect(page.locator('.msg.assistant').last()).toContainText(`done${tag}`, { timeout: 30_000 })
    return systemOf(callWith(llm, `Marrowbone ${tag}`)[0])
  }
  const sa = await ask(a, 'chat a', 'aa')
  expect(sa).toContain('tides.txt'); expect(sa).toContain('garden.txt'); expect(sa).not.toContain('recipes.txt')
  const sb = await ask(b, 'chat b', 'bb')
  expect(sb).toContain('recipes.txt'); expect(sb).toContain('garden.txt'); expect(sb).not.toContain('tides.txt')
  const si = await ask(iso, 'chat iso', 'ii')
  expect(si).not.toContain('garden.txt'); expect(si).not.toContain('tides.txt'); expect(si).not.toContain('recipes.txt')
  const sp = await ask(null, 'chat personal', 'pp')
  expect(sp).toContain('garden.txt'); expect(sp).not.toContain('tides.txt')
})

test('a deleted upload is no longer retrieved; a chat with no match sends no excerpts block', async ({ grain }) => {
  const { page, api, llm } = grain
  const d = await upload(grain, 'tides.txt', DOCS['tides.txt'])
  await api('/documents/' + d.id, { method: 'DELETE' })
  await newChat(page)
  await send(page, 'Zephyrine harbour tide? !!reply gone')
  await expect(page.locator('.msg.assistant').last()).toContainText('gone', { timeout: 30_000 })
  expect(systemOf(callWith(llm, 'Zephyrine harbour')[0])).not.toContain('Relevant file excerpts')
  clean(grain)
})

test('Advanced retrieval settings persist across relaunch and clamp out-of-range input', async ({ grain }) => {
  const { page, api } = grain
  await openAdvanced(page, 'Search')
  await page.getByLabel('Search by').selectOption('bm25')
  await page.getByRole('checkbox', { name: /Re-rank search results/ }).check({ force: true })
  await page.getByRole('button', { name: 'Save', exact: true }).click()
  await expect.poll(async () => (await api('/settings')).retrievalMode).toBe('bm25')
  expect(await api('/settings')).toMatchObject({ retrievalMode: 'bm25', retrievalRerank: true })
  // The per-document cap, similarity floor and candidate count have no controls any more; the API still holds their ranges.
  for (const body of [{ retrievalPerDocCap: 99 }, { retrievalCandidates: 1 }, { retrievalMinSimilarity: 7 }]) {
    const r = await api('/settings', { method: 'PUT', body, raw: true })
    expect(r.status, JSON.stringify(body)).toBeGreaterThanOrEqual(400)
    expect(r.status).toBeLessThan(500)
  }
  await api('/settings', { method: 'PUT', body: { retrievalPerDocCap: 4, retrievalMinSimilarity: 0.6, retrievalCandidates: 30 } })
  const p = await grain.relaunch()
  expect(await api('/settings')).toMatchObject({ retrievalPerDocCap: 4, retrievalMinSimilarity: 0.6, retrievalCandidates: 30 })
  await openAdvanced(p, 'Search')
  await expect(p.getByLabel('Search by')).toHaveValue('bm25')
  await expect(p.getByRole('checkbox', { name: /Re-rank search results/ })).toBeChecked()
  clean(grain)
})

test('a 400 KB upload and 40 small ones: indexing and retrieval still answer, window 820x520', async ({ grain }) => {
  const { page, api, llm } = grain
  const big = Array.from({ length: 8000 }, (_, i) => `Line ${i}: filler words about nothing in particular here.`).join('\n') + '\nThe NEEDLEQUAIL sentence is hidden in the middle of the bulk.\n' + 'more filler. '.repeat(500)
  const up = await upload(grain, 'bulk.txt', big)
  expect(up.chunk_count ?? 1).toBeGreaterThan(1)
  await Promise.all(Array.from({ length: 40 }, (_, i) => upload(grain, `small-${i}.txt`, `small file ${i} about topic${i}`)))
  const docs = await api('/documents')
  expect(docs.length).toBe(41)
  await shrink(grain)
  await newChat(page)
  await send(page, 'where is the NEEDLEQUAIL sentence? !!reply found it [1]')
  await expect(page.locator('.msg.assistant').last()).toContainText('found it', { timeout: 30_000 })
  expect(systemOf(callWith(llm, 'NEEDLEQUAIL')[0])).toContain('NEEDLEQUAIL')
  clean(grain)
})
