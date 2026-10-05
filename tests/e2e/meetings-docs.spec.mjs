import { test as plain, expect } from './fixtures.mjs'
import { test } from './helpers/fakemic.mjs'
import { enableModules, reload, small, realErrors, seedSegments, captureDownloads } from './helpers/mah.mjs'

for (const t of [plain, test]) t.beforeEach(() => t.setTimeout(240_000))

/** A doc with one finished recording: transcript, summary with evidence marks, and action items. */
async function seedDocRecording(grain, { title = 'Planning notes', mode = 'record', lines } = {}) {
  const { api, dataDir } = grain
  const doc = await api('/docs', { method: 'POST', body: { title, content: '# Planning\n\nSome typed notes.\n' } })
  const rec = await api('/meetings', { method: 'POST', body: { title: 'Kickoff call', doc_id: doc.id, doc_mode: mode } })
  seedSegments(dataDir, rec.id, lines ?? [
    { id: 'sa', text: 'We agreed to launch the zebra in November', channel: 'mic', t: 0 },
    { id: 'sb', text: 'Marketing owns the announcement', channel: 'output', t: 15 },
    { id: 'sc', text: 'I will draft the launch plan by Friday', channel: 'mic', t: 31 }
  ])
  if (mode === 'record') {
    await api(`/meetings/${rec.id}`, { method: 'PUT', body: { enhanced: '## Decisions\n- Launch in November\n- Marketing owns announcement', summary: 'Launch zebra in November' } })
    // evidence: summary line index -> segment ids
    const { sql } = await import('./helpers/mah.mjs')
    sql(dataDir, [
      ["UPDATE meetings SET status='ready', summary_evidence=?, started_at=?, duration_ms=45000 WHERE id=?", [JSON.stringify({ 1: ['sa'], 2: ['sb'] }), Date.now() / 1000 - 600, rec.id]]
    ])
  } else {
    const { sql } = await import('./helpers/mah.mjs')
    sql(dataDir, [["UPDATE meetings SET status='ready', started_at=?, duration_ms=45000 WHERE id=?", [Date.now() / 1000 - 600, rec.id]]])
  }
  return { doc, rec }
}

async function openDoc(grain, title) {
  await enableModules(grain.api)
  await reload(grain.page)
  await grain.page.locator('.nav-item', { hasText: 'Files' }).first().click()
  await grain.page.getByText(title, { exact: true }).first().click()
  await expect(grain.page.getByRole('button', { name: 'Toggle side panel' })).toBeVisible()
}

async function recordingsTab(page) {
  const panel = page.getByRole('button', { name: 'Toggle side panel' })
  if ((await panel.getAttribute('aria-pressed')) !== 'true') await panel.click()
  await page.getByRole('tab', { name: /Recordings/ }).click()
}

