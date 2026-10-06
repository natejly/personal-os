import { test, expect } from './fixtures.mjs'
import { actionsBtn, enterCanvas, spaces, windowsOf } from './helpers/spaces.mjs'

test.describe.configure({ timeout: 300_000 })

const presetsBtn = (page) => page.locator('.spaces-bar button[title="Presets"]')

test('save a space as a preset, apply it into a new space, rename and delete it', async ({ grain }) => {
  const { page, api } = grain
  page.on('dialog', (d) => void d.accept())
  const s = (await spaces(grain))[0]
  await api('/canvases/' + s.id, { method: 'PUT', body: { snap_mode: 'grid', grid_size: 24, zoom: 1.5, pan_x: 30, pan_y: -20 } })
  const n = (await api('/notes', { method: 'POST', body: { body: 'keep me' } })).id
  await api(`/canvases/${s.id}/windows`, { method: 'POST', body: { kind: 'note', ref_id: n, x: 144, y: 96, w: 312, h: 240 } })
  await api(`/canvases/${s.id}/windows`, { method: 'POST', body: { kind: 'todos', x: 480, y: 96, w: 420, h: 360 } })
  await api(`/canvases/${s.id}/windows`, { method: 'POST', body: { kind: 'face', x: 96, y: 400, w: 180, h: 180 } })
  await page.reload()
  await enterCanvas(grain)

  await presetsBtn(page).click()
  await expect(page.getByText('No presets yet')).toBeVisible()
  const input = page.getByPlaceholder('Preset name')
  await expect(input).toHaveValue(s.name)
  await input.fill('Morning layout')
  await page.getByRole('button', { name: 'Save', exact: true }).click()
  await expect.poll(async () => (await api('/canvas-presets')).length).toBe(1)
  const [preset] = await api('/canvas-presets')
  expect(preset.name).toBe('Morning layout')
  expect(preset.windows).toHaveLength(3)

  // saving twice keeps two presets (names are not unique), a blank name falls back to the space name
  await presetsBtn(page).click()
  await page.getByPlaceholder('Preset name').fill('')
  await page.getByRole('button', { name: 'Save', exact: true }).click()
  await expect.poll(async () => (await api('/canvas-presets')).length).toBe(2)
  expect((await api('/canvas-presets')).map((p) => p.name).sort()).toEqual(['Morning layout', s.name].sort())

  // apply: a NEW space appears with the same layout and snapping, the original is untouched
  await presetsBtn(page).click()
  await page.getByRole('button', { name: 'Morning layout', exact: true }).click()
  await expect.poll(async () => (await spaces(grain)).length).toBe(2)
  const all = await spaces(grain)
  const made = all.find((c) => c.id !== s.id)
  expect(made.snap_mode).toBe('grid')
  expect(made.grid_size).toBe(24)
  expect(made.zoom).toBe(1.5)
  const ws = await windowsOf(grain, made.id)
  expect(ws.map((w) => w.kind).sort()).toEqual(['face', 'note', 'todos'])
  const note = ws.find((w) => w.kind === 'note')
  expect([note.x, note.y, note.w, note.h]).toEqual([144, 96, 312, 240])
  expect((await windowsOf(grain, s.id))).toHaveLength(3)
  // the new space is the active one and its windows render
  await expect(page.locator('.win')).toHaveCount(3)

  // rename + delete from the popover
  await presetsBtn(page).click()
  await page.locator('.preset-row', { hasText: 'Morning layout' }).hover()
  await page.locator('.preset-row', { hasText: 'Morning layout' }).getByTitle('Rename preset').click()
  const rn = page.locator('.preset-row input')
  await rn.fill('Evening layout')
  await rn.press('Enter')
  await expect.poll(async () => (await api('/canvas-presets')).map((p) => p.name)).toContain('Evening layout')
  await page.locator('.preset-row', { hasText: 'Evening layout' }).getByTitle('Delete preset').click()
  await expect.poll(async () => (await api('/canvas-presets')).length).toBe(1)
  await expect(page.locator('.preset-row', { hasText: 'Evening layout' })).toHaveCount(0)
  await page.keyboard.press('Escape')
  await expect(page.locator('.preset-pop')).toHaveCount(0)
  expect(grain.consoleErrors).toEqual([])
})

test('a preset survives its content: deleted notes are skipped, the space still opens; sidebar menu saves too', async ({ grain }) => {
  const { page, api } = grain
  const s = (await spaces(grain))[0]
  const n = await api('/notes', { method: 'POST', body: { body: 'gone soon' } })
  await api(`/canvases/${s.id}/windows`, { method: 'POST', body: { kind: 'note', ref_id: n.id, x: 100, y: 100, w: 300, h: 240 } })
  await api(`/canvases/${s.id}/windows`, { method: 'POST', body: { kind: 'todos', x: 440, y: 100, w: 420, h: 360 } })
  const p = await api('/canvas-presets', { method: 'POST', body: { canvas_id: s.id, name: 'Fragile' } })
  await api('/notes/' + n.id, { method: 'DELETE' })
  await page.reload()
  await enterCanvas(grain)
  await presetsBtn(page).click()
  await page.getByRole('button', { name: 'Fragile', exact: true }).click()
  await expect.poll(async () => (await spaces(grain)).length).toBe(2)
  const made = (await spaces(grain)).find((c) => c.id !== s.id)
  await expect.poll(async () => (await windowsOf(grain, made.id)).map((w) => w.kind)).toEqual(['todos'])
  await expect(page.getByText(/1 window skipped/)).toBeVisible()

  // save from the sidebar row's menu
  await actionsBtn(page, s.name).click()
  await page.getByRole('menuitem', { name: 'Save as preset…' }).click()
  await page.getByPlaceholder('Preset name').fill('From sidebar')
  await page.getByRole('button', { name: 'Save', exact: true }).click()
  await expect.poll(async () => (await api('/canvas-presets')).map((x) => x.name)).toContain('From sidebar')
  void p
  expect(grain.consoleErrors).toEqual([])
})

test('presets: 40 saved presets list and apply; instantiating a missing preset errors cleanly', async ({ grain }) => {
  const { api } = grain
  const s = (await spaces(grain))[0]
  for (let i = 0; i < 40; i++) await api('/canvas-presets', { method: 'POST', body: { canvas_id: s.id, name: `P${i}` } })
  expect(await api('/canvas-presets')).toHaveLength(40)
  const r = await api('/canvas-presets/nope/instantiate', { method: 'POST', body: {}, raw: true })
  expect(r.status).toBe(404)
  const r2 = await api('/canvas-presets', { method: 'POST', body: { canvas_id: 'nope', name: 'x' }, raw: true })
  expect(r2.status).toBe(404)
  const r3 = await api('/canvas-presets/' + (await api('/canvas-presets'))[0].id, { method: 'PUT', body: { name: '   ' }, raw: true })
  expect(r3.status).toBe(400)
})
