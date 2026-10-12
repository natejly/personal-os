// Coverage for the 2026-10-12 merges: model duels, shipped sidebar rows, General first, appearance presets.
import { test, expect } from './fixtures.mjs'
import { dialog, openSettings } from './helpers/home.mjs'
import { newChat, say, assistants, users, anyStop, msgBox } from './helpers/chat.mjs'

const benign = (e) => /ResizeObserver|favicon|Autofill|DevTools/i.test(e)

test('Settings opens on General and the tabs run in the shipped order', async ({ grain }) => {
  const { page } = grain
  await openSettings(page)
  await expect(dialog(page).getByRole('tab', { selected: true })).toHaveText(/^General/)
  const tabs = await dialog(page).getByRole('tab').allInnerTexts()
  expect(tabs.map((t) => t.trim().split('\n')[0].replace(/(Dark|Light|System)$/, ''))).toEqual(['General', 'Sidebar', 'Model', 'Models', 'Agents', 'Memory', 'Integrations', 'Permissions', 'System access', 'Usage', 'Advanced'])
  expect(grain.consoleErrors.filter((e) => !benign(e))).toEqual([])
})

test('a fresh data dir shows Files, Lists, Calendar, Library, Jobs, Canvases and Projects; Mail and Health are off', async ({ grain }) => {
  const { page, api } = grain
  expect((await api('/settings')).sidebarHidden).toEqual(['mail', 'health'])
  const sb = page.locator('.sidebar')
  const rows = ['Files', 'Lists', 'Calendar', 'Library', 'Jobs']
  const labels = (await sb.locator('.nav-item:not(.nav-more)').allInnerTexts()).map((t) => t.split('\n')[0].trim())
  expect(labels.filter((l) => [...rows, 'Mail', 'Health'].includes(l))).toEqual(rows)
  await expect(sb.getByRole('button', { name: /Canvases/ })).toBeVisible()
  await expect(sb.getByRole('button', { name: /Projects/ })).toBeVisible()
})

test('an appearance preset sets theme, accent and fonts together, and Save keeps it', async ({ grain }) => {
  const { page, api } = grain
  const d = await openSettings(page, 'General')
  await d.getByRole('radiogroup', { name: 'Appearance presets' }).getByRole('radio', { name: /Terminal/ }).click()
  const attrs = () => page.evaluate(() => ({ ...document.documentElement.dataset }))
  await expect.poll(attrs).toMatchObject({ theme: 'dark', accent: 'mint', font: 'mono', code: 'menlo' })
  await d.getByRole('radiogroup', { name: 'Appearance presets' }).getByRole('radio', { name: /Paper/ }).click()
  await expect.poll(attrs).toMatchObject({ theme: 'light', accent: 'clay', font: 'serif' })
  await d.getByRole('button', { name: /^(Save|Done)/ }).first().click()
  await expect(dialog(page)).toHaveCount(0)
  await expect.poll(async () => (await api('/settings')).accent).toBe('clay')
  expect((await api('/settings')).uiFont).toBe('serif')
  expect(grain.consoleErrors.filter((e) => !benign(e))).toEqual([])
})

test('A/B duel: both columns stream, picking B keeps only B as the reply and the vote is counted', async ({ grain }) => {
  const { page, api } = grain
  await api('/settings', { method: 'PUT', body: { devTools: true } })
  await page.reload()
  await newChat(page)
  const ab = page.getByRole('button', { name: 'A/B', exact: true })
  await ab.click()
  await expect(ab).toHaveAttribute('aria-pressed', 'true')
  await say(page, '!!reply duel-answer')
  const panel = page.getByRole('group', { name: 'Model duel' })
  await expect(panel).toBeVisible({ timeout: 30_000 })
  await expect(panel.locator('.duel-body')).toHaveCount(2)
  await expect(panel.locator('.duel-body').first()).toContainText('duel-answer', { timeout: 30_000 })
  await expect(panel.locator('.duel-body').nth(1)).toContainText('duel-answer', { timeout: 30_000 })
  await expect(panel.getByRole('button', { name: 'B is better →' })).toBeEnabled({ timeout: 30_000 })
  await panel.getByRole('button', { name: 'B is better →' }).click()
  await expect(panel).toHaveCount(0)
  await expect(anyStop(page)).toHaveCount(0, { timeout: 30_000 })
  await expect(users(page)).toHaveCount(1)
  await expect(assistants(page)).toHaveCount(1)
  await expect(assistants(page).first()).toContainText('duel-answer')
  await expect.poll(async () => (await api('/model-votes')).votes).toBe(1)
  expect(grain.consoleErrors.filter((e) => !benign(e))).toEqual([])
})
