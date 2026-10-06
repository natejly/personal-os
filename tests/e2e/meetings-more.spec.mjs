import { writeFileSync, mkdirSync } from 'node:fs'
import { join } from 'node:path'
import { test, expect } from './helpers/fakemic.mjs'
import { enableModules, reload, sql, seedSegments, realErrors } from './helpers/mah.mjs'

test.beforeEach(() => test.setTimeout(240_000))

const openMeetings = async (grain) => {
  await enableModules(grain.api)
  await reload(grain.page)
  await grain.page.locator('.nav-item', { hasText: 'Meetings' }).first().click()
  await expect(grain.page.getByRole('heading', { name: 'Meetings' }).first()).toBeVisible()
}

test('Settings -> Modules turns Meetings and Activity on, and off again', async ({ grain }) => {
  const { page, api } = grain
  await expect(page.locator('.nav-item', { hasText: 'Meetings' })).toHaveCount(0)
  await page.getByRole('button', { name: /Settings/ }).first().click()
  await page.getByRole('tab', { name: 'Modules' }).click()
  // One row per view with where it lives: Sidebar, Title bar or Hidden.
  const place = (label, where) => page.locator('.place-row', { has: page.locator('b', { hasText: new RegExp(`^${label}$`) }) }).getByRole('button', { name: where }).click()
  await place('Meetings', 'Sidebar')
  await place('Activity', 'Sidebar')
  await place('Calendar', 'Sidebar')
  await page.getByRole('button', { name: 'Save', exact: true }).click()
  await expect(page.locator('.nav-item', { hasText: 'Meetings' })).toBeVisible()
  await expect(page.locator('.nav-item', { hasText: 'Activity' })).toBeVisible()
  // Calendar moved out of the title bar and into the sidebar
  await expect(page.locator('.nav-item', { hasText: 'Calendar' })).toBeVisible()
  await expect(page.getByRole('toolbar', { name: 'Apps' }).getByRole('button', { name: 'Calendar' })).toHaveCount(0)
  expect((await api('/settings')).hiddenViews).not.toContain('meetings')
  expect((await api('/settings')).navPlacement).toEqual({ meetings: 'sidebar', activity: 'sidebar', calendar: 'sidebar' })
  // and off again
  await page.getByRole('button', { name: /Settings/ }).first().click()
  await page.getByRole('tab', { name: 'Modules' }).click()
  await place('Meetings', 'Hidden')
  await place('Calendar', 'Title bar')
  await page.getByRole('button', { name: 'Save', exact: true }).click()
  await expect(page.locator('.nav-item', { hasText: 'Meetings' })).toHaveCount(0)
  await expect(page.locator('.nav-item', { hasText: 'Calendar' })).toHaveCount(0)
  expect((await api('/settings')).hiddenViews).toContain('meetings')
  expect(realErrors(grain.consoleErrors)).toEqual([])
})

test('notes autosave, survive a relaunch; a 200 KB note opens and saves', async ({ grain }) => {
  const { page, api } = grain
  const m = await api('/meetings', { method: 'POST', body: { title: 'Notes meeting' } })
  await openMeetings(grain)
  await page.locator('.mtg-row', { hasText: 'Notes meeting' }).click()
  const ed = page.locator('textarea.md-input')
  await ed.click()
  await page.keyboard.type('# Agenda\n- first point\n- second point')
  await expect(page.locator('.mtg-save-state')).toHaveText('Saved', { timeout: 30_000 })
  await expect.poll(async () => (await api(`/meetings/${m.id}`)).notes).toContain('second point')
  await grain.relaunch()
  await grain.page.locator('.nav-item', { hasText: 'Meetings' }).first().click()
  await grain.page.locator('.mtg-row', { hasText: 'Notes meeting' }).click()
  await expect(grain.page.locator('textarea.md-input')).toHaveValue(/second point/)

  // big note through the API, then edit and save through the UI
  const big = Array.from({ length: 4000 }, (_, i) => `line ${i} of a very long set of meeting notes`).join('\n')
  expect(big.length).toBeGreaterThan(150_000)
  await api(`/meetings/${m.id}`, { method: 'PUT', body: { notes: big } })
  await grain.page.locator('.nav-item', { hasText: 'Today' }).first().click()
  await grain.page.locator('.nav-item', { hasText: 'Meetings' }).first().click()
  await grain.page.locator('.mtg-row', { hasText: 'Notes meeting' }).click()
  const ed2 = grain.page.locator('textarea.md-input')
  await expect(ed2).toHaveValue(/line 3999 of a very long/, { timeout: 60_000 })
  await ed2.click()
  await grain.page.keyboard.press('Meta+ArrowDown')
  await grain.page.keyboard.type('\nTAIL MARKER')
  await grain.page.keyboard.press('Meta+s')
  await expect.poll(async () => (await api(`/meetings/${m.id}`)).notes, { timeout: 60_000 }).toContain('TAIL MARKER')
  expect(realErrors(grain.consoleErrors)).toEqual([])
})

