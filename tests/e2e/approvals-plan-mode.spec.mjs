import { test } from './fixtures.mjs'
import { existsSync } from 'node:fs'
import { join } from 'node:path'
import { scriptLLM } from './helpers/scriptllm.mjs'
import { expect, newChat, say, homeScratch, rmScratch, pending, realErrors, resize } from './helpers/cowork.mjs'
test.describe.configure({ timeout: 300_000 })

const touch = (ws, name) => ({ name: 'shell_run', args: { command: `touch ${name}`, cwd: ws } })
const PLAN = (ws, ...names) => ({
  name: 'propose_plan',
  args: { title: 'Touch files', steps: names.map((n) => ({ tool: 'shell_run', title: `Touch ${n}`, arguments: touch(ws, n).args })) }
})
const card = (page) => page.getByRole('group', { name: 'Run command' })
const planCard = (page) => page.locator('.aplan').first()

async function setup(grain, planMode = 'always') {
  const ws = homeScratch()
  await grain.api('/settings', { method: 'PUT', body: { workspaceRoots: [ws], toolDeferAbove: 0, planMode } })
  const llm = await scriptLLM(grain)
  return { ws, llm }
}

test('plan toggle: click and ⌘⇧P cycle off → auto → always → off and persist on the chat', async ({ grain }) => {
  const { page } = grain
  await newChat(page)
  const toggle = page.getByRole('button', { name: /^Plan/ })
  await expect(toggle).toHaveText('Plan')
  await toggle.click()
  await expect(toggle).toHaveText('Plan: auto')
  await toggle.click()
  await expect(toggle).toHaveText('Plan: always')
  await page.keyboard.press('Meta+Shift+P')
  await expect(toggle).toHaveText('Plan')
  await page.keyboard.press('Meta+Shift+P')
  await expect(toggle).toHaveText('Plan: auto')
  // sending creates the chat, and the mode rides along on it
  await say(page, '!!reply hello')
  await expect(page.locator('.msg.assistant').last()).toContainText('hello', { timeout: 60_000 })
  const [c] = await grain.api('/conversations')
  expect((await grain.api(`/conversations/${c.id}`)).settings.planMode).toBe('auto')
  expect(realErrors(grain)).toEqual([])
})

test('plan mode always: a plan card appears, nothing runs before approval, an approved step runs once without a second card', async ({ grain }) => {
  const { page } = grain
  const { ws, llm } = await setup(grain)
  try {
    llm.push({ calls: [PLAN(ws, 'planned.txt')] })
    await newChat(page)
    await say(page, 'please touch planned.txt')
    await expect(planCard(page)).toContainText('touch planned.txt', { timeout: 90_000 })
    await expect.poll(async () => (await pending(grain)).length).toBe(1)
    expect(existsSync(join(ws, 'planned.txt'))).toBe(false)
    // after approval the model makes the planned call, then repeats it
    llm.push({ calls: [touch(ws, 'planned.txt')] }, { calls: [touch(ws, 'planned.txt')] }, { text: 'plan done' })
    await page.getByRole('button', { name: 'Approve & run' }).click()
    // the first call is the approved step (no card); the identical second one is not covered any more
    await expect(card(page)).toBeVisible({ timeout: 90_000 })
    expect(existsSync(join(ws, 'planned.txt'))).toBe(true)
    await expect(card(page)).toContainText('touch planned.txt')
    await card(page).getByRole('button', { name: 'Deny', exact: true }).click()
    await expect(page.locator('.msg.assistant').last()).toContainText('plan done', { timeout: 90_000 })
    const rows = await grain.api('/approvals?status=all')
    expect(rows.filter((r) => r.tool === 'shell_run')).toHaveLength(1) // only the repeat asked
    expect(rows.filter((r) => r.tool === 'propose_plan')).toHaveLength(1)
    expect(realErrors(grain)).toEqual([])
  } finally { rmScratch(ws) }
})

test('plan mode always: a call that is not in the approved plan still asks', async ({ grain }) => {
  const { page } = grain
  const { ws, llm } = await setup(grain)
  try {
    llm.push({ calls: [PLAN(ws, 'in-plan.txt')] })
    await newChat(page)
    await say(page, 'touch a file')
    await expect(planCard(page)).toBeVisible({ timeout: 90_000 })
    llm.push({ calls: [touch(ws, 'not-in-plan.txt')] }, { text: 'finished' })
    await page.getByRole('button', { name: 'Approve & run' }).click()
    await expect(card(page)).toContainText('touch not-in-plan.txt', { timeout: 90_000 })
    expect(existsSync(join(ws, 'not-in-plan.txt'))).toBe(false)
    await card(page).getByRole('button', { name: 'Deny', exact: true }).click()
    await expect(page.locator('.msg.assistant').last()).toContainText('finished', { timeout: 90_000 })
    expect(existsSync(join(ws, 'not-in-plan.txt'))).toBe(false)
    expect(existsSync(join(ws, 'in-plan.txt'))).toBe(false)
  } finally { rmScratch(ws) }
})

