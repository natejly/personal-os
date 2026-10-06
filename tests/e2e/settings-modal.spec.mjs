import { test, expect } from './fixtures.mjs'
import { TABS, dialog, openSettings, closeSettings, save, setWindowSize } from './helpers/home.mjs'

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
  await dialog(page).getByRole('tab', { name: 'Data' }).focus()
  await page.keyboard.press('ArrowDown')
  await expect(dialog(page).getByRole('tab', { name: 'Provider & cost' })).toHaveAttribute('aria-selected', 'true')
  await page.keyboard.press('End')
  await expect(dialog(page).getByRole('tab', { name: 'Data' })).toHaveAttribute('aria-selected', 'true')
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
  // Behavior
  await openSettings(page, 'Behavior')
  await field(page, 'Notify me about chats').setChecked(false, { force: true })
  await field(page, 'Notify me about scheduled jobs').setChecked(false, { force: true })
  await dialog(page).getByLabel('Global system prompt').fill('Always answer in haiku.')
  await field(page, 'Dictation chord').fill('Control+Alt+K')
  await dialog(page).locator('summary', { hasText: 'Advanced' }).click()
  await field(page, 'Developer tools').setChecked(true, { force: true })
  // Memory
  await dialog(page).getByRole('tab', { name: 'Memory' }).click()
  await field(page, 'Auto-learn').setChecked(false, { force: true })
  await field(page, 'Auto-title chats').setChecked(false, { force: true })
  await field(page, 'Learn how you write').setChecked(false, { force: true })
  await field(page, 'Hybrid memory search').setChecked(false, { force: true })
  await dialog(page).getByPlaceholder('Same as the default model').fill('mock-chat-2')
  await field(page, 'Context window').fill('64000')
  await field(page, 'Keep recent messages verbatim').fill('12')
  // Tools
  await dialog(page).getByRole('tab', { name: 'Permissions' }).click()
  await field(page, 'Dangerously skip permissions').setChecked(true, { force: true })
  await dialog(page).getByRole('button', { name: 'Accept all', exact: true }).click()
  await field(page, 'Plan mode for new chats').selectOption('auto')
  // Autonomy
  await dialog(page).getByRole('tab', { name: 'Autonomy' }).click()
  await field(page, 'Max tool rounds per reply').fill('17')
  // Integrations
  await dialog(page).getByRole('tab', { name: 'Integrations' }).click()
  await field(page, 'Hold for').fill('100')
  await field(page, 'SearXNG URL').fill('http://localhost:8080')
  // Provider
  await dialog(page).getByRole('tab', { name: 'Provider & cost' }).click()
  await field(page, 'Default chat model').fill('mock-chat-2')
  await field(page, 'Daily spend alert, dollars').fill('3')
  await field(page, 'Monthly spend alert, dollars').fill('40')
  await save(page)

  const check = async () => {
    const s = await api('/settings')
    expect(s).toMatchObject({
      chatNotify: false, notifyJobs: false, systemPrompt: 'Always answer in haiku.', dictationChord: 'Control+Alt+K', devTools: true,
      autoLearn: false, autoTitle: false, learnStyle: false, hybridRetrieval: false, extractionModel: 'mock-chat-2',
      contextWindow: 64000, compactKeepRecent: 12, skipPermissions: true, docEditMode: 'apply', planMode: 'auto', maxToolRounds: 17,
      searxngUrl: 'http://localhost:8080', defaultModel: 'mock-chat-2'
    })
    expect(s.gmailSendHold.seconds).toBe(100)
    expect(s.usageAlerts).toMatchObject({ dailyCost: 3, monthlyCost: 40 })
  }
  await check()

  const p2 = await grain.relaunch()
  await check()
  await openSettings(p2, 'Behavior')
  await expect(field(p2, 'Notify me about chats')).not.toBeChecked()
  await expect(dialog(p2).getByLabel('Global system prompt')).toHaveValue('Always answer in haiku.')
  await dialog(p2).getByRole('tab', { name: 'Memory' }).click()
  await expect(field(p2, 'Context window')).toHaveValue('64000')
  await dialog(p2).getByRole('tab', { name: 'Autonomy' }).click()
  await expect(field(p2, 'Max tool rounds per reply')).toHaveValue('17')
  await dialog(p2).getByRole('tab', { name: 'Permissions' }).click()
  await expect(dialog(p2).getByRole('button', { name: 'Accept all', exact: true })).toHaveAttribute('aria-pressed', 'true')
  await dialog(p2).getByRole('tab', { name: 'Provider & cost' }).click()
  await expect(field(p2, 'Default chat model')).toHaveValue('mock-chat-2')
  await closeSettings(p2)
  noErrors(grain)
})

