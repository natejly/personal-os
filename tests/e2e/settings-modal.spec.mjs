import { test, expect } from './fixtures.mjs'
import { TABS, dialog, openAdvanced, openSettings, closeSettings, save, setWindowSize } from './helpers/home.mjs'

const benign = (e) => /ResizeObserver|Autofocus|favicon/i.test(e)
const noErrors = (grain) => expect(grain.consoleErrors.filter((e) => !benign(e))).toEqual([])
const field = (page, label) => dialog(page).getByLabel(label).first()

test('every settings tab opens and renders cleanly', async ({ grain }) => {
  const { page } = grain
  const bad = []
  page.on('response', (r) => { if (r.status() >= 500) bad.push(`${r.status()} ${r.url()}`) })
  await openSettings(page)
  for (const tab of TABS) {
    await dialog(page).getByRole('tab', { name: tab }).click()
    await expect(dialog(page).getByRole('tab', { name: tab })).toHaveAttribute('aria-selected', 'true')
    await expect(dialog(page).getByRole('tabpanel')).not.toBeEmpty()
    await page.waitForTimeout(250)
  }
  // Arrow keys walk the rail.
  await dialog(page).getByRole('tab', { name: 'Advanced' }).focus()
  await page.keyboard.press('ArrowDown')
  await expect(dialog(page).getByRole('tab', { name: 'Model' })).toHaveAttribute('aria-selected', 'true')
  await page.keyboard.press('End')
  await expect(dialog(page).getByRole('tab', { name: 'Advanced' })).toHaveAttribute('aria-selected', 'true')
  await closeSettings(page)
  expect(bad).toEqual([])
  noErrors(grain)
})

test('Voice input settings act at once and persist through /voice/config', async ({ grain }) => {
  const { page, api } = grain
  await openSettings(page, 'Advanced')
  await dialog(page).locator('summary', { hasText: 'Voice and shortcuts' }).click()
  await dialog(page).getByLabel('Transcription').first().selectOption('local')
  await field(page, 'Tidy dictation with the model').setChecked(true, { force: true })
  await expect.poll(async () => api('/voice/config')).toMatchObject({ sttBackend: 'local', dictationCleanup: true })
  await closeSettings(page)
  noErrors(grain)
})

test('toggles and fields persist through PUT /settings and survive relaunch', async ({ grain }) => {
  const { page, api } = grain
  // Advanced groups
  await openAdvanced(page, 'Assistant')
  await dialog(page).getByLabel('Standing instructions').fill('Always answer in haiku.')
  await field(page, 'Name new chats').setChecked(false, { force: true })
  await openAdvanced(page, 'Desks and workers')
  await field(page, 'Notify me about chats').setChecked(false, { force: true })
  await field(page, 'Notify me about scheduled jobs').setChecked(false, { force: true })
  await openAdvanced(page, 'Voice and shortcuts')
  await field(page, 'Dictation key').fill('Control+Alt+K')
  await openAdvanced(page, 'Developer')
  await field(page, 'Developer tools').setChecked(true, { force: true })
  await openSettings(page, 'Memory')
  await dialog(page).locator('summary', { hasText: 'Learning' }).click()
  await field(page, 'Learn from chats').setChecked(false, { force: true })
  await field(page, 'Learn how I write').setChecked(false, { force: true })
  await openAdvanced(page, 'Search')
  await field(page, 'Smarter memory search').setChecked(false, { force: true })
  await openAdvanced(page, 'Approvals')
  await dialog(page).getByRole('button', { name: 'Accept all', exact: true }).click()
  await field(page, 'Plan first').selectOption('auto')
  await openAdvanced(page, 'Files and web')
  await field(page, 'Your own search server').fill('http://localhost:8080')
  await openSettings(page, 'Integrations')
  await field(page, 'Hold outgoing email so I can undo').setChecked(false, { force: true })
  // Model
  await dialog(page).getByRole('tab', { name: 'Model' }).click()
  await field(page, 'Chat model').fill('mock-chat-2')
  await field(page, 'Helper model').fill('mock-chat-2')
  await save(page)

  const check = async () => {
    const s = await api('/settings')
    expect(s).toMatchObject({
      chatNotify: false, notifyJobs: false, systemPrompt: 'Always answer in haiku.', dictationChord: 'Control+Alt+K', devTools: true,
      autoLearn: false, autoTitle: false, learnStyle: false, hybridRetrieval: false, extractionModel: 'mock-chat-2',
      docEditMode: 'apply', planMode: 'auto', searxngUrl: 'http://localhost:8080', defaultModel: 'mock-chat-2'
    })
    expect(s.gmailSendHold.enabled).toBe(false)
  }
  await check()

  const p2 = await grain.relaunch()
  await check()
  await openAdvanced(p2, 'Assistant')
  await expect(field(p2, 'Name new chats')).not.toBeChecked()
  await expect(dialog(p2).getByLabel('Standing instructions')).toHaveValue('Always answer in haiku.')
  await openAdvanced(p2, 'Approvals')
  await expect(dialog(p2).getByRole('button', { name: 'Accept all', exact: true })).toHaveAttribute('aria-pressed', 'true')
  await dialog(p2).getByRole('tab', { name: 'Model' }).click()
  await expect(field(p2, 'Chat model')).toHaveValue('mock-chat-2')
  await closeSettings(p2)
  noErrors(grain)
})