test('plan mode always: while drafting, a consequential tool is refused, not carded and not run', async ({ grain }) => {
  const { page } = grain
  const { ws, llm } = await setup(grain)
  try {
    llm.push({ calls: [touch(ws, 'sneaky.txt')] }, { text: 'ok I will plan instead' })
    await newChat(page)
    await say(page, 'touch sneaky.txt now')
    await expect(page.locator('.msg.assistant').last()).toContainText('ok I will plan instead', { timeout: 90_000 })
    expect(existsSync(join(ws, 'sneaky.txt'))).toBe(false)
    expect(await grain.api('/approvals?status=all')).toHaveLength(0)
    expect(realErrors(grain)).toEqual([])
  } finally { rmScratch(ws) }
})

test('plan card: edited arguments are what the approval binds; the unedited call asks again', async ({ grain }) => {
  const { page } = grain
  const { ws, llm } = await setup(grain)
  try {
    llm.push({ calls: [PLAN(ws, 'proposed.txt')] })
    await newChat(page)
    await say(page, 'touch something')
    await expect(planCard(page)).toBeVisible({ timeout: 90_000 })
    await planCard(page).getByRole('button', { name: /Edit arguments/ }).first().click()
    const ta = planCard(page).locator('textarea').first()
    await ta.fill(JSON.stringify({ command: 'touch edited.txt', cwd: ws }))
    llm.push({ calls: [touch(ws, 'edited.txt')] }, { calls: [touch(ws, 'proposed.txt')] }, { text: 'done' })
    // an edited argument turns the untouched "Approve & run" into "Approve with changes"
    await expect(page.getByRole('button', { name: 'Approve & run' })).toHaveCount(0)
    await page.getByRole('button', { name: 'Approve with changes' }).click()
    await expect(card(page)).toContainText('touch proposed.txt', { timeout: 90_000 }) // the original args are no longer approved
    expect(existsSync(join(ws, 'edited.txt'))).toBe(true)
    expect(existsSync(join(ws, 'proposed.txt'))).toBe(false)
    await card(page).getByRole('button', { name: 'Deny', exact: true }).click()
    await expect(page.locator('.msg.assistant').last()).toContainText('done', { timeout: 90_000 })
  } finally { rmScratch(ws) }
})

test('plan card: Reject runs nothing and the model is told the plan was rejected', async ({ grain }) => {
  const { page } = grain
  const { ws, llm } = await setup(grain)
  try {
    llm.push({ calls: [PLAN(ws, 'never.txt')] })
    await newChat(page)
    await say(page, 'touch never')
    await expect(planCard(page)).toBeVisible({ timeout: 90_000 })
    llm.push({ text: 'understood, plan dropped' })
    await planCard(page).getByRole('button', { name: 'Reject', exact: true }).click()
    await expect(page.locator('.msg.assistant').last()).toContainText('plan dropped', { timeout: 90_000 })
    expect(existsSync(join(ws, 'never.txt'))).toBe(false)
    expect(JSON.stringify(llm.requests.at(-1))).toMatch(/rejected this plan/i)
    expect(await pending(grain)).toHaveLength(0)
    expect(realErrors(grain)).toEqual([])
  } finally { rmScratch(ws) }
})

test('plan mode auto: a read-only first turn needs no plan; the first change turns planning on', async ({ grain }) => {
  const { page } = grain
  const { ws, llm } = await setup(grain, 'auto')
  try {
    llm.push({ calls: [touch(ws, 'auto.txt')] }, { calls: [PLAN(ws, 'auto.txt')] })
    await newChat(page)
    await say(page, 'touch auto.txt')
    // the direct change is refused and the model must plan first
    await expect(planCard(page)).toBeVisible({ timeout: 90_000 })
    expect(existsSync(join(ws, 'auto.txt'))).toBe(false)
    await say(page, '!!reply and a plain reply with no tools') // queued behind the waiting plan
    expect(realErrors(grain)).toEqual([])
  } finally { rmScratch(ws) }
})

test('plan card and approval card fit at 820x520', async ({ grain }) => {
  const { page } = grain
  const { ws, llm } = await setup(grain)
  try {
    await resize(grain)
    llm.push({ calls: [PLAN(ws, 'a.txt', 'b.txt', 'c.txt', 'd.txt', 'e.txt')] })
    await newChat(page)
    await say(page, 'many files')
    await expect(planCard(page)).toBeVisible({ timeout: 90_000 })
    const approve = page.getByRole('button', { name: 'Approve & run' })
    await approve.scrollIntoViewIfNeeded()
    await expect(approve).toBeVisible()
    const box = await approve.boundingBox()
    expect(box.x + box.width).toBeLessThanOrEqual(820)
    expect(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1)).toBe(false)
    expect(realErrors(grain)).toEqual([])
  } finally { rmScratch(ws) }
})
