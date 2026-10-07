import { test, expect } from './fixtures.mjs'
import { dialog, openSettings, closeSettings } from './helpers/home.mjs'
import { newChat, msgBox } from './helpers/chat.mjs'
import { openLibrary, PY, STUB } from './helpers/library.mjs'

const quoted = (p) => `"${p}"`

test('Allow all domains and MCP servers: toggle saves at once and shows a red composer pill', async ({ grain }) => {
  const { page, api } = grain
  const pill = page.locator('[data-allow-all-connections]')
  await newChat(page)
  await expect(msgBox(page)).toBeVisible()
  await expect(pill).toHaveCount(0)
  await openSettings(page, 'Permissions')
  const toggle = dialog(page).getByLabel('Allow all domains and MCP servers')
  await expect(toggle).not.toBeChecked()
  await toggle.evaluate((el) => el.click()) // the real input is visually hidden behind the switch
  await expect.poll(async () => (await api('/settings')).allowAllConnections).toBe(true)
  await closeSettings(page)
  await expect(pill).toBeVisible()
  await expect(pill).toContainText('All domains + MCP')
  // the pill opens Settings on Permissions
  await pill.click()
  await expect(dialog(page).getByLabel('Allow all domains and MCP servers')).toBeChecked()
  await dialog(page).getByLabel('Allow all domains and MCP servers').evaluate((el) => el.click())
  await expect.poll(async () => (await api('/settings')).allowAllConnections).toBe(false)
  await closeSettings(page)
  await expect(pill).toHaveCount(0)
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
