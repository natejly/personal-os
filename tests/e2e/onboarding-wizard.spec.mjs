import { test, expect } from '@playwright/test'
import { withGrain } from './helpers/shell.mjs'

// apiKey '' means "unchanged" in PUT /settings (the env seed would survive); null is what clears it.
const FRESH = { settings: { onboardedAt: null, apiKey: null } }
const wizard = (page) => page.getByRole('dialog', { name: /./ }).filter({ has: page.locator('#ob-title') })
const cont = (page, name = 'Continue') => page.getByRole('button', { name: new RegExp(`^${name}`) })

async function pickCustom(page, url, { key = 'mock-key', model = 'mock-chat-2' } = {}) {
  await page.getByRole('radio', { name: /Custom/ }).click()
  await cont(page).click()
  await page.getByLabel('Provider address').fill(url)
  await page.getByRole('textbox', { name: /API key/ }).fill(key)
  await page.getByRole('combobox', { name: 'Model', exact: true }).fill(model)
}

test('first run: wizard connects on the key step, finishes, and never returns after relaunch', async () => {
  await withGrain(FRESH, async (grain) => {
    const { page, api, llm } = grain
    const w = page.locator('.onboarding')
    await expect(w).toBeVisible()
    await expect(w.getByRole('heading', { name: 'Welcome to Grain' })).toBeVisible()
    expect((await api('/setup/status')).needsOnboarding).toBe(true)
    await expect(w.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '1')
    await cont(page, 'Get started').click()

    // provider: Continue is blocked until one is chosen
    await expect(w.getByRole('heading', { name: 'Choose your AI provider' })).toBeVisible()
    // the endpoint this install is already pointed at is preselected (it is the mock, i.e. "custom")
    await expect(w.getByRole('radio', { name: /Custom/ })).toHaveAttribute('aria-checked', 'true')
    await expect(w.getByRole('radio')).toHaveCount(7)
    await w.getByRole('radio', { name: /Custom/ }).click()
    await cont(page).click()

    // key: base URL + model required
    await expect(w.getByRole('heading', { name: 'Connect your account' })).toBeVisible()
    await expect(w.getByLabel('Provider address')).toHaveValue(llm.url)
    await w.getByLabel('Provider address').fill('')
    await expect(cont(page)).toBeDisabled()
    await expect(w.getByText('Enter the base URL to continue.')).toBeVisible()
    await w.getByLabel('Provider address').fill(llm.url)
    await w.getByLabel('Model').fill('')
    await expect(cont(page)).toBeDisabled()
    await expect(w.getByText('Pick or type a model to continue.')).toBeVisible()
    // show/hide key toggle
    await w.getByRole('textbox', { name: /API key/ }).fill('mock-key')
    await expect(w.getByRole('textbox', { name: /API key/ })).toHaveAttribute('type', 'password')
    await w.getByRole('button', { name: 'Show API key' }).click()
    await expect(w.getByRole('textbox', { name: /API key/ })).toHaveAttribute('type', 'text')
    await w.getByLabel('Model').fill('mock-chat-2')
    // nothing is saved until the connection test passes
    expect((await api('/setup/status')).onboardedAt).toBeFalsy()
    await cont(page).click()

    // done: tested and saved by the same click
    await expect(w.getByRole('heading', { name: 'You are all set' })).toBeVisible()
    await expect(w.getByText(/Grain is connected to Custom/)).toBeVisible()
    const st = await api('/setup/status')
    expect(st.needsOnboarding).toBe(false)
    expect(st.onboardedAt).toBeTruthy()
    expect(st.model).toBe('mock-chat-2')
    await page.getByRole('button', { name: /Start chatting/ }).click()
    await expect(w).toHaveCount(0)
    // first prompts on the empty chat; clicking one sends it with the chosen model
    await expect(page.getByRole('button', { name: /get to know me/ })).toBeVisible()
    const prompt = page.getByRole('button', { name: /Put three things on my todo list/ })
    await expect(prompt).toBeVisible()
    await prompt.click()
    await expect.poll(() => llm.calls.length, { timeout: 20_000 }).toBeGreaterThan(0)
    expect(llm.calls.some((c) => c.model === 'mock-chat-2')).toBe(true)

    // relaunch: no wizard
    const p2 = await grain.relaunch()
    await expect(p2.locator('.sidebar')).toBeVisible()
    await p2.waitForTimeout(1500)
    await expect(p2.locator('.onboarding')).toHaveCount(0)
    expect(grain.consoleErrors).toEqual([])
  })
})

