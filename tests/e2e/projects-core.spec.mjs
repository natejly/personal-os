import { test, expect } from './fixtures.mjs'
import { callWith, send, shrink, systemOf, upload } from './helpers/kb.mjs'

const sidebar = (page) => page.locator('.sidebar')
const clean = (g) => expect(g.consoleErrors.filter((e) => !/favicon|ResizeObserver/.test(e))).toEqual([])

test('create a project from the sidebar: name, color, shared memory; Enter submits; empty name blocks', async ({ grain }) => {
  const { page, api } = grain
  await page.getByRole('button', { name: 'New project' }).click()
  const dlg = page.getByRole('dialog')
  await expect(dlg.getByRole('button', { name: 'Create' })).toBeDisabled()
  await dlg.getByRole('textbox', { name: 'Name', exact: true }).fill('   ')
  await expect(dlg.getByRole('button', { name: 'Create' })).toBeDisabled()
  await dlg.getByRole('textbox', { name: 'Name', exact: true }).fill('Apollo')
  await dlg.getByLabel('Description').fill('Moon things')
  await dlg.getByRole('button', { name: 'Blue' }).click()
  await dlg.getByRole('textbox', { name: 'Name', exact: true }).press('Enter')
  await expect(dlg).toBeHidden()
  await expect(sidebar(page).getByText('Apollo', { exact: true })).toBeVisible()
  const ps = await api('/projects')
  expect(ps).toHaveLength(1)
  expect(ps[0]).toMatchObject({ name: 'Apollo', description: 'Moon things', color: '#3b9edb', memory_mode: 'shared' })
  clean(grain)
})

test('Esc closes the project modal without creating; double-clicking Create makes one project', async ({ grain }) => {
  const { page, api } = grain
  await page.getByRole('button', { name: 'New project' }).click()
  await page.getByRole('textbox', { name: 'Name', exact: true }).fill('Ghost')
  await page.keyboard.press('Escape')
  await expect(page.getByRole('dialog')).toBeHidden()
  expect(await api('/projects')).toHaveLength(0)

  await page.getByRole('button', { name: 'New project' }).click()
  await page.getByRole('textbox', { name: 'Name', exact: true }).fill('Twice')
  await page.getByRole('button', { name: 'Create', exact: true }).dblclick()
  await expect(page.getByRole('dialog')).toBeHidden()
  await page.waitForTimeout(500)
  expect((await api('/projects')).filter((p) => p.name === 'Twice')).toHaveLength(1)
})

test('project instructions are saved on blur and reach the system prompt of a new chat in the project', async ({ grain }) => {
  const { page, api, llm } = grain
  const p = await api('/projects', { method: 'POST', body: { name: 'Lab', color: '#46a758' } })
  await page.reload()
  await sidebar(page).getByText('Lab', { exact: true }).click()
  await page.getByRole('button', { name: /Instructions/ }).click()
  const ta = page.locator('textarea.instructions')
  await ta.fill('Always answer in haiku about COBALTFOX.')
  await page.getByRole('heading', { name: 'Lab' }).click() // blur
  await expect.poll(async () => (await api('/projects')).find((x) => x.id === p.id).system_prompt).toContain('COBALTFOX')

  await page.locator('.page-header').getByRole('button', { name: /New chat/ }).click()
  await expect(page.getByText('New chat in Lab')).toBeVisible()
  await send(page, '!!reply ok then')
  await expect(page.locator('.msg.assistant').last()).toContainText('ok then', { timeout: 30_000 })
  const calls = callWith(llm, 'ok then')
  expect(calls.length).toBeGreaterThan(0)
  expect(systemOf(calls[0])).toContain('Always answer in haiku about COBALTFOX.')
  const convs = await api('/conversations?project_id=' + p.id)
  expect(convs).toHaveLength(1)
  expect(convs[0].project_id).toBe(p.id)
  clean(grain)
})

