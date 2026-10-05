import { test, expect } from './fixtures.mjs'
import { enterCanvas, sleep, spaces, windowsOf } from './helpers/spaces.mjs'

test.describe.configure({ timeout: 300_000 })

const SIMPLE = [
  ['Todos', 'todos'], ['Calendar', 'calendar'], ['Memory', 'memory'], ['Graph', 'graph'], ['Uploads', 'documents'],
  ['Recap', 'recap'], ['Usage', 'usage'], ['Activity', 'activity'], ['Web', 'web'], ['Face', 'face']
]

async function addMenu(page) {
  await page.locator('button[title="Add widget"]').click()
  return page.locator('[role=menu]').first()
}

test('every Add-widget entry opens a window that renders without console errors', async ({ grain }) => {
  const { page, api } = grain
  const s = (await spaces(grain))[0]
  await enterCanvas(grain)

  // the menu lists one entry per registry kind
  const menu = await addMenu(page)
  const labels = (await menu.locator('[role=menuitem]').allInnerTexts()).map((t) => t.trim())
  for (const l of ['Chat', 'Todos', 'Calendar', 'Sticky note', 'Dashboard widget', 'Memory', 'Graph', 'Uploads', 'Recap', 'Project', 'Usage', 'Activity', 'Web', 'Artifact', 'Face']) {
    expect(labels).toContain(l)
  }
  await page.keyboard.press('Escape')

  for (const [label, kind] of SIMPLE) {
    const before = (await windowsOf(grain, s.id)).length
    const m = await addMenu(page)
    await m.getByRole('menuitem', { name: label, exact: true }).click()
    await expect.poll(async () => (await windowsOf(grain, s.id)).length, { message: label }).toBe(before + 1)
    const ws = await windowsOf(grain, s.id)
    expect(ws.map((w) => w.kind)).toContain(kind)
  }

  // chat + note go through a submenu
  let m = await addMenu(page)
  await m.getByRole('menuitem', { name: 'Chat', exact: true }).click()
  await page.getByRole('menuitem', { name: 'New chat' }).click()
  m = await addMenu(page)
  await m.getByRole('menuitem', { name: 'Sticky note', exact: true }).click()
  await page.getByRole('menuitem', { name: 'New sticky note' }).click()
  await expect.poll(async () => (await windowsOf(grain, s.id)).length).toBe(SIMPLE.length + 2)

  // empty submenus say so instead of throwing
  m = await addMenu(page)
  await m.getByRole('menuitem', { name: 'Artifact', exact: true }).click()
  await expect(page.getByText('No artifacts yet')).toBeVisible()
  await page.keyboard.press('Escape')
  await page.keyboard.press('Escape')

  // artifact + project + dashboard widget via the API, then they render too
  const art = await api('/artifacts', { method: 'POST', body: { title: 'Demo page', code: '<h1>hello</h1>' } })
  const proj = await api('/projects', { method: 'POST', body: { name: 'Proj A' } })
  await api(`/canvases/${s.id}/windows`, { method: 'POST', body: { kind: 'artifact', ref_id: art.id } })
  await api(`/canvases/${s.id}/windows`, { method: 'POST', body: { kind: 'project', ref_id: proj.id } })
  await page.reload()
  await enterCanvas(grain)
  const ws = await windowsOf(grain, s.id)
  expect(ws.length).toBe(SIMPLE.length + 4)
  for (const w of ws) await expect(page.locator(`[data-window-id="${w.id}"]`), w.kind).toBeAttached()
  await sleep(2500)
  // every window has a non-empty body (no blank frame) and no error boundary text
  for (const w of ws) {
    const body = page.locator(`[data-window-id="${w.id}"] .win-body`)
    await expect(body, w.kind).not.toBeEmpty()
    await expect(body, w.kind).not.toContainText(/Something went wrong|Cannot read/i)
  }
  expect(grain.consoleErrors).toEqual([])
})

