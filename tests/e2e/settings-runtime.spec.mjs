import { test, expect } from './fixtures.mjs'
import { dialog, openSettings, closeSettings } from './helpers/home.mjs'
import { newChat, sayAndWait } from './helpers/chat.mjs'

const SLOTS = ['model.client', 'model.router', 'tools.source', 'sandbox.backend', 'agent.loop']

test('Settings > Runtime shows every slot on its built-in provider, with where it comes from', async ({ grain }) => {
  const { page, api } = grain
  await openSettings(page, 'Agent')
  for (const slot of SLOTS) {
    await expect(dialog(page).getByTestId(`runtime-${slot}-reason`)).toContainText('built-in default')
  }
  // The model router has only its built-in provider, so there is nothing to pick; the agent loop has one built-in alternative.
  await expect(dialog(page).getByLabel('Model router, Everywhere')).toBeDisabled()
  await expect(dialog(page).getByLabel('Agent loop, Everywhere').locator('option')).toHaveText(['Default (Chat loop)', 'Chat loop', 'Plan, execute, verify (built-in)'])
  const resolved = await api('/runtime/resolve')
  expect(Object.keys(resolved).sort()).toEqual([...SLOTS].sort())
  expect(Object.values(resolved).every((r) => r.default === true && r.source === 'default')).toBe(true)
  await closeSettings(page)
})

const provider = (over) => ({ id: 'acme/x', label: 'Acme', tier: 'user', extension: 'acme', core: false, available: true, locality: null, enforcement: null, ...over })
const resolution = (slot, over = {}) => ({ slot, provider: `core/${slot}`, extra: [], source: 'default', reason: 'Using the built-in by default.', default: true, fell_back_from: null, refused: false, locality: null, enforcement: null, ...over })
const slot = (id, providers, over = {}) => ({ id, noun: id, multi: false, default_id: `core/${id}`, providers: [provider({ id: `core/${id}`, label: 'Built-in', core: true, tier: 'built-in', extension: null }), ...providers],
  resolution: resolution(id), selected: { global: null, project: null, chat: null }, ...over })
const PARTIAL = { 'workspace-write': { fs_write: 'full', fs_read_deny: 'partial', network: 'full', process: 'full' } }

test('Settings > Runtime picks per scope, confirms a looser choice, tests a provider and accepts a partial dimension', async ({ grain }) => {
  const { page } = grain
  await newChat(page)
  await sayAndWait(page, '!!reply hello', 'hello')
  const puts = []
  const accepts = []
  const view = () => ({ accept_partial: {}, withheld_tools: [], slots: [
    slot('model.router', [provider({ id: 'acme/router', label: 'Acme router' })]),
    slot('sandbox.backend', [provider({ id: 'acme/box', label: 'Acme box', enforcement: PARTIAL })]) ] })
  await page.route(/\/runtime(\?.*)?$/, async (route) => {
    const r = route.request()
    if (r.method() === 'GET') return route.fulfill({ json: view() })
    const body = r.postDataJSON()
    puts.push(body)
    if (body.slot === 'sandbox.backend' && !body.confirm) {
      return route.fulfill({ status: 409, json: { detail: { needs_confirm: true, diff: ['Acme box enforces less: workspace-write fs_read_deny: partial instead of full'] } } })
    }
    return route.fulfill({ json: {} })
  })
  await page.route(/\/runtime\/test$/, (route) => route.fulfill({ json: { ok: false, detail: 'AssertionError: bad stream' } }))
  await page.route(/\/runtime\/accept-partial$/, (route) => { accepts.push(route.request().postDataJSON()); return route.fulfill({ json: {} }) })

  await openSettings(page, 'Agent')
  // The chat is focused, so the choice can be made for this chat.
  const runtime = dialog(page).locator('#sec-runtime')
  await runtime.getByRole('button', { name: 'This chat' }).click()
  await dialog(page).getByLabel('Model router, This chat').selectOption('acme/router')
  await expect.poll(() => puts.length).toBe(1)
  expect(puts[0]).toMatchObject({ slot: 'model.router', scope: 'chat', value: 'acme/router' })
  expect(puts[0].scope_id).toBeTruthy()
  await runtime.getByRole('button', { name: 'Everywhere' }).click()
  await dialog(page).getByLabel('Model router, Everywhere').selectOption('acme/router')
  await expect.poll(() => puts.length).toBe(2)
  expect(puts[1]).toMatchObject({ scope: 'global', scope_id: null })

  // A looser backend shows what it loosens and only applies once confirmed.
  await dialog(page).getByLabel('Sandbox backend, Everywhere').selectOption('acme/box')
  await expect(dialog(page).getByRole('alert')).toContainText('enforces less')
  await dialog(page).getByRole('button', { name: 'Use it anyway' }).click()
  await expect.poll(() => puts.length).toBe(4)
  expect(puts[3]).toMatchObject({ slot: 'sandbox.backend', value: 'acme/box', confirm: true })

  await dialog(page).getByRole('button', { name: 'Test' }).first().click()
  await expect(dialog(page).getByText('Failed: AssertionError: bad stream')).toBeVisible()
  await dialog(page).getByLabel('Accept partial fs read deny').locator('xpath=following-sibling::span[contains(@class,"switch")]').click()
  await expect.poll(() => accepts.length).toBe(1)
  expect(accepts[0]).toEqual({ provider: 'acme/box', dimensions: ['fs_read_deny'] })
  await closeSettings(page)
})

test('the chat\'s runtime menu picks the agent loop for this chat alone', async ({ grain }) => {
  const { page, api } = grain
  await newChat(page)
  await sayAndWait(page, '!!reply hello', 'hello')
  const chat = (await api('/conversations'))[0]
  await page.getByRole('button', { name: 'Runtime for this chat' }).click()
  await page.getByLabel('Agent loop, this chat').selectOption('core/plan-execute-verify')
  await expect.poll(async () => (await api(`/conversations/${chat.id}`)).settings.runtime?.['agent.loop']).toBe('core/plan-execute-verify')
  await expect(page.getByLabel('Agent loop, this chat')).toHaveValue('core/plan-execute-verify')
  expect((await api('/settings')).runtime?.['agent.loop']).toBeUndefined()
  await page.getByLabel('Agent loop, this chat').selectOption('')
  await expect.poll(async () => (await api(`/conversations/${chat.id}`)).settings.runtime?.['agent.loop']).toBeUndefined()
})
