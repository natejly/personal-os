import { test, expect } from './fixtures.mjs'
import { dialog, openAdvanced, openSettings } from './helpers/home.mjs'
import { newChat, sayAndWait } from './helpers/chat.mjs'

const preset = (page, id) => dialog(page).locator(`.preset-list li[data-preset="${id}"]`)

test('a stricter preset applies at once and one that allows more shows its diff first', async ({ grain }) => {
  const { page, api } = grain
  await openSettings(page, 'Permissions')
  await expect(dialog(page).locator('.preset-list li')).toHaveCount(4)

  await preset(page, 'builtin:read-only').getByRole('button', { name: 'Apply Read only' }).click()
  await expect.poll(async () => (await api('/settings')).permissionMode).toBe('manual')
  await expect.poll(async () => (await api('/settings')).tools?.shell_run).toBe('off')

  await preset(page, 'builtin:allow-all').getByRole('button', { name: 'Apply Allow everything' }).click()
  const held = dialog(page).getByRole('alert').filter({ hasText: 'would allow more' })
  await expect(held).toBeVisible()
  await expect(held.locator('tr.loosens')).toContainText('Permission mode')
  expect((await api('/settings')).permissionMode).toBe('manual')

  await held.getByRole('button', { name: 'Cancel' }).click()
  await expect(held).toHaveCount(0)
  expect((await api('/settings')).permissionMode).toBe('manual')

  await preset(page, 'builtin:allow-all').getByRole('button', { name: 'Apply Allow everything' }).click()
  await held.getByRole('button', { name: 'Apply anyway' }).click()
  await expect.poll(async () => (await api('/settings')).permissionMode).toBe('allow_all')
})

test('your own preset can be made, duplicated and deleted', async ({ grain }) => {
  const { page } = grain
  await openSettings(page, 'Permissions')
  await dialog(page).getByRole('button', { name: 'New preset' }).click()
  const editor = dialog(page).getByRole('group', { name: 'New preset' })
  await editor.getByLabel('Preset name').fill('Careful')
  await editor.getByRole('checkbox', { name: 'Permission mode' }).check()
  await editor.getByRole('button', { name: 'Save preset' }).click()
  await expect(preset(page, 'user:careful')).toBeVisible()
  await preset(page, 'user:careful').getByRole('button', { name: 'Duplicate Careful' }).click()
  await expect(preset(page, 'user:careful-copy')).toBeVisible()
  await preset(page, 'user:careful-copy').getByRole('button', { name: 'Delete Careful copy' }).click()
  await expect(preset(page, 'user:careful-copy')).toHaveCount(0)
  await expect(preset(page, 'builtin:auto').getByRole('button', { name: 'Edit Auto' })).toHaveCount(0)
})

test('the effective configuration lists every setting with its source and is read-only', async ({ grain }) => {
  const { page } = grain
  await openSettings(page, 'Permissions')
  await preset(page, 'builtin:allow-all').getByRole('button', { name: 'Apply Allow everything' }).click()
  await dialog(page).getByRole('alert').getByRole('button', { name: 'Apply anyway' }).click()
  await expect(page.getByText('Allow everything applied')).toBeVisible()
  await openAdvanced(page, 'Effective configuration')
  const table = dialog(page).locator('.eff-table')
  await expect(table.locator('tbody tr')).not.toHaveCount(0)
  await dialog(page).getByLabel('Search the effective configuration').fill('permissionMode')
  await expect(table.locator('tr[data-key="permissionMode"]')).toContainText('preset:builtin:allow-all')
  await expect(table.locator('tbody tr')).toHaveCount(1)
  await dialog(page).getByLabel('Search the effective configuration').fill('')
  await dialog(page).getByRole('group', { name: 'Source' }).getByRole('button', { name: 'preset', exact: true }).click()
  await expect(table.locator('tbody tr').first()).toContainText('preset:builtin:allow-all')
  await expect(table.getByRole('textbox')).toHaveCount(0)
})

test('the composer pill\'s menu applies a preset to this chat alone', async ({ grain }) => {
  const { page, api } = grain
  await newChat(page)
  await sayAndWait(page, '!!reply hello', 'hello')
  const chat = (await api('/conversations'))[0]
  await page.locator('.composer-footer .skip-perms').click()
  await page.getByRole('button', { name: /^Read only/ }).click()
  await expect(page.getByText('Read only applied to this chat')).toBeVisible()
  expect((await api(`/conversations/${chat.id}`)).settings.permissions).toBeTruthy()
  expect((await api('/settings')).tools?.shell_run).not.toBe('off')
  // A preset that allows more than the chat has shows what would change and applies on the second click.
  await page.locator('.composer-footer .skip-perms').click()
  await page.getByRole('button', { name: /^Allow everything/ }).click()
  await expect(page.getByRole('alert').filter({ hasText: 'would allow more' })).toBeVisible()
  await page.getByRole('button', { name: 'Apply anyway' }).click()
  await expect(page.getByText('Allow everything applied to this chat')).toBeVisible()
  // The menu still opens Settings > Permissions.
  await page.locator('.composer-footer .skip-perms').click()
  await page.getByRole('button', { name: 'Permission settings' }).click()
  await expect(dialog(page).getByRole('radiogroup', { name: 'Permission mode' })).toBeVisible()
})
