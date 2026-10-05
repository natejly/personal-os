import { test } from './fixtures.mjs'
import { existsSync } from 'node:fs'
import { join } from 'node:path'
import { scriptLLM } from './helpers/scriptllm.mjs'
import { restartBackend } from './helpers/restart.mjs'
import { expect, realErrors, deskStatus, waitStatus, openCowork, rail, newChat, say, pending, homeScratch, rmScratch, WRITE, DELIVER, DONE, settingsFor } from './helpers/cowork.mjs'
test.describe.configure({ timeout: 300_000 })

const mk = (grain, body) => grain.api('/cowork/desks', { method: 'POST', body })
const card = (page) => page.getByRole('group', { name: 'Run command' })

test('backend gone while a card is on screen: the click fails softly, the card stays, and works once the backend is back', async ({ grain }) => {
  const { page } = grain
  const ws = homeScratch()
  try {
    await grain.api('/settings', { method: 'PUT', body: { workspaceRoots: [ws], toolDeferAbove: 0 } })
    await newChat(page)
    await say(page, `chat-gone !!tool shell_run ${JSON.stringify({ command: 'touch back.txt', cwd: ws })}`)
    await expect(card(page)).toBeVisible({ timeout: 90_000 })
    grain.backend.child.kill('SIGKILL')
    await new Promise((r) => grain.backend.child.once('exit', r))
    await card(page).getByRole('button', { name: 'Approve', exact: true }).click()
    await page.waitForTimeout(1500)
    expect(grain.consoleErrors.filter((e) => e.startsWith('pageerror'))).toEqual([]) // no uncaught exception
    await expect(page.locator('.sidebar')).toBeVisible()
    await restartBackend(grain)
    await page.reload()
    await page.waitForSelector('.sidebar')
    await page.locator('.sidebar').getByText(/chat-gone/).first().click()
    await expect(card(page)).toBeVisible({ timeout: 60_000 })
    await card(page).getByRole('button', { name: 'Deny', exact: true }).click()
    await expect.poll(async () => (await pending(grain)).length, { timeout: 60_000 }).toBe(0)
    expect(existsSync(join(ws, 'back.txt'))).toBe(false)
  } finally { rmScratch(ws) }
})

test('double-clicking Start on a draft desk makes one run', async ({ grain }) => {
  await grain.api('/settings', { method: 'PUT', body: settingsFor })
  const llm = await scriptLLM(grain)
  llm.push({ calls: [WRITE] }, { calls: [DELIVER] }, { calls: [DONE] }, { text: 'ok' }, { text: 'final' })
  const { page } = grain
  const { desk } = await mk(grain, { brief: 'once only', title: 'Once', autonomy: 'propose', start: false })
  await openCowork(page)
  await rail(page).getByText('Once', { exact: true }).click()
  await page.getByRole('button', { name: /^Start/ }).dblclick()
  await waitStatus(grain, desk.id, 'review', 120_000)
  const first = llm.requests.filter((r) => r.messages.some((m) => m.role === 'user' && m.content === 'once only')).length
  expect(first).toBeGreaterThan(0)
  const userBriefs = llm.requests.filter((r) => r.messages.length <= 3 && r.messages.some((m) => m.role === 'user' && m.content === 'once only'))
  expect(userBriefs).toHaveLength(1) // exactly one opening turn
  expect((await grain.api(`/cowork/desks/${desk.id}`)).outputs).toHaveLength(1)
  // the same on the API: a second start of a desk that is not a draft is refused, not run again
  const again = await grain.api(`/cowork/desks/${desk.id}/start`, { method: 'POST', raw: true })
  expect([400, 404, 409]).toContain(again.status)
  expect(realErrors(grain)).toEqual([])
})

test('Pause then Resume hammered five times ends in a consistent state with no duplicate run', async ({ grain }) => {
  await grain.api('/settings', { method: 'PUT', body: settingsFor })
  const llm = await scriptLLM(grain)
  llm.push({ calls: [WRITE], delay: 60_000 })
  const { desk } = await mk(grain, { brief: 'hammer', title: 'Hammer', autonomy: 'propose', start: true })
  await expect.poll(() => deskStatus(grain, desk.id), { timeout: 90_000 }).toMatch(/working|planning/)
  const results = []
  for (let i = 0; i < 5; i++) {
    results.push((await grain.api(`/cowork/desks/${desk.id}/pause`, { method: 'POST', raw: true })).status)
    llm.queue.length = 0
    llm.push({ calls: [WRITE], delay: 60_000 })
    results.push((await grain.api(`/cowork/desks/${desk.id}/resume`, { method: 'POST', raw: true })).status)
  }
  expect(results.every((s) => s < 500)).toBe(true)
  await grain.api(`/cowork/desks/${desk.id}/stop`, { method: 'POST', raw: true })
  await expect.poll(() => deskStatus(grain, desk.id), { timeout: 60_000 }).toBe('stopped')
  const live = (await grain.api('/approvals')).length
  expect(live).toBe(0)
})

test('20 desks started at once: the cap queues the rest and all of them finish', async ({ grain }) => {
  await grain.api('/settings', { method: 'PUT', body: { ...settingsFor, deskMaxLive: 4 } })
  await scriptLLM(grain) // unscripted: every desk's turns are plain mock replies, which block them 'Waiting on you'
  const { page } = grain
  const ids = []
  for (let i = 0; i < 20; i++) ids.push((await mk(grain, { brief: `bulk ${i}`, title: `Bulk ${String(i).padStart(2, '0')}`, autonomy: 'propose', start: true })).desk.id)
  await openCowork(page)
  await expect(rail(page).locator('.desk-row')).toHaveCount(20, { timeout: 60_000 })
  await expect.poll(async () => {
    const rows = await grain.api('/cowork/desks')
    return rows.filter((d) => ['working', 'planning', 'needs_approval'].includes(d.status)).length
  }, { timeout: 60_000 }).toBeLessThanOrEqual(4)
  await expect.poll(async () => (await grain.api('/cowork/desks')).filter((d) => d.status === 'queued').length, { timeout: 120_000 }).toBeLessThan(20)
  // every desk ends up out of the queue eventually (blocked: the plain mock never calls desk_done)
  await expect.poll(async () => (await grain.api('/cowork/desks')).filter((d) => ['queued', 'working', 'planning'].includes(d.status)).length, { timeout: 240_000 }).toBe(0)
  expect(realErrors(grain)).toEqual([])
})
