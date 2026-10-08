import { test, expect } from './fixtures.mjs'
import { dialog, openSettings, closeSettings } from './helpers/home.mjs'
import { newChat, msgBox } from './helpers/chat.mjs'
import { openLibrary, PY, STUB } from './helpers/library.mjs'

const quoted = (p) => `"${p}"`

test('Allow all domains and MCP servers: lives in Settings > Permissions with the domain list, no composer pill', async ({ grain }) => {
  const { page, api } = grain
  await newChat(page)
  await expect(msgBox(page)).toBeVisible()
  await openSettings(page, 'Permissions')
  await expect(dialog(page).getByText('Allowed hosts after reading untrusted content')).toBeVisible()
  const toggle = dialog(page).getByLabel('Allow all domains and MCP servers')
  await expect(toggle).not.toBeChecked()
  await toggle.evaluate((el) => el.click()) // the real input is visually hidden behind the switch
  await expect.poll(async () => (await api('/settings')).allowAllConnections).toBe(true)
  await closeSettings(page)
  await expect(page.locator('.composer-footer')).not.toContainText('All domains')
  await openSettings(page, 'Permissions')
  await dialog(page).getByLabel('Allow all domains and MCP servers').evaluate((el) => el.click())
  await expect.poll(async () => (await api('/settings')).allowAllConnections).toBe(false)
  await closeSettings(page)
  expect(grain.consoleErrors).toEqual([])
})

test('a custom stdio connector connects and lists its tools', async ({ grain }) => {
  const { page } = grain
  await openLibrary(page, 'Connectors')
  await page.getByRole('button', { name: 'Add custom' }).click()
  const chips = page.getByRole('group', { name: 'Transport' })
  for (const t of ['stdio', 'HTTP', 'SSE']) await expect(chips.getByRole('button', { name: t, exact: true })).toBeVisible()
  await page.getByPlaceholder('Filesystem', { exact: true }).fill('Stub')
  await page.getByPlaceholder(/npx -y/).fill(`${quoted(PY)} ${quoted(STUB)} --mode friendly`)
  await page.getByRole('button', { name: 'Add connector' }).click()
  const head = page.locator('.mcp-server', { hasText: 'Stub' }).first()
  await expect(head.getByText(/connected/)).toBeVisible({ timeout: 60_000 })
  await expect(head.getByText(/5 tools/)).toBeVisible({ timeout: 30_000 })
  await expect(head.locator('.mcp-tool', { hasText: 'mcp__stub__echo' }).first()).toBeVisible()
  expect(grain.consoleErrors).toEqual([])
})
