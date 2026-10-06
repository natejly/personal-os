import { test } from './fixtures.mjs'
import { scriptLLM } from './helpers/scriptllm.mjs'
import { expect, realErrors, newChat, say, waitStatus, turnOn, strip, openPanel, panel, chatRow, deskOf, WRITE, DELIVER, DONE, settingsFor } from './helpers/cowork.mjs'
test.describe.configure({ timeout: 300_000 })


test('a chat turned autonomous works, delivers, reaches review in the chat, and accept puts the file into a doc', async ({ grain }) => {
  await grain.api('/settings', { method: 'PUT', body: settingsFor })
  const llm = await scriptLLM(grain)
  llm.push({ text: 'Happy to. Shall I go ahead?' })
  const { page } = grain
  await newChat(page)
  await say(page, 'Write a short report')
  await expect(page.locator('.msg.assistant').last()).toContainText('Shall I go ahead', { timeout: 60_000 })
  const [chat] = await grain.api('/conversations?include_desks=true')
  llm.push({ calls: [WRITE] }, { calls: [DELIVER] }, { calls: [DONE] }, { text: 'Finished.' })
  await turnOn(page, 'Work and propose')
  await expect(strip(page)).toBeVisible()
  const id = await deskOf(grain, chat.id)
  expect(id).toBeTruthy()
  expect((await grain.api(`/cowork/desks/${id}`)).conversation_id).toBe(chat.id) // no second conversation
  await waitStatus(grain, id, 'review')
  await expect(strip(page)).toContainText('Ready to review')
  await expect(chatRow(page, chat.title).locator('.attn-dot')).toHaveAttribute('aria-label', /Ready to review/)
  await openPanel(page, 'Review')
  await expect(panel(page).locator('.desk-output')).toContainText('The report')
  await panel(page).getByRole('button', { name: 'Preview' }).click()
  await expect(panel(page).locator('.desk-output-excerpt')).toContainText('Hello from the desk')
  await panel(page).getByRole('button', { name: /Accept selected/ }).click()
  await expect(panel(page).locator('.desk-output .desk-verified').first()).toBeVisible({ timeout: 30_000 })
  const d = await grain.api(`/cowork/desks/${id}`)
  expect(d.outputs[0].status).toMatch(/accepted|promoted/)
  expect(['done', 'review']).toContain(d.status)
  expect((await grain.api('/docs')).some((x) => /report/i.test(x.title) || /Hello from the desk/.test(x.content || ''))).toBe(true)
  expect(realErrors(grain)).toEqual([])
})

test('with autonomy on by default a new chat is a desk from its first message', async ({ grain }) => {
  await grain.api('/settings', { method: 'PUT', body: { autonomousByDefault: true } })
  await grain.page.reload()
  const { page } = grain
  await newChat(page)
  await say(page, '!!reply Hello there.')
  await expect(page.locator('.msg.assistant').last()).toContainText('Hello there.', { timeout: 60_000 })
  expect(await grain.api('/conversations')).toEqual([]) // desks are left out of the plain list
  const [chat] = await grain.api('/conversations?include_desks=true')
  await expect.poll(() => deskOf(grain, chat.id)).toBeTruthy()
  expect(realErrors(grain)).toEqual([])
})
