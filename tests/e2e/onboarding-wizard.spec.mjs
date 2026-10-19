import { test, expect } from '@playwright/test'
import { withGrain } from './helpers/shell.mjs'

const FRESH = { settings: { onboardedAt: null } }
const cont = (page, name = 'Continue') => page.getByRole('button', { name: new RegExp(`^${name}`) })

/** The harness seeds a provider; a first run has none. Remove them all, then reload so the wizard opens. */
async function fresh(grain) {
  for (const p of (await grain.api('/providers')).providers) await grain.api(`/providers/${p.id}`, { method: 'DELETE' })
  await grain.page.reload()
  await expect(grain.page.locator('.onboarding')).toBeVisible()
}

async function pickCustom(page, url, { key = 'mock-key' } = {}) {
  await page.getByRole('radio', { name: /Custom/ }).click()
  await cont(page).click()
  await page.getByLabel('Provider address').fill(url)
  await page.getByRole('textbox', { name: /API key/ }).fill(key)
}

test('first run: wizard connects on the key step, finishes, and never returns after relaunch', async () => {
  await withGrain(FRESH, async (grain) => {
    const { page, api, llm } = grain
    await fresh(grain)
    const w = page.locator('.onboarding')
    await expect(w.getByRole('heading', { name: 'Welcome to Grain' })).toBeVisible()
    const before = await api('/setup/status')
    expect(before.needsOnboarding).toBe(true)
    expect(before.providers).toEqual([])
    await expect(w.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '1')
    await cont(page, 'Get started').click()

    // provider: Continue is blocked until one is chosen
    await expect(w.getByRole('heading', { name: 'Choose your AI provider' })).toBeVisible()
    await expect(cont(page)).toBeDisabled()
    await w.getByRole('radio', { name: /Custom/ }).click()
    await cont(page).click()

    // key: the address is required; the model id is optional (only for servers that cannot list models)
    await expect(w.getByRole('heading', { name: 'Connect your account' })).toBeVisible()
    await expect(w.getByLabel('Model id (optional)')).toBeVisible()
    await w.getByLabel('Model id (optional)').fill('mock-chat-2')
    await w.getByLabel('Provider address').fill('')
    await expect(cont(page)).toBeDisabled()
    await expect(w.getByText('Enter the base URL to continue.')).toBeVisible()
    await w.getByLabel('Provider address').fill(llm.url)
    // show/hide key toggle
    await w.getByRole('textbox', { name: /API key/ }).fill('mock-key')
    await expect(w.getByRole('textbox', { name: /API key/ })).toHaveAttribute('type', 'password')
    await w.getByRole('button', { name: 'Show API key' }).click()
    await expect(w.getByRole('textbox', { name: /API key/ })).toHaveAttribute('type', 'text')
    // nothing is saved until the provider answers
    expect((await api('/setup/status')).onboardedAt).toBeFalsy()
    await cont(page).click()

    // done: checked and saved by the same click
    await expect(w.getByRole('heading', { name: 'You are all set' })).toBeVisible()
    const st = await api('/setup/status')
    expect(st.needsOnboarding).toBe(false)
    expect(st.onboardedAt).toBeTruthy()
    expect(st.providers).toContain('custom')
    await api('/settings', { method: 'PUT', body: { defaultModel: 'custom/mock-chat-2' } })
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
    await fresh(grain)
    const w = page.locator('.onboarding')
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
    await fresh(grain)
    const w = page.locator('.onboarding')
    await cont(page, 'Get started').click()
    await pickCustom(page, 'http://127.0.0.1:9/v1')
    await cont(page).click()
    await expect(w.locator('.ob-error')).toBeVisible({ timeout: 30_000 })
    const msg = await w.locator('.ob-error').innerText()
    expect(msg.length).toBeGreaterThan(5)
    expect(msg).not.toMatch(/\[object|undefined|Traceback/)
    // still on the key step with editable inputs, and nothing saved
    await expect(w.getByRole('heading', { name: 'Connect your account' })).toBeVisible()
    expect((await api('/setup/status')).onboardedAt).toBeFalsy()
    expect((await api('/providers')).providers).toEqual([])
    // switching provider: the key resets
    await w.getByRole('button', { name: /Back/ }).click()
    await w.getByRole('radio', { name: /Ollama/ }).click()
    await cont(page).click()
    await expect(w.getByRole('textbox', { name: /API key/ })).toHaveValue('')
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
    await w.getByRole('textbox', { name: /API key/ }).press('Enter')
    await expect(w.getByRole('heading', { name: 'You are all set' })).toBeVisible()
  })
})
