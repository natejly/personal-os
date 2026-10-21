import { test, expect } from './fixtures.mjs'
import { enterCanvas, menuClick, sleep, spaces, windowsOf } from './helpers/spaces.mjs'

test.describe.configure({ timeout: 300_000 })

const chatRow = (page, title) => page.locator('.convo-item', { hasText: title })

async function mkChat(g, title) {
  const c = await g.api('/conversations', { method: 'POST', body: { title } })
  return c
}

test('drag a chat from the sidebar onto the canvas opens a chat window; dragging it again focuses, not duplicates', async ({ grain }) => {
  const { page, api } = grain
  const s = (await spaces(grain))[0]
  const c1 = await mkChat(grain, 'Alpha chat')
  await mkChat(grain, 'Beta chat')
  await page.reload()
  await enterCanvas(grain)
  const canvas = page.locator('.canvas')
  await chatRow(page, 'Alpha chat').dragTo(canvas, { targetPosition: { x: 260, y: 160 } })
  await expect.poll(async () => (await windowsOf(grain, s.id)).filter((w) => w.kind === 'chat').length).toBe(1)
  const w = (await windowsOf(grain, s.id)).find((x) => x.kind === 'chat')
  expect(w.ref_id).toBe(c1.id)
  await expect(page.locator(`[data-window-id="${w.id}"]`)).toBeVisible()
  // dropped near the cursor, not stacked at the origin (grid snapped)
  expect(w.x).toBeGreaterThan(100)

  await chatRow(page, 'Alpha chat').dragTo(canvas, { targetPosition: { x: 400, y: 300 } })
  await sleep(1200)
  expect((await windowsOf(grain, s.id)).filter((x) => x.kind === 'chat')).toHaveLength(1)

  // a second chat gets its own window
  await chatRow(page, 'Beta chat').dragTo(canvas, { targetPosition: { x: 700, y: 300 } })
  await expect.poll(async () => (await windowsOf(grain, s.id)).filter((x) => x.kind === 'chat').length).toBe(2)

  // dropping on a locked space is refused
  await menuClick(grain.app, 'Lock / Unlock Canvas')
  await expect.poll(async () => !!(await api('/canvases/' + s.id)).locked).toBe(true)
  await mkChat(grain, 'Gamma chat')
  await page.reload()
  await enterCanvas(grain)
  await chatRow(page, 'Gamma chat').dragTo(page.locator('.canvas'), { targetPosition: { x: 600, y: 420 } })
  await sleep(1200)
  expect((await windowsOf(grain, s.id)).filter((x) => x.kind === 'chat')).toHaveLength(2)
  expect(grain.consoleErrors).toEqual([])
})

test('drop a chat on the sidebar canvas row adds it to the canvas', async ({ grain }) => {
  const { page } = grain
  const s1 = (await spaces(grain))[0]
  await mkChat(grain, 'Alpha chat')
  await page.reload()
  await enterCanvas(grain)
  await chatRow(page, 'Alpha chat').dragTo(page.locator('.space-row'))
  await expect.poll(async () => (await windowsOf(grain, s1.id)).length).toBe(1)
  await expect(page.locator('.space-tab.active')).toContainText(s1.name)
  expect(grain.consoleErrors).toEqual([])
})

test('a chat window sends a message and streams a reply, and has no face view', async ({ grain }) => {
  const { page, api } = grain
  const s = (await spaces(grain))[0]
  const c = await mkChat(grain, 'Windowed')
  const w = await api(`/canvases/${s.id}/windows`, { method: 'POST', body: { kind: 'chat', ref_id: c.id, x: 64, y: 48, w: 520, h: 560 } })
  await page.reload()
  await enterCanvas(grain)
  const win = page.locator(`[data-window-id="${w.id}"]`)
  await expect(win).toBeVisible()
  const box = win.getByRole('textbox', { name: 'Message' })
  await box.fill('!!reply Hello from a window')
  await box.press('Enter')
  await expect(win.locator('.msg.assistant').last()).toContainText('Hello from a window', { timeout: 40_000 })

  await expect(win.getByTitle('Shrink to a face')).toHaveCount(0)
  await grain.relaunch()
  await enterCanvas(grain)
  await expect(grain.page.locator(`[data-window-id="${w.id}"]`).locator('.msg.assistant').last()).toContainText('Hello from a window')
  expect(grain.consoleErrors).toEqual([])
})

test('faces: one creature per thread, stable across relaunch', async ({ grain }) => {
  const { page, api } = grain
  const sA = (await spaces(grain))[0]
  await mkChat(grain, 'Thread one')
  await mkChat(grain, 'Thread two')
  await mkChat(grain, 'Thread three')
  await page.reload()
  await enterCanvas(grain)
  const snap = async (p) => ({
    chats: await p.locator('.convo-item .face').evaluateAll((els) => els.map((e) => e.outerHTML))
  })
  const before = await snap(page)
  expect(before.chats).toHaveLength(3)
  // every thread draws its own creature
  expect(new Set(before.chats).size).toBe(3)
  await grain.relaunch()
  await enterCanvas(grain)
  const after = await snap(grain.page)
  expect(after).toEqual(before)
  void sA
  expect(grain.consoleErrors).toEqual([])
})

test('streaming chat window in a canvas keeps working after leaving the canvas view and coming back', async ({ grain }) => {
  const { page, api } = grain
  const s = (await spaces(grain))[0]
  const c = await mkChat(grain, 'Slowpoke')
  const w = await api(`/canvases/${s.id}/windows`, { method: 'POST', body: { kind: 'chat', ref_id: c.id, x: 64, y: 48, w: 520, h: 560 } })
  await page.reload()
  await enterCanvas(grain)
  const win = page.locator(`[data-window-id="${w.id}"]`)
  const box = win.getByRole('textbox', { name: 'Message' })
  await box.fill('!!slow 4000')
  await box.press('Enter')
  await menuClick(grain.app, 'Toggle Canvases') // leave the canvas view while the reply streams
  await expect(page.locator('.spaces-bar')).toHaveCount(0)
  await enterCanvas(grain)
  await expect(win.locator('.msg.assistant').last()).toBeVisible({ timeout: 40_000 })
  await expect(win.locator('.msg.assistant').last()).not.toBeEmpty()
  expect(grain.consoleErrors).toEqual([])
})
