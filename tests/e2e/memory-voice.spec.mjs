import { test, expect } from './fixtures.mjs'
import { callWith, msgText, send, shrink, sleep, systemOf } from './helpers/kb.mjs'

const clean = (g) => expect(g.consoleErrors.filter((e) => !/favicon|ResizeObserver|502 \(Bad Gateway\)/.test(e))).toEqual([])

const PROFILE = { summary: 'Writes short warm notes, VOICESUM style.', guidelines: ['Open with a first name', 'GUIDELINEMARK keep it brief'], phrases: ['cheers then'], avoid: ['exclamation marks'] }

async function openVoice(page) {
  await page.locator('.sidebar').getByRole('button', { name: /^Memory\s*\d*$/ }).click()
  await page.getByRole('button', { name: 'Voice', exact: true }).click()
  await expect(page.getByRole('heading', { name: /Writing samples/ })).toBeVisible()
}

async function chatIn(grain, title, settings = {}) {
  const { api, page } = grain
  const c = await api('/conversations', { method: 'POST', body: { title } })
  if (Object.keys(settings).length) await api('/conversations/' + c.id, { method: 'PATCH', body: { settings } })
  await page.reload()
  await page.locator('.sidebar').getByText(title, { exact: true }).first().click()
  return c
}

test('voice panel: empty state, add a sample, garbage relearn is survived, delete the sample', async ({ grain }) => {
  const { page, api, backend } = grain
  await openVoice(page)
  await expect(page.getByText(/Nothing learned yet/)).toBeVisible()
  await expect(page.getByText('no voice yet · 0 samples')).toBeVisible()
  await expect(page.getByRole('button', { name: /Learn my style/ })).toBeDisabled()
  await page.getByPlaceholder('Paste a piece of your own writing…').fill('Hi Sam, thanks for the notes. I will send the draft over on Thursday and we can pick it apart then. Cheers, N')
  await page.getByRole('button', { name: 'Add sample' }).click()
  await expect(page.locator('.style-sample')).toHaveCount(1)
  await expect(page.getByText(/1 sample/).first()).toBeVisible()
  await sleep(1500) // the queued relearn gets a non-JSON answer from the mock
  expect(backend.log()).not.toMatch(/Traceback/)
  expect((await api('/style')).profile).toBeNull()
  // forced learn with a garbage answer reports, does not crash
  await page.getByRole('button', { name: /Learn my style/ }).click()
  await expect(page.getByText(/did not return a usable style profile/)).toBeVisible()
  await expect(page.getByRole('button', { name: /Learn my style/ })).toBeEnabled()
  expect((await api('/style')).profile).toBeNull()
  await page.getByRole('button', { name: /Delete writing sample/ }).click()
  await expect(page.locator('.style-sample')).toHaveCount(0)
  await expect(page.getByText('No samples in this scope yet.')).toBeVisible()
  clean(grain)
})

test('voice panel: edit guidelines, phrases, never, summary; edits persist and mark the profile hand-edited', async ({ grain }) => {
  const { page, api } = grain
  await api('/style', { method: 'PUT', body: PROFILE })
  await openVoice(page)
  await expect(page.getByLabel('Style summary')).toHaveValue(PROFILE.summary)
  await page.getByLabel('Style summary').fill('Edited summary SUMEDIT')
  await page.getByLabel('Guidelines 1').click() // blur
  await expect.poll(async () => (await api('/style')).profile.summary).toBe('Edited summary SUMEDIT')
  // add a guideline with Enter
  await page.getByPlaceholder('Add a guideline…').fill('never open with Hope you are well')
  await page.getByPlaceholder('Add a guideline…').press('Enter')
  await expect(page.getByLabel('Guidelines 3')).toHaveValue('never open with Hope you are well')
  // remove a phrase
  await page.getByRole('button', { name: 'Remove: cheers then' }).click()
  await expect.poll(async () => (await api('/style')).profile.phrases).toEqual([])
  const s = await api('/style')
  expect(s.profile.guidelines).toHaveLength(3)
  expect(s.profile.edited).toBeTruthy()
  await expect(page.getByText(/Hand-edited/)).toBeVisible()
  const p2 = await grain.relaunch()
  await p2.locator('.sidebar').getByRole('button', { name: /^Memory\s*\d*$/ }).click()
  await p2.getByRole('button', { name: 'Voice', exact: true }).click()
  await expect(p2.getByLabel('Style summary')).toHaveValue('Edited summary SUMEDIT')
  clean(grain)
})

test('the voice is injected only when drafting as the user: normal and email-ish prompts get a hint at most', async ({ grain }) => {
  const { page, api, llm } = grain
  await api('/style', { method: 'PUT', body: PROFILE })
  await chatIn(grain, 'plain chat')
  await send(page, 'Please write an email for me to Sam about Thursday !!reply Subject: Thursday')
  await expect(page.locator('.msg.assistant').last()).toContainText('Subject: Thursday', { timeout: 30_000 })
  const normal = callWith(llm, 'write an email for me')[0]
  expect(systemOf(normal)).not.toContain('GUIDELINEMARK')
  expect(systemOf(normal)).not.toContain('VOICESUM')
  expect(systemOf(normal)).not.toContain('How the user writes')
  expect(systemOf(normal)).toMatch(/writing_style/) // one-line pointer to the tool, nothing more
  await send(page, 'what is two plus two !!reply four')
  await expect(page.locator('.msg.assistant').last()).toContainText('four', { timeout: 30_000 })
  expect(systemOf(callWith(llm, 'two plus two')[0])).not.toContain('GUIDELINEMARK')
  clean(grain)
})