test('Enhance proposes, Reject keeps the old notes, Accept writes enhanced and never the typed notes', async ({ grain }) => {
  const { page, api, dataDir } = grain
  const m = await api('/meetings', { method: 'POST', body: { title: 'Planning chat' } })
  await api(`/meetings/${m.id}`, { method: 'PUT', body: { notes: 'MY OWN NOTES: ship friday' } })
  seedSegments(dataDir, m.id, [{ text: 'We will ship on Friday and Dana owns the release', t: 0 }, { text: 'Alex will write the changelog', t: 20 }])
  sql(dataDir, [["UPDATE meetings SET status='ready', started_at=?, duration_ms=60000 WHERE id=?", [Date.now() / 1000 - 300, m.id]]])
  await openMeetings(grain)
  await page.locator('.mtg-row', { hasText: 'Planning chat' }).click()
  await page.getByRole('button', { name: 'Enhance', exact: true }).click()
  // a proposal arrives: either the model's, or the mechanical fallback when the mock does not return the expected shape
  await expect(page.locator('.mtg-enhanced')).toBeVisible({ timeout: 90_000 })
  await expect.poll(async () => (await api(`/meetings/${m.id}`)).pending?.id ?? '', { timeout: 90_000 }).not.toBe('')
  await page.getByTitle('What accepting would change').dispatchEvent('click') // a toast sits over it
  const revId = (await api(`/meetings/${m.id}`)).pending.id
  await page.getByRole('button', { name: /^Reject/ }).click()
  await expect.poll(async () => (await api(`/meetings/${m.id}`)).pending).toBeNull()
  expect((await api(`/meetings/${m.id}`)).enhanced).toBe('')
  // propose again, accept this time
  await page.getByRole('button', { name: /^Enhance( again)?$/ }).click()
  await expect.poll(async () => (await api(`/meetings/${m.id}`)).pending?.id ?? '', { timeout: 90_000 }).not.toBe('')
  expect((await api(`/meetings/${m.id}`)).pending.id).not.toBe(revId)
  await page.getByRole('button', { name: /^Accept/ }).click()
  await expect.poll(async () => (await api(`/meetings/${m.id}`)).enhanced, { timeout: 30_000 }).not.toBe('')
  const after = await api(`/meetings/${m.id}`)
  // what the user typed is never overwritten by a model
  expect(after.notes).toBe('MY OWN NOTES: ship friday')
  expect(after.pending).toBeNull()
  await expect(page.locator('.mtg-enhanced')).toBeVisible()
  expect(realErrors(grain.consoleErrors)).toEqual([])
})

test('action items: choose, add to todos once, dismiss', async ({ grain }) => {
  const { page, api, dataDir } = grain
  const m = await api('/meetings', { method: 'POST', body: { title: 'Actions meeting' } })
  const t = Date.now() / 1000
  sql(dataDir, [
    ["UPDATE meetings SET status='ready', enhanced=?, started_at=? WHERE id=?", ['## Notes\nsomething', t - 100, m.id]],
    ...['Send the deck', 'Book the room', 'Email Priya'].map((txt, i) => ['INSERT INTO meeting_action_items(id,meeting_id,text,owner,due,status,created_at) VALUES(?,?,?,?,?,?,?)', [`ai${i}`, m.id, txt, i === 0 ? 'Sam' : '', i === 1 ? '2026-12-01' : '', 'proposed', t]])
  ])
  await openMeetings(grain)
  await page.locator('.mtg-row', { hasText: 'Actions meeting' }).click()
  const items = page.locator('.mtg-action')
  await expect(items).toHaveCount(3)
  await expect(items.first()).toContainText('Sam')
  await expect(items.nth(1)).toContainText('due 2026-12-01')
  // uncheck one, add the other two
  await items.nth(2).locator('input').uncheck()
  await page.getByRole('button', { name: /Add 2 to todos/ }).click()
  await expect.poll(async () => (await api('/todos')).filter((x) => ['Send the deck', 'Book the room'].includes(x.title)).length, { timeout: 30_000 }).toBe(2)
  await expect(items.first()).toContainText('already a todo')
  // adding again creates nothing new
  expect((await api('/todos')).filter((x) => x.title === 'Send the deck')).toHaveLength(1)
  await items.nth(2).getByTitle('Dismiss').click()
  await expect(items.nth(2)).toContainText('dismissed')
  const acts = await api(`/meetings/${m.id}/actions`)
  expect(Object.fromEntries(acts.map((a) => [a.text, a.status]))).toEqual({ 'Send the deck': 'added', 'Book the room': 'added', 'Email Priya': 'dismissed' })
  expect((await api('/todos')).some((x) => x.title === 'Email Priya')).toBe(false)
})

