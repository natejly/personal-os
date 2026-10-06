import { test, expect } from './helpers/fakemic.mjs'
import { enableModules, reload, realErrors, small } from './helpers/mah.mjs'

test.beforeEach(() => test.setTimeout(240_000))

const openDoc = async (grain, title) => {
  await enableModules(grain.api)
  await reload(grain.page)
  await grain.page.locator('.nav-item', { hasText: 'Files' }).first().click()
  await grain.page.getByText(title, { exact: true }).first().click()
  await expect(grain.page.getByRole('button', { name: 'Toggle side panel' })).toBeVisible()
}

test('double-clicking Record on a meeting or on a doc starts exactly one recording', async ({ grain }) => {
  const { page, api } = grain
  await enableModules(api)
  await reload(page)
  await page.locator('.nav-item', { hasText: 'Meetings' }).first().click()
  await page.getByRole('button', { name: 'Record', exact: true }).first().dblclick()
  await expect(page.locator('.mtg-bar')).toBeVisible({ timeout: 60_000 })
  expect((await api('/meetings?include_docs=true')).length).toBe(1)
  await page.locator('.mtg-bar').getByRole('button', { name: 'Stop' }).click()
  await expect(page.locator('.mtg-bar')).toHaveCount(0, { timeout: 120_000 })

  const doc = await api('/docs', { method: 'POST', body: { title: 'Twice doc', content: 'x' } })
  await page.locator('.nav-item', { hasText: 'Files' }).first().click()
  await page.getByText('Twice doc', { exact: true }).first().click()
  await page.getByRole('button', { name: 'Record', exact: true }).dblclick()
  await expect(page.locator('.dr-bar')).toBeVisible({ timeout: 60_000 })
  await page.waitForTimeout(2000)
  expect((await api(`/docs/${doc.id}/recordings`)).length).toBe(1)
  await page.locator('.dr-bar').getByRole('button', { name: 'Stop' }).dblclick()
  await expect(page.locator('.dr-bar.recording, .dr-bar.paused')).toHaveCount(0, { timeout: 120_000 })
  expect((await api('/meetings/status')).active).toBeNull()
  expect(realErrors(grain.consoleErrors)).toEqual([])
})

test('a second recording while one is live is refused with the reason, not started over the first', async ({ grain }) => {
  const { page, api } = grain
  const a = await api('/meetings', { method: 'POST', body: { title: 'First' } })
  const b = await api('/meetings', { method: 'POST', body: { title: 'Second' } })
  await api(`/meetings/${a.id}/start`, { method: 'POST' })
  const r = await api(`/meetings/${b.id}/start`, { method: 'POST', raw: true })
  expect(r.status).toBe(409)
  expect(JSON.stringify(await r.json())).toMatch(/Already recording|busy/i)
  expect((await api('/meetings/status')).active.meeting_id).toBe(a.id)
  // the doc path refuses the same way and leaves no stray doc behind
  const docsBefore = (await api('/docs')).length
  const d = await api('/docs', { method: 'POST', body: { title: 'Busy target', content: 'x' } })
  const r2 = await api(`/docs/${d.id}/recordings`, { method: 'POST', body: { mode: 'record' }, raw: true })
  expect(r2.status).toBe(409)
  expect((await api(`/docs/${d.id}/recordings`)).length).toBe(0)
  expect((await api('/docs')).length).toBe(docsBefore + 1)
  await api(`/meetings/${a.id}/stop`, { method: 'POST' })
  expect(page).toBeTruthy()
})