test('the permission mode saves at once and survives relaunch', async ({ grain }) => {
  const { page, api } = grain
  await openSettings(page, 'Permissions')
  const modes = dialog(page).getByRole('radiogroup', { name: 'Permission mode' })
  await expect(modes.getByRole('radio', { name: /^Manual/ })).toHaveAttribute('aria-checked', 'true')
  await modes.getByRole('radio', { name: /^Auto/ }).click()
  await expect.poll(async () => (await api('/settings')).permissionMode).toBe('auto')
  await closeSettings(page)
  const p2 = await grain.relaunch()
  await openSettings(p2, 'Permissions')
  await expect(dialog(p2).getByRole('radiogroup', { name: 'Permission mode' }).getByRole('radio', { name: /^Auto/ })).toHaveAttribute('aria-checked', 'true')
  await closeSettings(p2)
  noErrors(grain)
})

test('dirty modal asks before discarding; Esc answers the question', async ({ grain }) => {
  const { page, api } = grain
  await openAdvanced(page, 'Assistant')
  await dialog(page).getByLabel('Standing instructions').fill('draft only')
  await page.keyboard.press('Escape')
  await expect(dialog(page).getByText('Discard unsaved changes?')).toBeVisible()
  await page.keyboard.press('Escape') // keep editing
  await expect(dialog(page).getByText('Discard unsaved changes?')).toHaveCount(0)
  await expect(dialog(page)).toBeVisible()
  await page.keyboard.press('Escape')
  await dialog(page).getByRole('button', { name: 'Discard' }).click()
  await expect(dialog(page)).toHaveCount(0)
  expect((await api('/settings')).systemPrompt).not.toBe('draft only')
})

test('invalid values are clamped or rejected without breaking the modal', async ({ grain }) => {
  const { page, api } = grain
  const before = await api('/settings')
  // A number field clamps in the form: 0 and blank fall back to the default, negative and huge values stop at the ends.
  // (Each value differs from the one saved before it, or Save stays disabled.)
  const rounds = 'Delegate after this many tool rounds'
  for (const [v, want] of [['999', 20], ['-5', 1], ['0', 2], ['2.6', 3], ['', 2]]) {
    await openAdvanced(page, 'Desks and workers')
    await field(page, rounds).fill(v)
    await dialog(page).getByRole('button', { name: 'Save', exact: true }).click()
    await expect(dialog(page)).toHaveCount(0)
    expect((await api('/settings')).delegationAfterRounds, `rounds ${v}`).toBe(want)
  }
  // The API refuses what the form cannot produce, and the context window the form no longer offers still has a range.
  for (const body of [
    { contextWindow: 5 }, { contextWindow: 99999999 }, { contextWindow: null }, { compactKeepRecent: 99999 }, { delegationAfterRounds: 'x' },
    { systemPrompt: null }, { tools: 'x' }, { sandboxRuntime: 'rm -rf' }, { retrievalMode: 'zzz' }, { planMode: 5 }
  ]) {
    const r = await api('/settings', { method: 'PUT', body, raw: true })
    expect(r.status, JSON.stringify(body)).toBeGreaterThanOrEqual(400)
    expect(r.status).toBeLessThan(500)
  }
  expect((await api('/settings')).contextWindow).toBe(before.contextWindow)
  expect((await api('/settings')).delegationAfterRounds).toBe(2)
  // Unknown keys are ignored.
  await api('/settings', { method: 'PUT', body: { nonsense: 1 } })
  expect((await api('/settings')).nonsense).toBeUndefined()
  expect(grain.consoleErrors.filter((e) => !benign(e) && !/status of 422/.test(e))).toEqual([]) // the refusals above
})

