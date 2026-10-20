import { createServer } from 'node:http'
import { test, expect } from './fixtures.mjs'
import { dialog, openSettings, closeSettings } from './helpers/home.mjs'

// Settings → Models → Providers: add a provider, a key that fails is refused inline, replace a key, and the
// model pickers list the models of every provider with a key.

/** An OpenAI-compatible server that answers only to one key: any other gets a 401 on /models. */
async function keyedServer(goodKey, models = ['alpha-1', 'alpha-2']) {
  const server = createServer((req, res) => {
    res.setHeader('Content-Type', 'application/json')
    if (req.headers.authorization !== `Bearer ${goodKey}`) {
      res.statusCode = 401
      return res.end(JSON.stringify({ error: { message: 'Invalid API key' } }))
    }
    if (req.url.split('?')[0].endsWith('/models')) return res.end(JSON.stringify({ data: models.map((id) => ({ id })) }))
    res.statusCode = 404
    res.end('{}')
  })
  await new Promise((r) => server.listen(0, '127.0.0.1', r))
  return { url: `http://127.0.0.1:${server.address().port}/v1`, close: () => { server.close(); server.closeAllConnections?.() } }
}

const rows = (page) => dialog(page).locator('.prov-item')
const options = (page, label) => dialog(page).getByLabel(label).first().locator('option').evaluateAll((os) => os.map((o) => o.value))

async function openAdd(page, { url, key, name = 'Second server' }) {
  await dialog(page).getByRole('button', { name: 'Add provider' }).click()
  await dialog(page).getByLabel('Provider', { exact: true }).selectOption('custom')
  await dialog(page).getByLabel('Name', { exact: true }).fill(name)
  await dialog(page).getByLabel('Provider address').fill(url)
  await dialog(page).getByRole('textbox', { name: /API key/ }).fill(key)
}

test('add a provider: the key is verified, then its models join the one list', async ({ grain }) => {
  const { page, api } = grain
  const srv = await keyedServer('good-key')
  try {
    await openSettings(page, 'Models')
    const before = (await api('/providers')).providers.length
    await expect(rows(page)).toHaveCount(before)
    await openAdd(page, { url: srv.url, key: 'good-key' })
    await dialog(page).getByRole('button', { name: 'Verify & add' }).click()
    await expect(rows(page)).toHaveCount(before + 1, { timeout: 20_000 })
    await expect(rows(page).filter({ hasText: 'Second server' })).toContainText('Key saved')
    // The union: the seeded provider's models and the new one's, both selectable, each under its own provider.
    await expect.poll(() => options(page, 'Chat model')).toEqual(expect.arrayContaining(['custom/mock-chat', 'custom-2/alpha-1', 'custom-2/alpha-2']))
    await expect(dialog(page).getByLabel('Chat model').first().locator('optgroup')).toHaveCount(2)
    await dialog(page).getByLabel('Chat model').first().selectOption('custom-2/alpha-1')
    await dialog(page).getByRole('button', { name: 'Save', exact: true }).click()
    await expect.poll(async () => (await api('/settings')).defaultModel).toBe('custom-2/alpha-1')
    await closeSettings(page)
  } finally { srv.close() }
})

test('a key the provider rejects is refused next to the field and nothing is stored', async ({ grain }) => {
  const { page, api } = grain
  const srv = await keyedServer('good-key')
  try {
    await openSettings(page, 'Models')
    const before = (await api('/providers')).providers.length
    await openAdd(page, { url: srv.url, key: 'wrong-key' })
    await dialog(page).getByRole('button', { name: 'Verify & add' }).click()
    const alert = dialog(page).getByRole('alert').first()
    await expect(alert).toBeVisible({ timeout: 20_000 })
    expect((await alert.innerText()).length).toBeGreaterThan(5)
    // The form stays open with what was typed; the provider list is unchanged.
    await expect(dialog(page).getByRole('textbox', { name: /API key/ })).toHaveValue('wrong-key')
    expect((await api('/providers')).providers.length).toBe(before)
    expect(Object.keys((await api('/settings')).providerKeysSet ?? {}).length).toBe(before)
    // Fixing the key and trying again adds it.
    await dialog(page).getByRole('textbox', { name: /API key/ }).fill('good-key')
    await dialog(page).getByRole('button', { name: 'Verify & add' }).click()
    await expect(rows(page)).toHaveCount(before + 1, { timeout: 20_000 })
    await closeSettings(page)
  } finally { srv.close() }
})

test('replace key: a bad one keeps the old key, a good one replaces it', async ({ grain }) => {
  const { page, api } = grain
  const srv = await keyedServer('good-key')
  try {
    await openSettings(page, 'Models')
    await openAdd(page, { url: srv.url, key: 'good-key' })
    await dialog(page).getByRole('button', { name: 'Verify & add' }).click()
    const row = rows(page).filter({ hasText: 'Second server' })
    await expect(row).toContainText('Key saved', { timeout: 20_000 })

    await row.getByRole('button', { name: 'Replace key' }).click()
    await row.getByLabel('Second server API key').fill('nope')
    await row.getByRole('button', { name: 'Verify & save' }).click()
    await expect(row.getByRole('alert')).toBeVisible({ timeout: 20_000 })
    await expect(row.getByLabel('Second server API key')).toHaveValue('nope')
    // The old key still works: its models are still listed.
    expect(await options(page, 'Chat model')).toContain('custom-2/alpha-1')
    expect((await api('/providers')).providers.find((p) => p.id === 'custom-2').hasKey).toBe(true)

    await row.getByLabel('Second server API key').fill('good-key')
    await row.getByRole('button', { name: 'Verify & save' }).click()
    await expect(row.getByLabel('Second server API key')).toHaveCount(0, { timeout: 20_000 })
    await expect(row).toContainText('Key saved')
    await closeSettings(page)
  } finally { srv.close() }
})

test('removing a provider drops its models; a selection on it stays visible, disabled', async ({ grain }) => {
  const { page, api } = grain
  const srv = await keyedServer('good-key')
  try {
    await openSettings(page, 'Models')
    await openAdd(page, { url: srv.url, key: 'good-key' })
    await dialog(page).getByRole('button', { name: 'Verify & add' }).click()
    const row = rows(page).filter({ hasText: 'Second server' })
    await expect(row).toContainText('Key saved', { timeout: 20_000 })
    await api('/settings', { method: 'PUT', body: { modelLow: 'custom-2/alpha-1' } })
    await row.getByRole('button', { name: 'Remove' }).click()
    await row.getByRole('button', { name: 'Remove' }).last().click()
    await expect(rows(page).filter({ hasText: 'Second server' })).toHaveCount(0, { timeout: 20_000 })
    await expect.poll(() => options(page, 'Chat model')).not.toContain('custom-2/alpha-1')
    await expect(dialog(page).getByRole('status').filter({ hasText: 'Still set to the removed provider' })).toBeVisible()
    const roles = dialog(page).locator('details[data-disclosure="model-roles"]')
    if (!(await roles.evaluate((e) => e.open))) await roles.locator(':scope > summary').click()
    const stale = dialog(page).getByLabel('Low tier').first().locator('option:disabled')
    await expect(stale).toContainText('provider removed')
    await closeSettings(page)
  } finally { srv.close() }
})