test('dirty modal asks before discarding; Esc answers the question', async ({ grain }) => {
  const { page, api } = grain
  await openSettings(page, 'Behavior')
  await dialog(page).getByLabel('Global system prompt').fill('draft only')
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
  await openSettings(page, 'Autonomy')
  // maxToolRounds: 0, negative, huge, blank all clamp in the form.
  let cur = before.maxToolRounds
  for (const [v, want] of [['0', () => cur], ['-5', () => cur], ['999', () => 60], ['', () => cur], ['2.6', () => 3]]) {
    await field(page, 'Max tool rounds per reply').fill(v)
    await dialog(page).getByRole('button', { name: 'Save', exact: true }).click()
    await expect(dialog(page)).toHaveCount(0)
    cur = (await api('/settings')).maxToolRounds
    expect(cur, `rounds ${v}`).toBe(want())
    await openSettings(page, 'Autonomy')
  }
  // Out-of-range context window: the backend refuses (422), the modal stays open with the draft.
  await dialog(page).getByRole('tab', { name: 'Memory' }).click()
  await field(page, 'Context window').fill('5')
  await dialog(page).getByRole('button', { name: 'Save', exact: true }).click()
  await expect(dialog(page)).toBeVisible()
  await expect(page.getByText(/contextWindow must be between/)).toBeVisible()
  await field(page, 'Context window').fill('99999999')
  await dialog(page).getByRole('button', { name: 'Save', exact: true }).click()
  await expect(dialog(page)).toBeVisible()
  await field(page, 'Keep recent messages verbatim').fill('99999')
  await field(page, 'Context window').fill('')
  await dialog(page).getByRole('button', { name: 'Save', exact: true }).click()
  await expect(dialog(page)).toBeVisible()
  expect((await api('/settings')).contextWindow).toBe(before.contextWindow)
  // Blank context window and valid keep-recent save as defaults.
  await field(page, 'Keep recent messages verbatim').fill('')
  await dialog(page).getByRole('button', { name: 'Save', exact: true }).click()
  await expect(dialog(page)).toHaveCount(0)
  const s = await api('/settings')
  expect(s.contextWindow).toBe(128000)
  expect(s.compactKeepRecent).toBe(8)

  // Negative spend alerts clamp to 0 in the form.
  await openSettings(page, 'Provider & cost')
  await field(page, 'Daily spend alert, dollars').fill('-4')
  await expect(field(page, 'Daily spend alert, dollars')).toHaveValue('0')
  await closeDirty(page)

  // The API refuses what the form cannot produce.
  for (const body of [
    { maxToolRounds: -1 }, { maxToolRounds: 1e12 }, { maxToolRounds: 'x' }, { contextWindow: null },
    { systemPrompt: null }, { tools: 'x' }, { sandboxRuntime: 'rm -rf' }, { retrievalMode: 'zzz' }, { planMode: 5 }
  ]) {
    const r = await api('/settings', { method: 'PUT', body, raw: true })
    expect(r.status, JSON.stringify(body)).toBeGreaterThanOrEqual(400)
    expect(r.status).toBeLessThan(500)
  }
  expect((await api('/settings')).maxToolRounds).toBe(3)
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
  await openSettings(page, 'Provider & cost')
  await field(page, 'Default chat model').fill('')
  await field(page, 'Base URL').fill('not a url ::: %%')
  const known = (e) => /status of 50[0-9]/.test(e) // the unreachable provider's 502 on /models
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
  await openSettings(page, 'Provider & cost')
  await closeDirty(page)
  expect(grain.consoleErrors.filter((e) => !benign(e) && !known(e))).toEqual([])
})

test('model list comes from the provider and leaves the embedding model out of chat choices', async ({ grain }) => {
  const { page } = grain
  await openSettings(page, 'Provider & cost')
  await expect(dialog(page).getByRole('tab', { name: 'Provider & cost' })).toBeVisible()
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
  await openSettings(page, 'Behavior')
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
  await openSettings(page, 'Behavior')
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
  await openSettings(page, 'Behavior')
  await dialog(page).getByLabel('Global system prompt').fill(big)
  await save(page)
  expect((await api('/settings')).systemPrompt.length).toBe(big.length)
  // Wrong-typed or odd-shaped values for the toggles: accepted or refused, never a broken app.
  for (const body of [{ hiddenViews: [null, {}, 3] }, { homeWidgets: { projects: 'yes', x: null } }, { hiddenViews: 'library' }, { homeWidgets: [] }]) {
    await api('/settings', { method: 'PUT', body, raw: true })
  }
  const p2 = await grain.relaunch()
  await expect(p2.locator('.sidebar').first()).toBeVisible()
  await openSettings(p2, 'Modules')
  await expect(dialog(p2).getByRole('group', { name: 'Where Library shows' })).toBeVisible()
  await closeSettings(p2)
  noErrors(grain)
})

test('modal is usable and scrolls at 820x520', async ({ grain }) => {
  const { page } = grain
  await setWindowSize(grain, 820, 520)
  await page.waitForTimeout(500)
  await openSettings(page, 'Permissions')
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
