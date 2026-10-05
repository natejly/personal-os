import { test } from './fixtures.mjs'
import { existsSync } from 'node:fs'
import { join } from 'node:path'
import { scriptLLM } from './helpers/scriptllm.mjs'
import { restartBackend } from './helpers/restart.mjs'
import { expect, realErrors, deskStatus, waitStatus, openCowork, rail, newChat, say, pending, homeScratch, rmScratch, WRITE, DELIVER, DONE, settingsFor } from './helpers/cowork.mjs'
test.describe.configure({ timeout: 360_000 })

const card = (page) => page.getByRole('group', { name: 'Run command' })

test('backend crash while a chat approval waits: card is recorded, answering it is safe and runs nothing twice', async ({ grain }) => {
  const { page } = grain
  const ws = homeScratch()
  try {
    await grain.api('/settings', { method: 'PUT', body: { workspaceRoots: [ws], toolDeferAbove: 0 } })
    await newChat(page)
    await say(page, `chat-crash !!tool shell_run {"command":"touch crashed.txt","cwd":"${ws}"}`)
    await expect(card(page)).toBeVisible({ timeout: 90_000 })
    await expect.poll(async () => (await pending(grain)).length).toBe(1)
    await restartBackend(grain)
    // the row survived; the run did not (a chat run that died stays dead), so the card says so rather than hanging
    const rows = await pending(grain)
    expect(rows).toHaveLength(1)
    expect(rows[0].live).toBe(false)
    await page.reload()
    await page.waitForSelector('.sidebar')
    await page.locator('.sidebar').getByText(/chat-crash/).first().click()
    await expect(card(page)).toBeVisible({ timeout: 60_000 })
    await card(page).getByRole('button', { name: 'Approve', exact: true }).click()
    await expect.poll(async () => (await pending(grain)).length, { timeout: 60_000 }).toBe(0)
    await expect(card(page)).toHaveCount(0, { timeout: 30_000 })
    // the interrupted run must not execute the call behind the user's back
    await page.waitForTimeout(1500)
    expect(existsSync(join(ws, 'crashed.txt'))).toBe(false)
    expect(await grain.api('/approvals?status=all')).toHaveLength(1)
    const runs = await grain.api('/approvals?status=approved')
    expect(runs).toHaveLength(1)
  } finally { rmScratch(ws) }
})

test('desk mid-run when the backend dies: it is Interrupted, never silently resumed, and Resume finishes it once', async ({ grain }) => {
  await grain.api('/settings', { method: 'PUT', body: settingsFor })
  const llm = await scriptLLM(grain)
  llm.push({ calls: [WRITE], delay: 600_000 })
  const { page } = grain
  const { desk } = await grain.api('/cowork/desks', { method: 'POST', body: { brief: 'long work', title: 'Crashy', autonomy: 'propose', start: true } })
  await openCowork(page)
  await rail(page).getByText('Crashy').click()
  await expect.poll(() => deskStatus(grain, desk.id), { timeout: 90_000 }).toMatch(/working|planning/)
  await expect.poll(() => llm.requests.length, { timeout: 60_000 }).toBeGreaterThan(0)
  await restartBackend(grain)
  llm.queue.length = 0
  expect(await deskStatus(grain, desk.id)).toBe('interrupted')
  const requestsAtRestart = llm.requests.length
  await page.waitForTimeout(3000)
  expect(llm.requests.length).toBe(requestsAtRestart) // nothing auto-resumed
  await page.reload()
  await page.waitForSelector('.sidebar')
  await openCowork(page)
  await rail(page).getByText('Crashy').click()
  await expect(page.getByText('Interrupted by a restart')).toBeVisible({ timeout: 60_000 })
  await expect(rail(page).locator('.desk-row-status').first()).toHaveText('Interrupted')
  llm.push({ calls: [WRITE] }, { calls: [DELIVER] }, { calls: [DONE] }, { text: 'ok' })
  await page.getByRole('button', { name: /Resume/ }).dblclick()
  await waitStatus(grain, desk.id, 'review', 120_000)
  const d = await grain.api(`/cowork/desks/${desk.id}`)
  expect(d.outputs).toHaveLength(1)
  expect(realErrors(grain).filter((e) => !/fetch|network|ERR_/i.test(e))).toEqual([])
})

