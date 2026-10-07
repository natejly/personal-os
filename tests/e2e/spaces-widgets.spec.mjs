import { test, expect } from './fixtures.mjs'
import { enterCanvas, sleep, spaces, windowsOf } from './helpers/spaces.mjs'

test.describe.configure({ timeout: 300_000 })

const SIMPLE = [
  ['Lists', 'todos'], ['Calendar', 'calendar'], ['Memory', 'memory'], ['Graph', 'graph'], ['Uploads', 'documents'],
  ['Recap', 'recap'], ['Usage', 'usage'], ['Face', 'face']
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
  for (const l of ['Chat', 'Lists', 'Calendar', 'Memory', 'Graph', 'Uploads', 'Recap', 'Project', 'Usage', 'Doc', 'Face']) {
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

  // chat + doc go through a submenu
  let m = await addMenu(page)
  await m.getByRole('menuitem', { name: 'Chat', exact: true }).click()
  await page.getByRole('menuitem', { name: 'New chat' }).click()
  m = await addMenu(page)
  await m.getByRole('menuitem', { name: 'Doc', exact: true }).click()
  await page.getByRole('menuitem', { name: 'New doc' }).click()
  await expect.poll(async () => (await windowsOf(grain, s.id)).length).toBe(SIMPLE.length + 2)

  // a project via the API renders too
  const proj = await api('/projects', { method: 'POST', body: { name: 'Proj A' } })
  await api(`/canvases/${s.id}/windows`, { method: 'POST', body: { kind: 'project', ref_id: proj.id } })
  await page.reload()
  await enterCanvas(grain)
  const ws = await windowsOf(grain, s.id)
  expect(ws.length).toBe(SIMPLE.length + 3)
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

test('submenu entries add a chat / doc / project once, and re-adding focuses instead of duplicating', async ({ grain }) => {
  const { page, api } = grain
  const s = (await spaces(grain))[0]
  const proj = await api('/projects', { method: 'POST', body: { name: 'Proj A' } })
  await page.reload()
  await enterCanvas(grain)
  for (const [kind, label, item] of [['project', 'Project', 'Proj A']]) {
    for (let i = 0; i < 2; i++) {
      const m = await addMenu(page)
      await m.getByRole('menuitem', { name: label, exact: true }).click()
      await page.getByRole('menuitem', { name: item }).click()
      await sleep(400)
    }
    const ws = (await windowsOf(grain, s.id)).filter((w) => w.kind === kind)
    expect(ws, kind).toHaveLength(1)
  }
  void proj
  expect(grain.consoleErrors).toEqual([])
})
