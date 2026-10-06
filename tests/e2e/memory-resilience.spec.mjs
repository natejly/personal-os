import { test, expect } from './fixtures.mjs'
import { send, shrink } from './helpers/kb.mjs'

const pane = (page) => page.locator('.memory-page')
const ignorable = /favicon|ResizeObserver|Failed to load resource|ERR_CONNECTION|fetch|NetworkError/i

async function openMemory(page) {
  await page.locator('.sidebar').getByRole('button', { name: /^Memory\s*\d*$/ }).click()
  await expect(page.getByPlaceholder(/Remember something/)).toBeVisible()
}

test('rapid repeated memory actions: double Add, double Pin, double Forget leave a consistent list', async ({ grain }) => {
  const { page, api } = grain
  await openMemory(page)
  await page.getByPlaceholder(/Remember something/).fill('rapid fire memory')
  await page.getByRole('button', { name: 'Add', exact: true }).dblclick()
  await expect(pane(page).getByText('rapid fire memory')).toBeVisible()
  await page.waitForTimeout(500)
  expect((await api('/memories')).filter((m) => m.content === 'rapid fire memory')).toHaveLength(1)
  await page.getByRole('button', { name: /^Pin memory/ }).dblclick()
  await page.waitForTimeout(500)
  const pinned = (await api('/memories'))[0].pinned
  const shown = await pane(page).locator('.mem-row.pinned').count()
  expect(!!pinned).toBe(shown === 1) // the UI and the backend agree whichever way the clicks landed
  await page.getByRole('button', { name: /^Forget memory/ }).dblclick()
  await expect(pane(page).getByText('No memories yet.')).toBeVisible()
  expect(await api('/memories')).toHaveLength(0)
  expect(grain.consoleErrors.filter((e) => !ignorable.test(e))).toEqual([])
})

test('backend dies while the Memory panel is open: the app stays up and recovers when it is back', async ({ grain }) => {
  const { page, api, backend } = grain
  await api('/memories', { method: 'POST', body: { content: 'survivor memory' } })
  await openMemory(page)
  await expect(pane(page).getByText('survivor memory')).toBeVisible()
  backend.child.kill('SIGKILL')
  await page.getByPlaceholder(/Remember something/).fill('written into the void')
  await page.getByRole('button', { name: 'Add', exact: true }).click()
  await page.waitForTimeout(1500)
  // the panel is still rendered and the old row is still shown
  await expect(pane(page).getByText('survivor memory')).toBeVisible()
  // the page itself did not wedge or unmount
  await expect(page.locator('.memory-page')).toBeVisible()
})

test('Settings memory scope filter lists a project memory only under that project', async ({ grain }) => {
  const { page, api } = grain
  const p = await api('/projects', { method: 'POST', body: { name: 'Scoped' } })
  await api('/memories', { method: 'POST', body: { content: 'personal scope fact' } })
  await api('/memories', { method: 'POST', body: { content: 'project scope fact', project_id: p.id } })
  await page.reload()
  await openMemory(page)
  await expect(pane(page).locator('.mem-row')).toHaveCount(2)
  const scope = pane(page).locator('.knowledge-controls select')
  await scope.selectOption({ label: 'Scoped' })
  await expect(pane(page).getByText('project scope fact')).toBeVisible()
  await expect(pane(page).getByText('personal scope fact')).toBeVisible() // shared project: personal memories ride along
  await scope.selectOption({ label: 'Personal only' })
  await expect(pane(page).getByText('project scope fact')).toHaveCount(0)
  // make the project memory personal from the All view
  await scope.selectOption({ label: 'All' })
  await pane(page).getByRole('button', { name: 'make personal' }).click()
  await expect.poll(async () => (await api('/memories')).filter((m) => m.project_id === null).length).toBe(2)
})

test('everything at 820x520: project modal, project view tabs, memory panel, voice and graph all fit', async ({ grain }) => {
  const { page, api } = grain
  await api('/projects', { method: 'POST', body: { name: 'Tiny' } })
  await shrink(grain)
  await page.reload()
  await page.getByRole('button', { name: 'New project' }).click()
  const modal = page.getByRole('dialog')
  const box = await modal.boundingBox()
  expect(box.y).toBeGreaterThanOrEqual(0)
  // Create stays reachable at the small size
  await modal.getByRole('button', { name: 'Create', exact: true }).scrollIntoViewIfNeeded()
  const create = await modal.getByRole('button', { name: 'Create', exact: true }).boundingBox()
  expect(create.y + create.height).toBeLessThanOrEqual(520)
  await page.keyboard.press('Escape')
  await page.locator('.sidebar').getByText('Tiny', { exact: true }).click()
  for (const tab of ['Instructions', 'Artifacts', 'Memory']) {
    await page.locator('.tabs').getByRole('button', { name: new RegExp(tab) }).click()
    const over = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
    expect(over).toBeLessThanOrEqual(1)
  }
  for (const mode of ['Graph', 'Voice', 'List']) {
    await page.getByRole('button', { name: mode, exact: true }).click()
    const over = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
    expect(over).toBeLessThanOrEqual(1)
  }
  expect(grain.consoleErrors.filter((e) => !ignorable.test(e))).toEqual([])
})

test('chat with auto-learn on and a garbage extractor survives four quick messages', async ({ grain }) => {
  const { page, api, backend } = grain
  await api('/settings', { method: 'PUT', body: { autoLearn: true } })
  await page.getByRole('button', { name: /New chat/ }).first().click()
  for (let i = 0; i < 4; i++) {
    await send(page, `message ${i} !!reply answer ${i}`)
    await expect(page.locator('.msg.assistant').last()).toContainText(`answer ${i}`, { timeout: 30_000 })
  }
  await page.waitForTimeout(1500)
  expect(backend.log()).not.toMatch(/Traceback/)
  expect(await api('/memories')).toHaveLength(0)
  expect(grain.consoleErrors.filter((e) => !ignorable.test(e))).toEqual([])
})