test('submenu entries add a chat / note / artifact / project once, and re-adding focuses instead of duplicating', async ({ grain }) => {
  const { page, api } = grain
  const s = (await spaces(grain))[0]
  const art = await api('/artifacts', { method: 'POST', body: { title: 'Demo page', code: '<h1>hello</h1>' } })
  const proj = await api('/projects', { method: 'POST', body: { name: 'Proj A' } })
  await page.reload()
  await enterCanvas(grain)
  for (const [kind, label, item] of [['artifact', 'Artifact', 'Demo page'], ['project', 'Project', 'Proj A']]) {
    for (let i = 0; i < 2; i++) {
      const m = await addMenu(page)
      await m.getByRole('menuitem', { name: label, exact: true }).click()
      await page.getByRole('menuitem', { name: item }).click()
      await sleep(400)
    }
    const ws = (await windowsOf(grain, s.id)).filter((w) => w.kind === kind)
    expect(ws, kind).toHaveLength(1)
  }
  void art; void proj
  expect(grain.consoleErrors).toEqual([])
})

test('clicking an artifact widget does not reload its iframe', async ({ grain }) => {
  const { page, api } = grain
  const s = (await spaces(grain))[0]
  const art = await api('/artifacts', { method: 'POST', body: { title: 'Demo page', code: '<h1>hello</h1>' } })
  const note = await api('/notes', { method: 'POST', body: { body: 'sibling' } })
  const w = await api(`/canvases/${s.id}/windows`, { method: 'POST', body: { kind: 'artifact', ref_id: art.id, x: 40, y: 40, w: 420, h: 320 } })
  const n = await api(`/canvases/${s.id}/windows`, { method: 'POST', body: { kind: 'note', ref_id: note.id, x: 500, y: 40, w: 300, h: 240 } })
  await api(`/canvases/${s.id}`, { method: 'PUT', body: { zoom: 1, pan_x: 0, pan_y: 0 } })
  await page.reload()
  await enterCanvas(grain)
  const frame = page.locator(`[data-window-id="${w.id}"] iframe`)
  await expect(frame).toBeAttached()
  await frame.evaluate((el) => {
    el.__mark = 'same'
    window.__loads = 0
    el.addEventListener('load', () => window.__loads++)
  })
  // let the first load (and any late src change under load) finish, then start counting from zero
  await expect.poll(() => page.evaluate(() => window.__loads), { timeout: 30_000 }).toBeGreaterThan(0)
  await sleep(2500)
  await page.evaluate(() => { window.__loads = 0 })
  const win = page.locator(`[data-window-id="${w.id}"]`)
  const other = page.locator(`[data-window-id="${n.id}"]`)
  for (let i = 0; i < 4; i++) {
    const b = await win.locator('.win-move').boundingBox()
    await page.mouse.click(b.x + 80, b.y + 2 + 10 * 0) // grip edge
    await win.click({ position: { x: 20, y: 150 }, force: true })
    await other.click({ position: { x: 20, y: 120 }, force: true })
    await win.dispatchEvent('pointerdown')
  }
  await sleep(800)
  expect(await frame.evaluate((el) => el.__mark)).toBe('same')
  expect(await page.evaluate(() => window.__loads)).toBe(0)
  expect(grain.consoleErrors).toEqual([])
})

test('clicking a web widget does not reload its webview', async ({ grain }) => {
  const { page, api, backend } = grain
  const s = (await spaces(grain))[0]
  const w = await api(`/canvases/${s.id}/windows`, { method: 'POST', body: { kind: 'web', x: 40, y: 40, w: 500, h: 360, config: { url: backend.url + '/health' } } })
  const other = await api(`/canvases/${s.id}/windows`, { method: 'POST', body: { kind: 'face', x: 600, y: 40, w: 200, h: 200 } })
  await page.reload()
  await enterCanvas(grain)
  const wv = page.locator(`[data-window-id="${w.id}"] webview`)
  await expect(wv).toBeAttached()
  await sleep(2500)
  await wv.evaluate((el) => {
    el.__mark = 'same'
    window.__wvLoads = 0
    el.addEventListener('did-start-loading', () => window.__wvLoads++)
  })
  for (let i = 0; i < 4; i++) {
    await page.locator(`[data-window-id="${other.id}"]`).click({ position: { x: 20, y: 100 }, force: true })
    await page.locator(`[data-window-id="${w.id}"] .win-move`).click({ position: { x: 100, y: 3 }, force: true })
    await wv.click({ force: true })
  }
  await sleep(800)
  expect(await wv.evaluate((el) => el.__mark)).toBe('same')
  expect(await page.evaluate(() => window.__wvLoads)).toBe(0)
  expect(grain.consoleErrors).toEqual([])
})