test('wizard keyboard: Enter only submits the key step, Esc goes back, Enter on a provider card advances; Set up later closes', async () => {
  await withGrain(FRESH, async (grain) => {
    const { page, llm } = grain
    const w = page.locator('.onboarding')
    await expect(w).toBeVisible()
    await page.keyboard.press('Enter') // welcome: Enter does not advance
    await expect(w.getByRole('heading', { name: 'Welcome to Grain' })).toBeVisible()
    await cont(page, 'Get started').click()
    await page.keyboard.press('Escape')
    await expect(w.getByRole('heading', { name: 'Welcome to Grain' })).toBeVisible()
    await page.keyboard.press('Escape') // nothing before welcome
    await expect(w).toBeVisible()
    await cont(page, 'Get started').click()
    const custom = w.getByRole('radio', { name: /Custom/ })
    await custom.focus()
    await page.keyboard.press('Enter')
    await expect(w.getByRole('heading', { name: 'Connect your account' })).toBeVisible()
    // Esc on the key step goes back; the choice is kept
    await page.keyboard.press('Escape')
    await expect(w.getByRole('radio', { name: /Custom/ })).toHaveAttribute('aria-checked', 'true')
    await custom.focus()
    await page.keyboard.press('Enter')
    await w.getByLabel('Provider address').fill(llm.url)
    await w.getByLabel('Model').fill('mock-chat')
    await w.getByRole('button', { name: /Set up later/ }).click()
    await expect(w).toHaveCount(0)
    // Setup was never completed: still needs onboarding on the next launch
    expect((await grain.api('/setup/status')).needsOnboarding).toBe(true)
    const p2 = await grain.relaunch()
    await expect(p2.locator('.onboarding')).toBeVisible()
  })
})

test('wizard: a bad endpoint fails on the key step readably and saves nothing; fixing it with Enter connects; switching provider drops the old key', async () => {
  await withGrain(FRESH, async (grain) => {
    const { page, api, llm } = grain
    const w = page.locator('.onboarding')
    await cont(page, 'Get started').click()
    await pickCustom(page, 'http://127.0.0.1:9/v1', { model: 'mock-chat' })
    await cont(page).click()
    await expect(w.locator('.ob-error')).toBeVisible({ timeout: 30_000 })
    const msg = await w.locator('.ob-error').innerText()
    expect(msg.length).toBeGreaterThan(5)
    expect(msg).not.toMatch(/\[object|undefined|Traceback/)
    // still on the key step with editable inputs, and nothing saved
    await expect(w.getByRole('heading', { name: 'Connect your account' })).toBeVisible()
    expect((await api('/setup/status')).onboardedAt).toBeFalsy()
    // switching provider: key & model reset
    await w.getByRole('button', { name: /Back/ }).click()
    await w.getByRole('radio', { name: /Ollama/ }).click()
    await cont(page).click()
    await expect(w.getByRole('textbox', { name: /API key/ })).toHaveValue('')
    await expect(w.getByLabel('Model')).not.toHaveValue('mock-chat')
    // a provider that needs a key is blocked without one
    await w.getByRole('button', { name: /Back/ }).click()
    await w.getByRole('radio', { name: /^OpenAI/ }).click()
    await cont(page).click()
    await expect(cont(page)).toBeDisabled()
    await expect(w.getByText('Paste your API key to continue.')).toBeVisible()
    // fix it and submit with Enter
    await w.getByRole('button', { name: /Back/ }).click()
    await w.getByRole('radio', { name: /Custom/ }).click()
    await cont(page).click()
    await w.getByLabel('Provider address').fill(llm.url)
    await w.getByRole('textbox', { name: /API key/ }).fill('mock-key')
    await w.getByLabel('Model').fill('mock-chat')
    await w.getByLabel('Model').press('Enter')
    await expect(w.getByRole('heading', { name: 'You are all set' })).toBeVisible()
  })
})

test('wizard: Settings → Run setup reopens it seeded with the current provider', async () => {
  await withGrain({ settings: { provider: 'custom' } }, async (grain) => {
    const { page, api } = grain
    await expect(page.locator('.onboarding')).toHaveCount(0)
    await page.getByRole('button', { name: /Settings/ }).first().click()
    await page.locator('#settings-tab-model').click() // Run setup lives on the Model tab
    await page.getByRole('button', { name: /Run setup/ }).click()
    const w = page.locator('.onboarding')
    await expect(w).toBeVisible()
    expect((await api('/setup/status')).onboardedAt).toBeFalsy()
    await cont(page, 'Get started').click()
    await expect(w.getByRole('radio', { name: /Custom/ })).toHaveAttribute('aria-checked', 'true')
    await cont(page).click()
    await expect(w.getByLabel('Provider address')).toHaveValue(grain.llm.url)
    // completing again re-stamps onboardedAt without a new key (unchanged endpoint keeps the stored one)
    await w.getByLabel('Model').fill('mock-chat')
    await cont(page).click()
    await expect(w.getByText(/Grain is connected/)).toBeVisible()
    await page.getByRole('button', { name: /Start chatting/ }).click()
    expect((await api('/setup/status')).onboardedAt).toBeTruthy()
  })
})
