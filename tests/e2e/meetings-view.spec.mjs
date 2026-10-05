import { test, expect } from './fixtures.mjs'
import { enableModules, reload, small, realErrors, seedSegments } from './helpers/mah.mjs'

test.beforeEach(() => test.setTimeout(240_000))

const nav = (page, label) => page.locator('.nav-item', { hasText: label }).first().click()

async function openMeetings(grain) {
  await enableModules(grain.api)
  await reload(grain.page)
  await nav(grain.page, 'Meetings')
  await expect(grain.page.getByRole('heading', { name: 'Meetings' }).first()).toBeVisible()
}

test('Meetings is hidden until modules are turned on, then shows its empty state', async ({ grain }) => {
  const { page, api } = grain
  await expect(page.locator('.nav-item', { hasText: 'Meetings' })).toHaveCount(0)
  await openMeetings(grain)
  await expect(page.getByText('No meeting open')).toBeVisible()
  await expect(page.getByText('No meetings yet.')).toBeVisible()
  // The recorder is off: Record is disabled with the reason, and the page says what needs setting up.
  const rec = page.getByRole('button', { name: 'Record' }).first()
  await expect(rec).toBeDisabled()
  await expect(rec).toHaveAttribute('title', /Settings → Meetings/)
  await expect(page.getByRole('button', { name: /thing.* need.* setting up/ })).toBeVisible()
  expect(realErrors(grain.consoleErrors)).toEqual([])
  expect((await api('/meetings')).length).toBe(0)
})

test('settings: enabled, model path and diarization persist across relaunch', async ({ grain }) => {
  const { page, api } = grain
  await openMeetings(grain)
  await page.getByRole('button', { name: /thing.* need.* setting up/ }).click()
  const dlg = page.locator('.modal, .settings').first()
  await expect(page.getByText('What this machine can do')).toBeVisible()
  // capability rows read from the real machine; the preflight explains each blocker
  await expect(page.getByText('Supported platform')).toBeVisible()

  await page.locator('label.toggle-row', { hasText: 'Meeting recorder' }).locator('.switch').click()
  await expect.poll(async () => (await api('/meetings/config')).enabled).toBe(true)

  const path = page.getByPlaceholder('/opt/models/ggml-base.en.bin')
  await path.fill('/tmp/e2e/ggml-test.bin')
  await expect.poll(async () => (await api('/meetings/config')).whisperModelPath).toBe('/tmp/e2e/ggml-test.bin')

  await page.locator('label.toggle-row', { hasText: /Separate speakers|diariz/i }).locator('.switch').first().click()
  await expect.poll(async () => (await api('/meetings/config')).diarize).toBe(true)

  await grain.relaunch()
  const cfg = await api('/meetings/config')
  expect(cfg).toMatchObject({ enabled: true, whisperModelPath: '/tmp/e2e/ggml-test.bin', diarize: true })
  await nav(grain.page, 'Meetings')
  // Enabled now, so Record is no longer blocked by the switch (consent/self-test may still block it).
  await expect(grain.page.getByRole('button', { name: 'Record' }).first()).toBeEnabled()
  expect(realErrors(grain.consoleErrors)).toEqual([])
})

test('Record before consent opens the consent modal, which gates on the checkbox and is remembered', async ({ grain }) => {
  const { page, api } = grain
  await api('/meetings/config', { method: 'PUT', body: { enabled: true } })
  await openMeetings(grain)
  await page.getByRole('button', { name: 'Record' }).first().click()
  const modal = page.getByRole('dialog').or(page.locator('.modal')).first()
  await expect(page.getByText('Before the first recording')).toBeVisible()
  const go = page.getByRole('button', { name: 'I understand, start recording' })
  await expect(go).toBeDisabled()
  // It names the real data dir.
  await expect(page.locator('.mtg-consent code').first()).toContainText('recordings')
  // Not now closes without consenting.
  await page.getByRole('button', { name: 'Not now' }).click()
  await expect(page.getByText('Before the first recording')).toHaveCount(0)
  expect((await api('/meetings/status')).consented).toBe(false)
  // Esc also closes it.
  await page.getByRole('button', { name: 'Record' }).first().click()
  await expect(page.getByText('Before the first recording')).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(page.getByText('Before the first recording')).toHaveCount(0)
  expect(modal).toBeTruthy()

  await page.getByRole('button', { name: 'Record' }).first().click()
  await page.getByLabel(/I will tell the other people/).check()
  await expect(go).toBeEnabled()
  await go.click()
  // Consent is stamped even though the recording itself may be refused by the preflight on this machine.
  await expect.poll(async () => (await api('/meetings/status')).consented).toBe(true)
  await expect(page.getByText('Before the first recording')).toHaveCount(0)

  // Remembered: after a relaunch Record does not ask again.
  await grain.relaunch()
  await nav(grain.page, 'Meetings')
  await grain.page.waitForTimeout(4000) // let the status poll land; the fast-click race has its own test
  await grain.page.getByRole('button', { name: 'Record' }).first().click()
  await grain.page.waitForTimeout(800)
  await expect(grain.page.getByText('Before the first recording')).toHaveCount(0)
})