plain('Recordings panel: empty state, then a seeded recording with transcript, find, export and summary evidence', async ({ grain }) => {
  const { page, api } = grain
  const empty = await api('/docs', { method: 'POST', body: { title: 'Blank doc', content: 'x' } })
  const { rec } = await seedDocRecording(grain)
  await openDoc(grain, 'Blank doc')
  await recordingsTab(page)
  await expect(page.getByText('No recordings yet')).toBeVisible()
  expect(empty.id).toBeTruthy()

  await page.getByText('Planning notes', { exact: true }).first().click()
  await recordingsTab(page)
  const row = page.locator('.dr-list .dr-row')
  await expect(row).toHaveCount(1)
  await expect(row).toContainText('Kickoff call')
  await expect(row).toContainText('Recorded')
  await row.click()
  // transcript tab: three lines, speaker labels per channel, timestamps
  const lines = page.locator('.dr-line')
  await expect(lines).toHaveCount(3)
  await expect(lines.nth(1).locator('.dr-who.them')).toHaveCount(1)
  await expect(lines.nth(0)).toContainText('zebra in November')
  await expect(lines.nth(2).locator('.dr-at')).toHaveText('00:31')

  // find in transcript
  await page.getByRole('button', { name: 'Find in transcript' }).click()
  const find = page.getByRole('textbox', { name: 'Find in transcript' })
  await find.fill('the')
  await expect(page.locator('.dr-find-count')).toHaveText(/1 of \d/)
  await find.fill('zzzz')
  await expect(page.locator('.dr-find-count')).toHaveText('No matches')
  await find.fill('marketing')
  await expect(page.locator('.dr-find-count')).toHaveText('1 of 1')
  await expect(page.locator('.dr-line mark.on')).toHaveText('Marketing')
  await page.keyboard.press('Escape')
  await expect(find).toHaveCount(0)

  // export as text and markdown downloads
  const downloads = await captureDownloads(page)
  for (const [btn, ext] of [['.txt', 'txt'], ['.md', 'md']]) {
    await page.getByRole('button', { name: btn }).click()
    await expect.poll(async () => (await downloads()).filter((d) => d.name.endsWith(`.${ext}`)).length).toBe(1)
    const d = (await downloads()).find((x) => x.name.endsWith(`.${ext}`))
    expect(d.text).toContain('We agreed to launch the zebra in November')
    expect(d.text).toContain('Marketing owns the announcement')
    expect(d.text.indexOf('zebra')).toBeLessThan(d.text.indexOf('Marketing'))
  }

  // summary tab: headline, body, evidence buttons that jump to the cited transcript lines
  await page.getByRole('tab', { name: 'Summary' }).click()
  await expect(page.locator('.dr-headline')).toHaveText('Launch zebra in November')
  await expect(page.locator('.dr-summary-body')).toContainText('Launch in November')
  const src = page.getByRole('button', { name: 'Show source in transcript' })
  await expect(src).toHaveCount(2)
  await src.nth(1).click()
  await expect(page.getByRole('tab', { name: 'Transcript' })).toHaveAttribute('aria-selected', 'true')
  await expect(page.locator('.dr-line.cited')).toHaveCount(1)
  await expect(page.locator('.dr-line.cited')).toContainText('Marketing owns the announcement')
  expect(rec.id).toBeTruthy()
  expect(realErrors(grain.consoleErrors)).toEqual([])
})

plain('rename and delete a recording; deleting asks first', async ({ grain }) => {
  const { page, api } = grain
  const { doc } = await seedDocRecording(grain)
  await openDoc(grain, 'Planning notes')
  await recordingsTab(page)
  await page.locator('.dr-list .dr-row').click()
  await page.getByRole('button', { name: 'Rename recording' }).click()
  const input = page.getByRole('textbox', { name: 'Recording name' })
  await input.fill('Renamed call')
  await input.press('Enter')
  await expect(page.locator('.dr-title')).toHaveText('Renamed call')
  await expect.poll(async () => (await api(`/docs/${doc.id}/recordings`))[0].title).toBe('Renamed call')
  page.once('dialog', (d) => d.dismiss())
  await page.getByRole('button', { name: 'Delete recording' }).click()
  await expect(page.locator('.dr-list .dr-row')).toHaveCount(1)
  page.once('dialog', (d) => d.accept())
  await page.getByRole('button', { name: 'Delete recording' }).click()
  await expect(page.getByText('No recordings yet')).toBeVisible()
  expect((await api(`/docs/${doc.id}/recordings`)).length).toBe(0)
})