async function closeDirty(page) {
  await page.keyboard.press('Escape')
  const d = dialog(page).getByRole('button', { name: 'Discard' })
  if (await d.count()) await d.click()
  await expect(dialog(page)).toHaveCount(0)
}

test('empty model and garbage base URL do not break the modal or the app', async ({ grain }) => {
  const { page, api } = grain
  await openSettings(page, 'Model')
  await field(page, 'Chat model').fill('')
  await field(page, 'Provider address').fill('not a url ::: %%')
  const known = (e) => /status of (50[0-9]|422)/.test(e) // the unreachable provider's 502 on /models, the refused address's 422
  await dialog(page).getByRole('button', { name: /Test connection/ }).click()
  await expect(dialog(page).locator('.test-msg')).toBeVisible({ timeout: 20_000 })
  await expect(dialog(page).locator('.test-msg')).toHaveClass(/fail/)
  await dialog(page).getByRole('button', { name: 'Save', exact: true }).click()
  // Whatever the backend decided, the app is still alive and the settings route answers.
  await page.waitForTimeout(500)
  const s = await api('/settings')
  expect(typeof s.defaultModel).toBe('string')
  expect(typeof s.baseUrl).toBe('string')
  if (await dialog(page).count()) await closeDirty(page)
  // The app still renders and can be reopened.
  await openSettings(page, 'Model')
  await closeDirty(page)
  expect(grain.consoleErrors.filter((e) => !benign(e) && !known(e))).toEqual([])
})

test('model list comes from the provider and leaves the embedding model out of chat choices', async ({ grain }) => {
  const { page } = grain
  await openSettings(page, 'Model')
  await expect(dialog(page).getByRole('tab', { name: 'Model' })).toBeVisible()
  await expect.poll(async () => page.locator('#model-options option').evaluateAll((os) => os.map((o) => o.value))).toEqual(expect.arrayContaining(['mock-chat', 'mock-chat-2']))
  const values = await page.locator('#model-options option').evaluateAll((os) => os.map((o) => o.value))
  expect(values).not.toContain('mock-embed')
  await dialog(page).getByRole('button', { name: /Test connection/ }).click()
  await expect(dialog(page).locator('.test-msg.ok')).toContainText(/Connected/, { timeout: 20_000 })
  await closeSettings(page)
  noErrors(grain)
})

test('theme and accent apply to the document and persist', async ({ grain }) => {
  const { page, api } = grain
  const html = page.locator('html')
  await openSettings(page, 'Appearance')
  await dialog(page).getByRole('radio', { name: 'Dark' }).click()
  await expect(html).toHaveAttribute('data-theme', 'dark')
  await dialog(page).getByRole('radio', { name: 'Light' }).click()
  await expect(html).toHaveAttribute('data-theme', 'light')
  const swatches = dialog(page).getByRole('radiogroup', { name: 'Accent color' }).getByRole('radio')
  const n = await swatches.count()
  expect(n).toBeGreaterThan(2)
  const seen = new Set()
  for (let i = 0; i < n; i++) {
    await swatches.nth(i).click()
    await expect(swatches.nth(i)).toHaveAttribute('aria-checked', 'true')
    seen.add(await html.getAttribute('data-accent'))
  }
  expect(seen.size).toBe(n)
  // Cancel restores the saved look.
  const saved = await api('/settings')
  await closeDirty(page)
  await expect(html).toHaveAttribute('data-theme', saved.theme)
  // Saving keeps it, across relaunch.
  await openSettings(page, 'Appearance')
  await dialog(page).getByRole('radio', { name: 'Dark' }).click()
  await swatches.nth(2).click()
  const accent = await html.getAttribute('data-accent')
  await save(page)
  await expect(html).toHaveAttribute('data-theme', 'dark')
  expect((await api('/settings')).theme).toBe('dark')
  const p2 = await grain.relaunch()
  await expect(p2.locator('html')).toHaveAttribute('data-theme', 'dark')
  await expect(p2.locator('html')).toHaveAttribute('data-accent', accent)
  noErrors(grain)
})

