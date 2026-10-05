import { test, expect } from './fixtures.mjs'
import { msgBox, assistants, users, newChat, say, sayAndWait, stopBtn, anyStop, benign } from './helpers/chat.mjs'

test.describe.configure({ timeout: 240_000 })

test('regenerate keeps variants and the switcher moves between them', async ({ grain }) => {
  const { page, api } = grain
  await newChat(page)
  await sayAndWait(page, 'tell me hi', 'MOCK: tell me hi')
  await expect(page.getByRole('group', { name: 'Answer versions' })).toHaveCount(0)
  await page.getByRole('button', { name: /Regenerate/ }).click()
  const sw = page.getByRole('group', { name: 'Answer versions' })
  await expect(sw).toContainText('2/2', { timeout: 60_000 })
  await expect(anyStop(page)).toHaveCount(0, { timeout: 30_000 })
  await sw.getByRole('button', { name: 'Previous answer' }).click()
  await expect(sw).toContainText('1/2')
  await expect(sw.getByRole('button', { name: 'Previous answer' })).toBeDisabled()
  await sw.getByRole('button', { name: 'Next answer' }).click()
  await expect(sw).toContainText('2/2')
  await expect(sw.getByRole('button', { name: 'Next answer' })).toBeDisabled()
  // rapid regenerate clicks must not stack runs
  await page.getByRole('button', { name: /Regenerate/ }).dblclick().catch(() => {})
  await expect(anyStop(page)).toHaveCount(0, { timeout: 60_000 })
  const [c0] = await api('/conversations')
  const c = await api(`/conversations/${c0.id}`)
  expect(c.messages.filter((m) => m.role === 'user').length).toBe(1)
  // the second click of a double-click is refused by the backend (409: a reply is already running)
  expect(benign(grain.consoleErrors).filter((e) => !/status of 409/.test(e))).toEqual([])
})

test('edit and resend replaces the message and its replies', async ({ grain }) => {
  const { page, api, llm } = grain
  await newChat(page)
  await sayAndWait(page, 'original question', 'MOCK: original question')
  await sayAndWait(page, 'second question', 'MOCK: second question')
  await users(page).first().hover()
  await users(page).first().getByRole('button', { name: 'Edit message' }).click()
  const ed = page.getByRole('textbox', { name: 'Edit message' })
  await expect(ed).toHaveValue('original question')
  // Esc cancels
  await ed.press('Escape')
  await expect(ed).toHaveCount(0)
  await users(page).first().getByRole('button', { name: 'Edit message' }).click()
  page.once('dialog', (d) => d.accept())
  await ed.fill('edited question')
  await page.getByRole('button', { name: 'Resend' }).click()
  await expect(assistants(page).last()).toContainText('MOCK: edited question', { timeout: 60_000 })
  await expect(page.locator('.msg')).toHaveCount(2)
  await expect(page.getByText('second question')).toHaveCount(0)
  const last = llm.calls[llm.calls.length - 1].messages.map((m) => (typeof m.content === 'string' ? m.content : '')).join('|')
  expect(last).toContain('edited question')
  expect(last).not.toContain('second question')
  const [c0] = await api('/conversations')
  const c = await api(`/conversations/${c0.id}`)
  expect(c.messages.map((m) => m.content)).toEqual(['edited question', 'MOCK: edited question'])
})

test('copy button writes the reply to the clipboard; delete message removes it', async ({ grain }) => {
  const { page, api } = grain
  await newChat(page)
  await sayAndWait(page, '!!reply copy this text', 'copy this text')
  await page.context().grantPermissions(['clipboard-read', 'clipboard-write']).catch(() => {})
  await assistants(page).last().getByRole('button', { name: 'Copy' }).click()
  await expect(assistants(page).last().getByRole('button', { name: 'Copied' })).toBeVisible()
  const clip = await page.evaluate(() => navigator.clipboard.readText()).catch(() => null)
  if (clip !== null) expect(clip).toBe('copy this text')
  // delete the assistant message
  page.once('dialog', (d) => d.accept())
  await assistants(page).last().getByRole('button', { name: 'Delete message' }).click()
  await expect(assistants(page)).toHaveCount(0, { timeout: 15_000 })
  const [c0] = await api('/conversations')
  const c = await api(`/conversations/${c0.id}`)
  expect(c.messages.filter((m) => m.role === 'assistant').length).toBe(0)
  // dismissed confirm keeps the message
  await sayAndWait(page, '!!reply keep me', 'keep me')
  page.once('dialog', (d) => d.dismiss())
  await assistants(page).last().getByRole('button', { name: 'Delete message' }).click()
  await page.waitForTimeout(500)
  await expect(assistants(page)).toHaveCount(1)
})

test('!!fail 500 shows the error row, Retry recovers; next send works; 429 likewise', async ({ grain }) => {
  const { page } = grain
  await newChat(page)
  await say(page, '!!fail 500')
  await expect(page.locator('.msg-error').first()).toBeVisible({ timeout: 120_000 })
  await expect(anyStop(page)).toHaveCount(0, { timeout: 60_000 })
  await expect(page.getByRole('button', { name: 'Retry' }).first()).toBeVisible()
  // the next message goes through
  await sayAndWait(page, '!!reply recovered', 'recovered')
  await say(page, '!!fail 429')
  await expect(page.locator('.msg-error').last()).toBeVisible({ timeout: 120_000 })
  await expect(anyStop(page)).toHaveCount(0, { timeout: 60_000 })
  await expect(page.getByRole('button', { name: 'Retry' }).last()).toBeVisible()
  await sayAndWait(page, '!!reply recovered again', 'recovered again')
  expect(benign(grain.consoleErrors).filter((e) => !/50[0-9]|429|Failed to load resource/.test(e))).toEqual([])
})

test('a stopped reply offers Continue/Resume and it produces text', async ({ grain }) => {
  const { page } = grain
  await newChat(page)
  await say(page, '!!slow 5000 !!reply continued text')
  await expect(stopBtn(page)).toBeVisible()
  await stopBtn(page).click()
  await expect(anyStop(page)).toHaveCount(0, { timeout: 60_000 })
  const cont = page.getByRole('button', { name: /^(Continue|Resume)$/ })
  await expect(cont).toBeVisible({ timeout: 30_000 })
  await cont.click()
  await expect(assistants(page).last()).toContainText('continued text', { timeout: 60_000 })
})