test('desk waiting on a question survives a backend crash; the answer still resumes it', async ({ grain }) => {
  await grain.api('/settings', { method: 'PUT', body: settingsFor })
  const llm = await scriptLLM(grain)
  llm.push({ calls: [{ name: 'desk_ask', args: { question: 'Which quarter?', options: ['Q1', 'Q3'] } }] })
  const { page } = grain
  const { desk } = await grain.api('/cowork/desks', { method: 'POST', body: { brief: 'compare', title: 'Question crash', autonomy: 'propose', start: true } })
  await waitStatus(grain, desk.id, 'needs_approval', 120_000)
  await restartBackend(grain)
  expect(['interrupted', 'blocked', 'needs_approval']).toContain(await deskStatus(grain, desk.id))
  await page.reload()
  await page.waitForSelector('.sidebar')
  await openCowork(page)
  await rail(page).getByText('Question crash').click()
  await expect(page.locator('.desk-ask').first()).toContainText('Which quarter?', { timeout: 60_000 })
  llm.push({ calls: [WRITE] }, { calls: [DELIVER] }, { calls: [DONE] }, { text: 'ok' })
  await page.getByRole('group', { name: 'Suggested answers' }).first().getByRole('button', { name: 'Q1' }).click()
  await waitStatus(grain, desk.id, 'review', 120_000)
  expect(JSON.stringify(llm.requests)).toContain('Q1')
})

test('desk waiting on a plan survives a backend crash; approving afterwards carries the plan out', async ({ grain }) => {
  await grain.api('/settings', { method: 'PUT', body: settingsFor })
  const llm = await scriptLLM(grain)
  const edited = { path: 'outputs/plan.md', content: '# After crash\n' }
  llm.push({ calls: [{ name: 'propose_plan', args: { title: 'Plan after crash', steps: [{ tool: 'desk_write_file', title: 'Write it', arguments: edited }] } }] })
  const { page } = grain
  const { desk } = await grain.api('/cowork/desks', { method: 'POST', body: { brief: 'plan then crash', title: 'Plan crash', autonomy: 'plan', start: true } })
  await waitStatus(grain, desk.id, 'awaiting_plan', 120_000)
  await restartBackend(grain)
  await page.reload()
  await page.waitForSelector('.sidebar')
  await openCowork(page)
  await rail(page).getByText('Plan crash').click()
  await page.locator('.desk-tabs').getByRole('button', { name: /^Plan/ }).click()
  await expect(page.locator('.aplan')).toContainText('Write it', { timeout: 60_000 })
  llm.push({ calls: [{ name: 'desk_write_file', args: edited }] }, { calls: [{ name: 'desk_deliver', args: { path: 'outputs/plan.md', title: 'Plan file' } }] }, { calls: [DONE] }, { text: 'ok' })
  await page.getByRole('button', { name: 'Approve & run' }).click()
  await waitStatus(grain, desk.id, 'review', 120_000)
  const d = await grain.api(`/cowork/desks/${desk.id}`)
  expect(d.plan.status).toBe('approved')
  expect((await grain.api(`/cowork/desks/${desk.id}/file?path=outputs/plan.md`)).text).toContain('After crash')
})

test('relaunching Electron mid-run shows the live desk once, and a finished one unchanged', async ({ grain }) => {
  await grain.api('/settings', { method: 'PUT', body: settingsFor })
  const llm = await scriptLLM(grain)
  llm.push({ calls: [WRITE] }, { calls: [DELIVER], delay: 8000 }, { calls: [DONE] }, { text: 'ok' })
  const { desk } = await grain.api('/cowork/desks', { method: 'POST', body: { brief: 'relaunch me', title: 'Relaunch desk', autonomy: 'propose', start: true } })
  await expect.poll(() => llm.requests.length, { timeout: 90_000 }).toBeGreaterThan(1)
  const page = await grain.relaunch()
  await openCowork(page)
  await expect(rail(page).locator('.desk-row')).toHaveCount(1)
  await expect(rail(page).getByText('Relaunch desk')).toHaveCount(1)
  await waitStatus(grain, desk.id, 'review', 120_000)
  await expect(rail(page).locator('.desk-row-status').first()).toHaveText('Ready to review', { timeout: 60_000 })
  const runs = await grain.api(`/cowork/desks/${desk.id}`)
  expect(runs.outputs).toHaveLength(1)
})
