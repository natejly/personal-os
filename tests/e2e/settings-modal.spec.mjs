import { test, expect } from './fixtures.mjs'
import { menu } from './helpers/shell.mjs'
import { TABS, openMoreAppearance, setToggle, dialog, tabBtn, openAdvanced, openSettings, closeSettings, save, setWindowSize } from './helpers/home.mjs'

const benign = (e) => /ResizeObserver|Autofocus|favicon/i.test(e)
const noErrors = (grain) => expect(grain.consoleErrors.filter((e) => !benign(e))).toEqual([])
const field = (page, label) => dialog(page).getByLabel(label).first()
const openRoles = async (page) => {
  const d = dialog(page).locator('details[data-disclosure="model-roles"]')
  if (!(await d.evaluate((e) => e.open))) await d.locator(':scope > summary').click()
}

test('every settings tab opens and renders cleanly', async ({ grain }) => {
  const { page } = grain
  const bad = []
  page.on('response', (r) => { if (r.status() >= 500) bad.push(`${r.status()} ${r.url()}`); if (r.status() === 409) console.log('409', r.url()) })
  await openSettings(page)
  for (const tab of TABS) {
    await tabBtn(page, tab).click()
    await expect(tabBtn(page, tab)).toHaveAttribute('aria-selected', 'true')
    await expect(dialog(page).getByRole('tabpanel')).not.toBeEmpty()
    await page.waitForTimeout(250)
  }
  // Coding recipes live in the Advanced section. Arrow keys walk the rail; Advanced is the last section.
  const advanced = tabBtn(page, 'Advanced')
  await advanced.click()
  await expect(dialog(page).getByRole('tabpanel')).toContainText('New recipe')
  await page.keyboard.press('ArrowDown')
  await expect(tabBtn(page, 'General')).toHaveAttribute('aria-selected', 'true')
  await page.keyboard.press('End')
  await expect(advanced).toHaveAttribute('aria-selected', 'true')
  await closeSettings(page)
  expect(bad).toEqual([])
  noErrors(grain)
})

test('Voice input settings act at once and persist through /voice/config', async ({ grain }) => {
  const { page, api } = grain
  await openAdvanced(page, 'Voice and shortcuts')
  await dialog(page).getByLabel('Transcription').first().selectOption('local')
  await setToggle(field(page, 'Tidy dictation with the model'), true)
  await expect.poll(async () => api('/voice/config')).toMatchObject({ sttBackend: 'local', dictationCleanup: true })
  await closeSettings(page)
  noErrors(grain)
})

test('the Developer panel lists extensions with their hooks and flips one at once', async ({ grain }) => {
  const { page, api } = grain
  const enabled = async () => (await api('/dev/extensions')).extensions.find((e) => e.id === 'health').enabled
  const was = await enabled()
  await openAdvanced(page, 'Developer')
  await setToggle(field(page, 'Developer tools'), true)
  const summary = dialog(page).locator('summary', { hasText: 'Health health 1.0.0' })
  const health = summary.locator('..')
  await summary.click()
  await expect(health.getByText('app.start')).toBeVisible()
  await health.getByRole('button', { name: was ? 'Disable' : 'Enable', exact: true }).click()
  await expect.poll(enabled).toBe(!was)
  await expect(health.getByRole('button', { name: was ? 'Enable' : 'Disable', exact: true })).toBeVisible()
  await save(page)
  noErrors(grain)
})

test('disabling the open view from the Developer panel falls back to chat', async ({ grain }) => {
  const { page, api } = grain
  await api('/settings', { method: 'PUT', body: { hiddenViews: [], sidebarHidden: [], devTools: true } })
  await page.reload()
  await page.locator('.sidebar .nav-item', { hasText: /^\s*Health/ }).first().click()
  await expect(page.locator('.sidebar .nav-item[aria-current="page"]', { hasText: 'Health' })).toBeVisible()
  await openAdvanced(page, 'Developer')
  const summary = dialog(page).locator('summary', { hasText: 'Health health 1.0.0' })
  await summary.click()
  await summary.locator('..').getByRole('button', { name: 'Disable', exact: true }).click()
  await expect(summary.locator('..').getByRole('button', { name: 'Enable', exact: true })).toBeVisible()
  await closeSettings(page)
  await expect(page.locator('.composer-footer')).toBeVisible()
  await api('/settings', { method: 'PUT', body: { hiddenViews: [] } })
})