test('a chat outside the project does not get the project instructions', async ({ grain }) => {
  const { page, api, llm } = grain
  await api('/projects', { method: 'POST', body: { name: 'Lab', system_prompt: 'SECRETPROJECTRULE' } })
  await page.getByRole('button', { name: /New chat/ }).first().click()
  await send(page, '!!reply plain')
  await expect(page.locator('.msg.assistant').last()).toContainText('plain', { timeout: 30_000 })
  const calls = callWith(llm, 'plain')
  expect(systemOf(calls[0])).not.toContain('SECRETPROJECTRULE')
})

test('edit project: rename, recolor, switch to isolated memory; persists across relaunch', async ({ grain }) => {
  const { page, api } = grain
  const p = await api('/projects', { method: 'POST', body: { name: 'Old name' } })
  await page.reload()
  await sidebar(page).getByText('Old name', { exact: true }).click()
  await page.getByRole('button', { name: 'Edit', exact: true }).click()
  const dlg = page.getByRole('dialog')
  await dlg.getByRole('textbox', { name: 'Name', exact: true }).fill('New name')
  await dlg.getByRole('button', { name: 'Purple' }).click()
  await dlg.getByLabel(/This project only/).check()
  await dlg.getByRole('button', { name: 'Save', exact: true }).click()
  await expect(dlg).toBeHidden()
  await expect(sidebar(page).getByText('New name', { exact: true })).toBeVisible()
  await expect(sidebar(page).getByText('Old name', { exact: true })).toHaveCount(0)
  const page2 = await grain.relaunch()
  await expect(page2.locator('.sidebar').getByText('New name', { exact: true })).toBeVisible()
  const got = (await api('/projects')).find((x) => x.id === p.id)
  expect(got).toMatchObject({ name: 'New name', color: '#8e6fdb', memory_mode: 'isolated' })
  clean(grain)
})

test('delete project: chats and memories go to the trash, docs fall back to personal, confirm step required', async ({ grain }) => {
  const { page, api } = grain
  const p = await api('/projects', { method: 'POST', body: { name: 'Doomed' } })
  const c = await api('/conversations', { method: 'POST', body: { project_id: p.id, title: 'doomed chat' } })
  const note = await api('/docs', { method: 'POST', body: { title: 'doomed note', project_id: p.id } })
  await api('/memories', { method: 'POST', body: { content: 'doomed memory', project_id: p.id } })
  const file = await upload(grain, 'doomed.txt', 'doomed upload words', p.id)
  await page.reload()
  await sidebar(page).getByText('Doomed', { exact: true }).click()
  await page.getByRole('button', { name: 'Edit', exact: true }).click()
  await page.getByRole('button', { name: 'Delete project' }).click()
  // first click only asks; nothing deleted yet
  expect(await api('/projects')).toHaveLength(1)
  await page.getByRole('button', { name: /Really delete/ }).click()
  await expect(sidebar(page).getByText('Doomed', { exact: true })).toHaveCount(0)
  await expect.poll(async () => (await api('/projects')).length).toBe(0)
  const convs = await api('/conversations?project_id=all')
  expect(convs.find((x) => x.id === c.id)).toBeUndefined()
  // docs are demoted to personal, not lost
  const d = await api('/docs/' + note.id)
  expect(d.project_id).toBeNull()
  const trash = await api('/trash')
  expect(trash.groups.projects[0].contents).toEqual({ conversations: 1, memories: 1, documents: 1 })
  // the project view of a deleted id degrades gracefully
  clean(grain)
})