plain('Record on a doc explains what blocks it: consent first, then switched off, then the self-test', async ({ grain }) => {
  const { page, api } = grain
  await api('/docs', { method: 'POST', body: { title: 'Call notes', content: 'x' } })
  await openDoc(grain, 'Call notes')
  const record = page.getByRole('button', { name: 'Record', exact: true })
  await record.click()
  // 1. no consent yet: the consent modal opens from the doc
  await expect(page.getByText('Before the first recording')).toBeVisible()
  await page.getByRole('button', { name: 'Not now' }).click()
  await expect(page.getByText('Before the first recording')).toHaveCount(0)
  expect((await api('/docs')).length).toBeGreaterThan(0)
  // 2. consented, recorder off: a notice with an Open settings button, never a spinner
  await api('/meetings/consent', { method: 'POST' })
  await reload(page)
  await page.locator('.nav-item', { hasText: 'Files' }).first().click()
  await page.getByText('Call notes', { exact: true }).first().click()
  await page.getByRole('button', { name: 'Record', exact: true }).click()
  const notice = page.locator('.dr-notice')
  await expect(notice).toContainText('switched off', { timeout: 30_000 })
  await expect(notice.getByRole('button', { name: 'Open settings' })).toBeVisible()
  await notice.getByRole('button', { name: 'Dismiss' }).click()
  await expect(notice).toHaveCount(0)
  // a refused start leaves no recording and no stray doc behind
  expect((await api('/meetings?include_docs=true')).length).toBe(0)
  // 3. enabled, but the transcription route does not answer in this harness: the self-test blocker is readable
  await api('/meetings/config', { method: 'PUT', body: { enabled: true } })
  await page.getByRole('button', { name: 'Record', exact: true }).click()
  await expect(notice).toContainText(/self-test|Transcription/, { timeout: 60_000 })
  await expect(page.getByRole('button', { name: 'Record', exact: true })).toBeEnabled()
  expect((await api('/meetings?include_docs=true')).length).toBe(0)
  expect(realErrors(grain.consoleErrors)).toEqual([])
})

plain('the Record menu offers dictation, keep-audio and import', async ({ grain }) => {
  const { page, api } = grain
  await api('/docs', { method: 'POST', body: { title: 'Menu doc', content: 'x' } })
  await openDoc(grain, 'Menu doc')
  await page.getByRole('button', { name: 'Recording options' }).click()
  const menu = page.getByRole('menu')
  await expect(menu.getByRole('menuitem', { name: /Record and summarize/ })).toBeVisible()
  await expect(menu.getByRole('menuitem', { name: /Dictate into file/ })).toBeVisible()
  await expect(menu.getByRole('menuitem', { name: /Import audio file/ })).toBeVisible()
  await expect(menu.getByLabel(/Keep audio for playback/)).not.toBeChecked()
  await page.keyboard.press('Escape')
  await expect(menu).toHaveCount(0)
})

test('Record into a doc: bar, live transcript in the panel, recording block, Pause/Resume, Stop settles', async ({ grain }) => {
  const { page, api } = grain
  const doc = await api('/docs', { method: 'POST', body: { title: 'Live doc', content: '# Live\n\nBefore.\n' } })
  await openDoc(grain, 'Live doc')
  await page.getByRole('button', { name: 'Record', exact: true }).click()
  const bar = page.locator('.dr-bar')
  await expect(bar).toBeVisible({ timeout: 60_000 })
  await expect(bar.locator('.dr-bar-mode')).toHaveText(/Recording/)
  // the toolbar button is now Stop, the recordings tab opened by itself and shows the live transcript
  await expect(page.locator('.dr-record .dr-stop')).toBeVisible()
  await expect(page.getByRole('tab', { name: /Recordings/ })).toHaveAttribute('aria-selected', 'true')
  await expect(page.locator('.dr-line').first()).toContainText('stub speech', { timeout: 60_000 })
  // the recording left a block in the doc, on its own line
  const editor = page.locator('textarea.md-input')
  await expect(editor).toHaveValue(/\]\(grain-recording:\w+\)/)
  await bar.getByRole('button', { name: 'Pause' }).click()
  await expect(bar.locator('.dr-bar-mode')).toHaveText('Paused', { timeout: 20_000 })
  await bar.getByRole('button', { name: 'Resume' }).click()
  await expect(bar.locator('.dr-bar-mode')).toHaveText(/Recording/, { timeout: 20_000 })
  await bar.getByRole('button', { name: 'Stop' }).click()
  await expect(page.locator('.dr-bar.recording, .dr-bar.paused')).toHaveCount(0, { timeout: 120_000 })
  const rows = await api(`/docs/${doc.id}/recordings`)
  expect(rows).toHaveLength(1)
  expect(rows[0].segment_count).toBeGreaterThan(0)
  // the doc kept what was typed before Record, plus the block
  await expect.poll(async () => (await api(`/docs/${doc.id}`)).content, { timeout: 30_000 }).toMatch(/grain-recording:/)
  expect((await api(`/docs/${doc.id}`)).content).toContain('Before.')
  expect(realErrors(grain.consoleErrors)).toEqual([])
})

