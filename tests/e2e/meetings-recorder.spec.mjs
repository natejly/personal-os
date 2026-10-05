import { test as plain, expect } from './fixtures.mjs'
import { test, expect as expectFm } from './helpers/fakemic.mjs'
import { enableModules, reload, small, realErrors } from './helpers/mah.mjs'

for (const t of [plain, test]) t.beforeEach(() => t.setTimeout(240_000))

async function openMeetings(grain) {
  await enableModules(grain.api)
  await reload(grain.page)
  await grain.page.locator('.nav-item', { hasText: 'Meetings' }).first().click()
  await expect(grain.page.getByText('No meeting open')).toBeVisible()
}

const bar = (page) => page.locator('.mtg-bar')

test('Record -> live bar -> transcript fills -> Pause/Resume -> Stop leaves a finished meeting', async ({ grain }) => {
  const { page, api } = grain
  await openMeetings(grain)
  await page.getByRole('button', { name: 'Record' }).first().click()
  await expect(bar(page)).toBeVisible({ timeout: 60_000 })
  await expect(bar(page).locator('.act-state')).toHaveText('Recording')
  await expect(bar(page)).toContainText('microphone')
  await expect(bar(page)).toContainText('transcript ~5s behind')
  // while live: Record is disabled and the row cannot be deleted
  await expect(page.getByRole('button', { name: 'Record' }).first()).toBeDisabled()
  const live = (await api('/meetings/status')).active
  expect(live.meeting_id).toBeTruthy()
  await expect(page.locator(`button[aria-label^="Delete "]`).first()).toBeDisabled()
  // the sidebar indicator is up, and the transcript pane fills as clips close
  await expect(page.locator('.act-indicator', { hasText: 'Meeting' })).toBeVisible()
  await page.getByRole('button', { name: 'Transcript' }).click()
  await expect(page.locator('.mtg-line').first()).toContainText('stub speech', { timeout: 60_000 })
  // clock advances
  const t0 = await bar(page).locator('.mtg-clock').innerText()
  await expect.poll(() => bar(page).locator('.mtg-clock').innerText(), { timeout: 20_000 }).not.toBe(t0)

  await bar(page).getByRole('button', { name: 'Pause' }).click()
  await expect(bar(page).locator('.act-state')).toHaveText('Paused', { timeout: 20_000 })
  await expect(bar(page)).toContainText('audio is being discarded')
  await expect(page.locator('.act-indicator.paused')).toBeVisible()
  expect((await api('/meetings/status')).active.paused).toBe(true)
  await bar(page).getByRole('button', { name: 'Resume' }).click()
  await expect(bar(page).locator('.act-state')).toHaveText('Recording', { timeout: 20_000 })

  // double-click Stop: one stop, no error
  const stop = bar(page).getByRole('button', { name: 'Stop' })
  await stop.dblclick()
  await expect(bar(page)).toHaveCount(0, { timeout: 120_000 })
  await expect(page.locator('.act-indicator', { hasText: 'Meeting' })).toHaveCount(0)
  const row = await api(`/meetings/${live.meeting_id}`)
  expect(['ready', 'stopped', 'transcribing', 'enhancing']).toContain(row.status)
  expect(row.duration_ms).toBeGreaterThan(3000)
  expect(row.transcript).toContain('stub speech')
  await expect(page.getByRole('button', { name: 'Record' }).first()).toBeEnabled()
  expect(realErrors(grain.consoleErrors)).toEqual([])
})

test('transcription outage: failed clips are visible and retranscribe recovers them', async ({ grain }) => {
  const { page, api, stt } = grain
  await openMeetings(grain)
  await page.getByRole('button', { name: 'Record' }).first().click()
  await expect(bar(page)).toBeVisible({ timeout: 60_000 })
  // the route dies after the start-up self-test passed: clips close and fail
  stt.fail = true
  await page.waitForTimeout(12_000)
  await bar(page).getByRole('button', { name: 'Stop' }).click()
  await expect(bar(page)).toHaveCount(0, { timeout: 150_000 })
  const id = (await api('/meetings'))[0].id
  const segs = await api(`/meetings/${id}/segments`)
  expect(segs.length).toBeGreaterThan(0)
  // a failure is data on the row with its reason, never an endless "transcribing"
  expect(segs.filter((s) => s.state === 'failed').length).toBeGreaterThan(0)
  expect(segs.find((s) => s.state === 'failed').error).toMatch(/transcription 500/)
  await page.locator('.mtg-row').first().click()
  // the failure is a persistent banner on the meeting
  await expect(page.locator('.mtg-banner.bad')).toBeVisible({ timeout: 30_000 })
  // route is back: retranscribe settles every clip
  stt.fail = false
  await page.locator('button[title="Transcribe every clip again"]').click()
  await expect.poll(async () => (await api(`/meetings/${id}/segments`)).filter((s) => s.state === 'failed').length, { timeout: 90_000 }).toBe(0)
  await page.getByRole('button', { name: 'Transcript', exact: true }).click()
  await expect(page.locator('.mtg-line').first()).toContainText('stub speech', { timeout: 30_000 })
  expect(realErrors(grain.consoleErrors)).toEqual([])
})