test('Draft mode puts the whole voice block in the system prompt; so does the writing_style tool', async ({ grain }) => {
  const { page, api, llm } = grain
  await api('/style', { method: 'PUT', body: PROFILE })
  await chatIn(grain, 'draft chat', { draftMode: true })
  await send(page, 'draft a note to Sam !!reply Hi Sam')
  await expect(page.locator('.msg.assistant').last()).toContainText('Hi Sam', { timeout: 30_000 })
  const sys = systemOf(callWith(llm, 'draft a note')[0])
  expect(sys).toContain('How the user writes')
  expect(sys).toContain('GUIDELINEMARK')
  expect(sys).toContain('VOICESUM')
  expect(sys).toContain('cheers then')
  expect(sys).toContain('exclamation marks')
  expect(sys).toContain('It is not permission to send')

  // tool path in a normal chat
  await chatIn(grain, 'tool chat')
  await send(page, 'fetch voice !!tool writing_style {}')
  await expect(page.locator('.msg.assistant').last()).toContainText('MOCK: tool done', { timeout: 30_000 })
  const toolMsgs = llm.calls.flatMap((c) => (c.messages || []).filter((m) => m.role === 'tool').map(msgText))
  expect(toolMsgs.join('\n')).toContain('GUIDELINEMARK')
  clean(grain)
})

test('voice off (toggle, per-chat switch, or no profile) injects nothing', async ({ grain }) => {
  const { page, api, llm } = grain
  await api('/style', { method: 'PUT', body: PROFILE })
  await api('/style', { method: 'PUT', body: { enabled: false } })
  await chatIn(grain, 'disabled voice', { draftMode: true })
  await send(page, 'draft one !!reply d1')
  await expect(page.locator('.msg.assistant').last()).toContainText('d1', { timeout: 30_000 })
  expect(systemOf(callWith(llm, 'draft one')[0])).not.toContain('GUIDELINEMARK')
  await api('/style', { method: 'PUT', body: { enabled: true } })
  await chatIn(grain, 'chat opted out', { draftMode: true, useStyle: false })
  await send(page, 'draft two !!reply d2')
  await expect(page.locator('.msg.assistant').last()).toContainText('d2', { timeout: 30_000 })
  const sys = systemOf(callWith(llm, 'draft two')[0])
  expect(sys).not.toContain('GUIDELINEMARK')
  expect(sys).not.toMatch(/writing_style/)
  clean(grain)
})

test('a project voice replaces the personal one inside that project; without it the personal voice applies', async ({ grain }) => {
  const { page, api, llm } = grain
  const p = await api('/projects', { method: 'POST', body: { name: 'Legal' } })
  const q = await api('/projects', { method: 'POST', body: { name: 'Casual' } })
  await api('/style', { method: 'PUT', body: PROFILE })
  await api('/style', { method: 'PUT', body: { project_id: p.id, summary: 'Formal LEGALVOICE', guidelines: ['LEGALGUIDE use whereas'] } })
  const ask = async (proj, title, tag) => {
    const c = await api('/conversations', { method: 'POST', body: { project_id: proj.id, title } })
    await api('/conversations/' + c.id, { method: 'PATCH', body: { settings: { draftMode: true } } })
    await page.reload()
    await page.locator('.sidebar').getByText(title, { exact: true }).first().click()
    await send(page, `draft ${tag} !!reply r${tag}`)
    await expect(page.locator('.msg.assistant').last()).toContainText(`r${tag}`, { timeout: 30_000 })
    return systemOf(callWith(llm, `draft ${tag}`)[0])
  }
  const legal = await ask(p, 'legal chat', 'alpha')
  expect(legal).toContain('LEGALGUIDE')
  expect(legal).not.toContain('GUIDELINEMARK')
  const casual = await ask(q, 'casual chat', 'beta')
  expect(casual).toContain('GUIDELINEMARK')
  expect(casual).not.toContain('LEGALGUIDE')
  // the project's Voice tab says drafts use the personal voice
  await page.locator('.sidebar').getByText('Casual', { exact: true }).first().click()
  await page.getByRole('button', { name: /Memory/ }).click()
  await page.getByRole('button', { name: 'Voice', exact: true }).click()
  await expect(page.getByText(/currently use your personal voice/)).toBeVisible()
  clean(grain)
})

test('voice panel at 820x520 with a long profile and 50 samples stays scrollable without horizontal overflow', async ({ grain }) => {
  const { page, api } = grain
  await api('/style', { method: 'PUT', body: { ...PROFILE, guidelines: Array.from({ length: 30 }, (_, i) => `guideline ${i} ` + 'word '.repeat(30)) } })
  await Promise.all(Array.from({ length: 50 }, (_, i) => api('/style/samples', { method: 'POST', body: { text: `Sample number ${i}. ` + 'I like writing plainly and with some warmth. '.repeat(10) } })))
  await shrink(grain)
  await openVoice(page)
  await expect(page.locator('.style-sample')).toHaveCount(50)
  const over = await page.evaluate(() => { const e = document.querySelector('.memory-page'); return e.scrollWidth - e.clientWidth })
  expect(over).toBeLessThanOrEqual(1)
  clean(grain)
})
