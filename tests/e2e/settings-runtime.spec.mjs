import { test, expect } from './fixtures.mjs'
import { dialog, openSettings, closeSettings } from './helpers/home.mjs'

const SLOTS = ['model.client', 'model.router', 'tools.source', 'sandbox.backend', 'agent.loop']

test('Settings > Runtime shows every slot on its built-in provider, with the reason', async ({ grain }) => {
  const { page, api } = grain
  await openSettings(page)
  await dialog(page).getByRole('tab', { name: 'Runtime' }).click()
  for (const slot of SLOTS) {
    await expect(dialog(page).getByTestId(`runtime-${slot}-reason`)).toContainText('by default')
  }
  // Nothing but the built-in providers exists, so there is nothing to pick and the endpoint agrees.
  await expect(dialog(page).getByLabel('Model router, Everywhere')).toBeDisabled()
  const resolved = await api('/runtime/resolve')
  expect(Object.keys(resolved).sort()).toEqual([...SLOTS].sort())
  expect(Object.values(resolved).every((r) => r.default === true && r.source === 'default')).toBe(true)
  await closeSettings(page)
})