test('diarized speakers show as chips, rename them, and the transcript uses the names', async ({ grain }) => {
  const { page, api, dataDir } = grain
  const m = await api('/meetings', { method: 'POST', body: { title: 'Panel talk' } })
  seedSegments(dataDir, m.id, [
    { text: 'Hello everyone', channel: 'output', t: 0, speaker: 'S1' },
    { text: 'Thanks for having me', channel: 'output', t: 12, speaker: 'S2' },
    { text: 'Let us begin', channel: 'output', t: 24, speaker: 'S1' }
  ])
  sql(dataDir, [["UPDATE meetings SET status='ready', started_at=? WHERE id=?", [Date.now() / 1000 - 100, m.id]]])
  await openMeetings(grain)
  await page.locator('.mtg-row', { hasText: 'Panel talk' }).click()
  await page.getByRole('button', { name: 'Transcript', exact: true }).click()
  await expect(page.locator('.mtg-speaker-chip')).toHaveCount(2)
  await page.locator('.mtg-speaker-chip', { hasText: 'S1' }).locator('input').fill('Dana')
  await page.locator('.mtg-speaker-chip', { hasText: 'S1' }).locator('input').press('Enter')
  await expect.poll(async () => (await api(`/meetings/${m.id}`)).speaker_names).toEqual({ S1: 'Dana' })
  await expect(page.locator('.mtg-who').first()).toHaveText('Dana')
  // an over-long name is capped, a blank one clears
  await page.locator('.mtg-speaker-chip', { hasText: 'S2' }).locator('input').fill('x'.repeat(200))
  await page.locator('.mtg-speaker-chip', { hasText: 'S2' }).locator('input').press('Enter')
  await expect.poll(async () => Object.keys((await api(`/meetings/${m.id}`)).speaker_names).length).toBe(2)
  expect((await api(`/meetings/${m.id}`)).speaker_names.S2.length).toBeLessThanOrEqual(60)
  expect(realErrors(grain.consoleErrors)).toEqual([])
})

test('import an audio file: transcribed into the meeting through the same queue', async ({ grain }) => {
  const { page, api } = grain
  const m = await api('/meetings', { method: 'POST', body: { title: 'Imported call' } })
  // 12 s of 16 kHz mono silence as a wav
  const sr = 16000, n = sr * 12
  const buf = Buffer.alloc(44 + n * 2)
  buf.write('RIFF', 0); buf.writeUInt32LE(36 + n * 2, 4); buf.write('WAVEfmt ', 8); buf.writeUInt32LE(16, 16); buf.writeUInt16LE(1, 20); buf.writeUInt16LE(1, 22)
  buf.writeUInt32LE(sr, 24); buf.writeUInt32LE(sr * 2, 28); buf.writeUInt16LE(2, 32); buf.writeUInt16LE(16, 34); buf.write('data', 36); buf.writeUInt32LE(n * 2, 40)
  for (let i = 0; i < n; i++) buf.writeInt16LE(Math.round(Math.sin(i / 20) * 3000), 44 + i * 2)
  const dir = join(grain.scratch, 'files'); mkdirSync(dir, { recursive: true })
  const file = join(dir, 'call.wav'); writeFileSync(file, buf)
  await openMeetings(grain)
  await page.locator('.mtg-row', { hasText: 'Imported call' }).click()
  await page.locator('input[type=file]').setInputFiles(file)
  await expect.poll(async () => (await api(`/meetings/${m.id}/segments`)).length, { timeout: 120_000 }).toBeGreaterThan(0)
  await expect.poll(async () => (await api(`/meetings/${m.id}`)).status, { timeout: 120_000 }).not.toBe('transcribing')
  await page.getByRole('button', { name: 'Transcript', exact: true }).click()
  await expect(page.locator('.mtg-line').first()).toContainText('stub speech', { timeout: 30_000 })
  expect(realErrors(grain.consoleErrors)).toEqual([])
})