test('Esc closes, the menu shortcut opens, rapid open/close leaves no duplicate dialogs', async ({ grain }) => {
  const { page, app } = grain
  const viaMenu = () => app.evaluate(({ Menu }) => {
    const find = (items) => { for (const i of items) { if (i.label === 'Settings…') return i; if (i.submenu) { const r = find(i.submenu.items); if (r) return r } } }
    const it = find(Menu.getApplicationMenu().items)
    it.click()
    return it.accelerator
  })
  expect(await viaMenu()).toBe('CmdOrCtrl+,')
  await expect(dialog(page)).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(dialog(page)).toHaveCount(0)
  // (A synthesized ⌘, never reaches the native menu accelerator, so the menu item's click stands in for it.)
  // Backdrop click closes too.
  await page.locator('.settings-btn').click()
  await page.locator('.modal-backdrop').click({ position: { x: 3, y: 3 } })
  await expect(dialog(page)).toHaveCount(0)
  // Rapid open / close.
  for (let i = 0; i < 20; i++) {
    await page.locator('.settings-btn').click()
    if (i % 2) await viaMenu()
    expect(await page.getByRole('dialog').count()).toBeLessThanOrEqual(1)
    await page.keyboard.press('Escape')
  }
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await page.locator('.settings-btn').click()
  await expect(page.getByRole('dialog')).toHaveCount(1)
  await closeSettings(page)
  expect(await page.locator('.modal-backdrop').count()).toBe(0)
  noErrors(grain)
})

test('a 200 KB system prompt saves and reloads; junk in view toggles cannot brick the shell', async ({ grain }) => {
  const { page, api } = grain
  const big = 'lorem ipsum '.repeat(17_000)
  await openAdvanced(page, 'Assistant')
  await dialog(page).getByLabel('Standing instructions').fill(big)
  await save(page)
  expect((await api('/settings')).systemPrompt.length).toBe(big.length)
  // Wrong-typed or odd-shaped values for the toggles: accepted or refused, never a broken app.
  for (const body of [{ hiddenViews: [null, {}, 3] }, { homeWidgets: { projects: 'yes', x: null } }, { hiddenViews: 'library' }, { homeWidgets: [] }]) {
    await api('/settings', { method: 'PUT', body, raw: true })
  }
  const p2 = await grain.relaunch()
  await expect(p2.locator('.sidebar').first()).toBeVisible()
  await openSettings(p2, 'Appearance')
  await expect(dialog(p2).getByRole('checkbox', { name: 'Library', exact: true })).toBeVisible()
  await closeSettings(p2)
  noErrors(grain)
})

test('modal is usable and scrolls at 820x520', async ({ grain }) => {
  const { page } = grain
  await setWindowSize(grain, 820, 520)
  await page.waitForTimeout(500)
  await openAdvanced(page, 'Assistant')
  const box = await dialog(page).boundingBox()
  const vp = await page.evaluate(() => ({ w: innerWidth, h: innerHeight }))
  expect(box.width).toBeLessThanOrEqual(vp.w + 1)
  expect(box.height).toBeLessThanOrEqual(vp.h + 1)
  expect(box.x).toBeGreaterThanOrEqual(-1)
  const pane = dialog(page).getByRole('tabpanel')
  const m = await pane.evaluate((el) => ({ sh: el.scrollHeight, ch: el.clientHeight }))
  expect(m.sh).toBeGreaterThan(m.ch)
  await pane.evaluate((el) => { el.scrollTop = el.scrollHeight })
  await expect(dialog(page).getByRole('button', { name: 'Save' })).toBeInViewport()
  await expect(dialog(page).getByRole('button', { name: 'Cancel' })).toBeInViewport()
  // Every tab fits its footer and close button on screen.
  for (const tab of TABS) {
    await dialog(page).getByRole('tab', { name: tab }).click()
    await expect(dialog(page).getByRole('button', { name: 'Close settings' })).toBeInViewport()
    await expect(dialog(page).locator('footer button').last()).toBeInViewport()
  }
  await closeSettings(page)
  noErrors(grain)
})
