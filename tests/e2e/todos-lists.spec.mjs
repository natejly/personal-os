import { test, expect } from './fixtures.mjs'
import { openTodos, addBox, ignoreErrs, row } from './helpers/todos.mjs'

test('lists: make one, add onto it, switch, rename, remove (its items go back to Todos)', async ({ grain }) => {
  const { page, api } = grain
  await api('/todos', { method: 'POST', body: { title: 'Default one' } })
  await openTodos(page)
  const rail = page.getByRole('complementary', { name: 'Lists' })
  await page.getByRole('button', { name: 'New list' }).click()
  await page.getByLabel('New list name').fill('Groceries')
  await page.getByLabel('New list name').press('Enter')
  // The new list is selected and the add box targets it.
  await expect(rail.locator('.list-row.active')).toContainText('Groceries')
  const box = page.getByPlaceholder('Add to Groceries…')
  await box.fill('Milk'); await box.press('Enter')
  await expect(row(page, 'Milk')).toBeVisible()
  await expect(row(page, 'Default one')).toHaveCount(0)
  await expect.poll(async () => (await api('/todos')).find((t) => t.title === 'Milk')?.list_name).toBe('Groceries')
  expect(await api('/todo-lists')).toEqual(['Groceries'])
  // Every list at once, then the default list alone
  await rail.locator('.list-row', { hasText: 'All' }).click()
  await expect(row(page, 'Milk')).toBeVisible()
  await expect(row(page, 'Default one')).toBeVisible()
  await rail.locator('.list-row', { hasText: 'Todos' }).click()
  await expect(row(page, 'Milk')).toHaveCount(0)
  await expect(addBox(page)).toBeVisible()
  // Rename moves the items along
  const gro = rail.locator('.list-row', { hasText: 'Groceries' })
  await gro.hover()
  await gro.getByRole('button', { name: 'Rename list Groceries' }).click()
  await page.getByLabel('Rename list Groceries').fill('Shopping')
  await page.getByLabel('Rename list Groceries').press('Enter')
  await expect(rail.locator('.list-row', { hasText: 'Shopping' })).toBeVisible()
  await expect.poll(async () => (await api('/todos')).find((t) => t.title === 'Milk')?.list_name).toBe('Shopping')
  // Removing the list keeps the item
  const shop = rail.locator('.list-row', { hasText: 'Shopping' })
  await shop.hover()
  await shop.getByRole('button', { name: 'Remove list Shopping' }).click()
  await expect(shop).toHaveCount(0)
  await expect.poll(async () => (await api('/todos')).find((t) => t.title === 'Milk')?.list_name).toBeNull()
  expect(await api('/todo-lists')).toEqual([])
  expect((await api('/todos')).map((t) => t.title).sort()).toEqual(['Default one', 'Milk'])
  expect(ignoreErrs(grain.consoleErrors)).toEqual([])
})

test('an empty list survives a reload; the add row keeps date, priority and repeat behind one toggle', async ({ grain }) => {
  const { page, api } = grain
  await api('/todo-lists', { method: 'POST', body: { name: 'Someday maybe' } })
  await openTodos(page)
  const rail = page.getByRole('complementary', { name: 'Lists' })
  await expect(rail.locator('.list-row', { hasText: 'Someday maybe' })).toBeVisible()
  // Hidden until asked for
  await expect(page.getByLabel('Due date (optional)')).toHaveCount(0)
  await page.getByRole('button', { name: 'Due date, priority, repeat' }).click()
  await page.getByLabel('Priority', { exact: true }).selectOption('1')
  await page.getByLabel('Repeat', { exact: true }).selectOption('week')
  await addBox(page).fill('Water plants'); await addBox(page).press('Enter')
  await expect.poll(async () => {
    const t = (await api('/todos')).find((x) => x.title === 'Water plants')
    return t && [t.priority, t.repeat?.unit, t.list_name]
  }).toEqual([1, 'week', null])
  expect(ignoreErrs(grain.consoleErrors)).toEqual([])
})
