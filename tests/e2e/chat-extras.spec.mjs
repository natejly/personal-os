import { test, expect } from './fixtures.mjs'
import { msgBox, assistants, users, newChat, say, sayAndWait, stopBtn, anyStop, benign } from './helpers/chat.mjs'

test.describe.configure({ timeout: 300_000 })

test('five follow-ups queued during a reply are answered in order, one run each', async ({ grain }) => {
  const { page, api } = grain
  await newChat(page)
  await say(page, '!!slow 2500 !!reply first')
  await expect(stopBtn(page)).toBeVisible()
  for (let i = 1; i <= 5; i++) {
    await msgBox(page).fill(`!!reply queued-${i}`)
    await msgBox(page).press('Enter')
  }
  await expect(page.getByLabel('Queued follow-ups')).toBeVisible()
  await expect(assistants(page).last()).toContainText('queued-5', { timeout: 240_000 })
  await expect(anyStop(page)).toHaveCount(0, { timeout: 60_000 })
  const [c0] = await api('/conversations')
  const c = await api(`/conversations/${c0.id}`)
  const replies = c.messages.filter((m) => m.role === 'assistant').map((m) => m.content)
  expect(replies).toEqual(['first', 'queued-1', 'queued-2', 'queued-3', 'queued-4', 'queued-5'])
})

test('ArrowUp recalls earlier prompts in the composer', async ({ grain }) => {
  const { page } = grain
  await newChat(page)
  await sayAndWait(page, '!!reply one', 'one')
  await sayAndWait(page, '!!reply two', 'two')
  const box = msgBox(page)
  await box.click()
  await page.keyboard.press('ArrowUp')
  await expect(box).toHaveValue('!!reply two')
  await page.keyboard.press('ArrowUp')
  await expect(box).toHaveValue('!!reply one')
  await page.keyboard.press('ArrowDown')
  await expect(box).toHaveValue('!!reply two')
})

test('find in conversation (menu Find) highlights and steps through matches; Esc closes', async ({ grain }) => {
  const { page } = grain
  await newChat(page)
  await sayAndWait(page, '!!reply needle alpha needle beta needle', 'needle alpha')
  await grain.app.evaluate(({ Menu }) => {
    const find = (items) => { for (const i of items) { if (i.label === 'Find…') return i; const r = i.submenu && find(i.submenu.items); if (r) return r } }
    find(Menu.getApplicationMenu().items).click()
  })
  const f = page.getByRole('textbox', { name: 'Find in conversation' })
  await expect(f).toBeVisible()
  await f.fill('needle')
  await expect(page.getByRole('button', { name: 'Next match' })).toBeVisible()
  await page.getByRole('button', { name: 'Next match' }).click()
  await page.getByRole('button', { name: 'Previous match' }).click()
  await f.press('Escape')
  await expect(f).toHaveCount(0)
  expect(benign(grain.consoleErrors)).toEqual([])
})

test('branch in new chat copies the conversation up to that message', async ({ grain }) => {
  const { page, api } = grain
  await newChat(page)
  await sayAndWait(page, '!!reply first reply', 'first reply')
  await sayAndWait(page, '!!reply second reply', 'second reply')
  await assistants(page).first().getByRole('button', { name: 'Branch in new chat' }).click()
  await expect(page.locator('.convo-list .convo-item')).toHaveCount(2, { timeout: 30_000 })
  await expect(assistants(page)).toHaveCount(1)
  await expect(assistants(page).first()).toContainText('first reply')
  const convs = await api('/conversations')
  expect(convs.length).toBe(2)
})

test('a message with markdown-looking and html text is shown literally in the user bubble', async ({ grain }) => {
  const { page } = grain
  await newChat(page)
  await sayAndWait(page, '<img src=x onerror=alert(1)> **not bold** !!reply fine', 'fine')
  await expect(users(page).first()).toContainText('<img src=x onerror=alert(1)> **not bold**')
  await expect(users(page).first().locator('img')).toHaveCount(0)
  await expect(users(page).first().locator('strong')).toHaveCount(0)
})

test('every chat has a side panel: Files and Changes, and no Review without a desk', async ({ grain }) => {
  const { page } = grain
  await newChat(page)
  await sayAndWait(page, '!!tool current_time {}', '')
  await page.getByRole('button', { name: 'Documents in this chat' }).click()
  const panel = page.locator('.desk-panel')
  await expect(panel).toBeVisible()
  const tabs = panel.locator('.desk-tabs')
  await expect(tabs.getByRole('button', { name: 'Files' })).toBeVisible()
  await expect(tabs.getByRole('button', { name: 'Changes' })).toBeVisible()
  await expect(tabs.getByRole('button', { name: /^Review/ })).toHaveCount(0)
  await expect(panel.getByText('No files in this chat yet.')).toBeVisible()
  await tabs.getByRole('button', { name: 'Changes' }).click()
  await expect(panel.getByText('No files changed in this chat yet.')).toBeVisible()
  await page.getByRole('button', { name: 'Documents in this chat' }).click()
  await expect(panel).toHaveCount(0)
})

test('the checklist is never above the composer; it is the Checklist tab of the side panel', async ({ grain }) => {
  const { page } = grain
  await newChat(page)
  const plan = { steps: [{ text: 'first step', status: 'in_progress' }, { text: 'second step', status: 'pending' }] }
  await say(page, '!!tool todo_write ' + JSON.stringify(plan))
  await expect(page.locator('.msg.assistant').last()).toContainText('MOCK: tool done', { timeout: 60_000 })
  await expect(page.getByRole('region', { name: 'Checklist' })).toHaveCount(0) // no status card for the main agent
  await page.getByRole('button', { name: 'Documents in this chat' }).click()
  await page.locator('.desk-panel .desk-tabs').getByRole('button', { name: 'Checklist' }).click()
  const list = page.locator('.desk-panel').getByRole('region', { name: 'Checklist' })
  await expect(list).toContainText('0/2 done')
  await expect(list.locator('.plan-steps .plan-step')).toHaveCount(2)
})