test('toggles and fields persist through PUT /settings and survive relaunch', async ({ grain }) => {
  const { page, api } = grain
  // Advanced groups
  await openAdvanced(page, 'Assistant')
  await dialog(page).getByLabel('Standing instructions').fill('Always answer in haiku.')
  await setToggle(field(page, 'Name new chats'), false)
  await openSettings(page, 'General')
  await setToggle(field(page, 'Notify me when a chat finishes'), false)
  await setToggle(field(page, 'Notify me about scheduled jobs'), false)
  await openAdvanced(page, 'Voice and shortcuts')
  await field(page, 'Dictation key').fill('Control+Alt+K')
  await openAdvanced(page, 'Developer')
  await setToggle(field(page, 'Developer tools'), true)
  await openSettings(page, 'Assistant')
  await dialog(page).locator('summary', { hasText: 'Learning' }).click()
  await setToggle(field(page, 'Learn from chats'), false)
  await setToggle(field(page, 'Learn how I write'), false)
  await openAdvanced(page, 'Approvals')
  await dialog(page).getByRole('button', { name: 'Full agentic editing', exact: true }).click()
  await dialog(page).getByRole('button', { name: 'Use full agentic editing', exact: true }).click() // the risky choice asks first
  await openSettings(page, 'Integrations')
  await field(page, 'Your own search server').fill('http://localhost:8080')
  await openSettings(page, 'Integrations')
  await setToggle(field(page, 'Hold outgoing email so I can undo'), false)
  // Model
  await tabBtn(page, 'Models').click()
  await field(page, 'Chat model').selectOption('custom/mock-chat-2')
  await openRoles(page)
  await field(page, 'Low tier').selectOption('custom/mock-chat-2')
  await save(page)

  const check = async () => {
    const s = await api('/settings')
    expect(s).toMatchObject({
      chatNotify: false, notifyJobs: false, systemPrompt: 'Always answer in haiku.', dictationChord: 'Control+Alt+K', devTools: true,
      autoLearn: false, autoTitle: false, learnStyle: false, modelLow: 'custom/mock-chat-2',
      docEditMode: 'apply', searxngUrl: 'http://localhost:8080', defaultModel: 'custom/mock-chat-2'
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
  await expect(dialog(p2).getByRole('button', { name: 'Full agentic editing', exact: true })).toHaveAttribute('aria-pressed', 'true')
  await tabBtn(p2, 'Models').click()
  await expect(field(p2, 'Chat model')).toHaveValue('custom/mock-chat-2')
  await closeSettings(p2)
  noErrors(grain)
})

test('the permission mode waits for Save and survives relaunch', async ({ grain }) => {
  const { page, api } = grain
  await openSettings(page, 'Permissions')
  const modes = dialog(page).getByRole('radiogroup', { name: 'Permission mode' })
  await expect(modes.getByRole('radio', { name: /^Manual/ })).toHaveAttribute('aria-checked', 'true')
  await modes.getByRole('radio', { name: /^Auto/ }).click()
  await expect(modes.getByRole('radio', { name: /^Auto/ })).toHaveAttribute('aria-checked', 'true')
  expect((await api('/settings')).permissionMode).toBe('manual') // a draft until Save
  await save(page)
  expect((await api('/settings')).permissionMode).toBe('auto')
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
  for (const [v, want] of [['999', 20], ['-5', 1], ['0', 4], ['2.6', 3], ['', 4]]) {
    await openAdvanced(page, 'Desks and workers')
    const lim = dialog(page).locator('details[data-disclosure="agent-workers"]')
    if (!(await lim.evaluate((e) => e.open))) await lim.locator(':scope > summary').click()
    await field(page, rounds).fill(v)
    await dialog(page).getByRole('button', { name: 'Save', exact: true }).click()
    await expect(dialog(page)).toHaveCount(0)
    expect((await api('/settings')).delegationAfterRounds, `rounds ${v}`).toBe(want)
  }
  // The API refuses what the form cannot produce, and the context window the form no longer offers still has a range.
  for (const body of [
    { contextWindow: 5 }, { contextWindow: 99999999 }, { contextWindow: null }, { compactKeepRecent: 99999 }, { delegationAfterRounds: 'x' },
    { systemPrompt: null }, { tools: 'x' }, { retrievalMode: 'zzz' }
  ]) {
    const r = await api('/settings', { method: 'PUT', body, raw: true })
    expect(r.status, JSON.stringify(body)).toBeGreaterThanOrEqual(400)
    expect(r.status).toBeLessThan(500)
  }
  expect((await api('/settings')).contextWindow).toBe(before.contextWindow)
  expect((await api('/settings')).delegationAfterRounds).toBe(4)
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

test('a provider with a garbage address is refused inline and the app stays alive', async ({ grain }) => {
  const { page, api } = grain
  await openSettings(page, 'Models')
  const before = (await api('/providers')).providers.length
  const known = (e) => /status of (50[0-9]|422)/.test(e) // the unreachable provider's 502, the refused address's 422
  await dialog(page).getByRole('button', { name: 'Add provider' }).click()
  await dialog(page).getByLabel('Provider', { exact: true }).selectOption('custom')
  await dialog(page).getByLabel('Provider address').fill('not a url ::: %%')
  await dialog(page).getByRole('textbox', { name: /API key/ }).fill('k')
  await dialog(page).getByRole('button', { name: 'Verify & add' }).click()
  await expect(dialog(page).getByRole('alert').first()).toBeVisible({ timeout: 20_000 })
  // Nothing was stored, the form keeps what was typed, and the settings route still answers.
  expect((await api('/providers')).providers.length).toBe(before)
  await expect(dialog(page).getByLabel('Provider address')).toHaveValue('not a url ::: %%')
  expect(typeof (await api('/settings')).defaultModel).toBe('string')
  await closeDirty(page)
  await openSettings(page, 'Models')
  await closeDirty(page)
  expect(grain.consoleErrors.filter((e) => !benign(e) && !known(e))).toEqual([])
})

test('model pickers list every model from the providers with a key and leave the embedding model out of chat choices', async ({ grain }) => {
  const { page } = grain
  await openSettings(page, 'Models')
  const options = (label) => field(page, label).locator('option').evaluateAll((os) => os.map((o) => o.value))
  await expect.poll(() => options('Chat model')).toEqual(expect.arrayContaining(['custom/mock-chat', 'custom/mock-chat-2']))
  expect(await options('Chat model')).not.toContain('custom/mock-embed')
  await openRoles(page)
  expect(await options('Search model')).toContain('mock-embed')
  await closeSettings(page)
  noErrors(grain)
})

test('theme and accent apply to the document and persist', async ({ grain }) => {
  const { page, api } = grain
  const html = page.locator('html')
  await openSettings(page, 'General')
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
  await openSettings(page, 'General')
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
  await openSettings(p2, 'General')
  await expect(dialog(p2).getByRole('checkbox', { name: 'Library', exact: true })).toBeVisible()
  await closeSettings(p2)
  noErrors(grain)
})

test('modal is usable and scrolls at 820x520', async ({ grain }) => {
  const { page } = grain
  await setWindowSize(grain, 820, 520)
  await page.waitForTimeout(500)
  await openAdvanced(page, 'Assistant')
  await dialog(page).getByLabel('Standing instructions').fill('x') // Save only shows once something changed
  const box = await dialog(page).boundingBox()
  const vp = await page.evaluate(() => ({ w: innerWidth, h: innerHeight }))
  expect(box.width).toBeLessThanOrEqual(vp.w + 1)
  expect(box.height).toBeLessThanOrEqual(vp.h + 1)
  expect(box.x).toBeGreaterThanOrEqual(-1)
  const pane = dialog(page).getByRole('tabpanel')
  const m = await pane.evaluate((el) => ({ sh: el.scrollHeight, ch: el.clientHeight }))
  expect(m.sh).toBeGreaterThan(m.ch)
  await pane.evaluate((el) => { el.scrollTop = el.scrollHeight })
  await expect(dialog(page).getByRole('button', { name: 'Save' })).toBeInViewport() // the edit above is unsaved
  await expect(dialog(page).getByRole('button', { name: 'Cancel' })).toBeInViewport()
  // Every tab fits its footer and close button on screen.
  for (const tab of TABS) {
    await tabBtn(page, tab).click()
    await expect(dialog(page).getByRole('button', { name: 'Close settings' })).toBeInViewport()
    await expect(dialog(page).locator('footer button').last()).toBeInViewport()
  }
  await closeDirty(page)
  noErrors(grain)
})

test('the Mascot setting swaps the faces for blobs or a status dot, and saves', async ({ grain }) => {
  const { page, api } = grain
  await api('/conversations', { method: 'POST', body: { title: 'Mascot chat' } })
  await page.reload()
  const row = page.locator('.sidebar .convo-item', { hasText: 'Mascot chat' })
  await expect(row.locator('svg.face.sprout')).toHaveCount(1)
  const pick = async (label) => {
    await openMoreAppearance(page)
    await dialog(page).getByRole('radiogroup', { name: 'Mascot' }).getByRole('radio', { name: label, exact: true }).click()
    await save(page)
  }
  await pick('Blobs')
  await expect(row.locator('.face .mo-root')).toHaveCount(1)
  await expect(row.locator('.sprout')).toHaveCount(0)
  expect((await api('/settings')).mascot).toBe('blobs')
  await pick('Off')
  await expect(row.locator('.face-dot')).toHaveCount(1)
  await expect(row.locator('.sprout, .mo-root')).toHaveCount(0)
  await expect(page.locator('html')).toHaveAttribute('data-mascot', 'off')
  expect((await api('/settings')).mascot).toBe('off')
  const p2 = await grain.relaunch()
  await expect(p2.locator('.sidebar .convo-item', { hasText: 'Mascot chat' }).locator('.face-dot')).toHaveCount(1)
  await openSettings(p2)
  await openMoreAppearance(p2)
  await expect(dialog(p2).getByRole('radio', { name: 'Off', exact: true })).toHaveAttribute('aria-checked', 'true')
  noErrors(grain)
})

test('searching for a setting inside a closed disclosure opens it and highlights the block', async ({ grain }) => {
  const { page } = grain
  await openSettings(page)
  const d = dialog(page)
  const search = d.getByLabel('Search settings')
  const pick = async (q, title) => {
    await search.fill(q)
    await d.locator('.settings-hits button', { hasText: title }).first().click()
  }
  // A limits row, then an Always ask first entry (both start collapsed), then Model roles.
  await pick('repeat', 'Agent loop limits')
  await expect(d.locator('details[data-disclosure="agent-limits"]')).toHaveAttribute('open', '')
  await expect(d.getByText('Repeated dead ends').first()).toBeVisible()
  await expect(d.locator('#sec-loop')).toHaveClass(/set-flash/)
  await pick('revoke', 'Always ask first')
  await expect(d.locator('details[data-disclosure="per-tool-access"]')).toHaveAttribute('open', '')
  await expect(d.locator('#sec-always-ask')).toBeVisible()
  await expect(d.locator('#sec-always-ask')).toHaveClass(/set-flash/)
  await pick('rerank', 'Models')
  await expect(d.locator('details[data-disclosure="model-roles"]')).toHaveAttribute('open', '')
  await expect(d.getByText('Search model').first()).toBeVisible()
  noErrors(grain)
})

test('the sidebar footer button cycles Light, Dark, System and saves at once; the shortcut and palette commands set the theme', async ({ grain }) => {
  const { page, api } = grain
  const html = page.locator('html')
  const btn = page.locator('.sidebar-bottom .theme-btn')
  await api('/settings', { method: 'PUT', body: { theme: 'dark' } })
  await page.reload()
  await expect(btn).toHaveAttribute('aria-label', 'Theme: Dark')
  await btn.click()
  await expect(html).toHaveAttribute('data-theme', 'system')
  await expect(btn).toHaveAttribute('aria-label', 'Theme: System')
  await expect.poll(async () => (await api('/settings')).theme).toBe('system')
  await btn.click()
  await expect(html).toHaveAttribute('data-theme', 'light')
  await expect.poll(async () => (await api('/settings')).theme).toBe('light')
  await btn.click()
  await expect(html).toHaveAttribute('data-theme', 'dark')
  await expect(btn).toHaveAttribute('aria-label', 'Theme: Dark')
  // The Toggle Theme chord flips light and dark.
  await menu(grain, 'Toggle Theme')
  await expect(html).toHaveAttribute('data-theme', 'light')
  await menu(grain, 'Toggle Theme')
  await expect(html).toHaveAttribute('data-theme', 'dark')
  await expect.poll(async () => (await api('/settings')).theme).toBe('dark')
  // Palette commands.
  const pal = page.getByRole('dialog', { name: 'Command palette' })
  await menu(grain, 'Command Palette…')
  await pal.getByPlaceholder('Go to, create, open…').fill('theme: light')
  await pal.getByRole('option', { name: /^Theme: Light/ }).click()
  await expect(html).toHaveAttribute('data-theme', 'light')
  await expect.poll(async () => (await api('/settings')).theme).toBe('light')
  noErrors(grain)
})

test('General keeps Theme, Accent and Mascot; presets, text colour, background and zoom are gone; removed font ids load as the default', async ({ grain }) => {
  const { page, api } = grain
  await api('/settings', { method: 'PUT', body: { uiFont: 'georgia', codeFont: 'courier' } })
  await page.reload()
  await expect(page.locator('html')).toHaveAttribute('data-font', 'system')
  await expect(page.locator('html')).toHaveAttribute('data-code', 'default')
  const d = await openMoreAppearance(page)
  await expect(d.getByRole('radiogroup', { name: 'Theme' })).toBeVisible()
  await expect(d.getByRole('radiogroup', { name: 'Accent color' })).toBeVisible()
  await expect(d.getByRole('radiogroup', { name: 'Mascot' })).toBeVisible()
  await expect(d.getByRole('radiogroup', { name: 'Interface font' }).getByRole('radio')).toHaveCount(3)
  await expect(d.getByRole('radiogroup', { name: 'Code font' }).getByRole('radio')).toHaveCount(2)
  for (const gone of ['Appearance presets', 'Text color', 'Background']) await expect(d.getByRole('radiogroup', { name: gone })).toHaveCount(0)
  await expect(d.getByRole('group', { name: 'Zoom' })).toHaveCount(0)
  noErrors(grain)
})