test('dictation commands: new line, scratch that and stop dictation act on the editor and are not typed', async ({ grain }) => {
  const { page, api, stt } = grain
  const doc = await api('/docs', { method: 'POST', body: { title: 'Command doc', content: '' } })
  await openDoc(grain, 'Command doc')
  const editor = page.locator('textarea.md-input')
  await editor.click()
  await page.getByRole('button', { name: 'Recording options' }).click()
  await page.getByRole('menuitem', { name: /Dictate into file/ }).click()
  await expect(page.locator('.dr-bar .dr-bar-mode')).toHaveText('Dictating', { timeout: 60_000 })
  // let the start-up clips drain, then queue the script (each clip is one line of it)
  await expect(editor).toHaveValue(/stub speech/i, { timeout: 60_000 })
  stt.next.length = 0
  for (const t of ['alpha line', 'new line', 'beta line', 'scratch that', 'gamma line']) stt.say(t)
  await expect(editor).toHaveValue(/Gamma line/, { timeout: 90_000 })
  const v = await editor.inputValue()
  // a clip that continues the previous sentence keeps its case; "new line" is a line break, not text
  expect(v).toMatch(/alpha line\s*\n+\s*Gamma line/i)
  expect(v).not.toMatch(/beta line/i) // scratched
  expect(v.toLowerCase()).not.toContain('scratch that')
  expect(v.toLowerCase()).not.toContain('new line')
  stt.say('stop dictation')
  await expect(page.locator('.dr-bar.recording, .dr-bar.paused')).toHaveCount(0, { timeout: 90_000 })
  expect((await api('/meetings/status')).active).toBeNull()
  expect(doc.id).toBeTruthy()
})

test('Settings -> Meetings self-test: passes with a working route, and says why it failed when it does not', async ({ grain }) => {
  const { page, api, stt } = grain
  await enableModules(api)
  await reload(page)
  await page.locator('.nav-item', { hasText: 'Meetings' }).first().click()
  await page.getByRole('button', { name: /Recorder, transcription and notes settings|thing.* need.* setting up/ }).click()
  await expect(page.getByText('What this machine can do')).toBeVisible()
  await page.getByRole('button', { name: 'Test transcription' }).click()
  await expect(page.locator('.mtg-selftest-msg')).toBeVisible({ timeout: 60_000 })
  await expect(page.locator('.mtg-selftest-msg')).toHaveClass(/ok|pass/)
  stt.fail = true
  await page.getByRole('button', { name: 'Test transcription' }).click()
  await expect(page.locator('.mtg-selftest-msg')).toContainText(/500|down|failed/i, { timeout: 60_000 })
  await expect(page.locator('.mtg-selftest-msg')).not.toHaveClass(/ok|pass/)
  // while the self-test fails, a recording is refused and the toast says why
  await page.keyboard.press('Escape')
  stt.fail = true
  const m = await api('/meetings', { method: 'POST', body: { title: 'Blocked' } })
  const r = await api(`/meetings/${m.id}/start`, { method: 'POST', raw: true })
  expect(r.status).toBe(409)
  expect(JSON.stringify(await r.json())).toContain('selftest')
  expect(realErrors(grain.consoleErrors)).toEqual([])
})

test('keep audio: a kept recording offers playback controls and its audio can be deleted, transcript kept', async ({ grain }) => {
  const { page, api } = grain
  await api('/meetings/config', { method: 'PUT', body: { keepAudio: true } })
  await enableModules(api)
  await reload(page)
  await page.locator('.nav-item', { hasText: 'Meetings' }).first().click()
  await page.getByRole('button', { name: 'Record', exact: true }).first().click()
  await expect(page.locator('.mtg-bar')).toBeVisible({ timeout: 60_000 })
  const id = (await api('/meetings/status')).active.meeting_id
  await expect.poll(async () => (await api(`/meetings/${id}/segments`)).filter((s) => s.state === 'done').length, { timeout: 60_000 }).toBeGreaterThan(0)
  await page.locator('.mtg-bar').getByRole('button', { name: 'Stop' }).click()
  await expect(page.locator('.mtg-bar')).toHaveCount(0, { timeout: 120_000 })
  await expect.poll(async () => (await api(`/meetings/${id}`)).audio_bytes, { timeout: 30_000 }).toBeGreaterThan(0)
  const segs = await api(`/meetings/${id}/segments`)
  const kept = segs.find((s) => s.wav_bytes > 0)
  expect(kept).toBeTruthy()
  // the wav is served, with a token, only for a kept clip
  const audio = await api(`/meetings/${id}/segments/${kept.id}/audio`, { raw: true })
  expect(audio.status).toBe(200)
  expect((await audio.arrayBuffer()).byteLength).toBeGreaterThan(1000)
  await page.locator('.mtg-row').first().click()
  const del = page.locator('button[title^="Delete"][title$="of recorded audio"], button[title*="MiB of recorded audio"]')
  await expect(del).toBeVisible()
  page.once('dialog', (d) => d.accept())
  await del.click()
  await expect.poll(async () => (await api(`/meetings/${id}`)).audio_bytes, { timeout: 30_000 }).toBe(0)
  expect((await api(`/meetings/${id}`)).transcript).toContain('stub speech')
  expect((await api(`/meetings/${id}/segments/${kept.id}/audio`, { raw: true })).status).toBeGreaterThanOrEqual(400)
  expect(realErrors(grain.consoleErrors)).toEqual([])
})

