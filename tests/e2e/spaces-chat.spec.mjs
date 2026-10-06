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
  await menuClick(grain.app, 'Lock / Unlock Space')
  await expect.poll(async () => !!(await api('/canvases/' + s.id)).locked).toBe(true)
  await mkChat(grain, 'Gamma chat')
  await page.reload()
  await enterCanvas(grain)
  await chatRow(page, 'Gamma chat').dragTo(page.locator('.canvas'), { targetPosition: { x: 600, y: 420 } })
  await sleep(1200)
  expect((await windowsOf(grain, s.id)).filter((x) => x.kind === 'chat')).toHaveLength(2)
  expect(grain.consoleErrors).toEqual([])
})

test('drop a chat on a sidebar space row adds it to that space without switching', async ({ grain }) => {
  const { page, api } = grain
  const s1 = (await spaces(grain))[0]
  const s2 = await api('/canvases', { method: 'POST', body: { name: 'Elsewhere' } })
  await mkChat(grain, 'Alpha chat')
  await page.reload()
  await enterCanvas(grain)
  await chatRow(page, 'Alpha chat').dragTo(page.locator('.space-row', { hasText: 'Elsewhere' }))
  await expect.poll(async () => (await windowsOf(grain, s2.id)).length).toBe(1)
  expect((await windowsOf(grain, s1.id))).toHaveLength(0)
  await expect(page.locator('.space-tab.active')).toContainText(s1.name)
  expect(grain.consoleErrors).toEqual([])
})

test('Send to space from a chat view: existing space, then new space, then "already in"', async ({ grain }) => {
  const { page, api } = grain
  const s = (await spaces(grain))[0]
  const c = await mkChat(grain, 'Sendable')
  await page.reload()
  await page.waitForSelector('.sidebar')
  await chatRow(page, 'Sendable').click()
  const send = page.getByRole('button', { name: 'Send to space' }).or(page.locator('button[title="Send to space"]')).first()
  await send.click()
  await page.locator('.send-row', { hasText: s.name }).click()
  await expect.poll(async () => (await windowsOf(grain, s.id)).filter((w) => w.ref_id === c.id).length).toBe(1)
  await expect(page.getByText(`Sent to "${s.name}"`)).toBeVisible()
  // second send focuses instead of duplicating
  await send.click()
  await page.locator('.send-row', { hasText: s.name }).click()
  await expect(page.getByText(`Already in "${s.name}"`)).toBeVisible()
  expect((await windowsOf(grain, s.id)).filter((w) => w.ref_id === c.id)).toHaveLength(1)
  // new space
  await send.click()
  await page.locator('.send-row.new').click()
  await expect.poll(async () => (await spaces(grain)).length).toBe(2)
  const made = (await spaces(grain)).find((x) => x.id !== s.id)
  await expect.poll(async () => (await windowsOf(grain, made.id)).length).toBe(1)
  expect(grain.consoleErrors).toEqual([])
})

test('a chat window sends a message, streams a reply, and the face view toggles and persists', async ({ grain }) => {
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

  // flip to the face view: transcript gone, creature shown; flip back: transcript intact
  const before = (await windowsOf(grain, s.id))[0]
  await win.getByTitle('Shrink to a face').click()
  await expect(win.locator('.chat-blob .face')).toBeVisible()
  await expect(win.locator('.msg')).toHaveCount(0)
  await expect.poll(async () => (await windowsOf(grain, s.id))[0].config.blob).toBe(true)
  await grain.relaunch()
  await enterCanvas(grain)
  const win2 = grain.page.locator(`[data-window-id="${w.id}"]`)
  await expect(win2.locator('.chat-blob .face')).toBeVisible()
  // the face is the same creature after relaunch (keyed on the conversation id)
  await win2.locator('.chat-blob').click()
  await expect(win2.locator('.msg.assistant').last()).toContainText('Hello from a window')
  await expect.poll(async () => (await windowsOf(grain, s.id))[0].config.blob).toBe(false)
  // grows back to exactly the rect it had before folding
  await expect.poll(async () => {
    const a = (await windowsOf(grain, s.id))[0]
    return [a.x, a.y, a.w, a.h]
  }).toEqual([before.x, before.y, before.w, before.h])
  expect(grain.consoleErrors).toEqual([])
})

test('faces: one creature per thread, one shared colour per space, stable across relaunch', async ({ grain }) => {
  const { page, api } = grain
  const sA = (await spaces(grain))[0]
  await api('/canvases', { method: 'POST', body: { name: 'Second' } })
  await api('/canvases', { method: 'POST', body: { name: 'Third' } })
  await mkChat(grain, 'Thread one')
  await mkChat(grain, 'Thread two')
  await mkChat(grain, 'Thread three')
  await page.reload()
  await enterCanvas(grain)
  const snap = async (p) => ({
    spaces: await p.locator('.space-row .face').evaluateAll((els) => els.map((e) => e.outerHTML)),
    chats: await p.locator('.convo-item .face').evaluateAll((els) => els.map((e) => e.outerHTML))
  })
  const before = await snap(page)
  expect(before.spaces).toHaveLength(3)
  expect(before.chats).toHaveLength(3)
  // every thread draws its own creature
  expect(new Set(before.chats).size).toBe(3)
  // every space draws its own silhouette ...
  expect(new Set(before.spaces).size).toBe(3)
  // ... in one shared colour (SPACE_HUE): the fill colours used are identical across spaces
  const colours = await page.locator('.space-row .face').evaluateAll((els) =>
    els.map((e) => [...new Set([...e.querySelectorAll('*')].flatMap((n) => [n.getAttribute('fill'), n.getAttribute('stroke'), n.style.fill, n.style.stroke]).filter(Boolean))].sort().join('|')))
  console.log('space face colours', JSON.stringify(colours))
  expect(new Set(colours).size).toBe(1)
  await grain.relaunch()
  await enterCanvas(grain)
  const after = await snap(grain.page)
  expect(after).toEqual(before)
  void sA
  expect(grain.consoleErrors).toEqual([])
})

test('streaming chat window in a space keeps working after a space switch and back', async ({ grain }) => {
  const { page, api } = grain
  const s = (await spaces(grain))[0]
  const other = await api('/canvases', { method: 'POST', body: { name: 'Other' } })
  const c = await mkChat(grain, 'Slowpoke')
  const w = await api(`/canvases/${s.id}/windows`, { method: 'POST', body: { kind: 'chat', ref_id: c.id, x: 64, y: 48, w: 520, h: 560 } })
  await page.reload()
  await enterCanvas(grain)
  const win = page.locator(`[data-window-id="${w.id}"]`)
  const box = win.getByRole('textbox', { name: 'Message' })
  await box.fill('!!slow 4000')
  await box.press('Enter')
  await page.locator('.space-tab', { hasText: 'Other' }).click()
  await expect(page.locator('.space-tab.active')).toContainText('Other')
  await page.locator('.space-tab', { hasText: s.name }).click()
  await expect(win.locator('.msg.assistant').last()).toBeVisible({ timeout: 40_000 })
  await expect(win.locator('.msg.assistant').last()).not.toBeEmpty()
  void other
  expect(grain.consoleErrors).toEqual([])
})