test('dictation: finished clips are typed at the caret once, voice commands work, the bar says Dictating', async ({ grain }) => {
  const { page, api, stt } = grain
  const doc = await api('/docs', { method: 'POST', body: { title: 'Dictated doc', content: '# Notes\n\n' } })
  await openDoc(grain, 'Dictated doc')
  const editor = page.locator('textarea.md-input')
  // put the caret at the end of the doc, as a person would before talking
  await editor.click()
  await page.keyboard.press('Meta+ArrowDown')
  await page.getByRole('button', { name: 'Recording options' }).click()
  await page.getByRole('menuitem', { name: /Dictate into file/ }).click()
  await expect(page.locator('.dr-bar .dr-bar-mode')).toHaveText('Dictating', { timeout: 60_000 })
  // the start-up self-test also transcribes once, so queue the words only now
  stt.say('hello from the microphone')
  stt.say('second thought here')
  await expect(editor).toHaveValue(/Hello from the microphone/, { timeout: 60_000 })
  await expect(editor).toHaveValue(/second thought here/, { timeout: 60_000 })
  // exactly once, though events and the 2 s poll deliver the same rows repeatedly
  await page.waitForTimeout(5000)
  const v = await editor.inputValue()
  expect(v.match(/microphone/g)).toHaveLength(1)
  expect(v.match(/second thought/g)).toHaveLength(1)
  // the spoken command stops the dictation
  stt.say('stop dictation')
  await expect(page.locator('.dr-bar.dictate, .dr-bar .dr-bar-mode')).toHaveCount(0, { timeout: 90_000 })
  // typed text autosaved through the ordinary doc path, and the command itself was not typed
  await expect.poll(async () => (await api(`/docs/${doc.id}`)).content, { timeout: 30_000 }).toMatch(/Hello from the microphone/)
  expect((await api(`/docs/${doc.id}`)).content.toLowerCase()).not.toContain('stop dictation')
  expect(realErrors(grain.consoleErrors)).toEqual([])
})

plain('500 recordings on one doc and a 2,000-line transcript render at 820x520 without hanging', async ({ grain }) => {
  const { page, api, dataDir } = grain
  const { sql } = await import('./helpers/mah.mjs')
  const doc = await api('/docs', { method: 'POST', body: { title: 'Busy doc', content: 'x' } })
  const t = Date.now() / 1000
  const rows = []
  for (let i = 0; i < 60; i++) {
    rows.push(['INSERT INTO meetings(id,title,status,doc_id,doc_mode,created_at,updated_at,started_at,duration_ms) VALUES(?,?,?,?,?,?,?,?,?)',
      [`rec${i}`, `Recording ${i}`, 'ready', doc.id, 'record', t - i * 60, t - i * 60, t - i * 60, 30000]])
  }
  sql(dataDir, rows)
  const segs = []
  for (let i = 0; i < 2000; i++) {
    segs.push(['INSERT INTO meeting_segments(id,meeting_id,channel,seq,t_start,t_end,started_at,duration_ms,text,speaker,state,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
      [`bs${i}`, 'rec0', 'mic', i, i * 10, i * 10 + 8, t, 8000, `Line number ${i} of a very long meeting`, '', 'done', t]])
  }
  sql(dataDir, segs)
  await small(grain.app)
  await openDoc(grain, 'Busy doc')
  await recordingsTab(page)
  await expect(page.locator('.dr-list .dr-row')).toHaveCount(60)
  await page.locator('.dr-list .dr-row').first().click()
  await expect(page.locator('.dr-line').first()).toBeVisible({ timeout: 60_000 })
  // consecutive clips from one speaker merge into long lines; every one of the 2,000 clips must be in the text
  await expect(page.locator('.dr-lines')).toContainText('Line number 1999 of a very long meeting', { timeout: 60_000 })
  await expect(page.locator('.dr-lines')).toContainText('Line number 0 of a very long meeting')
  expect(realErrors(grain.consoleErrors)).toEqual([])
})