test('a recording block in the rendered doc is a chip with the recording state; clicking it opens the recording', async ({ grain }) => {
  const { page, api } = grain
  const doc = await api('/docs', { method: 'POST', body: { title: 'Chip doc', content: '# Chip\n' } })
  await openDoc(grain, 'Chip doc')
  await page.getByRole('button', { name: 'Record', exact: true }).click()
  await expect(page.locator('.dr-bar')).toBeVisible({ timeout: 60_000 })
  await expect.poll(async () => (await api(`/docs/${doc.id}`)).content, { timeout: 30_000 }).toContain('grain-recording:')
  await page.locator('.dr-bar').getByRole('button', { name: 'Stop' }).click()
  await expect(page.locator('.dr-bar.recording, .dr-bar.paused')).toHaveCount(0, { timeout: 120_000 })
  await page.getByTitle('Preview only').click()
  const chip = page.locator('.rec-chip').first()
  await expect(chip).toBeVisible()
  await expect(chip).toContainText('Recording')
  await expect(chip).not.toContainText('unavailable')
  await chip.click()
  await expect(page.getByRole('tab', { name: /Recordings/ })).toHaveAttribute('aria-selected', 'true')
  expect(realErrors(grain.consoleErrors)).toEqual([])
})

test('at 820x520 recording into a doc keeps Stop and the panel toggle reachable above the Recordings drawer', async ({ grain }) => {
  const { page, api, app } = grain
  await api('/docs', { method: 'POST', body: { title: 'Narrow doc', content: '# Narrow\n' } })
  await small(app)
  await openDoc(grain, 'Narrow doc')
  await page.getByRole('button', { name: 'Record', exact: true }).click()
  await expect(page.locator('.dr-bar')).toBeVisible({ timeout: 60_000 })
  // the panel opened by itself as a drawer and shows the live transcript
  await expect(page.getByRole('tab', { name: /Recordings/ })).toBeVisible()
  await expect(page.locator('.dr-line').first()).toContainText('stub speech', { timeout: 60_000 })
  // nothing sits on top of Stop: a real click on it goes through
  await page.locator('.dr-bar').getByRole('button', { name: 'Stop' }).click({ timeout: 10_000 })
  await expect(page.locator('.dr-bar.recording, .dr-bar.paused')).toHaveCount(0, { timeout: 120_000 })
  // and the drawer closes from the toggle
  await page.getByRole('button', { name: 'Toggle side panel' }).click({ timeout: 10_000 })
  await expect(page.getByRole('tab', { name: /Recordings/ })).toHaveCount(0)
  expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(0)
  expect(realErrors(grain.consoleErrors)).toEqual([])
})

// Not testable here, kept visible so the gap is on record.
test.fixme('dictation: the in-flight words pill hangs under the caret while a clip is being spoken', async () => {
  // The pill is fed by the live-preview engine (stt_stream.make_engine), which needs an on-device streaming
  // speech model. The harness has neither; the preview store has no seeding route, so there is no seam to drive it.
})
test.fixme('Separate speakers: diarizing a kept recording labels S1/S2 on the far side', async () => {
  // Needs sherpa-onnx and two model files (Settings -> Meetings lists them as optional). The chips, renaming and
  // labels are covered with seeded utterances in meetings-more.spec.mjs; the model run itself is not.
})
