import { test, expect } from './fixtures.mjs'
import { dialog, openMemory, openSettings } from './helpers/home.mjs'

const benign = (e) => /ResizeObserver|favicon/i.test(e)
const noErrors = (grain) => expect(grain.consoleErrors.filter((e) => !benign(e))).toEqual([])
const row = (page, name) => page.locator('.sidebar .nav-item', { hasText: new RegExp(`^${name}`) })
/** The Sidebar switches in Settings → Appearance, by row label. */
const toggle = (page, name) => dialog(page).locator('h4', { hasText: 'Sidebar' }).locator('xpath=following-sibling::div[1]').getByRole('checkbox', { name, exact: true })

test('a sidebar row turns off at once with no Save, stays off after a relaunch, and comes back', async ({ grain }) => {
  const { page, api } = grain
  await expect(row(page, 'Lists')).toHaveCount(1)
  await openSettings(page, 'Appearance')
  await expect(toggle(page, 'Lists')).toBeChecked()
  await toggle(page, 'Lists').click({ force: true })
  await expect(toggle(page, 'Lists')).not.toBeChecked()
  await expect(row(page, 'Lists')).toHaveCount(0)
  await expect.poll(async () => (await api('/settings')).hiddenViews).toEqual(['todos'])
  // Nothing is unsaved: Settings closes without asking.
  await page.keyboard.press('Escape')
  await expect(dialog(page)).toHaveCount(0)

  const p2 = await grain.relaunch()
  await expect(p2.locator('.sidebar').first()).toBeVisible()
  await expect(row(p2, 'Lists')).toHaveCount(0)
  await openSettings(p2, 'Appearance')
  await expect(toggle(p2, 'Lists')).not.toBeChecked()
  await toggle(p2, 'Lists').click({ force: true })
  await expect(toggle(p2, 'Lists')).toBeChecked()
  await expect(row(p2, 'Lists')).toHaveCount(1)
  await expect.poll(async () => (await api('/settings')).hiddenViews).toEqual([])
  noErrors(grain)
})

test('Settings → Memory opens split, with the graph and the list side by side', async ({ grain }) => {
  const { page } = grain
  await openMemory(page)
  const group = dialog(page).getByRole('group', { name: 'Memory layout' })
  await expect(group.getByRole('button', { name: 'Split', exact: true })).toHaveAttribute('aria-pressed', 'true')
  await expect(dialog(page).locator('.knowledge-body .graph-body')).toBeVisible()
  await expect(dialog(page).locator('.knowledge-body .mem-pane')).toBeVisible()
  await group.getByRole('button', { name: 'List', exact: true }).click()
  await expect(dialog(page).locator('.knowledge-body .graph-body')).toHaveCount(0)
  await expect(dialog(page).locator('.knowledge-body .mem-pane')).toBeVisible()
  noErrors(grain)
})

test('Today → Recently learned → View all lands on the Memory tab', async ({ grain }) => {
  const { page } = grain
  await page.locator('.nav-item', { hasText: 'Today' }).first().click()
  await page.locator('main.home section.widget', { has: page.locator('header', { hasText: 'Recently learned' }) }).getByRole('button', { name: 'View all' }).click()
  await expect(dialog(page).getByRole('tab', { name: 'Memory' })).toHaveAttribute('aria-selected', 'true')
  await expect(dialog(page).locator('.knowledge-body .graph-body')).toBeVisible()
  await expect(dialog(page).locator('.knowledge-body .mem-pane')).toBeVisible()
  noErrors(grain)
})