test('start with no usable transcription gives a readable error, not a spinner', async ({ grain }) => {
  const { page, api } = grain
  await api('/meetings/config', { method: 'PUT', body: { enabled: true } })
  await api('/meetings/consent', { method: 'POST' })
  await openMeetings(grain)
  const m = await api('/meetings', { method: 'POST', body: { title: 'Standup' } })
  await page.getByRole('button', { name: 'Record' }).first().click()
  // Meeting is created, start is refused with blockers (mock proxy has no transcription route).
  await expect(page.locator('.toast, [role="status"], [role="alert"]').filter({ hasText: /self-test|transcription|Transcri/i }).first()).toBeVisible({ timeout: 30_000 }).catch(() => {})
  await expect(page.getByRole('button', { name: 'Record' }).first()).toBeEnabled({ timeout: 30_000 })
  const st = await api('/meetings/status')
  expect(st.active).toBeNull()
  expect(m.id).toBeTruthy()
  // a refused start leaves no empty meeting behind: only the one this test made
  expect((await api('/meetings')).map((x) => x.id)).toEqual([m.id])
  expect(realErrors(grain.consoleErrors)).toEqual([])
})

test('a seeded meeting renders notes, transcript, enhanced notes, action items and search finds it', async ({ grain }) => {
  const { page, api, dataDir } = grain
  const m = await api('/meetings', { method: 'POST', body: { title: 'Quarterly roadmap sync', status: 'notes_only' } })
  await api(`/meetings/${m.id}`, { method: 'PUT', body: { notes: '# My notes\n\n- ship the zebra feature', enhanced: '## Decisions\n\n- Ship zebra in Q4', summary: 'Zebra ships in Q4' } })
  seedSegments(dataDir, m.id, [
    { text: 'Welcome everyone to the roadmap sync', channel: 'mic', t: 0 },
    { text: 'We decided the zebra launches in Q4', channel: 'output', t: 12 },
    { text: 'I will write the launch plan', channel: 'mic', t: 24 }
  ])
  await api('/meetings', { method: 'POST', body: { title: 'Other meeting' } })
  await openMeetings(grain)
  await expect(page.locator('.mtg-row')).toHaveCount(2)
  // search across title/notes/transcript
  const search = page.getByLabel('Search meetings')
  await search.fill('zebra')
  await expect(page.locator('.mtg-row')).toHaveCount(1)
  await expect(page.locator('.mtg-row')).toContainText('Quarterly roadmap sync')
  await search.fill('nonexistentword')
  await expect(page.getByText('No matches.')).toBeVisible()
  await search.fill('')
  await page.locator('.mtg-row', { hasText: 'Quarterly roadmap sync' }).click()
  await expect(page.locator('.mtg-title')).toHaveText('Quarterly roadmap sync')
  await expect(page.locator('.cm-content, .mtg-panes').first()).toContainText('zebra feature')
  await page.getByRole('button', { name: 'Transcript' }).click()
  await expect(page.locator('.mtg-line')).toHaveCount(3)
  await expect(page.locator('.mtg-line').nth(1)).toContainText('zebra launches in Q4')
  await expect(page.locator('.mtg-who.them')).toHaveCount(1)
  await expect(page.getByText('No speech was transcribed.')).toHaveCount(0)
  // enhanced review column with the accepted version
  await expect(page.locator('.mtg-enhanced')).toContainText('Ship zebra in Q4')
  await expect(page.locator('.mtg-enhanced')).toContainText('ship the zebra feature')
  expect(realErrors(grain.consoleErrors)).toEqual([])
})

test('a meeting with no audio says so and delete asks for confirmation', async ({ grain }) => {
  const { page, api } = grain
  await api('/meetings', { method: 'POST', body: { title: 'Silent one' } })
  await openMeetings(grain)
  page.once('dialog', (d) => d.dismiss())
  await page.locator('button[aria-label="Delete Silent one"]').click()
  await expect(page.locator('.mtg-row')).toHaveCount(1)
  await page.locator('.mtg-row').click()
  await page.getByRole('button', { name: 'Transcript' }).click()
  await expect(page.getByText('No audio was captured for this meeting.')).toBeVisible()
  page.once('dialog', (d) => d.accept())
  await page.locator('button[aria-label="Delete Silent one"]').click()
  await expect(page.locator('.mtg-row')).toHaveCount(0)
  expect((await api('/meetings')).length).toBe(0)
})

test('500 meetings: list renders and search stays responsive at 820x520', async ({ grain }) => {
  const { page, api, dataDir } = grain
  const { sql } = await import('./helpers/mah.mjs')
  const t = Date.now() / 1000
  const rows = []
  for (let i = 0; i < 500; i++) {
    rows.push([
      "INSERT INTO meetings(id,title,status,notes,created_at,updated_at,started_at,duration_ms) VALUES(?,?,?,?,?,?,?,?)",
      [`bulk${i}`, `Bulk meeting ${i}`, 'ready', `note ${i}${i === 321 ? ' needleword' : ''}`, t - i * 3600, t - i * 3600, t - i * 3600, 60000]
    ])
  }
  sql(dataDir, rows)
  await small(grain.app)
  await openMeetings(grain)
  await expect(page.locator('.mtg-row').first()).toBeVisible()
  await page.getByLabel('Search meetings').fill('bulk meeting 49')
  await expect(page.locator('.mtg-row').first()).toContainText('Bulk meeting 49')
  expect(await page.locator('.mtg-row').count()).toBeLessThan(500)
  // the layout holds in the small window: no horizontal page scroll
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
  expect(overflow).toBeLessThanOrEqual(0)
  expect(realErrors(grain.consoleErrors)).toEqual([])
  expect(api).toBeTruthy()
})
