import { test } from './fixtures.mjs'
import { scriptLLM } from './helpers/scriptllm.mjs'
import { expect, realErrors, resize, waitStatus, deskChat, openChat, openPanel, panel, WRITE, DELIVER, DONE, settingsFor } from './helpers/cowork.mjs'
test.describe.configure({ timeout: 300_000 })

const noHScroll = (page) => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)
const inWindow = async (page, locator) => {
  await locator.scrollIntoViewIfNeeded()
  const b = await locator.boundingBox()
  const vp = await page.evaluate(() => ({ w: window.innerWidth, h: window.innerHeight }))
  return b && b.x >= 0 && b.x + b.width <= vp.w + 1 && b.y >= 0 && b.y + b.height <= vp.h + 1
}

test('820x520: a waiting plan, an ask-as-it-goes card, and the review panel keep their buttons reachable', async ({ grain }) => {
  await grain.api('/settings', { method: 'PUT', body: settingsFor })
  await resize(grain)
  const llm = await scriptLLM(grain)
  const { page } = grain
  // plan desk
  llm.push({ calls: [{ name: 'propose_plan', args: { title: 'Narrow plan', steps: [1, 2, 3, 4].map((i) => ({ tool: 'desk_write_file', title: `Write ${i}`, why: 'a reason that is a little long so the row wraps in a narrow window '.repeat(2), arguments: { path: `outputs/f${i}.md`, content: 'c'.repeat(300) } })) } }] })
  const plan = (await deskChat(grain, { brief: 'plan', title: 'Narrow plan desk', autonomy: 'plan' })).desk
  await waitStatus(grain, plan.id, 'awaiting_plan', 90_000)
  await openChat(page, 'Narrow plan desk')
  const approve = page.getByRole('button', { name: 'Approve & run' })
  await expect(approve).toBeVisible()
  expect(await inWindow(page, approve)).toBe(true)
  expect(await noHScroll(page)).toBe(true)
  // reject it so the next desk gets a clean queue
  llm.push({ text: 'ok' })
  await page.getByRole('button', { name: 'Reject', exact: true }).click()
  await expect.poll(async () => (await grain.api(`/cowork/desks/${plan.id}`)).plan?.status, { timeout: 60_000 }).toBe('rejected')

  await expect.poll(async () => (await grain.api('/cowork/desks/' + plan.id)).status, { timeout: 90_000 }).not.toMatch(/working|planning|awaiting_plan/)
  // ask desk
  llm.queue.length = 0
  llm.push({ calls: [WRITE] })
  const ask = (await deskChat(grain, { brief: 'ask', title: 'Narrow ask desk', autonomy: 'ask' })).desk
  await waitStatus(grain, ask.id, 'needs_approval', 90_000)
  await openChat(page, 'Narrow ask desk')
  const allow = page.locator('.messages').getByRole('button', { name: /^(Approve|Allow once|Allow)$/ }).first()
  await expect(allow).toBeVisible()
  // a forced card offers no standing grants: nothing to click that would silently switch the mode off
  await expect(page.locator('.messages').getByRole('button', { name: /in this chat|in every chat/ })).toHaveCount(0)
  expect(await inWindow(page, allow)).toBe(true)
  expect(await noHScroll(page)).toBe(true)
  llm.push({ text: 'done' })
  await allow.click()
  await expect.poll(async () => (await grain.api('/approvals?status=approved')).length, { timeout: 60_000 }).toBe(1)

  await expect.poll(async () => (await grain.api('/cowork/desks/' + ask.id)).status, { timeout: 90_000 }).not.toMatch(/working|planning|needs_approval/)
  // review pane
  llm.queue.length = 0
  llm.push({ calls: [WRITE] }, { calls: [DELIVER] }, { calls: [DONE] }, { text: 'ok' }, { text: 'final' })
  const rev = (await deskChat(grain, { brief: 'review', title: 'Narrow review desk' })).desk
  await waitStatus(grain, rev.id, 'review', 120_000)
  await openChat(page, 'Narrow review desk')
  await openPanel(page, 'Review')
  const accept = panel(page).getByRole('button', { name: /Accept selected/ })
  expect(await inWindow(page, accept)).toBe(true)
  expect(await noHScroll(page)).toBe(true)
  expect(realErrors(grain)).toEqual([])
})