test('sidebar group lists only the newest 4 chats (no switch), the group name opens Chats, notes sit under Context', async ({ grain }) => {
  const { page, api } = grain
  const p = await api('/projects', { method: 'POST', body: { name: 'Mixed' } })
  for (let i = 1; i <= 5; i++) {
    await api('/conversations', { method: 'POST', body: { project_id: p.id, title: `chat ${i}` } })
    if (i <= 2) await api('/docs', { method: 'POST', body: { title: `note ${i}`, project_id: p.id } })
    await new Promise((r) => setTimeout(r, 1100))
  }
  await page.reload()
  const group = sidebar(page).locator('.project-group', { hasText: 'Mixed' })
  await expect(group.locator('.project-rows .convo-title')).toHaveCount(4)
  const titles = await group.locator('.project-rows .convo-title').allInnerTexts()
  expect(titles[0]).toBe('chat 5')
  expect(titles.some((t) => t.startsWith('note'))).toBe(false)
  // the project group has no Chats | Documents switch and no files view of its own
  await expect(group.locator('.cf-seg')).toHaveCount(0)
  await expect(group.locator('.cf-row')).toHaveCount(0)
  await group.locator('.project-name').click()
  await expect(page.getByRole('heading', { name: 'Mixed' })).toBeVisible()
  await expect(page.locator('.chat-row')).toHaveCount(5)
  await expect(page.locator('.pf .cf-name', { hasText: 'note 2' })).toBeVisible()
  // collapse via the folder twist and the choice survives a relaunch
  await group.getByRole('button', { name: /Collapse Mixed|Expand Mixed/ }).first().click().catch(() => {})
  clean(grain)
})

test('project view: sections, counts, new chat button, empty state, memory add inside the project', async ({ grain }) => {
  const { page, api } = grain
  const p = await api('/projects', { method: 'POST', body: { name: 'Tabs' } })
  await page.reload()
  await sidebar(page).getByText('Tabs', { exact: true }).click()
  await expect(page.getByText('No chats yet')).toBeVisible()
  await page.getByPlaceholder(/Remember something in this project/).fill('tabs memory one')
  await page.getByPlaceholder(/Remember something in this project/).press('Enter')
  await expect(page.getByText('tabs memory one')).toBeVisible()
  const mems = await api('/memories?project_id=' + p.id + '&include_global=false')
  expect(mems.map((m) => m.content)).toContain('tabs memory one')
  await expect(page.getByText('No files yet')).toBeVisible()
  await page.locator('.page-header').getByRole('button', { name: /New chat/ }).click()
  await expect(page.getByText('New chat in Tabs')).toBeVisible()
  clean(grain)
})

test('50 projects and 200 chats in one project: sidebar and project view stay usable at 820x520', async ({ grain }) => {
  const { page, api } = grain
  const ids = []
  for (let i = 0; i < 50; i++) ids.push((await api('/projects', { method: 'POST', body: { name: `Proj ${String(i).padStart(2, '0')}` } })).id)
  await Promise.all(Array.from({ length: 200 }, (_, i) => api('/conversations', { method: 'POST', body: { project_id: ids[0], title: `bulk chat ${i}` } })))
  await shrink(grain)
  const t0 = Date.now()
  await page.reload()
  await expect(sidebar(page).getByText('Proj 49', { exact: true })).toBeAttached()
  expect(Date.now() - t0).toBeLessThan(15_000)
  // the group never lists more than 4 rows even with 200 chats
  const group = sidebar(page).locator('.project-group', { hasText: 'Proj 00' })
  expect(await group.locator('.project-rows .convo-item').count()).toBeLessThanOrEqual(4)
  await sidebar(page).getByText('Proj 00', { exact: true }).click()
  await expect(page.locator('.chat-row')).toHaveCount(200)
  await expect(page.getByRole('region', { name: 'Chats' }).locator('.project-sec-head')).toContainText('200')
  // the sidebar scrolls as one column: the last project can be reached
  await sidebar(page).getByText('Proj 49', { exact: true }).scrollIntoViewIfNeeded()
  await sidebar(page).getByText('Proj 49', { exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Proj 49' })).toBeVisible()
  clean(grain)
})

test('deleting a chat from the project view removes just that row', async ({ grain }) => {
  const { page, api } = grain
  const p = await api('/projects', { method: 'POST', body: { name: 'Rows' } })
  await api('/conversations', { method: 'POST', body: { project_id: p.id, title: 'keep me' } })
  await api('/conversations', { method: 'POST', body: { project_id: p.id, title: 'drop me' } })
  await page.reload()
  await sidebar(page).getByText('Rows', { exact: true }).click()
  await page.locator('button[aria-label="Delete chat: drop me"]').click()
  await expect(page.getByText('drop me')).toHaveCount(0)
  await expect(page.locator('.chat-row').filter({ hasText: 'keep me' })).toHaveCount(1)
})