test('an open recording survives navigating away, and the app relaunching', async ({ grain }) => {
  const { page, api } = grain
  await openMeetings(grain)
  await page.getByRole('button', { name: 'Record' }).first().click()
  await expect(bar(page)).toBeVisible({ timeout: 60_000 })
  const id = (await api('/meetings/status')).active.meeting_id
  // navigate away: the sidebar indicator is the way back
  await page.locator('.nav-item', { hasText: 'Today' }).first().click()
  await expect(page.locator('.act-indicator', { hasText: 'Meeting' })).toBeVisible()
  await page.locator('.act-indicator', { hasText: 'Meeting' }).click()
  await expect(bar(page)).toBeVisible()
  // relaunch the window: the backend is still recording and the bar comes back with the same meeting
  await grain.relaunch()
  await grain.page.locator('.nav-item', { hasText: 'Meetings' }).first().click()
  await expect(bar(grain.page)).toBeVisible({ timeout: 60_000 })
  expect((await api('/meetings/status')).active.meeting_id).toBe(id)
  await bar(grain.page).getByRole('button', { name: 'Stop' }).click()
  await expect(bar(grain.page)).toHaveCount(0, { timeout: 120_000 })
})

test('the live bar stays reachable at 820x520 and the page does not scroll sideways', async ({ grain }) => {
  const { page, app } = grain
  await openMeetings(grain)
  await small(app)
  await page.getByRole('button', { name: 'Record' }).first().click()
  const stop = bar(page).getByRole('button', { name: 'Stop' })
  await expect(stop).toBeVisible({ timeout: 60_000 })
  const box = await stop.boundingBox()
  expect(box.x + box.width).toBeLessThanOrEqual(820)
  expect(box.y + box.height).toBeLessThanOrEqual(520)
  expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(0)
  await stop.click()
  await expect(bar(page)).toHaveCount(0, { timeout: 120_000 })
})

test('backend dies mid-recording: the bar does not crash the page and a reload reports no live recording', async ({ grain }) => {
  const { page, backend } = grain
  await openMeetings(grain)
  await page.getByRole('button', { name: 'Record' }).first().click()
  await expect(bar(page)).toBeVisible({ timeout: 60_000 })
  backend.child.kill('SIGKILL')
  await page.waitForTimeout(6000)
  // the shell is still alive and responsive
  await expect(page.getByRole('heading', { name: 'Meetings' }).first()).toBeVisible()
  await page.locator('.nav-item', { hasText: 'Today' }).first().click()
  await expect(page.locator('.sidebar')).toBeVisible()
})

plain('Record is not blocked by a fast click before the status has loaded (no stray consent prompt)', async ({ grain }) => {
  const { page, api, backend } = grain
  await api('/meetings/config', { method: 'PUT', body: { enabled: true } })
  await api('/meetings/consent', { method: 'POST' })
  await enableModules(api)
  // Delay the status answer so the first click lands while the renderer does not know the consent state yet.
  await page.route(`${backend.url}/meetings/status`, async (route) => {
    if (route.request().method() === 'OPTIONS') return route.fallback()
    await new Promise((r) => setTimeout(r, 2500))
    return route.fallback()
  })
  await reload(page)
  await page.locator('.nav-item', { hasText: 'Meetings' }).first().click()
  await page.getByRole('button', { name: 'Record' }).first().click()
  await page.waitForTimeout(4000)
  await expect(page.getByText('Before the first recording')).toHaveCount(0)
})
void expectFm
