import { test, expect } from './fixtures.mjs'
import { openTodos, addBox, seed, dayStr, ignoreErrs, row } from './helpers/todos.mjs'

test('500 todos: toggle, filter and scroll stay responsive', async ({ grain }) => {
  test.setTimeout(180_000)
  const { page } = grain
  await seed(grain.api, 500, (i) => ({ title: `bulk todo ${i}`, due: i % 5 === 0 ? dayStr(i % 9 - 3) : null, tags: i % 7 === 0 ? ['seven'] : undefined }))
  await openTodos(page)
  await expect(page.locator('.todo')).toHaveCount(500, { timeout: 20_000 })
  // scroll
  const ms = await page.evaluate(async () => {
    const el = document.querySelector('.page-body')
    const t0 = performance.now()
    for (let y = 0; y < el.scrollHeight; y += 800) { el.scrollTop = y; await new Promise((r) => requestAnimationFrame(r)) }
    return performance.now() - t0
  })
  expect(ms).toBeLessThan(5000)
  // toggle latency: click until row disappears
  const t0 = Date.now()
  await page.getByRole('button', { name: 'Complete: bulk todo 3', exact: true }).click()
  await expect(page.locator('.todo')).toHaveCount(499)
  const dt = Date.now() - t0
  console.log('toggle latency ms', dt)
  expect(dt).toBeLessThan(300)
  // tag filter
  await page.locator('.todo-tag', { hasText: '#seven' }).first().click()
  await expect(page.locator('.todo')).toHaveCount(72)
  await page.getByRole('button', { name: 'Clear tag filter' }).click()
  await expect(page.locator('.todo')).toHaveCount(499)
  // show done
  await page.getByText('Show done').click()
  await expect(page.locator('h4.section-h', { hasText: 'Done' })).toBeVisible()
  expect(ignoreErrs(grain.consoleErrors)).toEqual([])
})

test('projects scope todos separately from personal', async ({ grain }) => {
  const { page } = grain
  const p = await grain.api('/projects', { method: 'POST', body: { name: 'Zeta' } })
  await grain.api('/todos', { method: 'POST', body: { title: 'in project', project_id: p.id } })
  await grain.api('/todos', { method: 'POST', body: { title: 'personal one' } })
  await page.reload()
  await openTodos(page)
  const scope = page.getByLabel('Project scope')
  await expect(page.locator('.todo')).toHaveCount(2)
  await scope.selectOption('personal')
  await expect(page.locator('.todo')).toHaveCount(1)
  await expect(row(page, 'personal one')).toBeVisible()
  await scope.selectOption(p.id)
  await expect(page.locator('.todo')).toHaveCount(1)
  await expect(row(page, 'in project')).toBeVisible()
  await addBox(page).fill('added in project'); await addBox(page).press('Enter')
  await expect(page.locator('.todo')).toHaveCount(2)
  const all = await grain.api('/todos')
  expect(all.find((t) => t.title === 'added in project').project_id).toBe(p.id)
  await scope.selectOption('personal')
  await expect(page.locator('.todo')).toHaveCount(1)
  await addBox(page).fill('added personal'); await addBox(page).press('Enter')
  await expect.poll(async () => (await grain.api('/todos')).find((t) => t.title === 'added personal')?.project_id ?? null).toBeNull()
  expect(ignoreErrs(grain.consoleErrors)).toEqual([])
})

test('persistence across relaunch, Today card matches and can be hidden', async ({ grain }) => {
  await grain.api('/todos', { method: 'POST', body: { title: 'Persist me', due: dayStr(0) } })
  await grain.api('/todos', { method: 'POST', body: { title: 'Done one' } }).then((t) => grain.api(`/todos/${t.id}`, { method: 'PUT', body: { done: true } }))
  const page = await grain.relaunch()
  await openTodos(page)
  await expect(page.locator('.todo')).toHaveCount(1)
  await expect(row(page, 'Persist me')).toBeVisible()
  // Today
  await page.getByRole('button', { name: 'Today', exact: true }).first().click()
  const card = page.locator('section.widget', { hasText: 'View all' }).filter({ hasText: 'Lists' })
  await expect(card.locator('.todo')).toHaveCount(1)
  await expect(card).toContainText('Persist me')
  await expect(card).toContainText('1 open')
  await page.getByRole('button', { name: 'Choose what shows on Today' }).click()
  await page.locator('.home-customize label', { hasText: 'Lists' }).locator('input').click()
  await expect(page.locator('section.widget', { hasText: 'Persist me' })).toHaveCount(0)
  expect(ignoreErrs(grain.consoleErrors)).toEqual([])
})

