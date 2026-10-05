import { test } from './fixtures.mjs'
import { scriptLLM } from './helpers/scriptllm.mjs'
import { expect, realErrors, deskStatus, waitStatus, deskChat, openChat, strip, newChat, say, WRITE, DELIVER, DONE, settingsFor } from './helpers/cowork.mjs'
test.describe.configure({ timeout: 300_000 })


test('desks working at once: the extra desk queues ("#1 in line") and starts when the first finishes', async ({ grain }) => {
  await grain.api('/settings', { method: 'PUT', body: { ...settingsFor, deskMaxLive: 1 } })
  const llm = await scriptLLM(grain)
  llm.push({ calls: [WRITE], delay: 12_000 }, { calls: [DELIVER] }, { calls: [DONE] }, { text: 'one done' }, { text: 'one final' })
  const { page } = grain
  const a = (await deskChat(grain, { brief: 'first', title: 'First desk' })).desk
  await expect.poll(() => deskStatus(grain, a.id), { timeout: 90_000 }).toMatch(/working|planning/)
  llm.push({ calls: [WRITE] }, { calls: [DELIVER] }, { calls: [DONE] }, { text: 'two done' })
  const b = (await deskChat(grain, { brief: 'second', title: 'Second desk' })).desk
  await waitStatus(grain, b.id, 'queued', 60_000)
  await openChat(page, 'Second desk')
  await expect(strip(page)).toContainText('Queued')
  await expect(strip(page)).toContainText('#1 in line')
  await waitStatus(grain, a.id, 'review', 120_000)
  await waitStatus(grain, b.id, 'review', 120_000)
  expect(realErrors(grain)).toEqual([])
})

test('a desk that hits its turn cap stops and asks instead of running on', async ({ grain }) => {
  await grain.api('/settings', { method: 'PUT', body: settingsFor })
  const llm = await scriptLLM(grain)
  // every turn only talks: the desk would chain forever, so the cap is what ends it
  for (let i = 0; i < 6; i++) llm.push({ text: `still thinking ${i}` })
  const { desk } = await deskChat(grain, { brief: 'never finishes', title: 'Capped', budget: { maxTurns: 2 } })
  await expect.poll(async () => (await grain.api(`/cowork/desks/${desk.id}`)).status, { timeout: 120_000 }).toMatch(/blocked|paused|review|done|needs_approval/)
  const d = await grain.api(`/cowork/desks/${desk.id}`)
  expect(d.turn).toBeLessThanOrEqual(2)
  const before = llm.requests.length
  await grain.page.waitForTimeout(4000)
  expect(llm.requests.length).toBe(before) // nothing keeps running behind the cap
})

test('a desk lets go of a card nobody answers (parkAfterSeconds) and the answer still wakes it', async ({ grain }) => {
  await grain.api('/settings', { method: 'PUT', body: { ...settingsFor, parkAfterSeconds: 2 } })
  const llm = await scriptLLM(grain)
  llm.push({ calls: [{ name: 'desk_ask', args: { question: 'Left waiting?', options: ['yes', 'no'] } }] })
  const { page } = grain
  const { desk } = await deskChat(grain, { brief: 'park me', title: 'Parker' })
  await expect.poll(async () => (await grain.api('/approvals')).find((a) => a.desk_id === desk.id)?.parked_at, { timeout: 120_000 }).toBeTruthy()
  await openChat(page, 'Parker')
  await expect(page.locator('.desk-ask').first()).toContainText('Left waiting?')
  llm.push({ calls: [WRITE] }, { calls: [DELIVER] }, { calls: [DONE] }, { text: 'ok' })
  await page.getByRole('group', { name: 'Suggested answers' }).first().getByRole('button', { name: 'yes' }).click()
  await waitStatus(grain, desk.id, 'review', 120_000)
  expect(JSON.stringify(llm.requests)).toContain('yes')
  expect(realErrors(grain)).toEqual([])
})

test('Settings → Autonomy numeric fields clamp, save, and survive a reopen', async ({ grain }) => {
  const { page } = grain
  await page.getByRole('button', { name: 'Settings' }).click()
  await page.getByRole('tab', { name: 'Tools' }).click()
  await expect(page.getByRole('heading', { name: 'Autonomy' })).toBeVisible()
  const turns = page.getByLabel(/Turns per desk/)
  await turns.scrollIntoViewIfNeeded()
  await turns.fill('-5')
  await turns.blur()
  expect(Number(await turns.inputValue())).toBeGreaterThanOrEqual(0)
  await turns.fill('7')
  await turns.press('Enter')
  await expect(turns).toHaveValue('7')
  await page.getByRole('button', { name: 'Save', exact: true }).click()
  await expect.poll(async () => (await grain.api('/settings')).deskMaxTurns).toBe(7)
  // the Work autonomously menu shows the cap as its placeholder
  await page.keyboard.press('Escape')
  await newChat(page)
  await say(page, 'hello there')
  await expect(page.getByRole('button', { name: /Work autonomously/ })).toBeEnabled({ timeout: 60_000 })
  await page.getByRole('button', { name: /Work autonomously/ }).click()
  await expect(page.locator('.autonomy-menu .desk-limits input[type=number]')).toHaveAttribute('placeholder', '7')
  expect(realErrors(grain)).toEqual([])
})
