import { test, expect } from './fixtures.mjs'
import { dialog, openSettings } from './helpers/home.mjs'

const row = (page, name) => page.locator('.sidebar .nav-item', { hasText: new RegExp(`^${name}`) })
const profile = (page, name) => dialog(page).getByRole('checkbox', { name, exact: true })
const flip = (page, name) => profile(page, name).locator('xpath=following-sibling::span[contains(@class,"switch")]').click()

test('a profile switch applies its defaults at once and turning it off puts them back', async ({ grain }) => {
  const { page, api } = grain
  await expect(row(page, 'Lists')).toHaveCount(1)
  await openSettings(page, 'Sidebar and layout')
  // The harness starts with every view on. If a first boot recorded Lean as on, switch it off first and wait for that to land.
  if (await profile(page, 'Lean').isChecked()) {
    await flip(page, 'Lean')
    await expect(profile(page, 'Lean')).not.toBeChecked()
    await expect(row(page, 'Lists')).toHaveCount(1)
  }
  await flip(page, 'Lean')
  await expect(profile(page, 'Lean')).toBeChecked()
  await expect(row(page, 'Lists')).toHaveCount(0)
  expect((await api('/settings')).hiddenViews).toEqual(expect.arrayContaining(['todos', 'calendar']))

  await flip(page, 'Lean')
  await expect(profile(page, 'Lean')).not.toBeChecked()
  await expect(row(page, 'Lists')).toHaveCount(1)
  expect((await api('/settings')).hiddenViews).toEqual([])

  await flip(page, 'Focus')
  await expect.poll(async () => (await api('/settings')).defaultEffort).toBe('low')
  await expect.poll(async () => (await api('/profiles')).find((x) => x.id === 'focus').enabled).toBe(true)
  await flip(page, 'Focus')
  await expect.poll(async () => (await api('/settings')).defaultEffort).toBe('high')
})

test('a profile that hides Lists takes its todo chips off the Calendar', async ({ grain }) => {
  const { page, api } = grain
  await api('/todos', { method: 'POST', body: { title: 'Chip probe', due: new Date().toISOString().slice(0, 10) } })
  await page.reload()
  const chips = page.locator('.cal-chip.todo')
  await row(page, 'Calendar').click()
  await expect(chips).toHaveCount(1)
  await openSettings(page, 'Sidebar and layout')
  await flip(page, 'Focus')
  await expect(row(page, 'Lists')).toHaveCount(0)
  await dialog(page).getByRole('button', { name: 'Close' }).first().click()
  await expect(chips).toHaveCount(0)
})