test('chat tool creates a todo and the open view updates without reload; API writes too', async ({ grain }) => {
  const { page } = grain
  await openTodos(page)
  await grain.api('/todos', { method: 'POST', body: { title: 'from api while open' } })
  await expect(row(page, 'from api while open')).toBeVisible({ timeout: 15_000 })
  // open a chat in the page agent panel (applies to the current view)
  await page.getByRole('button', { name: 'Ask about this page' }).click()
  const box = page.getByPlaceholder(/ask|message|follow/i).last()
  await box.fill('!!tool todo_add {"title":"Made by tool","due":"' + dayStr(1) + '"}')
  await box.press('Enter')
  await page.getByRole('button', { name: 'Approve', exact: true }).click()
  await expect(page.getByText('MOCK: tool done').first()).toBeVisible({ timeout: 30_000 })
  await expect(row(page, 'Made by tool')).toBeVisible({ timeout: 10_000 })
  expect((await grain.api('/todos')).some((t) => t.title === 'Made by tool')).toBeTruthy()
  expect(ignoreErrs(grain.consoleErrors)).toEqual([])
})

test('820x520 window: add row and rows usable without horizontal overflow', async ({ grain }) => {
  const { page } = grain
  await grain.app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].setSize(820, 520))
  await seed(grain.api, 5, (i) => ({ title: `small ${i}`, due: dayStr(i), tags: ['t'], priority: 1 }))
  await openTodos(page)
  await expect(page.locator('.todo')).toHaveCount(5)
  const o = await page.evaluate(() => { const b = document.querySelector('.page-body'); return { sw: b.scrollWidth, cw: b.clientWidth, w: window.innerWidth } })
  const offenders = await page.evaluate(() => { const b = document.querySelector('.page-body'); const r = b.getBoundingClientRect(); return [...b.querySelectorAll('*')].filter((e) => e.getBoundingClientRect().right > r.right + 1).slice(0, 6).map((e) => e.tagName + '.' + e.className) })
  console.log('OFFENDERS', JSON.stringify(offenders))
  expect(o.w).toBeLessThanOrEqual(830)
  expect(o.sw - o.cw).toBeLessThanOrEqual(1)
  // every row's delete button within viewport
  const box = await page.getByRole('button', { name: 'Delete todo: small 0' }).boundingBox()
  expect(box.x + box.width).toBeLessThanOrEqual(o.w)
  // The Add button appears once there is something to add; it must sit inside the window too.
  await addBox(page).fill('one more')
  await expect(page.getByRole('button', { name: 'Add', exact: true })).toBeInViewport()
  expect(ignoreErrs(grain.consoleErrors)).toEqual([])
})

test('sync UI degrades without Google; list/board view toggles; subtask; sort', async ({ grain }) => {
  const { page } = grain
  await grain.api('/todos', { method: 'POST', body: { title: 'Parent' } })
  await openTodos(page)
  await expect(page.getByTitle('Sync with Google Tasks now')).toHaveCount(0)
  await page.getByRole('button', { name: 'Add subtask to Parent' }).click()
  await page.getByLabel('New subtask of Parent').fill('Child'); await page.getByLabel('New subtask of Parent').press('Enter')
  await expect(page.locator('.todo')).toHaveCount(2)
  await page.getByLabel('Sort').selectOption('urgency')
  await expect(page.locator('.todo')).toHaveCount(2)
  await page.getByRole('button', { name: 'Board view' }).click()
  await expect(page.getByText('Parent').first()).toBeVisible()
  await page.getByRole('button', { name: 'List view' }).click()
  await expect(page.locator('.todo')).toHaveCount(2)
  expect(ignoreErrs(grain.consoleErrors)).toEqual([])
})
